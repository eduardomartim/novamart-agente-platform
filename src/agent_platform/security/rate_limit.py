"""Local sliding-window rate limiting.

Scope note: this limiter is in-process only. It is the correct shape for the
demo and for a single-process deployment, but it is explicitly *not* a
distributed rate limiter -- see ``docs/architecture.md`` for the Redis-backed
design that production would require.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final, Protocol

from .sanitization import sanitize_text

GLOBAL_KEY: Final[str] = "global"

#: Which bucket the request being served spends from.
#:
#: This exists to solve one specific problem. Quota used to be global: every
#: caller drew from one bucket, so one caller could deny every other with a
#: loop, and the API had no identity to charge anyway. Now the HTTP boundary
#: knows who is calling and can charge them individually -- but the *policy
#: engine* also consults the limiter, from deep inside the graph, through a call
#: site that has no access to the caller.
#:
#: A parameter would have to be threaded through the orchestrator, the policy
#: context and the executor to reach it. A context variable is the honest shape
#: for the question being asked, which is not "which key did my caller pass"
#: but "which bucket is *this request* spending from" -- a property of the
#: dynamic extent, exactly like ``require_gateway()``.
#:
#: It also makes an invariant structural that used to be maintained by
#: coincidence. PL011 must consult the same bucket the entry point consumed;
#: when it did not, the rule addressed a bucket that was always empty and could
#: never fire (see the F1 note at the PL011 call site). Both now resolve
#: "the current request's bucket" and cannot drift apart.
_quota_key: ContextVar[str] = ContextVar("agent_platform_quota_key", default=GLOBAL_KEY)


def current_quota_key() -> str:
    """The bucket the request being served spends from."""
    return _quota_key.get()


@contextmanager
def quota_scope(key: str | None) -> Iterator[None]:
    """Charge quota to ``key`` for the dynamic extent of one request.

    ``None`` means the global bucket, which is what every non-HTTP caller gets:
    the CLI, the dashboard and the evaluator are unchanged by this and keep
    drawing from exactly the bucket they always did.
    """
    token = _quota_key.set(key or GLOBAL_KEY)
    try:
        yield
    finally:
        _quota_key.reset(token)


@dataclass(frozen=True, slots=True)
class RateLimitResult:
    allowed: bool
    reason: str = ""
    retry_after_seconds: float = 0.0
    remaining_minute: int = 0
    remaining_hour: int = 0


def _failure_reason(exc: Exception) -> str:
    """Describe an internal failure without disclosing what it mentioned.

    This string is user-facing: ``platform.run`` interpolates the reason into
    the response and returns before the output pipeline runs, so it never
    reaches ``secure_output``. An exception raised deep inside the limiter can
    carry a credential or a local path, so it is sanitised here, at the source,
    rather than relying on a downstream gate that this path does not cross.

    The exception *type* is kept: it is what makes the failure diagnosable, and
    a class name carries no payload.
    """
    detail, _ = sanitize_text(str(exc), max_chars=120)
    return f"rate limiter unavailable: {type(exc).__name__}: {detail}"


class RateLimiterLike(Protocol):
    """What a rate limiter has to offer its callers.

    Two implementations satisfy it: the in-process one below and the shared one
    in ``state/limiter.py``. Naming the shape rather than the class is what
    lets the policy engine hold either without knowing which.
    """

    def check(self, key: str | None = ...) -> RateLimitResult: ...

    def acquire(self, key: str | None = ...) -> RateLimitResult: ...


class RateLimiter:
    """Sliding-window limiter enforcing a per-minute and a per-hour quota.

    Fails closed: any unexpected internal error results in denial rather than
    an unmetered request.
    """

    def __init__(
        self,
        requests_per_minute: int,
        requests_per_hour: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if requests_per_minute < 1 or requests_per_hour < 1:
            raise ValueError("rate limits must be >= 1")
        self._per_minute = requests_per_minute
        self._per_hour = requests_per_hour
        self._clock = clock
        self._lock = threading.Lock()
        self._events: defaultdict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        events = self._events[key]
        cutoff = now - 3600.0
        while events and events[0] <= cutoff:
            events.popleft()
        return events

    @staticmethod
    def _count_since(events: deque[float], since: float) -> int:
        return sum(1 for ts in events if ts > since)

    def _evaluate(self, key: str, now: float) -> RateLimitResult:
        events = self._prune(key, now)
        minute_count = self._count_since(events, now - 60.0)
        hour_count = len(events)

        if minute_count >= self._per_minute:
            oldest_in_minute = next(ts for ts in events if ts > now - 60.0)
            return RateLimitResult(
                allowed=False,
                reason=f"per-minute limit of {self._per_minute} requests exceeded",
                retry_after_seconds=max(0.0, 60.0 - (now - oldest_in_minute)),
            )
        if hour_count >= self._per_hour:
            return RateLimitResult(
                allowed=False,
                reason=f"per-hour limit of {self._per_hour} requests exceeded",
                retry_after_seconds=max(0.0, 3600.0 - (now - events[0])),
            )
        return RateLimitResult(
            allowed=True,
            remaining_minute=self._per_minute - minute_count,
            remaining_hour=self._per_hour - hour_count,
        )

    def check(self, key: str | None = None) -> RateLimitResult:
        """Report quota state without consuming it.

        ``None`` resolves to the bucket the current request is spending from,
        so a caller that cannot see the principal -- the policy engine -- still
        consults the same bucket the entry point consumed.
        """
        try:
            with self._lock:
                return self._evaluate(key or current_quota_key(), self._clock())
        except Exception as exc:
            return RateLimitResult(allowed=False, reason=_failure_reason(exc))

    def acquire(self, key: str | None = None) -> RateLimitResult:
        """Consume one unit of quota if available.

        This is the enforcement call, made once per inbound user request before
        the orchestration graph is entered.
        """
        try:
            with self._lock:
                now = self._clock()
                key = key or current_quota_key()
                result = self._evaluate(key, now)
                if result.allowed:
                    self._events[key].append(now)
                    return RateLimitResult(
                        allowed=True,
                        remaining_minute=result.remaining_minute - 1,
                        remaining_hour=result.remaining_hour - 1,
                    )
                return result
        except Exception as exc:
            return RateLimitResult(allowed=False, reason=_failure_reason(exc))

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._events.clear()
            else:
                self._events.pop(key, None)
