"""Shared question maker used by the pre-test (Stage 4) and lessons (Stage 5)."""
import random
from typing import Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel

import config
from backend import llm, retrieval, runtime
from backend.schemas import Question, Span, Topic

# id and topic_id are not asked for: finalize() sets them, so the model does not spend tokens writing them
Q_SHAPE = ('{"text":"question","options":["A","B","C","D"],"correct_index":0,'
           '"option_explanations":["why A is right or wrong","...","...","..."],'
           '"kind":"recall|explain|apply|compare|choose_and_justify","hint":"a gentle nudge",'
           '"source_spans":["S1"],"objective_index":0}')


class QuestionList(BaseModel):
    questions: List[Question]


def passages_text(topic: Topic, spans: List[Span], budget: Optional[int] = None, query: str = "") -> str:
    """The topic's passages as '[S1] text' blocks. With a budget, only the most relevant ones that fit are used
    (all of them when they already fit); without one, the old behaviour: leading passages up to PASSAGE_CHARS_PER_CALL."""
    by_id = {s.id: s for s in spans}
    mine = [by_id[sid] for sid in topic.source_spans if sid in by_id]
    if budget is not None:
        return "\n\n".join(f"[{s.id}] {s.text}" for s in retrieval.select(mine, query or f"{topic.title} {topic.summary}", budget))
    out, size = [], 0
    for s in mine:
        line = f"[{s.id}] {s.text}"
        if out and size + len(line) > config.PASSAGE_CHARS_PER_CALL:
            break
        out.append(line)
        size += len(line)
    return "\n\n".join(out)


def question_problems(q: Question) -> List[str]:
    problems = []
    opts = [o.strip().lower() for o in q.options]
    if len(set(opts)) != 4 or "" in opts:
        problems.append("options must be four different, non-empty answers")
    if any(not e.strip() for e in q.option_explanations):
        problems.append("every option needs an explanation")
    if not q.text.strip():
        problems.append("question text is empty")
    return problems


def finalize(q: Question, topic: Topic, qid: str, rng: random.Random) -> Question:
    """Set ids, keep only valid passage ids, and shuffle options so the answer is not always in the same place."""
    order = list(range(4))
    rng.shuffle(order)
    spans = [s for s in q.source_spans if s in topic.source_spans] or list(topic.source_spans)
    return q.model_copy(update={
        "id": qid, "topic_id": topic.id, "source_spans": spans,
        "options": [q.options[i] for i in order],
        "option_explanations": [q.option_explanations[i] for i in order],
        "correct_index": order.index(q.correct_index),
    })


def ask_questions(task: str, topic: Topic, spans: List[Span], prefix: str, count: int, *, query: str = "",
                  budget: Optional[int] = None, max_tokens: Optional[int] = None, role: str = "quiz") -> List[Question]:
    """Ask the model for `count` questions on one topic, using only the most relevant passages of that topic.
    If some come back invalid, only the MISSING number is asked for (the good ones are kept), at most once."""
    rng = random.Random()
    budget = budget if budget is not None else config.PASSAGE_BUDGET["quiz"]
    passages = passages_text(topic, spans, budget, query or task)
    rules = ("Each question has exactly 4 options and ONE correct answer. Wrong options should reflect common mistakes. "
             "Give a short explanation for EVERY option (why it is right or why it is wrong). Use ONLY the passages. "
             "Question shape:\n" + Q_SHAPE)
    good: List[Question] = []
    need = count
    for attempt in range(2):
        extra = "" if attempt == 0 else ("\nDo not repeat these questions: " + " | ".join(q.text for q in good) if good else "")
        prompt = (f"{task if attempt == 0 else f'Write {need} more quiz questions on: {topic.title}.'}\n\n{rules}{extra}\n"
                  f'Output {{"questions":[...]}} with exactly {need} questions.\n\nPASSAGES:\n{passages}')
        limit = max_tokens or min(config.MAX_TOKENS["question_batch"], 450 * need + 500)
        batch = llm.ask_json(prompt, QuestionList, system=llm.TEACHER_SYSTEM, max_tokens=limit, role=role).questions
        good += [q for q in batch if not question_problems(q)][:need]
        need = count - len(good)
        if need <= 0:
            break
    if not good:
        raise llm.LLMError("The teacher could not prepare questions this time. Please try again.")
    return [finalize(q, topic, f"{prefix}{i + 1}", rng) for i, q in enumerate(good[:count])]


# ---------- checks used by the pre-test ----------

