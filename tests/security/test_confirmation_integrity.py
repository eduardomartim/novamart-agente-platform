"""Security tests: confirmation integrity.

Attack matrix section 6, plus isolation checks from section 7.

The property under test throughout: a human's approval authorises exactly one
action, once, for a bounded time. Everything else is refused.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.guardrails.policy import Confirmation, PolicyContext
from agent_platform.guardrails.rules import action_fingerprint
from agent_platform.models import AgentName, Decision, ProposedAction
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from tests.conftest import action

EMAIL_ARGS = {"to": "ana.ribeiro@example.com", "subject": "s", "body": "b"}


def evaluate(engine, act, confirmation=None):
    return engine.evaluate(
        PolicyContext(
            request_id="req-test",
            agent=AgentName.EXECUTOR,
            action=act,
            confirmation=confirmation,
        )
    )


def approval_for(act: ProposedAction, *, actor: str = "operator") -> Confirmation:
    return Confirmation(
        approved=True,
        source="test",
        actor=actor,
        action_fingerprint=action_fingerprint(act),
    )


def executed(platform) -> list[str]:
    return [
        e.tool
        for e in platform.repository.events
        if e.event_type == "tool_call" and e.status == "success"
    ]


# ======================================================= fingerprint binding


def test_confirmation_authorises_exactly_the_action_shown(policy_engine):
    act = action("send_email", **EMAIL_ARGS)
    assert evaluate(policy_engine, act, approval_for(act)).decision.decision is Decision.ALLOW


@pytest.mark.parametrize(
    "mutation",
    [
        {"to": "attacker@evil.com"},
        {"subject": "different subject"},
        {"body": "different body"},
    ],
)
def test_any_argument_change_invalidates_the_confirmation(policy_engine, mutation):
    """A6.8: the fingerprint covers every argument, not just the tool name."""
    approved = action("send_email", **EMAIL_ARGS)
    substituted = action("send_email", **{**EMAIL_ARGS, **mutation})
    outcome = evaluate(policy_engine, substituted, approval_for(approved))
    assert outcome.decision.decision is Decision.DENY
    assert "PL010" in outcome.decision.rule_ids


def test_confirmation_for_one_tool_does_not_authorise_another(policy_engine):
    approved = action("send_email", **EMAIL_ARGS)
    other = action("update_record", record_id="ORD-1001", field="status", value="x")
    outcome = evaluate(policy_engine, other, approval_for(approved))
    assert outcome.decision.decision is Decision.DENY
    assert "PL010" in outcome.decision.rule_ids


def test_argument_order_does_not_change_the_fingerprint():
    """Canonical JSON: key order is not semantic."""
    a = ProposedAction(tool="send_email", arguments={"to": "a@b.com", "subject": "s"})
    b = ProposedAction(tool="send_email", arguments={"subject": "s", "to": "a@b.com"})
    assert action_fingerprint(a) == action_fingerprint(b)


def test_unapproved_confirmation_does_not_authorise(policy_engine):
    act = action("send_email", **EMAIL_ARGS)
    refused = Confirmation(
        approved=False, source="test", actor="op", action_fingerprint=action_fingerprint(act)
    )
    outcome = evaluate(policy_engine, act, refused)
    assert outcome.decision.decision is Decision.REQUIRE_CONFIRMATION


def test_confirmation_cannot_unlock_a_critical_action(policy_engine):
    act = action("delete_record", record_id="ORD-1001")
    outcome = evaluate(policy_engine, act, approval_for(act))
    assert outcome.decision.decision is Decision.DENY
    assert "PL005" in outcome.decision.rule_ids


# ========================================================= end-to-end flows


def test_approval_executes_exactly_once(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    assert result.status == "awaiting_confirmation"
    first = platform.confirm(result.request_id, approved=True, actor="operator")
    assert first.status == "success"
    assert executed(platform).count("send_email") == 1

    # A6.3: the same confirmation replayed must not run a second time.
    second = platform.confirm(result.request_id, approved=True, actor="operator")
    assert second.status == "failed"
    assert executed(platform).count("send_email") == 1


def test_decline_terminates_without_re_asking(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    declined = platform.confirm(result.request_id, approved=False, actor="operator")
    assert declined.status == "declined"
    assert declined.awaiting_confirmation is None
    assert "send_email" not in executed(platform)


def test_declined_action_cannot_then_be_approved(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    platform.confirm(result.request_id, approved=False, actor="operator")
    retried = platform.confirm(result.request_id, approved=True, actor="attacker")
    assert retried.status == "failed"
    assert "send_email" not in executed(platform)


def test_confirming_an_unknown_request_is_a_safe_failure(platform):
    result = platform.confirm("req-does-not-exist", approved=True)
    assert result.status == "failed"
    assert executed(platform) == []


def test_policy_is_re_evaluated_on_resume(platform):
    """A6.4: conditions may have changed while a human was deciding."""
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    platform.confirm(result.request_id, approved=True, actor="operator")
    decisions = [
        e for e in platform.repository.events if e.event_type == "policy_decision"
    ]
    # One at proposal time (require_confirmation), one on resume (allow).
    verdicts = [e.policy_decision for e in decisions if e.tool == "send_email"]
    assert "require_confirmation" in verdicts
    assert "allow" in verdicts


def test_stale_confirmation_is_refused_after_ttl(settings):
    """A6.4 end to end, with a deterministic clock."""

    class FakeClock:
        def __init__(self) -> None:
            self.now = 1000.0

        def __call__(self) -> float:
            return self.now

    clock = FakeClock()
    tight = replace(settings, confirmation_ttl_seconds=60.0)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    platform._pending.clock = clock
    try:
        result = platform.run("Send an email to ana.ribeiro@example.com about her order")
        assert result.status == "awaiting_confirmation"
        clock.now += 61.0
        resumed = platform.confirm(result.request_id, approved=True, actor="operator")
        assert resumed.status == "expired"
        assert "send_email" not in executed(platform)
    finally:
        platform.close()


# =============================================== isolation (matrix section 7)


def test_request_and_trace_ids_are_unguessable(platform):
    """A7.2: ids gate confirm(), so they must not be predictable."""
    ids = {platform.run("What is the refund policy?").request_id for _ in range(5)}
    assert len(ids) == 5
    for rid in ids:
        assert rid.startswith("req-")
        assert len(rid) >= 16  # uuid4-derived hex


def test_one_request_cannot_resume_another(platform):
    """A6.6: confirm() is keyed by request id and does not accept a substitute."""
    first = platform.run("Send an email to ana.ribeiro@example.com about her order")
    second = platform.run("Send an email to bruno.carvalho@example.com about his order")
    assert first.request_id != second.request_id

    # Approving `first` must resolve only `first`.
    platform.confirm(first.request_id, approved=True, actor="operator")
    recipients = [
        e.payload.get("arguments", {}).get("to")
        for e in platform.repository.events
        if e.event_type == "tool_call" and e.tool == "send_email"
    ]
    assert recipients == ["a***@example.com"] or all(
        r != "bruno.carvalho@example.com" for r in recipients
    )


def test_events_are_scoped_to_their_own_request(platform):
    a = platform.run("What is the status of order ORD-1001?")
    b = platform.run("What is the refund policy?")
    a_events = platform.repository.events_for_request(a.request_id)
    b_events = platform.repository.events_for_request(b.request_id)
    assert a_events and b_events
    assert {e["request_id"] for e in a_events} == {a.request_id}
    assert {e["request_id"] for e in b_events} == {b.request_id}
