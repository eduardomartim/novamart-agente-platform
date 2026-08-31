"""`find_customer`: natural-language lookup without a natural-language hole.

A visitor asks "what is happening with Ana Ribeiro's order?" -- a perfectly
ordinary question that the tool layer could not answer, because every read tool
took an identifier. The gap mattered: with no way to resolve a name, a live
model's only options were to guess an id or search the knowledge base, and
neither answers the question.

This is the smallest capability that closes it, and it is deliberately narrow:
read-only, bounded, over the same dataset, through the same registry, under the
same policy engine. It resolves a name *and* returns that customer's orders in
one call, because the orchestration graph performs a single research hop -- so
a two-hop design would have needed a graph change to be useful.

What it must not become is a query interface. These tests pin the boundary as
much as the behaviour.
"""

from __future__ import annotations

import pytest

from agent_platform.models import AgentName, Capability, RiskLevel
from agent_platform.tools import fake_tools
from agent_platform.tools.execution import (
    DirectToolInvocationError,
    gateway_execution,
)
from agent_platform.tools.registry import default_registry


def call(**kwargs):
    with gateway_execution():
        return fake_tools.find_customer(**kwargs)


# ------------------------------------------------------------- it resolves


def test_resolves_a_full_name():
    result = call(name="Ana Ribeiro")
    assert result["found"] is True
    assert result["matches"][0]["customer_id"] == "CUS-2001"


def test_resolves_a_first_name():
    """A visitor will type "Ana", not "Ana Ribeiro"."""
    result = call(name="Ana")
    assert result["found"] is True
    assert any(m["customer_id"] == "CUS-2001" for m in result["matches"])


def test_match_is_case_insensitive():
    assert call(name="ana ribeiro")["found"] is True
    assert call(name="ANA RIBEIRO")["found"] is True


def test_returns_the_orders_so_one_hop_answers_the_question():
    """The graph performs one research hop; the answer must fit in it."""
    match = call(name="Ana Ribeiro")["matches"][0]
    assert "orders" in match, "a name lookup that omits orders answers nothing"
    assert match["orders"], "Ana Ribeiro has orders in the dataset"
    for order in match["orders"]:
        assert order["order_id"] in fake_tools.ORDERS


def test_unknown_name_reports_not_found_rather_than_inventing():
    result = call(name="Nobody Whatsoever")
    assert result["found"] is False
    assert not result.get("matches")


def test_empty_name_is_refused_not_treated_as_match_everything():
    """A blank query must never become a full dataset dump."""
    result = call(name="   ")
    assert result["found"] is False
    assert not result.get("matches")


# --------------------------------------------------------- it stays bounded


def test_a_broad_match_is_capped():
    """Bounded output is a resource control, not a formatting choice."""
    result = call(name="a")
    assert len(result.get("matches", [])) <= fake_tools.MAX_CUSTOMER_MATCHES


def test_it_cannot_be_used_to_enumerate_every_customer():
    total = len(fake_tools.CUSTOMERS)
    for probe in ("", " ", "a", "e", "%", "*"):
        matches = call(name=probe).get("matches", [])
        assert len(matches) < total, (
            f"probe {probe!r} returned the whole customer table"
        )


def test_it_exposes_no_more_than_the_existing_customer_tool():
    """A new read must not widen the field set the platform already exposes."""
    with gateway_execution():
        existing = fake_tools.get_customer(customer_id="CUS-2001")["customer"]
    match = call(name="Ana Ribeiro")["matches"][0]
    extra = set(match) - set(existing) - {"orders"}
    assert not extra, f"find_customer exposes new fields: {extra}"


# ------------------------------------------------ it obeys the same controls


def test_direct_invocation_is_refused_like_every_other_tool():
    with pytest.raises(DirectToolInvocationError):
        fake_tools.find_customer(name="Ana Ribeiro")


def test_it_is_registered_and_read_only():
    tool = default_registry().get("find_customer")
    assert tool.capability is Capability.READ_DATA
    assert tool.risk_level in {RiskLevel.LOW, RiskLevel.MEDIUM}


def test_it_is_not_reachable_by_an_agent_that_holds_no_read_capability():
    """The router classifies; it must not gain a data reach through this."""
    tool = default_registry().get("find_customer")
    assert AgentName.ROUTER not in tool.allowed_agents


def test_it_never_mutates_the_dataset():
    before = {c["customer_id"]: dict(c) for c in fake_tools.CUSTOMERS.values()}
    call(name="Ana")
    call(name="Bruno")
    after = {c["customer_id"]: dict(c) for c in fake_tools.CUSTOMERS.values()}
    assert before == after


def test_returned_records_are_copies_not_live_references():
    """Mutating a result must not reach the dataset behind it."""
    match = call(name="Ana Ribeiro")["matches"][0]
    match["name"] = "TAMPERED"
    assert fake_tools.CUSTOMERS["CUS-2001"]["name"] == "Ana Ribeiro"
