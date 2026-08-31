"""Integration tests for the gateway: policy decision to executed call."""

from __future__ import annotations

import time

import pytest

from agent_platform.guardrails.policy import Confirmation, PolicyContext
from agent_platform.guardrails.rules import action_fingerprint
from agent_platform.models import AgentName, Decision
from agent_platform.tools.execution import is_inside_gateway
from agent_platform.tools.gateway import ToolGateway
from agent_platform.tools.models import ToolDefinition
from agent_platform.tools.registry import ToolRegistry
from tests.conftest import action


def submit(gateway, tracer, agent, act, **kwargs):
    context = PolicyContext(
        request_id=tracer.request_id, agent=agent, action=act, **kwargs
    )
    return gateway.submit(act, agent=agent, context=context, tracer=tracer)


def test_allowed_read_executes_and_returns_data(gateway, tracer):
    result = submit(
        gateway, tracer, AgentName.RESEARCHER, action("get_order", order_id="ORD-1001")
    )
    assert result.decision.decision is Decision.ALLOW
    assert result.result.status == "success"
    assert result.result.output["order"]["order_id"] == "ORD-1001"
    assert result.result.simulated is True


def test_denied_action_never_reaches_the_handler(gateway, tracer, repository):
    result = submit(
        gateway,
        tracer,
        AgentName.RESEARCHER,
        action("update_record", record_id="ORD-1001", field="status", value="x"),
    )
    assert result.decision.decision is Decision.DENY
    assert result.result.status == "denied"
    assert result.executed is False
    # No tool_call event means no handler ran.
    assert [e for e in repository.events if e.event_type == "tool_call"] == []


def test_confirmation_required_does_not_execute(gateway, tracer, repository):
    result = submit(
        gateway,
        tracer,
        AgentName.EXECUTOR,
        action("send_email", to="a@b.com", subject="s", body="b"),
    )
    assert result.awaiting_confirmation is True
    assert [e for e in repository.events if e.event_type == "tool_call"] == []
    assert any(e.event_type == "confirmation_requested" for e in repository.events)


def test_confirmed_action_executes(gateway, tracer):
    act = action("send_email", to="a@b.com", subject="s", body="b")
    confirmation = Confirmation(
        approved=True,
        source="test",
        actor="tester",
        action_fingerprint=action_fingerprint(act),
    )
    result = submit(
        gateway, tracer, AgentName.EXECUTOR, act, confirmation=confirmation
    )
    assert result.result.status == "success"
    assert result.result.output["sent"] is False  # still simulated


def test_gateway_records_a_full_event_sequence(gateway, tracer, repository):
    submit(gateway, tracer, AgentName.RESEARCHER, action("search", query="refund"))
    types = [e.event_type for e in repository.events]
    assert types == ["action_proposed", "policy_decision", "tool_call"]
    assert [e.sequence for e in repository.events] == [1, 2, 3]


def test_handler_exception_is_captured_not_raised(tracer, policy_engine):
    """A failing tool must produce an error result, not crash the request."""

    def explode(**_: object) -> None:
        raise RuntimeError("tool blew up")

    from agent_platform.models import Capability, RiskLevel
    from agent_platform.tools.fake_tools import SearchArgs

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search",
                description="failing tool",
                risk_level=RiskLevel.LOW,
                capability=Capability.SEARCH,
                parameters=SearchArgs,
                handler=explode,
                allowed_agents=frozenset({AgentName.RESEARCHER}),
            )
        ]
    )
    from agent_platform.guardrails.policy import PolicyEngine

    gateway = ToolGateway(registry, PolicyEngine(registry))
    try:
        result = submit(
            gateway, tracer, AgentName.RESEARCHER, action("search", query="x")
        )
        assert result.result.status == "error"
        assert "tool blew up" in (result.result.error or "")
    finally:
        gateway.shutdown()


def test_slow_tool_times_out(tracer):
    """A tool exceeding its timeout is reported as a timeout, not a hang."""

    def slow(**_: object) -> None:
        time.sleep(2.0)

    from agent_platform.guardrails.policy import PolicyEngine
    from agent_platform.models import Capability, RiskLevel
    from agent_platform.tools.fake_tools import SearchArgs

    registry = ToolRegistry(
        [
            ToolDefinition(
                name="search",
                description="slow tool",
                risk_level=RiskLevel.LOW,
                capability=Capability.SEARCH,
                parameters=SearchArgs,
                handler=slow,
                allowed_agents=frozenset({AgentName.RESEARCHER}),
                timeout_seconds=0.2,
            )
        ]
    )
    gateway = ToolGateway(registry, PolicyEngine(registry))
    try:
        result = submit(
            gateway, tracer, AgentName.RESEARCHER, action("search", query="x")
        )
        assert result.result.status == "timeout"
    finally:
        gateway.shutdown()


def test_execution_context_is_released_after_the_call(gateway, tracer):
    submit(gateway, tracer, AgentName.RESEARCHER, action("get_order", order_id="ORD-1001"))
    assert is_inside_gateway() is False


def test_arguments_are_validated_before_execution(gateway, tracer, repository):
    submit(
        gateway, tracer, AgentName.RESEARCHER, action("get_order", order_id="bad-format")
    )
    assert [e for e in repository.events if e.event_type == "tool_call"] == []


@pytest.mark.parametrize(
    "agent,tool_name,arguments,expected",
    [
        (AgentName.RESEARCHER, "search", {"query": "x"}, Decision.ALLOW),
        (AgentName.RESEARCHER, "get_order", {"order_id": "ORD-1001"}, Decision.ALLOW),
        (AgentName.VALIDATOR, "get_order", {"order_id": "ORD-1001"}, Decision.ALLOW),
        (AgentName.VALIDATOR, "search", {"query": "x"}, Decision.DENY),
        (AgentName.EXECUTOR, "delete_record", {"record_id": "ORD-1001"}, Decision.DENY),
        (AgentName.ROUTER, "search", {"query": "x"}, Decision.DENY),
    ],
)
def test_permission_matrix_end_to_end(gateway, tracer, agent, tool_name, arguments, expected):
    result = submit(gateway, tracer, agent, action(tool_name, **arguments))
    assert result.decision.decision is expected
