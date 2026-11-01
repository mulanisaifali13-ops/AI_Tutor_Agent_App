"""Stage 5: structural checks (code only) and human approval marker. The LLM quality reviewer is not in the MVP."""
from typing import List

import config
from backend import storage
from backend.schemas import Lesson, Question, Topic


def _question_problems(q: Question, label: str, valid_spans: set) -> List[str]:
    p = []
    if len({o.strip().lower() for o in q.options}) != 4:
        p.append(f"{label}: options are not 4 different answers")
    if not 0 <= q.correct_index <= 3:
        p.append(f"{label}: invalid correct answer")
    if any(s not in valid_spans for s in q.source_spans):
        p.append(f"{label}: unknown passage id")
    return p


def structural_checks(lesson: Lesson, topic: Topic) -> List[str]:
    problems: List[str] = []
    valid = set(topic.source_spans)
    short = lesson.variant == "short"
    for name, val in [("objectives", lesson.objectives), ("explanation", lesson.explanation),
                      ("worked_example", lesson.worked_example), ("extra_worked_example", lesson.extra_worked_example),
                      ("practice", lesson.practice), ("quiz", lesson.quiz), ("summary", lesson.summary)]:
        if not val:
            problems.append(f"{name} is empty")
    if not short and not lesson.hook.strip():
        problems.append("hook is empty")
    if not short and not lesson.warmup:
        problems.append("warm-up is empty")
    every = (lesson.warmup + lesson.practice + lesson.quiz + lesson.quiz_variants + [lesson.transfer_question])
    for q in every:
        problems += _question_problems(q, q.id, valid)
    for i, _ in enumerate(lesson.objectives):
        if not any(q.objective_index == i for q in lesson.quiz):
            problems.append(f"objective {i + 1} has no quiz question")
        if sum(q.objective_index == i for q in lesson.quiz_variants) < config.VARIANTS_PER_OBJECTIVE:
            problems.append(f"objective {i + 1} has fewer than {config.VARIANTS_PER_OBJECTIVE} retry questions")
    if sum(q.kind in ("apply", "choose_and_justify") for q in lesson.practice) < 2:
        problems.append("fewer than 2 apply-level practice questions")
    return problems


def approval_path(course_id: str, topic_id: str, variant: str):
    return storage.course_dir(course_id) / "lessons" / f"{topic_id}_{variant}.approved.json"


def approve(course_id: str, topic_id: str, variant: str) -> None:
    storage.save_json(approval_path(course_id, topic_id, variant), {"approved_by": "person"})


def is_approved(course_id: str, topic_id: str, variant: str) -> bool:
    return approval_path(course_id, topic_id, variant).exists()
