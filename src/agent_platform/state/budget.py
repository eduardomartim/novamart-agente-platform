"""The provider-call ledger, shared.

``SqliteProviderBudget`` is already atomic and already durable -- it charges
inside a ``BEGIN IMMEDIATE`` transaction, so two processes on one filesystem
cannot both spend the last call. What it cannot do is span replicas that do not
share a disk, which is exactly the deployment this phase is preparing for.

This implements the same one-verb ``ProviderBudget`` protocol against the shared
backend, so choosing between them is a wiring decision and nothing downstream
changes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from .backend import Backend

#: Seconds a day's counter is kept. Comfortably longer than a day so a counter
#: written just before midnight is not reclaimed while it is still the current
#: day somewhere in the rollover, and short enough that yesterday's key does not
#: accumulate forever.
DAY_TTL_SECONDS = 60 * 60 * 36


def _utc_day() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


class SharedProviderBudget:
    """A per-day ceiling on physical provider calls, counted across replicas.

    One verb, ``try_consume``, and it can only ever refuse: there is no method
    that grants permission or raises a limit. The counter is keyed by UTC day
    and expires on its own, so no reset job exists to forget to run.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        daily_limit: int,
        today: Callable[[], str] = _utc_day,
        prefix: str = "provider-budget",
    ) -> None:
        if daily_limit < 0:
            raise ValueError("daily_limit must be >= 0")
        self._backend = backend
        self._limit = daily_limit
        self._today = today
        self._prefix = prefix

    @property
    def daily_limit(self) -> int:
        return self._limit

    @property
    def backend_kind(self) -> str:
        return self._backend.kind

    def _key(self) -> str:
        return f"{self._prefix}:{self._today()}"

    def try_consume(self) -> bool:
        """Charge one physical call, or refuse.

        The read and the increment are one backend operation. Written as a
        Python-side ``if used < limit: incr`` this would be the textbook race:
        under concurrency the ceiling is a suggestion, and the overshoot grows
        with the number of replicas.
        """
        return self._backend.counter_charge(
            self._key(), limit=self._limit, ttl_seconds=DAY_TTL_SECONDS
        )

    def used(self) -> int:
        return self._backend.counter_value(self._key())

    def remaining(self) -> int:
        return max(0, self._limit - self.used())
