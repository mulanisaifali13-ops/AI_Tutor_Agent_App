"""Stage 5: lesson maker with the nine-step blueprint, cached on disk.

Speed design (same lesson content and checks as before):
- the teaching text is written first (it decides the objectives); then the practice questions, the quiz questions and the
  retry questions are written TOGETHER, because they only need the objectives
- each job gets only the most relevant passages inside a budget, and a token limit that fits the job
- if the automatic checks find a problem, only the broken part is written again (not the whole lesson)
- two identical requests at the same time (a Streamlit rerun) share one generation; finished lessons are read from disk"""
import random
from typing import Callable, List, Optional, Tuple

from pydantic import BaseModel

import config
from backend import llm, review, runtime, storage, usage
from backend import questions as qgen
from backend.schemas import Lesson, Question, ReviewResult, Span, StudentProfile, Topic

VARIANT_FOR_STATE = {"full_lesson": "full", "short_path": "short", "skip_with_check": "short",
                     "misconception_lesson": "misconception", "bridge_first": "bridge"}

VARIANT_RULES = {
    "full": "A complete lesson from the beginning.",
    "short": "A short lesson. Skip the hook (use an empty string) and go straight to the key missing ideas.",
    "misconception": "Open the hook with an example that shows why a common wrong belief about this topic fails, then teach the correct idea.",
    "bridge": "A gentle bridge lesson: only the basic ideas a beginner needs first. Very small steps and very simple words.",
}


class _Core(BaseModel):
    hook: str
    objectives: List[str]
    explanation: List[str]
    worked_example: str
    extra_worked_example: str
    summary: str


class _Practice(BaseModel):
    warmup: List[Question] = []
    practice: List[Question]
    transfer_question: Question


def _context(profile: StudentProfile) -> str:
    return (f"Student level: {profile.level}. Interests: {', '.join(profile.interests) or 'not given'}. "
            f"Goal: {profile.short_term_goal or 'not given'}. Likes to learn by: {', '.join(profile.learning_styles) or 'not given'}.")


# ---------- the four parts, each one model call ----------

def _make_core(topic: Topic, spans: List[Span], profile: StudentProfile, variant: str, n_obj: int) -> _Core:
    text = qgen.passages_text(topic, spans, config.PASSAGE_BUDGET["lesson_core"], f"{topic.title} {topic.summary}")
    return llm.ask_json(
        f"Write the teaching part of a lesson on: {topic.title} - {topic.summary}\n{VARIANT_RULES[variant]}\n{_context(profile)}\n"
        "Use simple words for this level and use the student's interests in examples where it fits.\n"
        f'Output {{"hook":"a real problem or curious question","objectives":["By the end you will be able to ..."] (exactly {n_obj}),'
        '"explanation":["short segment, one idea each, key terms in **bold**"] (3 to 5 segments),'
        '"worked_example":"solved step by step with the reasoning shown",'
        '"extra_worked_example":"a different example in a different context",'
        '"summary":"two or three sentences"}\n'
        f"Use ONLY the passages. If they do not say it, leave it out.\n\nPASSAGES:\n{text}",
        _Core, system=llm.TEACHER_SYSTEM, max_tokens=config.MAX_TOKENS["lesson_core"], role="lesson")


def _make_practice(topic: Topic, spans: List[Span], profile: StudentProfile, variant: str, obj_text: str) -> _Practice:
    text = qgen.passages_text(topic, spans, config.PASSAGE_BUDGET["lesson_practice"], f"{topic.title} {obj_text}")
    return llm.ask_json(
        f"Write practice questions for a lesson on: {topic.title}. Objectives: {obj_text}\n{_context(profile)}\n"
        f"Output {{\"warmup\":[{'2 easy recall questions on what the student may already know' if variant != 'short' else 'empty list'}],"
        '"practice":[4 questions from easy to harder; at least 2 must have kind "apply" using a NEW case],'
        '"transfer_question":one question in a brand new situation}\nEach question uses this shape: ' + qgen.Q_SHAPE +
        f"\nOptions must be 4 different answers; give an explanation for EVERY option. Use ONLY the passages.\n\nPASSAGES:\n{text}",
        _Practice, system=llm.TEACHER_SYSTEM, max_tokens=config.MAX_TOKENS["lesson_practice"], role="quiz")


