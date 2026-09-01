"""The questions a person actually asks a back-office dashboard.

Every test here failed before the analytics tools existed, and each failed the
same way: the platform answered a question about the *set* with a confident
sentence about one arbitrary *row*, and reported success. "How many customers
do we have?" came back as "Customer CUS-2001 is Ana Ribeiro (gold tier)". The
data was real, the question was ignored, and nothing in the trace said so.

So these assert three things at once, and all three matter:

* the **right tool** ran -- an aggregate question must not reach a record
  lookup, however plausible the sentence it produces;
* the **numbers are the dataset's**, computed here independently rather than
  copied from the tool, so a tool that drifts from the data fails;
* the **prose is for a person** -- no raw payload, and no internal identifier
  in an answer that did not ask for one.

Plus the case that has no answer. The product catalogue records no stock
level, so "which products have low stock?" must be declined rather than
approximated. A demo that invents an inventory to look complete is exactly
what this file exists to prevent.

Offline throughout: the stub provider is selected because no key is
configured, and the live gate would refuse a real one anyway.
"""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from agent_platform.config import Settings
from agent_platform.platform import AgentPlatform
from agent_platform.tools import dataset as data

#: Identifiers that must never appear in an answer to an aggregate question.
#: These three were the defaults the stub used to invent, and their presence in
#: a total or a ranking is the signature of the original defect.
FABRICATED = ("CUS-2001", "ORD-1001", "TKT-4001")


@pytest.fixture
def platform(settings: Settings, tmp_path):
    """A platform with room to ask several questions in one test run."""
    tuned = replace(
        settings,
        database_path=tmp_path / "business.db",
        requests_per_minute=500,
        requests_per_hour=5000,
        global_requests_per_minute=5000,
        global_requests_per_hour=50000,
    )
    instance = AgentPlatform(tuned)
    try:
        yield instance
    finally:
        instance.close()


def ask(platform: AgentPlatform, question: str) -> tuple[str, str, list[str]]:
    """Run one request and report what a visitor would see and what ran."""
    result = platform.run(question)
    events = platform.repository.events_for_request(result.request_id)
    tools = [
        str(event.get("tool"))
        for event in events
        if event.get("event_type") == "tool_call"
    ]
    return result.response or "", result.status, tools


def digits(text: str) -> set[str]:
    return set(re.findall(r"\d[\d.,]*", text))


# =========================================================== the ten questions


@pytest.mark.slow
def test_how_many_customers(platform):
    answer, status, tools = ask(platform, "How many customers do we have?")
    assert tools == ["count_customers"], tools
    assert status == "success"
    assert str(len(data.CUSTOMERS)) in answer
    assert "clientes" in answer.lower()
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_top_customers(platform):
    answer, status, tools = ask(platform, "Who are our top customers?")
    assert tools == ["top_customers"], tools
    assert status == "success"

    # Computed here from the dataset, so the tool cannot quietly disagree.
    revenue: dict[str, float] = {}
    for order in data.ORDERS.values():
        revenue[order["customer_id"]] = (
            revenue.get(order["customer_id"], 0.0) + order["total_brl"]
        )
        top = max(revenue.items(), key=lambda item: item[1])
    assert data.CUSTOMERS[top[0]]["name"] in answer
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_which_customer_placed_the_most_orders(platform):
    answer, status, tools = ask(platform, "Which customer placed the most orders?")
    assert tools == ["top_customers"], tools
    assert status == "success"

    counts: dict[str, int] = {}
    for order in data.ORDERS.values():
        counts[order["customer_id"]] = counts.get(order["customer_id"], 0) + 1
    most = max(counts.values())
    leaders = {
        data.CUSTOMERS[cid]["name"] for cid, n in counts.items() if n == most
    }
    assert any(name in answer for name in leaders), answer
    assert str(most) in answer


@pytest.mark.slow
def test_which_orders_are_delayed(platform):
    """Answerable, but only as "still open" -- and the answer has to say so.

    The dataset records no promised delivery date, so lateness cannot be
    computed. Listing the undelivered orders is the honest reading; claiming
    they are late would not be.
    """
    answer, status, tools = ask(platform, "Which orders are delayed?")
    assert tools == ["list_orders"], tools
    assert status == "success"

    open_orders = [
        o for o in data.ORDERS.values() if o["status"] in ("processing", "shipped")
    ]
    assert str(len(open_orders)) in answer
    assert "prazo de entrega" in answer, (
        "the answer treats undelivered as late without saying the dataset "
        "records no delivery deadline"
    )


