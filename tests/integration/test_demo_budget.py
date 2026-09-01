"""A recruiter must not be able to exhaust the day's provider quota by clicking.

The measured exposure, before this gate existed:

* the dashboard runs under the base rate limiter -- 100 requests per hour;
* a typical request costs 2-3 provider calls;
* so roughly 250 calls per hour, against a free-tier ceiling of 500 per day.

Two hours of steady clicking would end the day. The existing cost budget does
not help: at the measured $0.00051 per call, the $1.00 daily limit binds at
about 1 960 calls -- nearly four times past the point where the quota is
already gone. It was sized for money, and the free tier does not charge money.

This gate counts **recorded `llm_call` events**, not estimates, so it measures
what actually happened rather than what was predicted. It is a demo guard on
top of the platform's own controls, and it weakens none of them.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

import demo_budget  # noqa: E402

from agent_platform.persistence.memory import InMemoryRepository  # noqa: E402
from agent_platform.persistence.repository import EventRecord  # noqa: E402


def _event(event_type: str, when: datetime, provider: str = "gemini") -> EventRecord:
    return EventRecord(
        request_id="r1",
        trace_id="t1",
        sequence=1,
        event_type=event_type,
        status="info",
        created_at=when.isoformat().replace("+00:00", "Z"),
        payload={"provider": provider},
    )


@pytest.fixture
def repository():
    return InMemoryRepository()


def _record(repository, count: int, *, event_type: str = "llm_call", days_ago: int = 0):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    for _ in range(count):
        repository.save_event(_event(event_type, when))


# ------------------------------------------------------------- the counting


def test_a_fresh_day_starts_at_zero(repository):
    assert demo_budget.calls_used_today(repository) == 0


def test_it_counts_recorded_model_calls(repository):
    _record(repository, 7)
    assert demo_budget.calls_used_today(repository) == 7


def test_it_ignores_events_that_are_not_model_calls(repository):
    _record(repository, 5, event_type="tool_call")
    _record(repository, 3, event_type="policy_decision")
    _record(repository, 2)
    assert demo_budget.calls_used_today(repository) == 2


def test_yesterdays_calls_do_not_count_against_today(repository):
    """The free-tier quota resets daily; so must the budget."""
    _record(repository, 50, days_ago=1)
    _record(repository, 4)
    assert demo_budget.calls_used_today(repository) == 4


# --------------------------------------------------------------- the gate


def test_the_gate_is_open_well_below_the_budget(repository):
    _record(repository, 10)
    state = demo_budget.budget_state(repository, live=True)
    assert state.exhausted is False
    assert state.remaining == demo_budget.LIVE_CALL_BUDGET - 10


def test_the_gate_closes_at_the_budget(repository):
    _record(repository, demo_budget.LIVE_CALL_BUDGET)
    state = demo_budget.budget_state(repository, live=True)
    assert state.exhausted is True
    assert state.remaining == 0


def test_the_gate_stays_closed_past_the_budget(repository):
    """Overshoot must not wrap around into a fresh allowance."""
    _record(repository, demo_budget.LIVE_CALL_BUDGET + 25)
    state = demo_budget.budget_state(repository, live=True)
    assert state.exhausted is True
    assert state.remaining == 0


def test_stub_mode_is_never_gated(repository):
    """The stub makes no provider calls, so there is nothing to protect."""
    _record(repository, demo_budget.LIVE_CALL_BUDGET * 3)
    state = demo_budget.budget_state(repository, live=False)
    assert state.exhausted is False


# ------------------------------------------------- the budget is defensible


def test_the_budget_leaves_room_for_the_live_verification_suites():
    """The gate must not be able to starve the project's own live gate.

    The documented round costs ~52 calls. A demo budget that consumed the
    whole free tier would make the publication gate unrunnable.
    """
    assert demo_budget.LIVE_CALL_BUDGET + demo_budget.VERIFICATION_RESERVE <= (
        demo_budget.FREE_TIER_DAILY_CALLS
    )


def test_the_budget_is_below_the_free_tier_ceiling():
    assert demo_budget.LIVE_CALL_BUDGET < demo_budget.FREE_TIER_DAILY_CALLS


def test_the_budget_still_allows_a_meaningful_demo():
    """A gate so tight that nobody can try the product is not a good gate."""
    typical = demo_budget.TYPICAL_CALLS_PER_REQUEST
    assert demo_budget.LIVE_CALL_BUDGET // typical >= 50, (
        "the budget allows fewer than 50 requests; too tight for a demo"
    )


def test_the_message_explains_itself_without_jargon(repository):
    _record(repository, demo_budget.LIVE_CALL_BUDGET)
    message = demo_budget.budget_state(repository, live=True).message
    assert message
    lowered = message.lower()
    assert "limite" in lowered or "capacidade" in lowered
    for jargon in ("gemini_api_key", "429", "resource_exhausted", "traceback"):
        assert jargon not in lowered


# ================================ Phase 4: the protection must be visible
#
# A safety control nobody can see is, to a visitor, indistinguishable from one
# that does not exist. The budget is real engineering; hiding it in LIVE-only
# code meant the audience most likely to appreciate it -- someone exploring the
# default STUB demo -- never learned it was there.


def test_status_is_ok_well_below_the_budget(repository):
    _record(repository, 10)
    assert demo_budget.budget_state(repository, live=True).status is (
        demo_budget.BudgetStatus.OK
    )


def test_status_becomes_running_low_before_it_is_spent(repository):
    """A visitor should get warning, not a surprise."""
    threshold = int(demo_budget.LIVE_CALL_BUDGET * demo_budget.LOW_WATER_FRACTION)
    _record(repository, threshold)
    state = demo_budget.budget_state(repository, live=True)
    assert state.status is demo_budget.BudgetStatus.RUNNING_LOW
    assert state.exhausted is False, "running low must not block anything"


def test_status_is_exhausted_at_the_budget(repository):
    _record(repository, demo_budget.LIVE_CALL_BUDGET)
    assert demo_budget.budget_state(repository, live=True).status is (
        demo_budget.BudgetStatus.EXHAUSTED
    )


def test_the_figures_are_reported_for_display(repository):
    _record(repository, 42)
    state = demo_budget.budget_state(repository, live=True)
    assert state.used == 42
    assert state.budget == demo_budget.LIVE_CALL_BUDGET
    assert state.remaining == demo_budget.LIVE_CALL_BUDGET - 42


def test_stub_mode_still_reports_the_real_figures(repository):
    """Visible in STUB, but never gating there: the stub calls no provider."""
    _record(repository, 25)
    state = demo_budget.budget_state(repository, live=False)
    assert state.used == 25, "stub mode hides the real usage"
    assert state.budget == demo_budget.LIVE_CALL_BUDGET
    assert state.exhausted is False


def test_stub_is_not_gated_even_when_the_budget_is_spent(repository):
    """Reaching the LIVE limit must not break the offline demonstration."""
    _record(repository, demo_budget.LIVE_CALL_BUDGET * 2)
    state = demo_budget.budget_state(repository, live=False)
    assert state.exhausted is False
    assert state.gating is False


def test_the_exhausted_message_is_honest_about_whose_fault_it_is(repository):
    """The provider is fine. We chose not to call it. Say that."""
    _record(repository, demo_budget.LIVE_CALL_BUDGET)
    message = demo_budget.budget_state(repository, live=True).message.lower()
    assert "capacidade" in message or "limite" in message
    for dishonest in ("indisponível", "provedor caiu", "fora do ar", "erro"):
        assert dishonest not in message, (
            f"the message blames the provider ({dishonest!r}) for our own limit"
        )


def test_no_internal_detail_reaches_the_display(repository):
    """Used/limit/remaining only -- never a path, a query or a credential."""
    for count in (0, 10, demo_budget.LIVE_CALL_BUDGET):
        _record(repository, count)
        text = demo_budget.budget_state(repository, live=True).message.lower()
        for leak in (
            "sqlite", ".db", "select ", "insert ", "provider_budget",
            "aizasy", "gemini_api_key", "c:\\", "/home/",
        ):
            assert leak not in text, f"the budget display leaked {leak!r}"


def test_the_budget_cannot_authorise_anything(repository):
    """One verb: stop. There is no state in which it grants a call."""
    state = demo_budget.budget_state(repository, live=True)
    assert not hasattr(state, "allow")
    assert not hasattr(demo_budget, "grant")
    # `gating` says whether to stop; nothing says whether to proceed.
    assert isinstance(state.gating, bool)
