"""Groq wrapper: ask() and ask_json(). Reads the key from the environment only. No Streamlit here.

Speed and safety features:
- role based model routing ("chat", "quiz", "lesson", "complex"), see config.MODEL_ROUTES
- low "thinking" for reasoning models, set per role (config.ROLE_EFFORT)
- a timeout on every request, and a hard cap on attempts and total time (no more multi-minute silent waits)
- a rate-limited model is skipped (the next model in the role is used) instead of sleeping for a long time
- an answer cut off by max_tokens is retried once with a larger limit instead of being treated as bad JSON
- read-only requests can be cached (cache=True); identical requests running at the same time share one call
- every answer is counted with Groq's real token numbers, plus an estimate made before the call and the time it took"""
import hashlib
import json
import os
import random
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Type, TypeVar

from groq import (APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, Groq, InternalServerError,
                  NotFoundError, PermissionDeniedError, RateLimitError)
from pydantic import BaseModel, ValidationError

import config
from backend import runtime, usage

T = TypeVar("T", bound=BaseModel)

TEACHER_SYSTEM = (
    "You are the best teacher in the world: warm, patient, and encouraging. You never scold. "
    "You say 'not yet' instead of 'failed'. Use simple words that fit the student's level. "
    "Use only the provided passages when teaching; if the passages do not say it, say you do not know."
)
JSON_RULE = "Reply with ONE valid JSON object only. No explanation and no markdown. Use exactly the keys given."


class LLMError(Exception):
    """Raised with a friendly message that is safe to show to the student."""


@dataclass
class Reply:
    text: str
    model: str
    finish_reason: str = "stop"
    seconds: float = 0.0


_client: Optional[Groq] = None
_ids: List[str] = []                      # chat models this key can use
_state_lock = threading.Lock()
_dead: set = set()                        # models that do not exist for this key (skipped for the session)
_cooldown: Dict[str, float] = {}          # model -> time.monotonic() until which it is rate limited
_no_extras: set = set()                   # models that rejected the thinking setting
_cache = runtime.TTLCache(config.CACHE_MAX_ITEMS, config.CACHE_TTL_S)


# ---------- text helpers ----------

