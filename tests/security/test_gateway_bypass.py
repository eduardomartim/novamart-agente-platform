"""Security tests: attempts to reach a tool without the gateway.

Attack matrix section 3, plus the ContextVar concurrency isolation the guard
depends on.
"""

from __future__ import annotations

import threading

import pytest

from agent_platform.tools.execution import (
    DirectToolInvocationError,
    gateway_execution,
    is_inside_gateway,
    require_gateway,
)
from agent_platform.tools.registry import default_registry

SAMPLES = {
    "search": {"query": "x"},
    "get_order": {"order_id": "ORD-1001"},
    "find_customer": {"name": "Ana Ribeiro"},
    "get_customer": {"customer_id": "CUS-2001"},
    "list_customer_orders": {"customer_id": "CUS-2001"},
    "get_ticket": {"ticket_id": "TKT-4001"},
    "update_record": {"record_id": "ORD-1001", "field": "status", "value": "x"},
    "send_email": {"to": "a@b.com", "subject": "s", "body": "b"},
    "delete_record": {"record_id": "ORD-1001"},
    # The aggregate reads. They take no identifier -- that is the point of
    # them -- but they are handlers like any other and must refuse a direct
    # call just the same.
    "count_customers": {},
    "revenue_total": {},
    "list_orders": {},
    "top_customers": {},
    "open_tickets": {},
    "business_overview": {},
    # The catalogue reads. Products were visible in the dashboard and reachable
    # by no tool at all; these close that gap and refuse a direct call like
    # every other handler.
    "count_products": {},
    "list_products": {},
    "product_price_range": {},
    "top_selling_products": {},
}


def test_registry_and_samples_stay_in_sync():
    """A new tool without a bypass test would otherwise go unchecked."""
    assert set(SAMPLES) == set(default_registry().names())


@pytest.mark.parametrize("name", sorted(SAMPLES))
def test_direct_handler_invocation_is_refused(name):
    """A3.2: reaching the handler through the registry is still not the gateway."""
    with pytest.raises(DirectToolInvocationError):
        default_registry().get(name).handler(**SAMPLES[name])


def test_token_cannot_be_reused_after_the_context_closes():
    """A3.3: the authorisation is the live context, not a value to keep."""
    with gateway_execution() as token:
        assert is_inside_gateway() is True
        assert token
    assert is_inside_gateway() is False
    with pytest.raises(DirectToolInvocationError):
        require_gateway("search")


def test_each_gateway_context_issues_a_distinct_token():
    with gateway_execution() as first:
        pass
    with gateway_execution() as second:
        pass
    assert first != second


def test_authorisation_does_not_leak_into_another_thread():
    """A3.4: ContextVar is per-context, so a sibling thread is not authorised."""
    outcome: dict[str, object] = {}

    def sibling() -> None:
        try:
            require_gateway("send_email")
            outcome["result"] = "AUTHORISED"
        except DirectToolInvocationError:
            outcome["result"] = "refused"

    with gateway_execution():
        assert is_inside_gateway() is True
        thread = threading.Thread(target=sibling)
        thread.start()
        thread.join()

    assert outcome["result"] == "refused"


def test_concurrent_gateway_contexts_do_not_interfere():
    """Two threads each in their own context; neither sees the other's."""
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def worker() -> None:
        try:
            with gateway_execution():
                barrier.wait(timeout=5)
                if not is_inside_gateway():
                    errors.append("lost own context")
            if is_inside_gateway():
                errors.append("context leaked after exit")
        except Exception as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []


def test_context_is_released_even_when_the_tool_raises():
    """A handler that blows up must not leave the process authorised."""
    with pytest.raises(RuntimeError), gateway_execution():
        raise RuntimeError("tool exploded")
    assert is_inside_gateway() is False


def test_nested_contexts_restore_correctly():
    with gateway_execution():
        with gateway_execution():
            assert is_inside_gateway() is True
        assert is_inside_gateway() is True
    assert is_inside_gateway() is False


def test_agents_never_receive_a_callable(registry):
    """A tool an agent cannot reference is a tool it cannot call."""
    from agent_platform.models import AgentName

    for agent in AgentName:
        for spec in registry.specs_for(agent):
            assert "handler" not in spec
            assert not any(callable(v) for v in spec.values())
