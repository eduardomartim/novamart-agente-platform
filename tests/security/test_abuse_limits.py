"""Security tests: resource exhaustion, abuse limits and the circuit breaker.

Covers attack matrix section 21 and the regressions for findings F1-F5.

The governing property throughout: a resource limit may only ever *stop* work.
None of these controls can grant permission, and every one of them fails
closed.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.guardrails.policy import PolicyContext, PolicyEngine
from agent_platform.llm.circuit import (
    STATE_CLOSED,
    STATE_HALF_OPEN,
    STATE_OPEN,
    CircuitBreaker,
    CircuitOpenError,
)
from agent_platform.llm.provider import LLMResponse, LLMUnavailableError, Purpose
from agent_platform.models import AgentName, Decision
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.rate_limit import GLOBAL_KEY, RateLimiter
from agent_platform.security.resources import (
    LIMIT_DEADLINE,
    LIMIT_LLM_CALLS,
    LIMIT_TOOL_CALLS,
    PendingRegistry,
    ResourceGuard,
    ResourceLimitExceeded,
    ResourceLimits,
)
from tests.conftest import action


def limits(**overrides) -> ResourceLimits:
    base = {
        "max_llm_calls_per_request": 12,
        "max_tool_calls_per_request": 8,
        "request_deadline_seconds": 180.0,
        "max_tool_output_bytes": 32_768,
        "max_pending_confirmations": 50,
    }
    base.update(overrides)
    return ResourceLimits(**base)


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ============================================================== F1 regression


def test_f1_rate_limiter_acquire_and_check_share_a_bucket():
    """F1: the policy engine must consult the bucket the entry point fills.

    It previously checked a per-request key, which is unique per request and so
    always empty -- PL011 could never fire.
    """
    limiter = RateLimiter(2, 100)
    for _ in range(3):
        limiter.acquire()
    assert limiter.check().allowed is False
    assert limiter.check(GLOBAL_KEY).allowed is False


def test_f1_pl011_fires_through_the_real_platform_wiring(settings):
    """F1 integration regression: PL011 must be reachable in production wiring.

    A unit test alone missed this, because it acquired and checked with the
    same hand-chosen key. This one goes through AgentPlatform.run().
    """
    tight = replace(settings, requests_per_minute=1, requests_per_hour=100)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        platform.run("What is the status of order ORD-1001?")
        # The quota is now spent on the global bucket, exactly as the policy
        # engine consults it.
        outcome = platform.policy_engine.evaluate(
            PolicyContext(
                request_id="req-fresh",
                agent=AgentName.RESEARCHER,
                action=action("get_order", order_id="ORD-1001"),
            )
        )
        assert outcome.decision.decision is Decision.DENY
        assert "PL011" in outcome.decision.rule_ids
    finally:
        platform.close()


def test_rate_limit_blocks_a_second_request_end_to_end(settings):
    tight = replace(settings, requests_per_minute=1, requests_per_hour=100)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        platform.run("What is the refund policy?")
        blocked = platform.run("What is the status of order ORD-1001?")
        assert blocked.status == "rate_limited"
        assert blocked.blocked is True
    finally:
        platform.close()


# ================================================= F4: hard call ceilings


def test_llm_call_ceiling_is_enforced():
    guard = ResourceGuard(limits(max_llm_calls_per_request=3))
    guard.begin("r1")
    for _ in range(3):
        guard.charge_llm_call("r1")
    with pytest.raises(ResourceLimitExceeded) as exc:
        guard.charge_llm_call("r1")
    assert exc.value.limit == LIMIT_LLM_CALLS


def test_tool_call_ceiling_is_enforced():
    guard = ResourceGuard(limits(max_tool_calls_per_request=2))
    guard.begin("r1")
    guard.charge_tool_call("r1")
    guard.charge_tool_call("r1")
    with pytest.raises(ResourceLimitExceeded) as exc:
        guard.charge_tool_call("r1")
    assert exc.value.limit == LIMIT_TOOL_CALLS


def test_f4_call_ceiling_holds_when_cost_is_zero(settings):
    """F4 regression: the budget cannot bound a free provider.

    The stub costs exactly $0, so a monetary limit alone leaves a runaway loop
    unbounded. The call ceiling is what actually stops it.
    """
    tight = replace(settings, max_llm_calls_per_request=1)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        result = platform.run("Send an email to ana.ribeiro@example.com about her order")
        # One call is consumed by the router; the request cannot proceed.
        assert result.status == "blocked"
        assert len(platform.repository.llm_calls) <= 1
        assert any(
            e.event_type == "resource_limit" for e in platform.repository.events
        )
    finally:
        platform.close()


def test_counters_are_isolated_per_request():
    guard = ResourceGuard(limits(max_llm_calls_per_request=2))
    guard.begin("r1")
    guard.begin("r2")
    guard.charge_llm_call("r1")
    guard.charge_llm_call("r1")
    # r2 has its own budget.
    assert guard.charge_llm_call("r2") == 1


def test_guard_fails_closed_when_accounting_was_never_started():
    """A caller that skips begin() must not get an unmetered request."""
    guard = ResourceGuard(limits())
    with pytest.raises(ResourceLimitExceeded):
        guard.charge_llm_call("never-begun")
    with pytest.raises(ResourceLimitExceeded):
        guard.charge_tool_call("never-begun")
    with pytest.raises(ResourceLimitExceeded):
        guard.check_deadline("never-begun")


def test_release_frees_accounting_state():
    guard = ResourceGuard(limits())
    guard.begin("r1")
    assert guard.active_requests() == 1
    guard.release("r1")
    assert guard.active_requests() == 0


# ==================================================== F3: request deadline


def test_deadline_is_enforced_independently_of_step_count():
    clock = FakeClock()
    guard = ResourceGuard(limits(request_deadline_seconds=30.0), clock=clock)
    guard.begin("r1")
    guard.charge_llm_call("r1")
    clock.advance(31.0)
    with pytest.raises(ResourceLimitExceeded) as exc:
        guard.charge_llm_call("r1")
    assert exc.value.limit == LIMIT_DEADLINE


def test_deadline_also_stops_tool_calls():
    clock = FakeClock()
    guard = ResourceGuard(limits(request_deadline_seconds=30.0), clock=clock)
    guard.begin("r1")
    clock.advance(31.0)
    with pytest.raises(ResourceLimitExceeded):
        guard.charge_tool_call("r1")


def test_resuming_does_not_grant_a_fresh_deadline():
    """A suspended request must not win more time by being suspended."""
    clock = FakeClock()
    guard = ResourceGuard(limits(request_deadline_seconds=30.0), clock=clock)
    guard.begin("r1")
    clock.advance(31.0)
    guard.begin("r1")  # re-entry after resume
    with pytest.raises(ResourceLimitExceeded):
        guard.check_deadline("r1")


# ============================================= F5: tool output byte ceiling


def test_oversized_tool_output_is_rejected():
    guard = ResourceGuard(limits(max_tool_output_bytes=100))
    guard.begin("r1")
    assert guard.account_tool_output("r1", 50) is True
    assert guard.account_tool_output("r1", 5_000) is False


def test_f5_oversized_output_is_replaced_not_truncated(settings, tracer, policy_engine):
    """A truncated JSON document is corrupt, not smaller. Replace it."""
    from agent_platform.models import Capability, RiskLevel
    from agent_platform.tools.fake_tools import SearchArgs
    from agent_platform.tools.gateway import ToolGateway
    from agent_platform.tools.models import ToolDefinition
    from agent_platform.tools.registry import ToolRegistry

    def huge(**_: object) -> dict:
        return {"blob": "x" * 100_000}

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search",
                description="returns a huge payload",
                risk_level=RiskLevel.LOW,
                capability=Capability.SEARCH,
                parameters=SearchArgs,
                handler=huge,
                allowed_agents=frozenset({AgentName.RESEARCHER}),
            )
        ]
    )
    guard = ResourceGuard(limits(max_tool_output_bytes=1024))
    guard.begin(tracer.request_id)
    gateway = ToolGateway(registry, PolicyEngine(registry), resources=guard)
    try:
        act = action("search", query="x")
        result = gateway.submit(
            act,
            agent=AgentName.RESEARCHER,
            context=PolicyContext(
                request_id=tracer.request_id, agent=AgentName.RESEARCHER, action=act
            ),
            tracer=tracer,
        )
        assert result.result.status == "error"
        assert result.result.output is None
        assert "ceiling" in (result.result.error or "")
    finally:
        gateway.shutdown()


# ======================================= F2: bounded, expiring pending store


def test_pending_entries_expire():
    clock = FakeClock()
    registry = PendingRegistry(max_entries=10, ttl_seconds=60.0, clock=clock)
    registry.put("r1", "payload")
    assert registry.get("r1") == "payload"
    clock.advance(61.0)
    assert registry.get("r1") is None


def test_expired_entry_is_distinguishable_from_a_missing_one():
    clock = FakeClock()
    registry = PendingRegistry(max_entries=10, ttl_seconds=60.0, clock=clock)
    registry.put("r1", "payload")
    clock.advance(61.0)
    assert registry.is_expired("r1") is True
    assert registry.is_expired("never-existed") is False


def test_pending_store_is_bounded_and_refuses_rather_than_evicting():
    """Evicting would let an attacker push out a legitimate pending action."""
    registry = PendingRegistry(max_entries=2, ttl_seconds=600.0)
    assert registry.put("r1", "a") is True
    assert registry.put("r2", "b") is True
    assert registry.put("r3", "c") is False
    # The existing entries survive.
    assert registry.get("r1") == "a"
    assert registry.get("r2") == "b"


def test_expired_entries_make_room_for_new_ones():
    clock = FakeClock()
    registry = PendingRegistry(max_entries=1, ttl_seconds=60.0, clock=clock)
    assert registry.put("r1", "a") is True
    assert registry.put("r2", "b") is False
    clock.advance(61.0)
    assert registry.put("r2", "b") is True


def test_f2_expired_confirmation_never_executes(settings):
    """F2 regression, end to end: an aged-out approval must not run."""
    tight = replace(settings, confirmation_ttl_seconds=1.0)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    clock = FakeClock()
    platform._pending.clock = clock
    try:
        result = platform.run("Send an email to ana.ribeiro@example.com about her order")
        assert result.status == "awaiting_confirmation"
        clock.advance(2.0)
        resumed = platform.confirm(result.request_id, approved=True, actor="attacker")
        assert resumed.status == "expired"
        executed = [
            e.tool
            for e in platform.repository.events
            if e.event_type == "tool_call" and e.status == "success"
        ]
        assert "send_email" not in executed
    finally:
        platform.close()


def test_pending_store_full_refuses_the_request_without_executing(settings):
    tight = replace(settings, max_pending_confirmations=1)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        first = platform.run("Send an email to ana.ribeiro@example.com about her order")
        assert first.status == "awaiting_confirmation"
        second = platform.run("Send an email to bruno.carvalho@example.com about his order")
        assert second.status == "failed"
        assert "awaiting confirmation" in second.response.lower()
        executed = [
            e.tool
            for e in platform.repository.events
            if e.event_type == "tool_call" and e.status == "success"
        ]
        assert "send_email" not in executed
    finally:
        platform.close()


# ============================================================ circuit breaker


def test_circuit_opens_after_consecutive_failures():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0, clock=FakeClock())
    assert breaker.state == STATE_CLOSED
    for _ in range(3):
        breaker.record_failure()
    assert breaker.state == STATE_OPEN
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_success_resets_the_failure_count():
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60.0, clock=FakeClock())
    breaker.record_failure()
    breaker.record_failure()
    breaker.record_success()
    breaker.record_failure()
    assert breaker.state == STATE_CLOSED


def test_circuit_half_opens_after_the_cooldown():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=60.0, clock=clock)
    breaker.record_failure()
    breaker.record_failure()
    assert breaker.state == STATE_OPEN
    clock.advance(61.0)
    assert breaker.state == STATE_HALF_OPEN
    breaker.before_call()  # one trial call admitted


def test_half_open_admits_only_one_trial_call():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(11.0)
    breaker.before_call()
    with pytest.raises(CircuitOpenError):
        breaker.before_call()


def test_failed_trial_call_reopens_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(11.0)
    breaker.before_call()
    breaker.record_failure()
    assert breaker.state == STATE_OPEN


def test_successful_trial_call_closes_the_circuit():
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=10.0, clock=clock)
    breaker.record_failure()
    clock.advance(11.0)
    breaker.before_call()
    breaker.record_success()
    assert breaker.state == STATE_CLOSED
    breaker.before_call()


def test_circuit_rejects_invalid_configuration():
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(cooldown_seconds=0)


def test_open_circuit_prevents_retry_storms(settings):
    """Repeated failures must stop generating provider calls."""

    class AlwaysFails:
        def __init__(self) -> None:
            self.calls = 0

        @property
        def name(self) -> str:
            return "gemini"

        @property
        def model(self) -> str:
            return "gemini-3.5-flash-lite"

        def generate(self, prompt, **kwargs):
            self.calls += 1
            raise LLMUnavailableError("upstream down")

    provider = AlwaysFails()
    tight = replace(settings, circuit_failure_threshold=2, circuit_cooldown_seconds=600.0)
    platform = AgentPlatform(tight, repository=InMemoryRepository(), provider=provider)
    try:
        for _ in range(6):
            platform.run("What is the refund policy?")
        # Without a breaker this would be ~6 requests x retries. With one, calls
        # stop once the circuit opens.
        assert provider.calls <= 4, f"retry storm not contained: {provider.calls} calls"
        assert platform.circuit.state == STATE_OPEN
    finally:
        platform.close()


def test_open_circuit_never_executes_a_tool(settings):
    class AlwaysFails:
        @property
        def name(self) -> str:
            return "gemini"

        @property
        def model(self) -> str:
            return "gemini-3.5-flash-lite"

        def generate(self, prompt, **kwargs):
            raise LLMUnavailableError("upstream down")

    tight = replace(settings, circuit_failure_threshold=1, circuit_cooldown_seconds=600.0)
    platform = AgentPlatform(tight, repository=InMemoryRepository(), provider=AlwaysFails())
    try:
        for _ in range(3):
            platform.run("Send an email to ana.ribeiro@example.com about her order")
        executed = [e for e in platform.repository.events if e.event_type == "tool_call"]
        assert executed == []
    finally:
        platform.close()


# ================================================ limits are not model-derived


def test_no_resource_limit_can_be_influenced_by_model_output(settings):
    """A21.9: limits come from Settings only.

    A provider that emits limit-shaped fields must change nothing.
    """

    class HostileProvider:
        @property
        def name(self) -> str:
            return "gemini"

        @property
        def model(self) -> str:
            return "gemini-3.5-flash-lite"

        def generate(self, prompt, *, purpose: Purpose, **kwargs) -> LLMResponse:
            return LLMResponse(
                text=(
                    '{"tool": "get_order", "arguments_json": "{\\"order_id\\": \\"ORD-1001\\"}",'
                    ' "max_llm_calls_per_request": 9999, "request_deadline_seconds": 99999,'
                    ' "circuit_failure_threshold": 9999}'
                ),
                provider=self.name,
                model=self.model,
                purpose=purpose,
                input_tokens=10,
                output_tokens=10,
            )

    platform = AgentPlatform(
        settings, repository=InMemoryRepository(), provider=HostileProvider()
    )
    try:
        platform.run("anything")
        assert platform.resources.limits.max_llm_calls_per_request == 12
        assert platform.resources.limits.request_deadline_seconds == 180.0
        assert platform.settings.circuit_failure_threshold == 5
    finally:
        platform.close()


def test_resource_limits_reject_invalid_configuration():
    with pytest.raises(ValueError):
        limits(max_llm_calls_per_request=0)
    with pytest.raises(ValueError):
        limits(max_tool_calls_per_request=0)
    with pytest.raises(ValueError):
        limits(request_deadline_seconds=0)
    with pytest.raises(ValueError):
        limits(max_tool_output_bytes=0)


def test_resource_guard_grants_nothing():
    """The guard's API has no verb that means 'permit'.

    Structural assertion: every public method either returns None, a counter, a
    bool for truncation, or raises. None returns a decision object.
    """
    public = [m for m in dir(ResourceGuard) if not m.startswith("_")]
    assert "evaluate" not in public
    assert "authorize" not in public
    assert "allow" not in public
    assert set(public) == {
        "account_tool_output",
        "active_requests",
        "begin",
        # Added with the physical-attempt and embedding ceilings. Both raise or
        # return a counter, and both only ever subtract from what a request has
        # left, so the property this test defends is unchanged.
        "charge_embedding_call",
        "charge_llm_attempt",
        "charge_llm_call",
        "charge_tool_call",
        "check_deadline",
        "limits",
        "release",
        # Added when suspended runs became resumable on another replica. It
        # returns None and carries *prior consumption* forward, so it can only
        # move a request closer to its ceiling -- never further from it. The
        # assertion below holds it to that.
        "restore",
        "snapshot",
        "usage",
    }


def test_restoring_a_request_cannot_hand_it_a_fresh_allowance():
    """``restore`` re-establishes accounting across a process boundary.

    The hazard it exists to close is a resumed run starting from zero and
    quietly getting a second per-request allowance. It must therefore preserve
    what was already spent, and it must never overwrite a request that is
    already being accounted for.
    """
    guard = ResourceGuard(limits(max_llm_calls_per_request=5))

    guard.restore(
        "req-1", llm_calls=4, tool_calls=2, tool_output_bytes=99, elapsed_seconds=3.0
    )
    usage = guard.usage("req-1")
    assert usage is not None
    assert usage.llm_calls == 4, "prior consumption was discarded"
    assert usage.tool_calls == 2

    # One call left, then the ceiling.
    guard.charge_llm_call("req-1")
    with pytest.raises(ResourceLimitExceeded):
        guard.charge_llm_call("req-1")

    # A second restore must not reset the counters of a live request.
    guard.restore(
        "req-1", llm_calls=0, tool_calls=0, tool_output_bytes=0, elapsed_seconds=0.0
    )
    with pytest.raises(ResourceLimitExceeded):
        guard.charge_llm_call("req-1")