def is_duplicate(text: str, others: Sequence[str]) -> bool:
    """True when the question is (nearly) the same as one we already have: same normalised text or high word overlap."""
    a = set(retrieval.tokens(text))
    norm = " ".join((text or "").lower().split())
    for o in others:
        if norm == " ".join((o or "").lower().split()):
            return True
        b = set(retrieval.tokens(o))
        if a and b and len(a & b) / len(a | b) >= config.DUPLICATE_SIMILARITY:
            return True
    return False


class Verdict(BaseModel):
    id: str
    answer_index: int = -1               # the option the checker finds correct (0 to 3), -1 if the passages do not settle it
    only_one_correct: bool = True        # False when more than one option could be defended
    supported: bool = True               # False when the passages cannot answer the question


class VerdictList(BaseModel):
    verdicts: List[Verdict]


def _question_spans(q: Question, topics: Dict[str, Topic], by_span: Dict[str, Span]) -> List[Span]:
    ids = [i for i in q.source_spans if i in by_span]
    if not ids and q.topic_id in topics:
        ids = [i for i in topics[q.topic_id].source_spans if i in by_span]
    return [by_span[i] for i in ids]


def _batches(questions: List[Question], topics: Dict[str, Topic], by_span: Dict[str, Span]) -> List[List[Question]]:
    """Group questions so the passages sent in one call stay within VERIFY_PASSAGE_CHARS."""
    out, cur, seen, size = [], [], set(), 0
    for q in questions:
        new = [s for s in _question_spans(q, topics, by_span) if s.id not in seen]
        add = sum(len(s.text) for s in new)
        if cur and size + add > config.VERIFY_PASSAGE_CHARS:
            out.append(cur)
            cur, seen, size = [], set(), 0
            new = _question_spans(q, topics, by_span)
            add = sum(len(s.text) for s in new)
        cur.append(q)
        seen.update(s.id for s in new)
        size += add
    if cur:
        out.append(cur)
    return out


def _check_batch(batch: List[Question], tmap: Dict[str, Topic], by_span: Dict[str, Span]) -> List[Question]:
    used: Dict[str, Span] = {}
    for q in batch:
        for sp in _question_spans(q, tmap, by_span):
            used[sp.id] = sp
    passages = "\n\n".join(f"[{sp.id}] {sp.text}" for sp in used.values())[: config.VERIFY_PASSAGE_CHARS * 2]
    listing = "\n\n".join(
        f"ID: {q.id}\nQuestion: {q.text}\n" + "\n".join(f"{i}. {o}" for i, o in enumerate(q.options)) for q in batch)
    prompt = (
        "You are checking quiz questions. For EACH question, using ONLY the passages, decide which option is correct.\n"
        "- answer_index: the number (0 to 3) of the correct option, or -1 if the passages do not settle it.\n"
        "- only_one_correct: false if a careful student could defend more than one option, or none.\n"
        "- supported: false if the passages do not contain what is needed to answer the question.\n"
        'Output {"verdicts":[{"id":"...","answer_index":0,"only_one_correct":true,"supported":true}]} with one entry '
        f"per question.\n\nPASSAGES:\n{passages}\n\nQUESTIONS:\n{listing}")
    # The checker never sees the answer key, so identical questions + passages always give the same verdict: cache it.
    got = {v.id: v for v in llm.ask_json(prompt, VerdictList, system=llm.TEACHER_SYSTEM,
                                         max_tokens=min(config.MAX_TOKENS["verify"], 150 * len(batch) + 900),   # room for thinking + one verdict each

                                         role="complex", cache=True).verdicts}
    kept = []
    for q in batch:
        v = got.get(q.id)
        if v and v.supported and v.only_one_correct and v.answer_index == q.correct_index:
            kept.append(q.model_copy(update={"verified": True}))
    return kept


def verify_questions(questions: List[Question], topics: Sequence[Topic], spans: Sequence[Span]) -> List[Question]:
    """Ask a stronger model to answer each question WITHOUT seeing the answer key, using only the passages.
    Keep a question only if the checker picks the same option, finds only one defensible option, and says the passages
    support it. Kept questions are marked verified=True. Raises LLMError if the check cannot run (never keeps unchecked
    questions silently). Batches are checked together; each question is checked once."""
    if not questions:
        return []
    tmap, by_span = {t.id: t for t in topics}, {s.id: s for s in spans}
    batches = _batches(questions, tmap, by_span)
    results = runtime.parallel([(lambda b=b: _check_batch(b, tmap, by_span)) for b in batches])
    kept: List[Question] = []
    for r in results:
        if isinstance(r, Exception):
            raise r
        kept += r
    order = {q.id: i for i, q in enumerate(questions)}
    return sorted(kept, key=lambda q: order.get(q.id, 0))
