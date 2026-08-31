"""Unit tests for the policy engine, risk model and rule catalogue."""

from __future__ import annotations

import pytest

from agent_platform.guardrails.policy import PolicyContext
from agent_platform.guardrails.risk import assess_risk, escalate
from agent_platform.guardrails.rules import (
    RULES,
    RULES_BY_ID,
    action_fingerprint,
    describe_rules,
    is_blocked_by_default,
    needs_confirmation,
)
from agent_platform.models import AgentName, Decision, ProposedAction, RiskLevel
from tests.conftest import action


def test_risk_ordering_is_not_alphabetical():
    assert RiskLevel.CRITICAL.rank > RiskLevel.HIGH.rank > RiskLevel.MEDIUM.rank
    assert RiskLevel.MEDIUM.rank > RiskLevel.LOW.rank
    # The bug this guards against: "critical" < "low" as plain strings.
    assert RiskLevel.CRITICAL.at_least(RiskLevel.LOW)
    assert not RiskLevel.LOW.at_least(RiskLevel.HIGH)


def test_escalate_saturates_at_critical():
    assert escalate(RiskLevel.LOW) is RiskLevel.MEDIUM
    assert escalate(RiskLevel.HIGH) is RiskLevel.CRITICAL
    assert escalate(RiskLevel.CRITICAL) is RiskLevel.CRITICAL
    assert escalate(RiskLevel.LOW, 10) is RiskLevel.CRITICAL


def test_confirmation_needed_for_high_and_above():
    assert needs_confirmation(RiskLevel.HIGH, False) is True
    assert needs_confirmation(RiskLevel.LOW, True) is True
    assert needs_confirmation(RiskLevel.LOW, False) is False


def test_only_critical_is_blocked_by_default():
    assert is_blocked_by_default(RiskLevel.CRITICAL) is True
    assert is_blocked_by_default(RiskLevel.HIGH) is False


def test_fingerprint_is_stable_and_argument_sensitive():
    a = ProposedAction(tool="send_email", arguments={"to": "a@b.com", "subject": "x"})
    b = ProposedAction(tool="send_email", arguments={"subject": "x", "to": "a@b.com"})
    c = ProposedAction(tool="send_email", arguments={"to": "evil@x.com", "subject": "x"})
    assert action_fingerprint(a) == action_fingerprint(b)  # key order is irrelevant
    assert action_fingerprint(a) != action_fingerprint(c)


def test_rule_catalogue_is_consistent():
    assert len(RULES) == len(RULES_BY_ID)
    ids = [r.rule_id for r in RULES]
    assert ids == sorted(ids), "rule ids should be listed in order"
    assert len(set(ids)) == len(ids), "duplicate rule id"
    for entry in describe_rules():
        assert entry["description"]
        assert entry["outcome"] in {"allow", "deny", "require_confirmation"}


def test_allow_path_returns_validated_arguments(policy_engine):
    outcome = policy_engine.evaluate(
        PolicyContext(
            request_id="req-test",
            agent=AgentName.RESEARCHER,
            action=action("get_order", order_id="ORD-1001"),
        )
    )
    assert outcome.decision.decision is Decision.ALLOW
    assert outcome.validated_arguments == {"order_id": "ORD-1001"}
    assert outcome.tool is not None
    assert outcome.risk is not None


def test_decision_reason_is_populated(policy_engine):
    outcome = policy_engine.evaluate(
        PolicyContext(
            request_id="req-test",
            agent=AgentName.EXECUTOR,
            action=action("nonexistent"),
        )
    )
    assert outcome.decision.reason
    assert outcome.decision.rule_ids


@pytest.mark.parametrize(
    "capability,expected",
    [("send_message", True), ("read_data", False)],
)
def test_pii_escalates_only_for_outbound_messages(registry, capability, expected):
    tool_name = "send_email" if capability == "send_message" else "get_customer"
    tool = registry.get(tool_name)
    arguments = (
        {"to": "a@b.com", "subject": "s", "body": "CPF 123.456.789-09"}
        if capability == "send_message"
        else {"customer_id": "CUS-2001"}
    )
    assessment = assess_risk(tool, arguments)
    escalated = any("PII" in reason for reason in assessment.reasons)
    assert escalated is expected


def test_non_idempotent_tools_are_noted(registry):
    assessment = assess_risk(
        registry.get("update_record"),
        {"record_id": "ORD-1001", "field": "status", "value": "x"},
    )
    assert any("non-idempotent" in reason for reason in assessment.reasons)
