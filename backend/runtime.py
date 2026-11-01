"""Small helpers that make repeated or parallel work safe. No Streamlit here.

- singleflight(key, fn): when two identical requests arrive together (for example a Streamlit rerun while a lesson is
  still being written), only the first runs; the others wait for its result. Nothing is cached after it finishes.
- parallel(jobs): run independent jobs in a few threads, keep the order, and carry the usage-tracking context along.
- TTLCache: a small thread-safe cache with expiry, for read-only requests."""
import contextvars
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

import config

_LOCK = threading.Lock()
_INFLIGHT: Dict[str, "_Flight"] = {}


class _Flight:
    def __init__(self) -> None:
        self.done = threading.Event()
        self.result: Any = None
        self.error: Optional[BaseException] = None


def singleflight(key: str, fn: Callable[[], Any], wait_s: Optional[float] = None) -> Any:
    """Run fn() once per key at a time. Callers that arrive while it runs share its result (or its error)."""
    with _LOCK:
        flight = _INFLIGHT.get(key)
        leader = flight is None
        if leader:
            flight = _INFLIGHT[key] = _Flight()
    if leader:
        try:
            flight.result = fn()
            return flight.result
        except BaseException as e:           # share the failure, then let the leader raise it too
            flight.error = e
            raise
        finally:
            with _LOCK:
                _INFLIGHT.pop(key, None)
            flight.done.set()
    if not flight.done.wait(wait_s or config.SINGLEFLIGHT_WAIT_S):
        from backend.llm import LLMError
        raise LLMError("This is still being prepared. Please wait a moment and try again.")
    if flight.error is not None:
        raise flight.error
    return flight.result


def parallel(jobs: Sequence[Callable[[], Any]], workers: Optional[int] = None) -> List[Any]:
    """Run jobs together. Returns one entry per job, in order: the result, or the exception it raised."""
    jobs = list(jobs)
    n = max(1, min(workers or config.PARALLEL_WORKERS, len(jobs)))
    if n == 1:
        out: List[Any] = []
        for j in jobs:
            try:
                out.append(j())
            except Exception as e:
                out.append(e)
        return out

    def call(j: Callable[[], Any], ctx: contextvars.Context) -> Any:
        try:
            return ctx.run(j)
        except Exception as e:
            return e

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(call, j, contextvars.copy_context()) for j in jobs]
        return [f.result() for f in futures]


class TTLCache:
    def __init__(self, max_items: int = 256, ttl_s: float = 3600) -> None:
        self._d: "OrderedDict[str, tuple]" = OrderedDict()
        self._lock = threading.Lock()
        self.max_items, self.ttl_s = max_items, ttl_s
        self.hits = 0

    def get(self, key: str) -> Any:
        with self._lock:
            item = self._d.get(key)
            if item is None:
                return None
            if time.monotonic() - item[0] > self.ttl_s:
                del self._d[key]
                return None
            self._d.move_to_end(key)
            self.hits += 1
            return item[1]

    def put(self, key: str, value: Any) -> None:
        with self._lock:
            self._d[key] = (time.monotonic(), value)
            self._d.move_to_end(key)
            while len(self._d) > self.max_items:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()
