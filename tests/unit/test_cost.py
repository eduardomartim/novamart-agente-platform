"""Unit tests for pricing, cost tracking and budget enforcement."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from agent_platform.cost.pricing import (
    PRICING,
    PRICING_VERIFIED_ON,
    estimate_cost,
    is_stale,
    pricing_notice,
    rate_for,
)
from agent_platform.llm.provider import LLMResponse, Purpose
from agent_platform.persistence.repository import nano_to_usd, usd_to_nano


def test_dated_rates_roll_over_when_introductory_pricing_expires():
    """gemini-3.7-flash is on introductory pricing that expires 2026-12-31."""
    introductory = rate_for("gemini-3.7-flash", date(2026, 8, 27))
    standard = rate_for("gemini-3.7-flash", date(2027, 3, 1))
    assert introductory is not None and standard is not None
    assert introductory.input_per_1m == Decimal("0.75")
    assert standard.input_per_1m == Decimal("1.50")
    assert standard.input_per_1m > introductory.input_per_1m


def test_unknown_model_is_flagged_not_silently_free():
    estimate = estimate_cost("some-model-that-does-not-exist", 1000, 1000)
    assert estimate.known_model is False
    assert estimate.amount_usd == Decimal(0)
    assert "unknown" in estimate.display


def test_known_model_cost_is_exact():
    # 1000 in + 500 out at $1.50 / $9.00 per 1M.
    estimate = estimate_cost("gemini-3.5-flash", 1000, 500)
    expected = (Decimal(1000) * Decimal("1.50") + Decimal(500) * Decimal("9.00")) / Decimal(
        1_000_000
    )
    assert estimate.amount_usd == expected
    assert estimate.known_model is True


def test_stub_model_costs_nothing_and_is_known():
    estimate = estimate_cost("deterministic-stub-v1", 10_000, 10_000)
    assert estimate.known_model is True
    assert estimate.amount_usd == Decimal(0)


def test_negative_token_counts_do_not_produce_negative_cost():
    assert estimate_cost("gemini-3.5-flash", -100, -100).amount_usd == Decimal(0)


def test_every_rate_card_entry_has_a_covering_period():
    for model in PRICING:
        assert rate_for(model, PRICING_VERIFIED_ON) is not None


def test_staleness_is_reported():
    assert is_stale(PRICING_VERIFIED_ON) is False
    assert is_stale(PRICING_VERIFIED_ON + timedelta(days=400)) is True
    assert "STALE" in pricing_notice(PRICING_VERIFIED_ON + timedelta(days=400))


def test_nano_usd_round_trip_is_exact():
    for amount in ("0", "0.00003", "1.23456789", "999.999999999"):
        value = Decimal(amount)
        assert nano_to_usd(usd_to_nano(value)) == value


def test_money_accumulates_without_float_drift():
    """Ten thousand tiny charges must sum exactly."""
    unit = Decimal("0.000001")
    total = sum((unit for _ in range(10_000)), Decimal(0))
    assert total == Decimal("0.010000")
    assert nano_to_usd(sum(usd_to_nano(unit) for _ in range(10_000))) == Decimal("0.010000")


# ---------------------------------------------------------------- budget guard


def test_budget_allows_within_limits(budget_guard):
    assert budget_guard.check(Decimal("0.001"), request_id="req-1").allowed is True


def test_per_request_cap_blocks_a_runaway_loop(budget_guard):
    """Accumulated in-request spend eventually exceeds the per-request cap."""
    for _ in range(60):
        budget_guard.record_spend("req-1", Decimal("0.001"))
    decision = budget_guard.check(Decimal("0.001"), request_id="req-1")
    assert decision.allowed is False
    assert "per-request" in decision.reason


def test_release_clears_the_request_accumulator(budget_guard):
    budget_guard.record_spend("req-1", Decimal("0.02"))
    assert budget_guard.release("req-1") == Decimal("0.02")
    assert budget_guard.status("req-1").request_spent_usd == Decimal(0)


def test_daily_budget_reflects_persisted_spend(repository, settings):
    from agent_platform.cost.budget import BudgetGuard
    from agent_platform.persistence.repository import LLMCallRecord

    repository.save_llm_call(
        LLMCallRecord(
            request_id="r",
            trace_id="t",
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("0.99"),
        )
    )
    guard = BudgetGuard(
        repository,
        daily_budget_usd=Decimal("1.00"),
        max_request_cost_usd=Decimal("1.00"),
    )
    assert guard.check(Decimal("0.005"), request_id="req-1").allowed is True
    decision = guard.check(Decimal("0.02"), request_id="req-1")
    assert decision.allowed is False
    assert "daily" in decision.reason


# -------------------------------------------------------------------- tracker


def test_tracker_persists_and_charges(cost_tracker, repository, budget_guard):
    response = LLMResponse(
        text="{}",
        provider="gemini",
        model="gemini-3.5-flash",
        purpose=Purpose.ROUTE,
        input_tokens=1000,
        output_tokens=500,
    )
    tracked = cost_tracker.track(response, request_id="req-1", trace_id="t-1", agent="router")
    assert len(repository.llm_calls) == 1
    assert tracked.cost_usd > 0
    assert budget_guard.status("req-1").request_spent_usd == tracked.cost_usd


def test_estimated_flag_is_set_for_approximated_tokens(cost_tracker, repository):
    response = LLMResponse(
        text="{}",
        provider="stub",
        model="deterministic-stub-v1",
        purpose=Purpose.ROUTE,
        input_tokens=10,
        output_tokens=5,
        tokens_estimated=True,
    )
    cost_tracker.track(response, request_id="req-1", trace_id="t-1")
    assert repository.llm_calls[0].estimated is True


def test_unknown_model_marks_the_record_estimated(cost_tracker, repository):
    response = LLMResponse(
        text="{}",
        provider="gemini",
        model="brand-new-model",
        purpose=Purpose.ROUTE,
        input_tokens=10,
        output_tokens=5,
        tokens_estimated=False,
    )
    cost_tracker.track(response, request_id="req-1", trace_id="t-1")
    assert repository.llm_calls[0].estimated is True