@pytest.mark.slow
def test_low_stock_is_declined_because_there_is_no_stock(platform):
    """The one question in the list that has no answer here.

    Products carry sku, name, category, unit price and warranty. No inventory
    level exists, so a `low_stock_products` tool could only invent one.
    """
    assert not any(
        "stock" in key or "quantity" in key
        for product in data.PRODUCTS.values()
        for key in product
    ), "the dataset grew a stock field; this test and the refusal need revisiting"

    answer, status, tools = ask(platform, "Which products have low stock?")
    assert tools == [], f"a tool ran for a question nothing can answer: {tools}"
    assert status == "declined", status
    assert "não consigo responder" in answer.lower()
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_urgent_tickets(platform):
    answer, status, tools = ask(platform, "Are there any urgent tickets?")
    assert tools == ["open_tickets"], tools
    assert status == "success"

    unresolved = [t for t in data.TICKETS.values() if t["status"] != "resolved"]
    high = [t for t in unresolved if t["priority"] == "high"]
    assert str(len(unresolved)) in answer
    assert str(len(high)) in answer
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_total_order_value(platform):
    answer, status, tools = ask(platform, "What is the total value of all orders?")
    assert tools == ["revenue_total"], tools
    assert status == "success"

    total = sum(order["total_brl"] for order in data.ORDERS.values())
    # Rendered in Brazilian format, so compare on the digits rather than the
    # separators: 33002.5 is shown as "33.002,50".
    assert "33.002,50" in answer or f"{total:.2f}" in answer, answer
    assert "R$" in answer


@pytest.mark.slow
def test_recent_orders(platform):
    answer, status, tools = ask(platform, "Show me recent orders.")
    assert tools == ["list_orders"], tools
    assert status == "success"
    newest = max(order["placed_on"] for order in data.ORDERS.values())
    assert newest in answer, "an answer about recent orders names the oldest one"


@pytest.mark.slow
def test_customer_with_most_purchases(platform):
    answer, status, tools = ask(platform, "Which customer has the most purchases?")
    assert tools == ["top_customers"], tools
    assert status == "success"
    assert digits(answer), "a ranking with no numbers in it"


@pytest.mark.slow
def test_anything_a_manager_should_know(platform):
    answer, status, tools = ask(
        platform, "Is there any problem that needs a manager's attention?"
    )
    assert tools == ["business_overview"], tools
    assert status == "success"

    high = [
        t
        for t in data.TICKETS.values()
        if t["status"] != "resolved" and t["priority"] == "high"
    ]
    assert str(len(high)) in answer
    assert "atenção" in answer.lower()


# ================================================================ regressions


@pytest.mark.slow
@pytest.mark.parametrize(
    "question",
    [
        "How many customers do we have?",
        "What is the total value of all orders?",
        "Are there any urgent tickets?",
        "Who are our top customers?",
    ],
)
def test_an_aggregate_question_never_becomes_a_record_lookup(platform, question):
    """The defect itself, pinned: the shape of the failure, not one instance.

    Every one of these used to reach `get_customer`, `get_order` or
    `list_customer_orders` with an identifier the question never contained.
    """
    answer, _status, tools = ask(platform, question)
    for record_tool in ("get_customer", "get_order", "get_ticket",
                        "list_customer_orders", "find_customer"):
        assert record_tool not in tools, (
            f"{question!r} was served by {record_tool}, which reads one row"
        )
    assert not any(marker in answer for marker in FABRICATED), (
        f"{question!r} was answered with a fabricated identifier: {answer}"
    )


@pytest.mark.slow
def test_a_missing_identifier_still_reports_not_found(platform):
    """The honest path that already worked must keep working."""
    answer, status, tools = ask(platform, "What is the status of order ORD-9999?")
    assert tools == ["get_order"], tools
    assert status == "success"
    assert "no record was found" in answer.lower()


@pytest.mark.slow
def test_nonsense_runs_no_tool(platform):
    answer, _status, tools = ask(platform, "asdkjhasd qwe zxcvb")
    assert tools == [], tools
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_an_out_of_scope_question_is_declined_not_answered(platform):
    """Weather is not in the dataset and no tool pretends otherwise."""
    answer, status, tools = ask(platform, "What is the weather in Tokyo?")
    assert tools == [], f"a tool ran for an out-of-scope question: {tools}"
    assert status == "declined"
    assert not any(marker in answer for marker in FABRICATED)


@pytest.mark.slow
def test_a_record_question_with_an_identifier_still_reaches_its_tool(platform):
    """Aggregates must not swallow the lookups they sit beside."""
    answer, status, tools = ask(platform, "What is the status of order ORD-1001?")
    assert tools == ["get_order"], tools
    assert status == "success"
    assert "ORD-1001" in answer


def test_every_analytics_tool_is_read_only_and_low_risk():
    """Risk and permissions are platform-owned; assert them, do not assume."""
    from agent_platform.models import AgentName, Capability, RiskLevel
    from agent_platform.tools.analytics import ANALYTICS_TOOL_DEFINITIONS

    assert ANALYTICS_TOOL_DEFINITIONS, "the analytics tools disappeared"
    for definition in ANALYTICS_TOOL_DEFINITIONS:
        assert definition.risk_level is RiskLevel.LOW, definition.name
        assert definition.capability is Capability.READ_DATA, definition.name
        assert not definition.requires_confirmation, definition.name
        assert AgentName.ROUTER not in definition.allowed_agents, (
            f"{definition.name} is offered to the router, which PL002 forbids"
        )


def test_the_analytics_tools_are_registered_in_the_one_registry():
    """A tool the registry does not know cannot be executed at all."""
    from agent_platform.tools.analytics import ANALYTICS_TOOL_DEFINITIONS
    from agent_platform.tools.registry import default_registry

    registry = default_registry()
    for definition in ANALYTICS_TOOL_DEFINITIONS:
        assert definition.name in registry, definition.name