def _make_quiz(topic: Topic, spans: List[Span], variant: str, objectives: List[str], obj_text: str) -> List[Question]:
    n = len(objectives)
    quiz = qgen.ask_questions(
        f"Write {n} quiz questions, one for each objective in order (objective_index 0 to {n - 1}). "
        f"Topic: {topic.title}. Objectives: {obj_text}", topic, spans, f"{topic.id}-{variant}-qz", n,
        query=f"{topic.title} {obj_text}", max_tokens=config.MAX_TOKENS["quiz"])
    return [q.model_copy(update={"objective_index": i}) for i, q in enumerate(quiz)]


def _make_variants(topic: Topic, spans: List[Span], variant: str, objectives: List[str], obj_text: str) -> List[Question]:
    n, per = len(objectives), config.VARIANTS_PER_OBJECTIVE
    out = qgen.ask_questions(
        f"Write {per * n} extra quiz questions for retries: the first {per} for objective 0, the next {per} for objective 1"
        f"{', and so on' if n > 2 else ''}. They must be different from each other. "
        f"Topic: {topic.title}. Objectives: {obj_text}", topic, spans, f"{topic.id}-{variant}-rt", per * n,
        query=f"{topic.title} {obj_text}", max_tokens=config.MAX_TOKENS["variants"])
    return [q.model_copy(update={"objective_index": min(i // per, n - 1)}) for i, q in enumerate(out)]


def _first(results: list) -> list:
    """Raise the first failure from a parallel batch, otherwise return the results."""
    for r in results:
        if isinstance(r, Exception):
            raise r
    return results


def generate_lesson(topic: Topic, spans: List[Span], profile: StudentProfile, variant: str = "full") -> Lesson:
    n_obj = 2 if variant == "short" else config.OBJECTIVES_PER_LESSON
    core = _make_core(topic, spans, profile, variant, n_obj)
    objectives = core.objectives[:n_obj]
    obj_text = "; ".join(f"({i}) {o}" for i, o in enumerate(objectives))
    part, quiz, variants = _first(runtime.parallel([
        lambda: _make_practice(topic, spans, profile, variant, obj_text),
        lambda: _make_quiz(topic, spans, variant, objectives, obj_text),
        lambda: _make_variants(topic, spans, variant, objectives, obj_text)]))
    rng = random.Random()

    def rename(qs: List[Question], prefix: str) -> List[Question]:
        return [qgen.finalize(q, topic, f"{topic.id}-{variant}-{prefix}{i + 1}", rng) for i, q in enumerate(qs)]

    return Lesson(
        topic_id=topic.id, variant=variant, hook=core.hook if variant != "short" else "", objectives=objectives,
        warmup=rename(part.warmup[:2], "wu") if variant != "short" else [], explanation=core.explanation,
        worked_example=core.worked_example, extra_worked_example=core.extra_worked_example,
        practice=rename(part.practice[:4], "pr"), quiz=quiz, quiz_variants=variants, summary=core.summary,
        transfer_question=rename([part.transfer_question], "tr")[0], source_spans=list(topic.source_spans))


# ---------- repair: write again only what the checks flagged ----------

def _parts_to_redo(problems: List[str]) -> Optional[set]:
    """Which parts the structural problems point at: {'practice','quiz','variants'}; None means a text part is
    broken (or we cannot tell), so the whole lesson is written again, as before."""
    parts = set()
    for p in problems:
        label = p.split(":")[0]
        if "-qz" in label or "no quiz question" in p or p.startswith("quiz"):
            parts.add("quiz")
        elif "-rt" in label or "retry questions" in p:
            parts.add("variants")
        elif any(k in label for k in ("-pr", "-wu", "-tr")) or "apply-level" in p or p.startswith(("practice", "warm-up")):
            parts.add("practice")
        else:
            return None
    return parts or None


def repair_lesson(lesson: Lesson, problems: List[str], topic: Topic, spans: List[Span],
                  profile: StudentProfile) -> Optional[Lesson]:
    """Return the lesson with only the flagged question sets rewritten, or None if the whole lesson must be redone."""
    parts = _parts_to_redo(problems)
    if not parts:
        return None
    variant, objectives = lesson.variant, lesson.objectives
    obj_text = "; ".join(f"({i}) {o}" for i, o in enumerate(objectives))
    jobs = {"practice": lambda: _make_practice(topic, spans, profile, variant, obj_text),
            "quiz": lambda: _make_quiz(topic, spans, variant, objectives, obj_text),
            "variants": lambda: _make_variants(topic, spans, variant, objectives, obj_text)}
    names = sorted(parts)
    got = dict(zip(names, _first(runtime.parallel([jobs[n] for n in names]))))
    rng, update = random.Random(), {}
    if "practice" in got:
        def rename(qs: List[Question], prefix: str) -> List[Question]:
            return [qgen.finalize(q, topic, f"{topic.id}-{variant}-{prefix}{i + 1}", rng) for i, q in enumerate(qs)]
        part = got["practice"]
        update.update(practice=rename(part.practice[:4], "pr"), transfer_question=rename([part.transfer_question], "tr")[0],
                      warmup=rename(part.warmup[:2], "wu") if variant != "short" else [])
    if "quiz" in got:
        update["quiz"] = got["quiz"]
    if "variants" in got:
        update["quiz_variants"] = got["variants"]
    return lesson.model_copy(update=update)


def _check(lesson: Lesson, topic: Topic, attempt: int) -> Tuple[List[str], ReviewResult]:
    problems = review.structural_checks(lesson, topic)
    decision = "pass" if not problems else ("revise" if attempt < config.MAX_REVISIONS else "person_review")
    return problems, ReviewResult(topic_id=topic.id, structural_ok=not problems, structural_problems=problems,
                                  decision=decision, attempt=attempt)


def get_or_create_lesson(course_id: str, topic: Topic, spans: List[Span], profile: StudentProfile,
                         variant: str = "full") -> Tuple[Lesson, ReviewResult]:
    """Load the lesson from disk if it exists; otherwise generate, check, repair, and save.
    Safe to call twice at once (for example after a Streamlit rerun): the second call waits for the first."""
    base = storage.course_dir(course_id) / "lessons"
    lpath, rpath = base / f"{topic.id}_{variant}.json", base / f"{topic.id}_{variant}.review.json"

    def load() -> Optional[Tuple[Lesson, ReviewResult]]:
        if lpath.exists() and rpath.exists():
            try:
                return Lesson.model_validate(storage.load_json(lpath)), ReviewResult.model_validate(storage.load_json(rpath))
            except Exception:
                return None                                  # unreadable file: write it again
        return None

    saved = load()
    if saved:
        return saved

    def build() -> Tuple[Lesson, ReviewResult]:
        saved = load()                                       # a rerun may have finished it while we waited
        if saved:
            return saved
        with usage.task(f"lesson {topic.id} ({variant})"):
            lesson = generate_lesson(topic, spans, profile, variant)
            problems, result = _check(lesson, topic, 0)
            for attempt in range(1, config.MAX_REVISIONS + 1):
                if not problems:
                    break
                fixed = repair_lesson(lesson, problems, topic, spans, profile)
                lesson = fixed if fixed is not None else generate_lesson(topic, spans, profile, variant)
                problems, result = _check(lesson, topic, attempt)
        storage.save_json(lpath, lesson)
        storage.save_json(rpath, result)
        return lesson, result

    return runtime.singleflight(f"lesson:{course_id}:{topic.id}:{variant}", build)
