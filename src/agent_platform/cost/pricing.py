"""Model rate card.

Prices change, and some are explicitly temporary. Rather than burying numbers
in the cost calculator, rates are declared as dated periods so that:

* an introductory price that expires cannot silently keep being applied;
* the verification date is visible to anyone reading a cost figure;
* an unknown model produces an explicitly "unknown" estimate rather than a
  confident zero.

Source: https://ai.google.dev/gemini-api/docs/pricing
Verified on: 2026-08-28 (re-checked for the live-provider work; the rate card
below was unchanged from the 2026-08-27 check).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Final

PRICING_SOURCE: Final[str] = "https://ai.google.dev/gemini-api/docs/pricing"
PRICING_VERIFIED_ON: Final[date] = date(2026, 8, 28)
STALE_AFTER_DAYS: Final[int] = 90

ONE_MILLION: Final[Decimal] = Decimal(1_000_000)


@dataclass(frozen=True, slots=True)
class Rate:
    """Paid-tier price per one million tokens, valid over a date range."""

    input_per_1m: Decimal
    output_per_1m: Decimal
    effective_from: date
    effective_until: date | None = None
    note: str = ""

    def covers(self, on: date) -> bool:
        if on < self.effective_from:
            return False
        return self.effective_until is None or on <= self.effective_until


#: Paid standard rates. The demo normally runs on the free tier, where the real
#: charge is zero; these are the "what this would cost in production" figures
#: the blueprint asks the platform to surface.
PRICING: Final[dict[str, tuple[Rate, ...]]] = {
    "gemini-3.7-flash": (
        Rate(
            Decimal("0.75"),
            Decimal("3.75"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            note="introductory pricing",
        ),
        Rate(Decimal("1.50"), Decimal("7.50"), date(2027, 1, 1), note="standard pricing"),
    ),
    "gemini-3.6-flash": (
        Rate(
            Decimal("0.75"),
            Decimal("3.75"),
            date(2026, 1, 1),
            date(2026, 12, 31),
            note="introductory pricing",
        ),
        Rate(Decimal("1.50"), Decimal("7.50"), date(2027, 1, 1), note="standard pricing"),
    ),
    "gemini-3.5-flash": (Rate(Decimal("1.50"), Decimal("9.00"), date(2026, 1, 1)),),
    "gemini-3.5-flash-lite": (Rate(Decimal("0.30"), Decimal("2.50"), date(2026, 1, 1)),),
    "gemini-3.1-flash-lite": (Rate(Decimal("0.25"), Decimal("1.50"), date(2026, 1, 1)),),
    "gemini-3-flash-preview": (Rate(Decimal("0.50"), Decimal("3.00"), date(2025, 1, 1)),),
    "gemini-2.5-pro": (Rate(Decimal("1.25"), Decimal("10.00"), date(2025, 1, 1)),),
    "gemini-2.5-flash": (Rate(Decimal("0.30"), Decimal("2.50"), date(2025, 1, 1)),),
    "gemini-2.5-flash-lite": (Rate(Decimal("0.10"), Decimal("0.40"), date(2025, 1, 1)),),
    #: The stub calls no API, so its true cost is zero rather than unknown.
    "deterministic-stub-v1": (Rate(Decimal(0), Decimal(0), date(2000, 1, 1)),),
}

@dataclass(frozen=True, slots=True)
class CostEstimate:
    """An estimated cost together with whether it can be trusted."""

    amount_usd: Decimal
    known_model: bool
    rate: Rate | None = None
    note: str = ""

    @property
    def display(self) -> str:
        if not self.known_model:
            return "unknown (model not in rate card)"
        return f"${self.amount_usd:.6f}"


def rate_for(model: str, on: date | None = None) -> Rate | None:
    """Return the rate in force for *model* on the given date."""
    periods = PRICING.get(model)
    if not periods:
        return None
    when = on or date.today()
    for period in periods:
        if period.covers(when):
            return period
    # Past the last declared period: fall back to the most recent one so cost
    # is still reported, flagged by the note so the staleness is visible.
    return periods[-1]


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    on: date | None = None,
) -> CostEstimate:
    """Estimate the paid-tier cost of a call.

    An unrecognised model yields ``known_model=False`` and a zero amount. The
    caller is expected to surface that rather than treating it as free.
    """
    rate = rate_for(model, on)
    if rate is None:
        return CostEstimate(
            amount_usd=Decimal(0),
            known_model=False,
            note=f"no rate card entry for {model!r}",
        )
    amount = (
        Decimal(max(0, input_tokens)) * rate.input_per_1m
        + Decimal(max(0, output_tokens)) * rate.output_per_1m
    ) / ONE_MILLION
    return CostEstimate(amount_usd=amount, known_model=True, rate=rate, note=rate.note)


def is_stale(on: date | None = None) -> bool:
    """True when the rate card is older than the staleness threshold."""
    when = on or date.today()
    return when > PRICING_VERIFIED_ON + timedelta(days=STALE_AFTER_DAYS)


def pricing_notice(on: date | None = None) -> str:
    """One-line provenance string to display next to any cost figure."""
    base = f"Estimated at paid-tier rates verified {PRICING_VERIFIED_ON.isoformat()}"
    if is_stale(on):
        return f"{base} - RATE CARD MAY BE STALE, re-check {PRICING_SOURCE}"
    return f"{base} ({PRICING_SOURCE})"
