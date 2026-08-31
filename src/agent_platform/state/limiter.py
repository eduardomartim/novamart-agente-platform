"""The rate limiter, shared across replicas.

The in-process limiter is correct on one process and quietly wrong on several:
each replica keeps its own sliding window, so a limit of 60/minute becomes
60 times replicas per minute, and nothing in the configuration or the logs says the
number changed. That is the failure this class exists to remove.

It mirrors ``security.rate_limit.RateLimiter``'s interface exactly -- same
methods, same ``RateLimitResult`` -- so the platform's single call site is
untouched.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from ..security.rate_limit import RateLimitResult, current_quota_key
from .backend import Backend


class SharedRateLimiter:
    """Sliding-window limiter whose window lives in shared state.

    Fails **closed**, matching V1: if the backend cannot be reached the request
    is denied rather than admitted. An unreachable limiter that allows traffic
    is not a degraded limiter, it is no limiter, and the failure is invisible
    precisely when load is highest.
    """

    def __init__(
        self,
        backend: Backend,
        requests_per_minute: int,
        requests_per_hour: int,
        *,
        clock: Callable[[], float] = time.time,
        prefix: str = "ratelimit",
    ) -> None:
        if requests_per_minute < 1 or requests_per_hour < 1:
            raise ValueError("rate limits must be >= 1")
        self._backend = backend
        self._per_minute = requests_per_minute
        self._per_hour = requests_per_hour
        self._clock = clock
        self._prefix = prefix

    @property
    def backend_kind(self) -> str:
        return self._backend.kind

    def _key(self, key: str) -> str:
        return f"{self._prefix}:{key}"

    def check(self, key: str | None = None) -> RateLimitResult:
        """Report quota state without consuming it.

        The policy engine calls this while evaluating an action, so it runs
        several times per request. Consuming here would make the effective
        limit a fraction of the configured one, and the discrepancy would grow
        with the number of policy evaluations rather than with traffic.

        ``None`` resolves to the bucket the current request is spending from --
        see ``security.rate_limit.quota_scope``. That is what keeps this
        consultation addressed at the bucket the entry point actually consumed
        once quota became per-principal.
        """
        try:
            allowed = self._backend.window_check(
                self._key(key or current_quota_key()),
                windows=((60.0, self._per_minute), (3600.0, self._per_hour)),
                now=self._clock(),
            )
        except Exception as exc:
            return RateLimitResult(
                allowed=False,
                reason=f"rate limiter unavailable ({type(exc).__name__}); denying",
            )
        if allowed:
            return RateLimitResult(allowed=True)
        return RateLimitResult(allowed=False, reason=self._exceeded_reason())

    def _exceeded_reason(self) -> str:
        return (
            f"rate limit exceeded (per-minute {self._per_minute}, "
            f"per-hour {self._per_hour})"
        )

    def acquire(self, key: str | None = None) -> RateLimitResult:
        """Consume one unit of quota if both windows have room.

        The count and the append are one backend operation. Split into a read
        and a write they would race, and the race is not theoretical: it is
        precisely what happens when several replicas receive a burst.
        """
        try:
            allowed = self._backend.window_allow(
                self._key(key or current_quota_key()),
                windows=((60.0, self._per_minute), (3600.0, self._per_hour)),
                now=self._clock(),
            )
        except Exception as exc:
            return RateLimitResult(
                allowed=False,
                reason=f"rate limiter unavailable ({type(exc).__name__}); denying",
            )

        if allowed:
            return RateLimitResult(allowed=True)
        return RateLimitResult(allowed=False, reason=self._exceeded_reason())
