"""A demo-usage budget for provider calls, enforced above the platform.

Why this exists, in measured terms rather than in principle:

* The dashboard runs under the base rate limiter: 100 requests per hour.
* A typical request costs 2-3 provider calls (measured against the same graph
  using the deterministic stub).
* That is roughly 250 calls per hour, against a free-tier ceiling of 500 per
  day. Two hours of steady clicking ends the day for everyone.

The platform's existing cost budget does not close this. At the measured
$0.00051 per call, the $1.00 daily limit binds at about 1 960 calls -- nearly
four times past the point where the quota is already gone. It was sized in
money, and the free tier does not charge money. That is not a defect in the
cost tracker; it is a different failure mode than the one it was built for.

What this module is, precisely:

* a **demo guard**, layered on top of the platform's controls, not inside them;
* it can only *prevent* provider calls, never authorise one;
* it counts recorded ``llm_call`` events -- what actually happened -- rather
  than predicting what a request will cost;
* it never touches the policy engine, the gateway, the rate limiter, the
  circuit breaker or the resource guard, and weakens none of them.

Scope, stated honestly: this gate sits in the dashboard, so it protects the
path a recruiter can reach by clicking. It does **not** cover the CLI or a
direct library caller. Closing that gap needs a provider-level budget inside
the composition root, which is a change to core execution logic and is
deliberately left to a later phase.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

#: Google's free tier for the configured model, per project per day.
FREE_TIER_DAILY_CALLS = 500

#: Held back so the project's own live verification round (~52 calls, measured)
#: can always run. A demo that eats the publication gate is a bad trade.
VERIFICATION_RESERVE = 100

#: Measured against the same orchestration graph using the stub: a read-only
#: lookup costs 2 calls, an action 3.
TYPICAL_CALLS_PER_REQUEST = 3

#: Chosen from the numbers above, not guessed: it leaves the verification
#: reserve plus headroom, and still allows on the order of a hundred live
#: requests in a day -- far more than a recruiter will use in one sitting.
LIVE_CALL_BUDGET = 300

#: Where "running low" begins. Four fifths spent leaves roughly twenty live
#: requests -- enough warning to finish what you were doing, and early enough
#: that the warning is useful rather than an epitaph.
LOW_WATER_FRACTION = 0.8


class BudgetStatus(Enum):
    """How much of today's demo allowance is left."""

    OK = "OK"
    RUNNING_LOW = "RUNNING LOW"
    EXHAUSTED = "EXHAUSTED"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


#: How far back to scan for today's events. Comfortably above what the hourly
#: rate limiter could produce in a day, so the count cannot silently truncate.
_SCAN_LIMIT = 8000


class _Repository(Protocol):
    def recent_events(self, limit: int = ...) -> list[dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class BudgetState:
    """What the gate decided, and why, in terms a visitor can read.

    ``status`` is for display and is reported in both modes, because a safety
    control nobody can see is indistinguishable from one that does not exist.
    ``gating`` is the decision, and it is only ever true in live mode: the
    simulation contacts no provider, so there is no quota to protect and
    nothing to stop.

    The two are deliberately separate. Collapsing them would either hide the
    figures in simulation mode or block a demonstration that costs nothing.
    """

    used: int
    budget: int
    status: BudgetStatus
    message: str
    gating: bool

    @property
    def remaining(self) -> int:
        return max(0, self.budget - self.used)

    @property
    def exhausted(self) -> bool:
        """Whether this request must be stopped before it starts."""
        return self.gating and self.status is BudgetStatus.EXHAUSTED


def _start_of_utc_day() -> str:
    now = datetime.now(UTC)
    return now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat()


def calls_used_today(repository: _Repository) -> int:
    """Count model calls actually recorded since midnight UTC.

    Counts events rather than estimating from request volume, because the two
    diverge: a retry, a validation second opinion or a suspended request all
    change the ratio. The trace already records the truth.
    """
    start = _start_of_utc_day()
    used = 0
    for event in repository.recent_events(limit=_SCAN_LIMIT):
        if event.get("event_type") != "llm_call":
            continue
        created = str(event.get("created_at") or "")
        # Stored as ISO-8601; a plain string comparison is correct here because
        # both sides are UTC and zero-padded.
        if created.replace("Z", "+00:00") >= start:
            used += 1
    return used


EXHAUSTED_MESSAGE = (
    "**Today's demo capacity is spent.** This demonstration sets its own daily "
    "budget for calls to the AI provider, and the system stopped *before* "
    "making another one.\n\n"
    "The provider is not down and nothing has failed -- this deployment simply "
    "chose not to spend more today. The deterministic simulation remains fully "
    "available: it runs the same orchestration graph, the same policy engine "
    "and the same security controls."
)


def _status_for(used: int, budget: int) -> BudgetStatus:
    if used >= budget:
        return BudgetStatus.EXHAUSTED
    if used >= int(budget * LOW_WATER_FRACTION):
        return BudgetStatus.RUNNING_LOW
    return BudgetStatus.OK


def budget_state(repository: _Repository, *, live: bool) -> BudgetState:
    """Report the day's provider-call allowance, and whether to stop.

    Reported in both modes; enforced in only one. The figures are real in
    simulation mode too -- a visitor exploring the stub should be able to see
    that the protection exists, and what it is currently doing.
    """
    used = calls_used_today(repository)
    status = _status_for(used, LIVE_CALL_BUDGET)
    remaining = max(0, LIVE_CALL_BUDGET - used)

    if not live:
        return BudgetState(
            used=used,
            budget=LIVE_CALL_BUDGET,
            status=status,
            gating=False,
            message=(
                "Simulation mode makes no provider calls, so nothing is "
                f"charged against today's demo capacity ({used} of "
                f"{LIVE_CALL_BUDGET} used). The budget applies to live mode."
            ),
        )

    if status is BudgetStatus.EXHAUSTED:
        return BudgetState(
            used=used,
            budget=LIVE_CALL_BUDGET,
            status=status,
            gating=True,
            message=EXHAUSTED_MESSAGE,
        )

    requests = remaining // TYPICAL_CALLS_PER_REQUEST
    if status is BudgetStatus.RUNNING_LOW:
        message = (
            f"**Demo capacity is running low.** {remaining} of "
            f"{LIVE_CALL_BUDGET} provider calls remain today, roughly "
            f"{requests} more requests."
        )
    else:
        message = (
            f"Live mode. {remaining} of {LIVE_CALL_BUDGET} provider calls "
            f"remain in today's demo capacity, roughly {requests} more "
            "requests."
        )

    return BudgetState(
        used=used,
        budget=LIVE_CALL_BUDGET,
        status=status,
        gating=True,
        message=message,
    )
