"""Stage 7 core: attempt counting, fresh retry questions, cause guessing, and teacher alerts. Rules first, no LLM."""
from typing import List, Optional

import config
from backend import storage
from backend.schemas import Attempt, Lesson, Question, SupportAction, TeacherAlert, Topic

CAUSE_TEXT = {
    "rushing": "It looks like the answers came very quickly. Next time, tell yourself your reason before you choose.",
    "missing_prerequisite": "Some basics from an earlier topic may still be a little shaky, and that is okay.",
    "misconception": "One idea may be stuck in a slightly different shape in your mind. A new example can help.",
    "too_much": "This may be too much at once. Let's go in smaller steps.",
}


def _path(student_id: str):
    return storage.student_dir(student_id) / "attempts.json"


def load_attempts(student_id: str) -> List[Attempt]:
    return [Attempt.model_validate(a) for a in storage.load_json(_path(student_id), [])]


def save_attempt(student_id: str, attempt: Attempt) -> None:
    storage.save_json(_path(student_id), load_attempts(student_id) + [attempt])


def recent_attempts(attempts: List[Attempt], topic_id: str) -> List[Attempt]:
    """Attempts for one topic since the last pass (the count resets after a pass)."""
    out: List[Attempt] = []
    for a in attempts:
        if a.topic_id != topic_id:
            continue
        out = [] if a.passed else out + [a]
    return out


def failed_count(attempts: List[Attempt], topic_id: str) -> int:
    return len(recent_attempts(attempts, topic_id))


def pick_quiz(lesson: Lesson, attempts: List[Attempt]) -> List[Question]:
    """One question per objective. Never repeats a question from the previous attempt; prefers unseen ones."""
    prev = set(attempts[-1].asked_question_ids) if attempts else set()
    seen = {q for a in attempts for q in a.asked_question_ids}
    picked = []
    for i in range(len(lesson.objectives)):
        pool = [q for q in lesson.quiz + lesson.quiz_variants if q.objective_index == i]
        choice = ([q for q in pool if q.id not in seen] or [q for q in pool if q.id not in prev] or pool)[0]
        picked.append(choice)
    return picked


def diagnose_cause(attempts: List[Attempt], n_questions: int, has_prerequisite: bool) -> str:
    """Rules only. The cause is a guess, not a certainty."""
    last = attempts[-1]
    if last.seconds_taken and last.seconds_taken < config.RUSH_SECONDS_PER_QUESTION * n_questions:
        return "rushing"
    repeated = set(attempts[-2].wrong_question_ids) & set(last.wrong_question_ids) if len(attempts) > 1 else set()
    if repeated:
        return "misconception"
    if has_prerequisite and len(last.wrong_question_ids) * 3 >= n_questions * 2:
        return "missing_prerequisite"
    return "too_much"


def plan_for_attempt(topic: Topic, attempts: List[Attempt], n_questions: int, already_bridged: bool) -> SupportAction:
    """`attempts` = failed attempts since the last pass, including the one just made."""
    n = len(attempts)
    if n == 1:
        return SupportAction(kind="hint_retry", message="Not yet, and that is completely okay. Let's look again at the part that was tricky, "
                             "then try some fresh questions.")
    cause = diagnose_cause(attempts, n_questions, bool(topic.prerequisites))
    if n == 2:
        return SupportAction(kind="switch_route", cause=cause, use_extra_example=True,
                             message=f"This idea is tricky. Let's come at it from a different angle. {CAUSE_TEXT[cause]}")
    if already_bridged or n >= 4:
        return SupportAction(kind="to_person", cause=cause, alert=True,
                             message="You have worked hard on this one. Let's ask your teacher to help you with it personally.")
    return SupportAction(kind="bridge_and_alert", cause=cause, alert=True,
                         bridge_topic_id=topic.prerequisites[0] if topic.prerequisites else None,
                         message=f"Let's step back and build a stronger base first. {CAUSE_TEXT[cause]} I've also let your teacher know, so you have extra help.")


def create_alert(student_id: str, topic: Topic, attempts: List[Attempt], cause: str, tried: List[str], day: int) -> TeacherAlert:
    """Exactly one open alert per student and topic."""
    path = storage.student_dir(student_id) / "alerts.json"
    alerts = [TeacherAlert.model_validate(a) for a in storage.load_json(path, [])]
    for a in alerts:
        if a.topic_id == topic.id and not a.resolved:
            return a
    wrong = sorted({q for at in attempts for q in at.wrong_question_ids})
    alert = TeacherAlert(student_id=student_id, topic_id=topic.id,
                         reason=f"{len(attempts)} quiz attempts not yet passed on '{topic.title}'. Wrong questions: {', '.join(wrong) or 'n/a'}.",
                         likely_cause=CAUSE_TEXT.get(cause, cause) + " (a guess, not certain)", tried=tried, created_day=day)
    storage.save_json(path, alerts + [alert])
    return alert
