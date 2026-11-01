"""Find the passages of the uploaded document that are relevant to a question. No Streamlit and no LLM here.

Plain keyword matching weighted by how rare a word is across the passages (IDF). It needs no extra libraries.
If nothing matches, it returns an empty list so callers can say "that is not in your material" instead of guessing."""
import math
import re
from typing import List, Optional, Sequence, Tuple

from backend.schemas import Span

STOPWORDS = set(
    "the a an and or but if then so to of in on at for with by from as is are was were be been being am do does did "
    "this that these those it its i me my we our you your he she they them his her their what which who whom whose "
    "when where why how can could should would will shall may might must about into over under than too very just "
    "please tell explain mean means meaning thing things topic subject question teach say said know like also not "
    "no yes any some there here have has had get got give make more most".split())


def _stem(w: str) -> str:
    for suffix in ("ing", "ed", "es", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 3:
            return w[: -len(suffix)]
    return w


def tokens(text: str) -> List[str]:
    out = []
    for w in re.findall(r"\w+", (text or "").lower()):
        if w in STOPWORDS or w.isdigit():
            continue
        if w.isascii() and len(w) < 3:
            continue
        out.append(_stem(w))
    return out


def retrieve(query: str, spans: Sequence[Span], k: int = 3, prefer_ids: Optional[Sequence[str]] = None) -> List[Span]:
    """Return up to k passages that best match the query, best first. Empty list if nothing matches.
    prefer_ids: passages (for example the current topic's) that get a small bonus."""
    q = set(tokens(query))
    if not q or not spans:
        return []
    span_tokens = [set(tokens(s.text)) for s in spans]
    n = len(spans)
    df = {t: sum(1 for st in span_tokens if t in st) for t in q}
    prefer = set(prefer_ids or [])
    # With three or more meaningful words, a passage must contain at least a third of them: one stray word
    # (for example "holes" in an unrelated question) is not enough to call it relevant.
    need = 1 if len(q) < 3 else math.ceil(len(q) / 3)
    scored: List[Tuple[float, int]] = []
    for i, (s, st) in enumerate(zip(spans, span_tokens)):
        if sum(1 for t in q if t in st) < need:
            continue
        score = sum(math.log(1 + n / df[t]) for t in q if t in st)
        if score > 0 and s.id in prefer:
            score *= 1.25
        if score > 0:
            scored.append((score, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [spans[i] for _, i in scored[:k]]


def select(spans: Sequence[Span], query: str, budget_chars: int) -> List[Span]:
    """Passages to send to the model for one job. If all of them fit in the budget, all are sent (nothing is lost).
    Otherwise the most relevant ones for the query are kept until the budget is used, and returned in document order.
    With no match at all, the leading passages are used. The first chosen passage is always kept, even if it is long."""
    spans = list(spans)
    if sum(len(s.text) for s in spans) <= budget_chars:
        return spans
    ranked = retrieve(query, spans, k=len(spans))
    ranked += [s for s in spans if s not in ranked]          # unmatched passages only fill leftover room, in order
    chosen, size = [], 0
    for s in ranked:
        if chosen and size + len(s.text) > budget_chars:
            continue
        chosen.append(s)
        size += len(s.text)
    order = {s.id: i for i, s in enumerate(spans)}
    return sorted(chosen, key=lambda s: order[s.id])
