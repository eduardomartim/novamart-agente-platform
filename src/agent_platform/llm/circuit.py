"""Circuit breaker for a failing model provider.

Purpose is narrow: stop hammering a provider that is already failing. Under
sustained `503`/`429`/timeout, the retry policy turns every request into
several doomed calls, which wastes quota, multiplies latency, and on a rate
limiter makes the underlying problem worse.

**This is not an authorisation mechanism.** It can only *prevent* a provider
call. It never permits an action, is never consulted when deciding whether a
tool may run, and an open circuit produces exactly the same outcome as any
other provider failure: a recorded, handled failure with no tool execution.

States:

    closed    -> calls flow; consecutive failures counted
    open      -> calls fail fast until the cooldown elapses
    half_open -> exactly one trial call allowed; success closes, failure reopens

Deterministic by construction: the clock is injectable, so cooldown behaviour
is testable without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Final, Literal

CircuitState = Literal["closed", "open", "half_open"]

STATE_CLOSED: Final[CircuitState] = "closed"
STATE_OPEN: Final[CircuitState] = "open"
STATE_HALF_OPEN: Final[CircuitState] = "half_open"


class CircuitOpenError(RuntimeError):
    """The circuit is open; the call was not attempted."""

    def __init__(self, retry_after_seconds: float, failures: int) -> None:
        super().__init__(
            f"provider circuit is open after {failures} consecutive failures; "
            f"retry in {retry_after_seconds:.0f}s"
        )
        self.retry_after_seconds = retry_after_seconds
        self.failures = failures


class CircuitBreaker:
    """Trips after consecutive failures, recovers after a bounded cooldown."""

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_seconds <= 0:
            raise ValueError("cooldown_seconds must be > 0")
        self._threshold = failure_threshold
        self._cooldown = cooldown_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._failures = 0
        self._opened_at: float | None = None
        self._half_open_in_flight = False

    # ------------------------------------------------------------------ state

    def _state_locked(self) -> CircuitState:
        if self._opened_at is None:
            return STATE_CLOSED
        if self._clock() - self._opened_at >= self._cooldown:
            return STATE_HALF_OPEN
        return STATE_OPEN

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state_locked()

    @property
    def failures(self) -> int:
        with self._lock:
            return self._failures

    def _retry_after_locked(self) -> float:
        if self._opened_at is None:
            return 0.0
        return max(0.0, self._cooldown - (self._clock() - self._opened_at))

    def retry_after(self) -> float:
        with self._lock:
            return self._retry_after_locked()

    # ----------------------------------------------------------------- verbs

    def before_call(self) -> None:
        """Raise :class:`CircuitOpenError` if the call must not be attempted.

        In ``half_open`` exactly one trial call is admitted; concurrent callers
        are refused so a recovering provider is probed once, not stampeded.
        """
        with self._lock:
            state = self._state_locked()
            if state == STATE_CLOSED:
                return
            if state == STATE_OPEN:
                # _state_locked() only reports "open" when _opened_at is set,
                # so this is guaranteed. Asserting states the invariant rather
                # than hiding it behind an `or 0.0` that -- because `-` binds
                # tighter than `or` -- would not actually have guarded a None.
                assert self._opened_at is not None
                remaining = self._cooldown - (self._clock() - self._opened_at)
                raise CircuitOpenError(max(0.0, remaining), self._failures)
            # half_open
            if self._half_open_in_flight:
                raise CircuitOpenError(0.0, self._failures)
            self._half_open_in_flight = True

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    def record_failure(self) -> None:
        """Count a failure and open the circuit once the threshold is reached.

        A failed trial call in ``half_open`` reopens the circuit and restarts
        the cooldown, so a provider that is still broken is not probed in a
        tight loop.
        """
        with self._lock:
            was_half_open = self._half_open_in_flight
            self._half_open_in_flight = False
            self._failures += 1
            if was_half_open or self._failures >= self._threshold:
                self._opened_at = self._clock()

    def reset(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None
            self._half_open_in_flight = False

    def snapshot(self) -> dict[str, str | int | float]:
        """Status for the dashboard. Counters and timings only."""
        with self._lock:
            return {
                "state": self._state_locked(),
                "consecutive_failures": self._failures,
                "failure_threshold": self._threshold,
                "cooldown_seconds": self._cooldown,
                "retry_after_seconds": round(self._retry_after_locked(), 1),
            }
