"""Saving and loading JSON files. No Streamlit here."""
import json
import re
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pydantic import BaseModel

import config
from backend.schemas import Course, LearningPath, Span, StudentProfile


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug or "student"


def student_dir(student_id: str) -> Path:
    p = config.DATA_DIR / "students" / student_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def course_dir(course_id: str) -> Path:
    p = config.DATA_DIR / "courses" / course_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def _plain(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return obj.model_dump()
    if isinstance(obj, (list, tuple)):
        return [_plain(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _plain(v) for k, v in obj.items()}
    return obj


def save_json(path: Path, obj: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(_plain(obj), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def list_students() -> List[str]:
    root = config.DATA_DIR / "students"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def list_courses() -> List[str]:
    root = config.DATA_DIR / "courses"
    return sorted(p.name for p in root.iterdir() if p.is_dir()) if root.exists() else []


def load_course(course_id: str) -> Optional[Tuple[Course, List[Span]]]:
    d = config.DATA_DIR / "courses" / course_id
    c, s = load_json(d / "topics.json"), load_json(d / "source_spans.json")
    if not c or not s:
        return None
    return Course.model_validate(c), [Span.model_validate(x) for x in s]


def load_profile(student_id: str) -> Optional[StudentProfile]:
    data = load_json(student_dir(student_id) / "profile.json")
    return StudentProfile.model_validate(data) if data else None


def load_path(student_id: str) -> Optional[LearningPath]:
    data = load_json(student_dir(student_id) / "path.json")
    return LearningPath.model_validate(data) if data else None


# Pre-test files are kept per student AND per course, so a second course never overwrites the first.
def pretest_path(student_id: str, course_id: str) -> Path:
    return student_dir(student_id) / "pretests" / f"{slugify(course_id)}.json"


def pretest_result_path(student_id: str, course_id: str) -> Path:
    return student_dir(student_id) / "pretest_results" / f"{slugify(course_id)}.json"


def save_pretest(student_id: str, course_id: str, pretest: Any) -> None:
    save_json(pretest_path(student_id, course_id), {"student_id": student_id, "course_id": course_id, "pretest": pretest})


def save_pretest_result(student_id: str, course_id: str, summary: Any, placements: Any, answers: Any) -> None:
    save_json(pretest_result_path(student_id, course_id), {
        "student_id": student_id, "course_id": course_id, "summary": summary, "placements": placements, "answers": answers})


def load_pretest_result(student_id: str, course_id: str) -> Optional[dict]:
    return load_json(pretest_result_path(student_id, course_id))
