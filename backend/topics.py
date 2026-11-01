"""Stage 2: find topics in the passages."""
import hashlib
import json
from typing import Callable, List, Optional, Tuple

from pydantic import BaseModel

import config
from backend import llm, runtime
from backend.schemas import Span, Topic


class TopicList(BaseModel):
    topics: List[Topic]


def make_windows(spans: List[Span]) -> Tuple[List[List[Span]], bool]:
    """Group passages into windows of at most WINDOW_CHARS. Returns (windows, was_cut_short)."""
    windows, cur, size = [], [], 0
    for s in spans:
        n = len(s.text) + 8
        if cur and size + n > config.WINDOW_CHARS:
            windows.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        windows.append(cur)
    return windows[:config.MAX_WINDOWS], len(windows) > config.MAX_WINDOWS


def _renumber(topics: List[Topic], offset: int) -> List[Topic]:
    mapping = {t.id: f"T{offset + i + 1}" for i, t in enumerate(topics)}
    out = []
    for t in topics:
        d = t.model_dump()
        d["id"] = mapping[t.id]
        d["prerequisites"] = [mapping[p] for p in t.prerequisites if p in mapping]
        out.append(Topic(**d))
    return out


def clean_topics(topics: List[Topic], valid_span_ids: set) -> List[Topic]:
    """Drop topics with no valid passage, renumber T1..Tn, keep prerequisites only if they point to earlier topics."""
    kept = []
    for t in topics:
        spans = [s for s in t.source_spans if s in valid_span_ids]
        if spans:
            kept.append((t, spans))
    new_id = {t.id: f"T{i + 1}" for i, (t, _) in enumerate(kept)}
    out = []
    for i, (t, spans) in enumerate(kept):
        prereqs = [new_id[p] for p in t.prerequisites
                   if p in new_id and int(new_id[p][1:]) < i + 1 and new_id[p] != new_id[t.id]]
        out.append(t.model_copy(update={"id": f"T{i + 1}", "source_spans": spans,
                                        "prerequisites": list(dict.fromkeys(prereqs))}))
    return out


def find_topics(spans: List[Span], progress_callback: Optional[Callable[[float, str], None]] = None) -> List[Topic]:
    """Same result as before. The parts of a long document are read together instead of one after another, and the
    same document read twice (a rerun, a second click) is answered once."""
    key = "topics:" + hashlib.sha256("|".join(f"{s.id}:{s.text}" for s in spans).encode("utf-8")).hexdigest()
    return runtime.singleflight(key, lambda: _find_topics(spans, progress_callback))


def _find_topics(spans: List[Span], progress_callback: Optional[Callable[[float, str], None]] = None) -> List[Topic]:
    windows, _ = make_windows(spans)
    per = config.MAX_TOPICS_SINGLE if len(windows) == 1 else config.MAX_TOPICS_PER_WINDOW

    def read(w: List[Span]) -> List[Topic]:
        text = "\n\n".join(f"[{s.id}] {s.text}" for s in w)
        prompt = (
            f"Read the passages and list up to {per} main topics a student should learn, simple to complex.\n"
            'Output {"topics":[{"id":"T1","title":"short title","summary":"one or two simple sentences",'
            '"prerequisites":["ids of EARLIER topics in your list, or empty"],"source_spans":["S1","S2"],'
            '"difficulty":"beginner|intermediate|advanced"}]}\n'
            "Use ONLY the passages. source_spans must be passage ids shown in square brackets.\n\n"
            f"PASSAGES:\n{text}")
        return llm.ask_json(prompt, TopicList, system=llm.TEACHER_SYSTEM, max_tokens=2500, role="complex", cache=True).topics

    if progress_callback:
        progress_callback(0.0, f"Reading {len(windows)} part{'s' if len(windows) != 1 else ''}...")
    parts = runtime.parallel([(lambda w=w: read(w)) for w in windows])
    found: List[Topic] = []
    for part in parts:
        if isinstance(part, Exception):
            raise part
        found.extend(_renumber(part, len(found)))
    if len(windows) > 1:
        if progress_callback:
            progress_callback(len(windows) / (len(windows) + 1), "Putting the topics in order...")
        listing = json.dumps([t.model_dump() for t in found])
        prompt = ("Merge duplicate topics, order them from simple to complex, keep at most 10, and renumber ids T1, T2... "
                  "Prerequisites may only point to EARLIER topics. Keep each topic's source_spans.\n"
                  'Output {"topics":[...]} with the same keys.\n\nTOPICS:\n' + listing)
        try:
            found = llm.ask_json(prompt, TopicList, system=llm.TEACHER_SYSTEM, max_tokens=3000, role="complex", cache=True).topics
        except llm.LLMError:
            pass  # keep the unmerged list
    if progress_callback:
        progress_callback(1.0, "Done.")
    return clean_topics(found, {s.id for s in spans})


def make_course_title(topics: List[Topic]) -> str:
    fallback = f"{topics[0].title} course" if topics else "My course"
    try:
        titles = "; ".join(t.title for t in topics)
        raw = llm.ask(f"Give a short course title (at most 6 words) for these topics: {titles}. Reply with the title only.",
                      temperature=0.3, max_tokens=60, role="chat", cache=True)
        return raw.strip().strip('"\'').splitlines()[0][:80] or fallback
    except (llm.LLMError, IndexError):
        return fallback