def clean_text(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()


def extract_json(text: str) -> dict:
    text = re.sub(r"```(?:json)?", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON object found in the reply.")
    body = text[start:end + 1]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", body))     # one cheap local repair: trailing commas


# ---------- models ----------

def has_key() -> bool:
    return bool(os.environ.get("GROQ_API_KEY"))


def get_client() -> Groq:
    global _client
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise LLMError("No API key found. Add GROQ_API_KEY to .streamlit/secrets.toml (or the app's Secrets).")
    with _state_lock:
        if _client is None:
            # max_retries=0: the SDK would otherwise retry silently on top of our own bounded retries
            _client = Groq(api_key=key, max_retries=0, timeout=float(max(config.ROLE_TIMEOUT_S.values())))
        return _client


def _usable_ids() -> List[str]:
    global _ids
    if _ids:
        return _ids
    try:
        ids = [m.id for m in get_client().models.list().data]
    except LLMError:
        raise
    except Exception as e:  # never include the key in messages
        raise LLMError(f"I could not reach the model service ({type(e).__name__}). Check your key and internet.")
    ids = [i for i in ids if not any(w in i.lower() for w in config.BLOCKED_MODEL_WORDS)]
    if not ids:
        raise LLMError("No chat models are available for this key.")
    _ids = ids
    return _ids


def available_chat_models() -> List[str]:
    """Legacy list (best first) used for the status line: preferred models your key can use, or everything it can use."""
    ids = _usable_ids()
    return [m for m in config.PREFERRED_MODELS if m in ids] or sorted(ids)


def route(role: str) -> List[str]:
    """Models for a role, best first: the configured ones your key can use and that are not known to be broken."""
    ids = _usable_ids()
    chosen = [m for m in config.MODEL_ROUTES.get(role, config.MODEL_ROUTES["chat"]) if m in ids and m not in _dead]
    if not chosen:                                           # none of the configured models exist: old behaviour
        chosen = [m for m in available_chat_models() if m not in _dead]
    if not chosen:
        raise LLMError("No chat models are available for this key.")
    return chosen


def routes_summary() -> Dict[str, str]:
    """role -> the model that will be tried first (for the sidebar)."""
    return {r: route(r)[0] for r in config.MODEL_ROUTES}


def verify_models(roles: Optional[List[str]] = None) -> Dict[str, Dict[str, Any]]:
    """Live check: send one tiny request to each model used by the routes and time it. Models that fail are marked
    broken for this session. Returns {model: {"ok": bool, "seconds": float, "error": str}}."""
    ids = _usable_ids()
    wanted: List[str] = []
    for r in roles or list(config.MODEL_ROUTES):
        for m in config.MODEL_ROUTES[r]:
            if m in ids and m not in wanted:
                wanted.append(m)
    out: Dict[str, Dict[str, Any]] = {}
    for m in wanted:
        t0 = time.perf_counter()
        try:
            kwargs: Dict[str, Any] = dict(model=m, messages=[{"role": "user", "content": "Reply with the word OK."}],
                                          max_tokens=64, temperature=0, timeout=20)
            extra = _extras(m, "chat")
            if extra:
                kwargs["extra_body"] = extra
            get_client().chat.completions.create(**kwargs)
            out[m] = {"ok": True, "seconds": round(time.perf_counter() - t0, 2), "error": ""}
        except Exception as e:
            out[m] = {"ok": False, "seconds": round(time.perf_counter() - t0, 2), "error": type(e).__name__}
            with _state_lock:
                _dead.add(m)
    return out


def _extras(model: str, role: str) -> Dict[str, Any]:
    """Provider-specific thinking setting, sent in extra_body so it works with any groq SDK version."""
    if model in _no_extras:
        return {}
    name = model.lower()
    if "gpt-oss" in name:
        return {"reasoning_effort": config.ROLE_EFFORT.get(role, "low")}
    if "qwen3" in name:
        return {"reasoning_effort": "default" if config.ROLE_EFFORT.get(role) == "high" else "none"}
    return {}


# ---------- one request ----------

def _retry_after(e: Exception, default: float) -> float:
    try:
        v = e.response.headers.get("retry-after")          # type: ignore[attr-defined]
        return float(v) if v is not None else default
    except Exception:
        return default


def _failed_generation(e: Exception) -> str:
    """Groq returns the text it could not validate as JSON; sometimes it is fine and saves a whole new call."""
    try:
        body = getattr(e, "body", None) or {}
        err = body.get("error", body) if isinstance(body, dict) else {}
        return str(err.get("failed_generation") or "")
    except Exception:
        return ""


def _cache_key(role: str, system: Optional[str], prompt: str, history: Optional[list], temperature: float,
               json_mode: bool, max_tokens: int) -> str:
    raw = json.dumps([role, system, prompt, history or [], temperature, json_mode, max_tokens], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def ask_full(prompt: str, system: Optional[str] = None, history: Optional[list] = None, temperature: float = 0.7,
             json_mode: bool = False, max_tokens: int = 2000, retries: Optional[int] = None, role: str = "chat",
             cache: bool = False) -> Reply:
    """Send one request (with bounded retries) and return the cleaned text plus how it ended.
    cache=True is only for read-only requests whose answer does not depend on who is asking or on chance."""
    if cache:
        key = _cache_key(role, system, prompt, history, temperature, json_mode, max_tokens)
        hit = _cache.get(key)
        if hit is not None:
            usage.bump("cache_hits")
            return hit
        reply = runtime.singleflight("llm:" + key, lambda: _ask_uncached(
            prompt, system, history, temperature, json_mode, max_tokens, retries, role))
        if reply.text and reply.finish_reason != "length":      # never keep an empty or cut-off answer
            _cache.put(key, reply)
        return reply
    return _ask_uncached(prompt, system, history, temperature, json_mode, max_tokens, retries, role)


def _ask_uncached(prompt: str, system: Optional[str], history: Optional[list], temperature: float, json_mode: bool,
                  max_tokens: int, retries: Optional[int], role: str) -> Reply:
    models = route(role)
    messages = ([{"role": "system", "content": system}] if system else []) + list(history or [])
    messages.append({"role": "user", "content": prompt})
    est_input = usage.estimate_tokens(" ".join(m["content"] for m in messages))   # estimate BEFORE the call
    attempts = min(retries or config.MAX_ATTEMPTS, config.MAX_ATTEMPTS)
    deadline = time.monotonic() + config.CALL_DEADLINE_S
    timeout = float(config.ROLE_TIMEOUT_S.get(role, 60))
    use_json, use_extras, mi = json_mode, True, 0
    for attempt in range(attempts):
        # skip models that are cooling down from a rate limit, if another one is free
        while mi < len(models) - 1 and _cooldown.get(models[mi], 0) > time.monotonic():
            mi += 1
        if mi >= len(models):
            break
        model = models[mi]
        kwargs: Dict[str, Any] = dict(model=model, messages=messages, temperature=temperature, max_tokens=max_tokens,
                                      timeout=max(5.0, min(timeout, deadline - time.monotonic())))
        if use_json:
            kwargs["response_format"] = {"type": "json_object"}
        extra = _extras(model, role) if use_extras else {}
        if extra:
            kwargs["extra_body"] = extra
        t0 = time.perf_counter()
        try:
            resp = get_client().chat.completions.create(**kwargs)
            seconds = time.perf_counter() - t0
            choice = resp.choices[0]
            answer = clean_text(choice.message.content)
            usage.record(model, getattr(resp, "usage", None), answer, role=role, seconds=seconds,
                         est_input=est_input, out_budget=max_tokens)
            return Reply(answer, model, getattr(choice, "finish_reason", "stop") or "stop", seconds)
        except RateLimitError as e:
            usage.bump("retries")
            wait = _retry_after(e, 2.0 * (attempt + 1))
            if mi + 1 < len(models) and wait > config.SWITCH_MODEL_IF_WAIT_S:
                _cooldown[model] = time.monotonic() + wait     # other models have their own limits: use the next one
                mi += 1
                continue
            if wait > config.MAX_RATE_WAIT_S or time.monotonic() + wait > deadline:
                raise LLMError("The teacher is very busy right now. Please wait a minute and try again.")
            time.sleep(wait)
        except (APITimeoutError, APIConnectionError, InternalServerError):
            usage.bump("retries")
            if time.monotonic() > deadline:
                break
            if attempt >= 1 and mi + 1 < len(models):          # the same model failed twice: try the next one
                mi += 1
            time.sleep(min(0.5 * 2 ** attempt + random.random() * 0.25, 4.0))
        except NotFoundError:
            with _state_lock:
                _dead.add(model)
            mi += 1
        except BadRequestError as e:
            usage.bump("retries")
            text = str(e).lower()
            salvage = _failed_generation(e) if use_json else ""
            if salvage:
                try:
                    extract_json(salvage)
                    usage.record(model, None, salvage, role=role, seconds=time.perf_counter() - t0,
                                 est_input=est_input, out_budget=max_tokens)
                    return Reply(clean_text(salvage), model, "recovered", time.perf_counter() - t0)
                except ValueError:
                    pass
            if use_json and ("json" in text or "response_format" in text):
                use_json = False
            elif use_extras and extra:
                use_extras = False
                with _state_lock:
                    _no_extras.add(model)
            elif use_json:
                use_json = False
            elif mi + 1 < len(models):
                mi += 1
            else:
                raise LLMError("The model could not answer this request. Please try again.")
        except (AuthenticationError, PermissionDeniedError):
            raise LLMError("The API key was not accepted. Please check GROQ_API_KEY.")
        except LLMError:
            raise
        except Exception as e:
            raise LLMError(f"Something went wrong while talking to the model ({type(e).__name__}).")
    raise LLMError("The teacher is very busy right now. Please wait a minute and try again.")


def ask(prompt: str, system: Optional[str] = None, history: Optional[list] = None,
        temperature: float = 0.7, json_mode: bool = False, max_tokens: int = 2000, retries: Optional[int] = None,
        role: str = "chat", cache: bool = False) -> str:
    return ask_full(prompt, system, history, temperature, json_mode, max_tokens, retries, role, cache).text


def ask_json(prompt: str, model_cls: Type[T], system: Optional[str] = None, tries: int = 2, max_tokens: int = 3000,
             role: str = "chat", cache: bool = False) -> T:
    """Ask for a JSON object and validate it. At most `tries` answers are asked for; a reply cut off by max_tokens
    gets one bigger limit (that is not counted as a bad answer). With cache=True only a VALIDATED result is reused."""
    if cache:
        key = "json:" + _cache_key(role, system, prompt, [model_cls.__name__], 0.2, True, max_tokens)
        hit = _cache.get(key)
        if hit is not None:
            usage.bump("cache_hits")
            return hit.model_copy(deep=True)
        result = runtime.singleflight(key, lambda: _ask_json(prompt, model_cls, system, tries, max_tokens, role))
        _cache.put(key, result)
        return result.model_copy(deep=True)
    return _ask_json(prompt, model_cls, system, tries, max_tokens, role)


def _ask_json(prompt: str, model_cls: Type[T], system: Optional[str], tries: int, max_tokens: int, role: str) -> T:
    base = f"{prompt}\n\n{JSON_RULE}"
    last_error, limit, bumped = "", max_tokens, False
    tries_left = tries
    while tries_left > 0:
        p = base if not last_error else f"{base}\n\nYour last reply was not valid: {last_error}\nFix it and reply again."
        reply = ask_full(p, system=system, temperature=0.2, json_mode=True, max_tokens=limit, role=role)
        try:
            return model_cls.model_validate(extract_json(reply.text))
        except (ValueError, ValidationError) as e:
            if reply.finish_reason == "length" and not bumped and limit < config.TOKEN_CEILING:
                limit, bumped, last_error = min(int(limit * 1.6), config.TOKEN_CEILING), True, ""
                continue                                       # cut off, not wrong: ask again with room, same try
            last_error = str(e)[:400]
            tries_left -= 1
    raise LLMError("The teacher could not prepare a clean answer. Please try again.")
