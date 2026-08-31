"""Budget enforcement.

Two independent limits are enforced:

* a **daily** cap across all requests, read from persisted spend, and
* a **per-request** cap, accumulated in process.

The per-request cap is what stops a retry loop or a chatty agent from
consuming the whole daily allowance inside a single user request; the daily cap
is what stops many cheap requests from doing the same over an afternoon.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from ..persistence.repository import Repository


def _start_of_utc_day(now: datetime) -> str:
    midnight = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    daily_limit_usd: Decimal
    daily_spent_usd: Decimal
    request_limit_usd: Decimal
    request_spent_usd: Decimal

    @property
    def daily_remaining_usd(self) -> Decimal:
        return max(Decimal(0), self.daily_limit_usd - self.daily_spent_usd)

    @property
    def daily_used_fraction(self) -> float:
        if self.daily_limit_usd <= 0:
            return 0.0
        return float(min(Decimal(1), self.daily_spent_usd / self.daily_limit_usd))


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    allowed: bool
    reason: str
    status: BudgetStatus


class BudgetGuard:
    """Checks projected spend against the configured limits."""

    def __init__(
        self,
        repository: Repository,
        *,
        daily_budget_usd: Decimal,
        max_request_cost_usd: Decimal,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._repository = repository
        self._daily_budget = daily_budget_usd
        self._max_request_cost = max_request_cost_usd
        self._clock = clock
        self._lock = threading.Lock()
        self._per_request: dict[str, Decimal] = {}

    def _daily_spent(self) -> Decimal:
        try:
            return self._repository.spend_since(_start_of_utc_day(self._clock()))
        except Exception:
            # Fail closed: report the budget as fully consumed so the guard
            # denies rather than allowing unmetered spend.
            return self._daily_budget

    def status(self, request_id: str | None = None) -> BudgetStatus:
        with self._lock:
            request_spent = self._per_request.get(request_id or "", Decimal(0))
        return BudgetStatus(
            daily_limit_usd=self._daily_budget,
            daily_spent_usd=self._daily_spent(),
            request_limit_usd=self._max_request_cost,
            request_spent_usd=request_spent,
        )

    def check(self, estimated_cost_usd: Decimal, *, request_id: str) -> BudgetDecision:
        """Decide whether a call whose cost is *estimated_cost_usd* may proceed."""
        status = self.status(request_id)

        projected_request = status.request_spent_usd + estimated_cost_usd
        if projected_request > status.request_limit_usd:
            return BudgetDecision(
                allowed=False,
                reason=(
                    f"per-request budget exceeded: projected ${projected_request:.6f} "
                    f"> limit ${status.request_limit_usd:.6f}"
                ),
                status=status,
            )

        projected_daily = status.daily_spent_usd + estimated_cost_usd
        if projected_daily > status.daily_limit_usd:
            return BudgetDecision(
                allowed=False,
                reason=(
                    f"daily budget exceeded: projected ${projected_daily:.6f} "
                    f"> limit ${status.daily_limit_usd:.6f}"
                ),
                status=status,
            )

        return BudgetDecision(allowed=True, reason="within budget", status=status)

    def record_spend(self, request_id: str, amount_usd: Decimal) -> None:
        """Accumulate in-process spend for the active request."""
        with self._lock:
            self._per_request[request_id] = (
                self._per_request.get(request_id, Decimal(0)) + amount_usd
            )

    def release(self, request_id: str) -> Decimal:
        """Drop the per-request accumulator once a request finishes."""
        with self._lock:
            return self._per_request.pop(request_id, Decimal(0))
