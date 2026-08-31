"""Security tests: least privilege and the gateway-only execution rule."""

from __future__ import annotations

import pytest

from agent_platform.guardrails.authorization import (
    AGENT_CAPABILITIES,
    UNASSIGNED_CAPABILITIES,
    authorize,
    capabilities_for,
)
from agent_platform.guardrails.policy import PolicyContext
from agent_platform.models import AgentName, Capability, Decision
from agent_platform.tools.execution import (
    DirectToolInvocationError,
    gateway_execution,
    is_inside_gateway,
)
from agent_platform.tools.fake_tools import (
    delete_record,
    get_order,
    get_ticket,
    list_customer_orders,
    search,
    send_email,
    update_record,
)
from tests.conftest import action


def evaluate(engine, agent, act):
    return engine.evaluate(
        PolicyContext(request_id="req-test", agent=agent, action=act)
    )


# 13. The router holds no tools.
def test_router_holds_no_capabilities():
    assert capabilities_for(AgentName.ROUTER) == frozenset()


def test_router_is_offered_no_tools(registry):
    assert registry.permitted_names(AgentName.ROUTER) == ()
    assert registry.specs_for(AgentName.ROUTER) == []


@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("search", {"query": "anything"}),
        ("get_order", {"order_id": "ORD-1001"}),
        ("send_email", {"to": "a@b.com", "subject": "s", "body": "b"}),
    ],
)
def test_router_proposing_any_tool_is_denied(policy_engine, tool_name, arguments):
    outcome = evaluate(policy_engine, AgentName.ROUTER, action(tool_name, **arguments))
    assert outcome.decision.decision is Decision.DENY
    assert "PL002" in outcome.decision.rule_ids


# 14. The researcher cannot perform a write.
@pytest.mark.parametrize(
    "tool_name,arguments",
    [
        ("update_record", {"record_id": "ORD-1001", "field": "status", "value": "x"}),
        ("send_email", {"to": "a@b.com", "subject": "s", "body": "b"}),
    ],
)
def test_researcher_cannot_write(policy_engine, tool_name, arguments):
    outcome = evaluate(policy_engine, AgentName.RESEARCHER, action(tool_name, **arguments))
    assert outcome.decision.decision is Decision.DENY
    assert "PL003" in outcome.decision.rule_ids


def test_validator_is_read_only():
    caps = capabilities_for(AgentName.VALIDATOR)
    assert caps == frozenset({Capability.READ_DATA})


def test_no_agent_holds_delete():
    for agent, caps in AGENT_CAPABILITIES.items():
        assert Capability.DELETE not in caps, f"{agent} unexpectedly holds DELETE"
    assert Capability.DELETE in UNASSIGNED_CAPABILITIES


def test_authorisation_requires_both_gates(registry):
    """The capability matrix still refuses if a tool allow-list is wrong.

    Simulates the misconfiguration of adding the researcher to a write tool's
    allow-list: authorisation must still fail on the capability check.
    """
    import dataclasses

    tool = registry.get("update_record")
    misconfigured = dataclasses.replace(
        tool, allowed_agents=frozenset({AgentName.RESEARCHER, AgentName.EXECUTOR})
    )
    result = authorize(AgentName.RESEARCHER, misconfigured)
    assert result.authorized is False
    assert "capability" in result.reason


# 12. Tools cannot be invoked outside the gateway.
@pytest.mark.parametrize(
    "func,kwargs",
    [
        (search, {"query": "x"}),
        (get_order, {"order_id": "ORD-1001"}),
        (list_customer_orders, {"customer_id": "CUS-2001"}),
        (get_ticket, {"ticket_id": "TKT-4001"}),
        (update_record, {"record_id": "ORD-1001", "field": "status", "value": "x"}),
        (send_email, {"to": "a@b.com", "subject": "s", "body": "b"}),
        (delete_record, {"record_id": "ORD-1001"}),
    ],
)
def test_direct_tool_invocation_is_refused(func, kwargs):
    with pytest.raises(DirectToolInvocationError):
        func(**kwargs)


def test_every_registered_tool_guards_itself(registry):
    """No tool may be missing its require_gateway() call."""
    samples = {
        "search": {"query": "x"},
        "get_order": {"order_id": "ORD-1001"},
        "get_customer": {"customer_id": "CUS-2001"},
        "find_customer": {"name": "Ana Ribeiro"},
        "list_customer_orders": {"customer_id": "CUS-2001"},
        "get_ticket": {"ticket_id": "TKT-4001"},
        "update_record": {"record_id": "ORD-1001", "field": "status", "value": "x"},
        "send_email": {"to": "a@b.com", "subject": "s", "body": "b"},
        "delete_record": {"record_id": "ORD-1001"},
    }
    assert set(samples) == set(registry.names()), "a tool was added without a guard test"
    for name, arguments in samples.items():
        with pytest.raises(DirectToolInvocationError):
            registry.get(name).handler(**arguments)


def test_gateway_context_is_scoped():
    assert is_inside_gateway() is False
    with gateway_execution():
        assert is_inside_gateway() is True
    assert is_inside_gateway() is False


def test_registry_never_exposes_callables_to_agents(registry):
    """Agents receive descriptions, never references they could invoke."""
    for agent in AgentName:
        for spec in registry.specs_for(agent):
            assert "handler" not in spec
            assert not any(callable(value) for value in spec.values())
