"""Unit tests for the registry and the simulated tools."""

from __future__ import annotations

import pytest

from agent_platform.models import AgentName, Capability, RiskLevel
from agent_platform.tools.execution import gateway_execution
from agent_platform.tools.fake_tools import (
    SIMULATED_EMAIL_MARKER,
    delete_record,
    get_customer,
    get_order,
    reset_dataset,
    search,
    send_email,
    update_record,
)
from agent_platform.tools.models import ToolNotRegisteredError
from agent_platform.tools.registry import ToolRegistry, default_registry


def test_registry_rejects_duplicate_registration(registry):
    definition = registry.get("search")
    with pytest.raises(ValueError, match="already registered"):
        registry.register(definition)


def test_registry_raises_for_unknown_tool(registry):
    with pytest.raises(ToolNotRegisteredError):
        registry.get("does_not_exist")
    assert registry.try_get("does_not_exist") is None
    assert "does_not_exist" not in registry


def test_empty_registry_is_usable():
    empty = ToolRegistry()
    assert len(empty) == 0
    assert empty.names() == ()


def test_permitted_names_match_the_matrix(registry):
    assert registry.permitted_names(AgentName.ROUTER) == ()
    researcher = set(registry.permitted_names(AgentName.RESEARCHER))
    # Exhaustive on purpose: a tool that quietly enters the researcher's reach
    # fails here rather than being noticed later. The second group reads the
    # dataset in aggregate -- counts, totals and rankings -- and is read-only
    # for the same reason the first group is.
    assert researcher == {
        "search",
        "get_order",
        "get_customer",
        "find_customer",
        "list_customer_orders",
        "get_ticket",
        "count_customers",
        "revenue_total",
        "list_orders",
        "top_customers",
        "open_tickets",
        "business_overview",
        "count_products",
        "list_products",
        "product_price_range",
        "top_selling_products",
    }
    executor = set(registry.permitted_names(AgentName.EXECUTOR))
    assert "update_record" in executor and "send_email" in executor
    assert "delete_record" not in executor


def test_public_spec_excludes_the_handler(registry):
    spec = registry.get("send_email").public_spec()
    assert set(spec) == {"name", "description", "risk_level", "requires_confirmation", "parameters"}
    assert spec["parameters"]["type"] == "object"


def test_risk_levels_cover_every_band(registry):
    levels = {definition.risk_level for definition in registry}
    assert levels == {RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL}


def test_write_tools_are_marked_non_idempotent(registry):
    for name in ("update_record", "send_email", "delete_record"):
        assert registry.get(name).idempotent is False
    for name in ("search", "get_order", "get_customer"):
        assert registry.get(name).idempotent is True


def test_every_tool_is_marked_simulated(registry):
    assert all(definition.simulated for definition in registry)


# ------------------------------------------------------------------ behaviour


def test_tools_operate_only_in_memory():
    with gateway_execution():
        assert get_order("ORD-1001")["found"] is True
        assert get_order("ORD-9999")["found"] is False
        assert get_customer("CUS-2001")["found"] is True
        assert search("refund policy")["result_count"] >= 1


def test_send_email_sends_nothing():
    with gateway_execution():
        result = send_email(to="a@b.com", subject="s", body="hello")
    assert result["sent"] is False
    assert result["simulated"] is True
    assert result["status"] == SIMULATED_EMAIL_MARKER
    # The body is not echoed back, only its length.
    assert "hello" not in str(result)


def test_update_record_mutates_only_the_in_memory_dataset():
    with gateway_execution():
        result = update_record(record_id="ORD-1001", field="status", value="refunded")
        assert result["updated"] is True
        assert result["previous_value"] == "shipped"
        assert get_order("ORD-1001")["order"]["status"] == "refunded"

    reset_dataset()
    with gateway_execution():
        assert get_order("ORD-1001")["order"]["status"] == "shipped"


def test_update_record_rejects_unknown_fields():
    with gateway_execution():
        result = update_record(record_id="ORD-1001", field="nope", value="x")
    assert result["updated"] is False
    assert "allowed_fields" in result


def test_delete_record_is_reversible_via_reset():
    with gateway_execution():
        assert delete_record(record_id="ORD-1002")["deleted"] is True
        assert get_order("ORD-1002")["found"] is False
    reset_dataset()
    with gateway_execution():
        assert get_order("ORD-1002")["found"] is True


def test_default_registry_is_a_fresh_instance():
    assert default_registry() is not default_registry()


def test_capabilities_are_assigned_sensibly(registry):
    assert registry.get("search").capability is Capability.SEARCH
    assert registry.get("get_order").capability is Capability.READ_DATA
    assert registry.get("update_record").capability is Capability.WRITE_DATA
    assert registry.get("send_email").capability is Capability.SEND_MESSAGE
    assert registry.get("delete_record").capability is Capability.DELETE
