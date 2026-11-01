"""Token, time and character usage for the whole app. No Streamlit here.

ACTUAL numbers come from Groq: after every successful answer llm.ask() hands in the `usage` object Groq returned
(prompt_tokens -> input, completion_tokens -> output). ESTIMATED numbers are made BEFORE the call from the text we are
about to send (about 4 characters per token) plus the max_tokens limit we set, so you can see how good the estimate is and
whether the limits are set sensibly. Estimates never replace the actual counts. Failed or retried calls return no
answer and are not counted as answers (they are counted as retries). Totals live in data/usage.json."""
import contextvars
import json
import math
import threading
import time
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

import config

MAX_LOG = 200                      # newest answers kept for the table and charts
MAX_TASKS = 100                    # newest timed tasks (lesson, pre-test ...) kept
_LOCK = threading.Lock()
_TASK: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("usage_task", default=None)
_ZERO_ROLE = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "est_input_tokens": 0, "output_budget": 0, "seconds": 0.0}


def estimate_tokens(text: str) -> int:
    """Rough token estimate before a call: about 4 characters per token for English, never below 1 for real text."""
    n = len(text or "")
    return max(1, math.ceil(n / 4)) if n else 0


def _path():
    return config.DATA_DIR / "usage.json"


def _empty() -> Dict[str, Any]:
    return {"input_tokens": 0, "output_tokens": 0, "chars": 0, "calls": 0, "since": time.time(),
            "by_model": {}, "log": [], "est_input_tokens": 0, "output_budget": 0, "reasoning_tokens": 0,
            "seconds": 0.0, "retries": 0, "cache_hits": 0, "by_role": {}, "tasks": []}


def _load() -> Dict[str, Any]:
    state = _empty()
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items() if k in state})
    except (OSError, ValueError):
        pass
    return state


def _save(state: Dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(p)


def _count(usage_obj: Any, name: str) -> int:
    value = usage_obj.get(name) if isinstance(usage_obj, dict) else getattr(usage_obj, name, 0)
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def _reasoning(usage_obj: Any) -> int:
    details = usage_obj.get("completion_tokens_details") if isinstance(usage_obj, dict) else getattr(usage_obj, "completion_tokens_details", None)
    return _count(details, "reasoning_tokens") if details is not None else 0


def record(model: str, usage_obj: Optional[Any], text: str, role: str = "", seconds: float = 0.0,
           est_input: int = 0, out_budget: int = 0) -> None:
    """Add ONE successful answer to the totals. Never raises: counting must not break the teacher."""
    try:
        prompt_tokens = _count(usage_obj, "prompt_tokens")           # actual, from Groq -> input
        completion_tokens = _count(usage_obj, "completion_tokens")   # actual, from Groq -> output
        thinking = _reasoning(usage_obj) if usage_obj is not None else 0
        chars = len(text or "")                                      # counted from the response text
        seconds = round(float(seconds or 0.0), 3)
        with _LOCK:
            s = _load()
            s["input_tokens"] += prompt_tokens
            s["output_tokens"] += completion_tokens
            s["chars"] += chars
            s["calls"] += 1
            s["est_input_tokens"] += est_input
            s["output_budget"] += out_budget
            s["reasoning_tokens"] += thinking
            s["seconds"] = round(s["seconds"] + seconds, 3)
            m = s["by_model"].setdefault(model, {"calls": 0, "input_tokens": 0, "output_tokens": 0, "chars": 0, "seconds": 0.0})
            m["calls"] += 1
            m["input_tokens"] += prompt_tokens
            m["output_tokens"] += completion_tokens
            m["chars"] += chars
            m["seconds"] = round(m.get("seconds", 0.0) + seconds, 3)
            if role:
                r = s["by_role"].setdefault(role, dict(_ZERO_ROLE))
                r["calls"] += 1
                r["input_tokens"] += prompt_tokens
                r["output_tokens"] += completion_tokens
                r["est_input_tokens"] += est_input
                r["output_budget"] += out_budget
                r["seconds"] = round(r["seconds"] + seconds, 3)
            s["log"] = (s["log"] + [{"time": time.time(), "model": model, "role": role, "input_tokens": prompt_tokens,
                                     "output_tokens": completion_tokens, "chars": chars, "seconds": seconds,
                                     "est_input_tokens": est_input, "output_budget": out_budget,
                                     "reasoning_tokens": thinking}])[-MAX_LOG:]
            _save(s)
            t = _TASK.get()
            if t is not None:
                with t["lock"]:
                    t["calls"] += 1
                    t["input_tokens"] += prompt_tokens
                    t["output_tokens"] += completion_tokens
                    t["est_input_tokens"] += est_input
                    t["output_budget"] += out_budget
    except Exception:
        pass


def bump(name: str, n: int = 1) -> None:
    """Count an event that is not an answer: 'retries' (a failed attempt that was tried again) or 'cache_hits'."""
    try:
        with _LOCK:
            s = _load()
            s[name] = s.get(name, 0) + n
            _save(s)
            t = _TASK.get()
            if t is not None and name in t:
                with t["lock"]:
                    t[name] += n
    except Exception:
        pass


@contextmanager
def task(name: str) -> Iterator[dict]:
    """Time a whole job (a lesson, a pre-test ...) and add up the answers made inside it, including answers made
    by helper threads started with runtime.parallel. The result is appended to the 'tasks' list when the job ends."""
    t = {"name": name, "lock": threading.Lock(), "calls": 0, "input_tokens": 0, "output_tokens": 0,
         "est_input_tokens": 0, "output_budget": 0, "retries": 0, "cache_hits": 0}
    token = _TASK.set(t)
    t0 = time.perf_counter()
    try:
        yield t
    finally:
        _TASK.reset(token)
        t["seconds"] = round(time.perf_counter() - t0, 2)
        try:
            with _LOCK:
                s = _load()
                row = {k: v for k, v in t.items() if k != "lock"}
                row["time"] = time.time()
                s["tasks"] = (s["tasks"] + [row])[-MAX_TASKS:]
                _save(s)
        except Exception:
            pass


def snapshot() -> Dict[str, Any]:
    """Current totals (read fresh from disk, so every page and process sees the same numbers)."""
    s = _load()
    s["total_tokens"] = s["input_tokens"] + s["output_tokens"]
    return s


def reset() -> None:
    with _LOCK:
        _save(_empty())
