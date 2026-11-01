"""Stage 6: daily targets, check-ins and progress memory. LLM is used only for wording, with plain fallbacks."""
from typing import Dict, Optional

import config
from backend import llm, storage
from backend.schemas import DailyTarget, LearningPath, PathItem, StudentProfile


def default_progress() -> dict:
    return {"day_index": 1, "targets": [], "next_target": None, "streak": 0, "missed_streak": 0,
            "quiz_scores": {}, "completed_topics": [], "set_aside": [], "bridged": [],
            "history": [], "morning": {}, "last_evening": None, "interest": []}


def load_progress(student_id: str) -> dict:
    data = storage.load_json(storage.student_dir(student_id) / "progress.json", {}) or {}
    return {**default_progress(), **data}


def save_progress(student_id: str, progress: dict) -> None:
    storage.save_json(storage.student_dir(student_id) / "progress.json", progress)


def next_item(path: LearningPath, progress: dict) -> Optional[PathItem]:
    skip = set(progress["completed_topics"]) | set(progress["set_aside"])
    return next((i for i in path.items if i.topic_id not in skip), None)


def current_target(progress: dict) -> Optional[DailyTarget]:
    return DailyTarget.model_validate(progress["targets"][-1]) if progress["targets"] else None


def _describe(item: Optional[PathItem], minutes: int, titles: Dict[str, str]) -> str:
    if item is None:
        return "You finished your whole plan. Take a moment to enjoy that, then review what you learned."
    return f"Learn '{titles.get(item.topic_id, item.topic_id)}' for {minutes} minutes, then come back and tell me how it went."


def first_target(profile: StudentProfile, path: LearningPath, titles: Optional[Dict[str, str]] = None,
                 progress: Optional[dict] = None) -> DailyTarget:
    item = next_item(path, progress or default_progress())
    minutes = max(config.MIN_TARGET_MINUTES, min(profile.daily_minutes, 20))
    return DailyTarget(day_index=(progress or {}).get("day_index", 1), topic_id=item.topic_id if item else "",
                       minutes=minutes, description=_describe(item, minutes, titles or {}))


def next_target(previous: DailyTarget, completed: bool, profile: StudentProfile, path: LearningPath,
                progress: Optional[dict] = None, titles: Optional[Dict[str, str]] = None) -> DailyTarget:
    """Completed: a little more (never above the daily limit). Not completed: a little less (never below 5) and repeat."""
    progress = progress or default_progress()
    if completed:
        minutes = min(previous.minutes + config.TARGET_STEP_MINUTES, profile.daily_minutes)
        item = next_item(path, progress)
    else:
        minutes = max(config.MIN_TARGET_MINUTES, previous.minutes - config.TARGET_STEP_MINUTES)
        item = next((i for i in path.items if i.topic_id == previous.topic_id), None) or next_item(path, progress)
    minutes = max(config.MIN_TARGET_MINUTES, min(minutes, max(profile.daily_minutes, config.MIN_TARGET_MINUTES)))
    return DailyTarget(day_index=previous.day_index + 1, topic_id=item.topic_id if item else "", minutes=minutes,
                       description=_describe(item, minutes, titles or {}))


def update_streak(progress: dict, completed: bool) -> None:
    if completed:
        progress["streak"] += 1
        progress["missed_streak"] = 0
    else:
        progress["streak"] = 0
        progress["missed_streak"] += 1


def morning_message(profile: StudentProfile, target: DailyTarget) -> str:
    fallback = f"Good morning, {profile.name}! Today's small step: {target.description}"
    try:
        goal = profile.short_term_goal or (profile.interests[0] if profile.interests else "your learning")
        return llm.ask(f"Write a 1 to 2 sentence good-morning message for {profile.name}. Remind them of today's target: "
                       f"\"{target.description}\" and add one motivating line linked to their goal: {goal}.",
                       system=llm.TEACHER_SYSTEM, max_tokens=120)
    except llm.LLMError:
        return fallback


def _praise(profile: StudentProfile, target: DailyTarget) -> str:
    fallback = f"Well done, {profile.name}! You finished today's target. Each small step brings you closer to your goal."
    try:
        return llm.ask(f"Praise {profile.name} for completing: \"{target.description}\". Be specific and forward looking. "
                       "One or two sentences.", system=llm.TEACHER_SYSTEM, max_tokens=100)
    except llm.LLMError:
        return fallback


def missed_message(progress: dict) -> str:
    if progress["missed_streak"] >= config.KIND_MESSAGE_AFTER_MISSED_DAYS:
        return ("I noticed the last few days were hard to fit in, and that is okay. Skipping a lot can make the next topic "
                "feel harder, so let's try a smaller step today. Even a few minutes counts.")
    return ""


def evening_flow(progress: dict, profile: StudentProfile, path: LearningPath, target: DailyTarget,
                 completed: Optional[bool], titles: Optional[Dict[str, str]] = None) -> dict:
    """completed=None means the student sent nothing: ask. True: praise. False: reduce the target and repeat."""
    if completed is None:
        return {"kind": "ask", "message": "Did you complete today's target?"}
    target.status = "completed" if completed else "not_completed"
    progress["targets"][-1] = target.model_dump()
    update_streak(progress, completed)
    progress["history"].append({"day": target.day_index, "minutes": target.minutes if completed else 0, "completed": completed})
    if completed:
        msg = _praise(profile, target)
    else:
        msg = "That's okay, not yet! I'll make tomorrow's step a little smaller so it feels doable."
    kind_note = missed_message(progress)
    nxt = next_target(target, completed, profile, path, progress, titles)
    progress["next_target"] = nxt.model_dump()
    progress["last_evening"] = {"day": target.day_index, "message": (msg + " " + kind_note).strip()}
    return {"kind": "praise" if completed else "gentle", "message": progress["last_evening"]["message"], "next_target": nxt}


def advance_day(progress: dict) -> None:
    """Simulated clock: move to the next day and start the prepared target."""
    if progress.get("next_target"):
        progress["day_index"] += 1
        progress["targets"].append(progress["next_target"])
        progress["next_target"] = None
        progress["last_evening"] = None
