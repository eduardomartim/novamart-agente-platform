"""The catalogue the dashboard showed and no tool could read.

Fifteen products were loaded, counted on the landing page and named in the
architecture diagram, and not one tool touched `PRODUCTS`. Six of the twenty
questions a visitor is most likely to type -- how many products, which ones,
what do they cost, which is cheapest, which is dearest, which sells most --
had no tool that could answer them, so all six were declined.

Every figure below is recomputed here from `dataset` rather than copied out of
the tool, for the reason the rest of the suite does it: a test that asserts the
tool agrees with itself passes even when both are wrong.

Two things the catalogue does *not* record are asserted as well. There is no
stock level and no unit cost, so availability and margin cannot be derived from
it, and a later edit that invents either should fail here rather than surface
as a confident answer.
"""

from __future__ import annotations

import collections

import pytest

from agent_platform.tools import analytics
from agent_platform.tools import dataset as data
from agent_platform.tools.execution import (
    DirectToolInvocationError,
    gateway_execution,
)

#: The four reads added for the catalogue, plus the one that was extended.
NEW_TOOLS = (
    "count_products",
    "list_products",
    "product_price_range",
    "top_selling_products",
)


@pytest.fixture
def authorised():
    """Tool handlers refuse outside the gateway; this opens it, as the
    executor does."""
    with gateway_execution():
        yield


# ============================================================ what they report


def test_the_catalogue_is_counted_by_category(authorised):
    expected = collections.Counter(p["category"] for p in data.PRODUCTS.values())

    result = analytics.count_products()

    assert result["total"] == len(data.PRODUCTS)
    assert result["by_category"] == dict(expected)
    assert str(len(data.PRODUCTS)) in result["summary"]


def test_the_category_totals_add_up_to_the_catalogue(authorised):
    """A breakdown that loses a product is worse than no breakdown."""
    result = analytics.count_products()
    assert sum(result["by_category"].values()) == result["total"]


def test_listing_the_catalogue_names_products_and_prices(authorised):
    result = analytics.list_products()

    assert result["matched"] == len(data.PRODUCTS)
    assert len(result["products"]) == len(data.PRODUCTS)
    names = {p["name"] for p in result["products"]}
    assert names == {p["name"] for p in data.PRODUCTS.values()}
    # Prices are what the question "how much does each product cost?" wants,
    # and they must be the dataset's own.
    for row in result["products"]:
        source = next(
            p for p in data.PRODUCTS.values() if p["name"] == row["name"]
        )
        assert row["unit_price_brl"] == source["unit_price_brl"]


def test_a_category_filter_narrows_to_that_category(authorised):
    result = analytics.list_products(category="furniture")

    expected = [p for p in data.PRODUCTS.values() if p["category"] == "furniture"]
    assert expected, "the dataset changed; this test picked an empty category"
    assert result["matched"] == len(expected)
    assert {p["name"] for p in result["products"]} == {p["name"] for p in expected}
    assert result["category_filter"] == "furniture"


def test_the_price_range_names_both_ends_and_the_middle(authorised):
    prices = [p["unit_price_brl"] for p in data.PRODUCTS.values()]
    dearest = max(data.PRODUCTS.values(), key=lambda p: p["unit_price_brl"])
    cheapest = min(data.PRODUCTS.values(), key=lambda p: p["unit_price_brl"])

    result = analytics.product_price_range()

    assert result["most_expensive"]["name"] == dearest["name"]
    assert result["cheapest"]["name"] == cheapest["name"]
    assert result["average_price_brl"] == pytest.approx(
        round(sum(prices) / len(prices), 2)
    )
    assert dearest["name"] in result["summary"]
    assert cheapest["name"] in result["summary"]


def test_sales_are_counted_from_the_order_lines(authorised):
    """The catalogue stores no sales figure, so this has to be derived.

    If it were ever backed by a stored field, that field would be fabricated:
    `PRODUCTS` has five keys and none of them is a total.
    """
    units: collections.Counter[str] = collections.Counter()
    for order in data.ORDERS.values():
        for line in order["items"]:
            units[line["name"]] += line["quantity"]

    result = analytics.top_selling_products()

    assert result["ranked_by"] == "units"
    assert result["products"][0]["name"] == units.most_common(1)[0][0]
    assert result["products"][0]["units"] == units.most_common(1)[0][1]
    for row in result["products"]:
        assert row["units"] == units[row["name"]]


def test_ranking_by_revenue_is_a_different_question(authorised):
    """Most units and most money are not the same product here.

    Which is the point of offering both: the cheapest item leads on volume and
    the dearest leads on revenue, so a single ranking would answer one question
    and quietly mis-answer the other.
    """
    revenue: collections.Counter[str] = collections.Counter()
    for order in data.ORDERS.values():
        for line in order["items"]:
            revenue[line["name"]] += line["line_total_brl"]

    result = analytics.top_selling_products(by="revenue")

    assert result["ranked_by"] == "revenue"
    assert result["products"][0]["name"] == revenue.most_common(1)[0][0]
    assert result["products"][0]["revenue_brl"] == pytest.approx(
        round(revenue.most_common(1)[0][1], 2)
    )


def test_the_two_rankings_actually_disagree(authorised):
    """Guards the test above: if they ever agree it proves nothing."""
    by_units = analytics.top_selling_products(by="units")["products"][0]["name"]
    by_revenue = analytics.top_selling_products(by="revenue")["products"][0]["name"]
    assert by_units != by_revenue, (
        "volume and revenue now lead with the same product; the ranking tests "
        "no longer distinguish the two orderings"
    )


def test_the_limit_is_respected(authorised):
    assert len(analytics.top_selling_products(limit=3)["products"]) == 3
    assert len(analytics.list_products(limit=4)["products"]) == 4


# ================================================== tickets carry the customer


def test_open_tickets_name_the_customers_they_belong_to(authorised):
    """"Which customers have tickets?" had no tool, and the link was there.

    Every ticket carries a `customer_id`; the tool used to report only the
    subject, so the answer listed complaints and named nobody.
    """
    unresolved = [
        t for t in data.TICKETS.values() if t["status"] in ("open", "escalated")
    ]
    expected = sorted(
        {data.CUSTOMERS[t["customer_id"]]["name"] for t in unresolved}
    )

    result = analytics.open_tickets()

    assert result["customers"] == expected
    assert all(row["customer"] for row in result["tickets"])
    for name in expected:
        assert name in result["summary"], f"{name} is in the data, not in the answer"


def test_filtering_by_priority_counts_only_that_priority(authorised):
    high = [
        t
        for t in data.TICKETS.values()
        if t["status"] in ("open", "escalated") and t["priority"] == "high"
    ]

    result = analytics.open_tickets(priority="high")

    assert result["open_count"] == len(high)
    assert result["priority_filter"] == "high"
    assert str(len(high)) in result["summary"]
    assert all(row["priority"] == "high" for row in result["tickets"])


def test_the_unfiltered_count_is_unchanged(authorised):
    """The extension is additive; the number the dashboard shows must hold."""
    unresolved = [
        t for t in data.TICKETS.values() if t["status"] in ("open", "escalated")
    ]
    assert analytics.open_tickets()["open_count"] == len(unresolved)


# =========================================== what the catalogue does not record


def test_the_catalogue_records_no_stock_and_no_cost():
    """The boundary these tools must not cross.

    Availability and margin are the two things a product catalogue is normally
    asked for and this one cannot supply. Asserting their absence is what makes
    the refusal in `test_question_phrasings.py` a fact about the data rather
    than a policy someone could quietly relax.
    """
    keys = {key for product in data.PRODUCTS.values() for key in product}
    assert keys == {"sku", "name", "category", "unit_price_brl", "warranty_months"}
    for forbidden in ("stock", "quantity", "available", "cost", "margin"):
        assert not any(forbidden in key for key in keys), (
            f"the dataset grew a '{forbidden}' field; the tools and the "
            f"refusals that depend on its absence need revisiting"
        )


def test_no_new_tool_reports_a_field_the_dataset_lacks(authorised):
    """A payload key nobody can source is a fabrication waiting to be read."""
    payloads = [
        analytics.count_products(),
        analytics.list_products(),
        analytics.product_price_range(),
        analytics.top_selling_products(),
    ]
    blob = " ".join(str(key) for payload in payloads for key in payload)
    for forbidden in ("stock", "inventory", "cost_brl", "margin", "profit"):
        assert forbidden not in blob, forbidden


# ====================================================== they refuse a bare call


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_a_new_tool_refuses_to_run_outside_the_gateway(name):
    """The guard every handler carries. Called directly, it must not answer.

    This is the check that caught the first draft of these four: they read the
    dataset and returned it without `require_gateway`, which is a way to reach
    the data without a policy decision ever being made.
    """
    handler = getattr(analytics, name)
    with pytest.raises(DirectToolInvocationError):
        handler()


def test_open_tickets_still_refuses_outside_the_gateway():
    with pytest.raises(DirectToolInvocationError):
        analytics.open_tickets(priority="high")


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_the_guard_names_the_tool_it_protects(name):
    """A bypass attempt has to be attributable in the log."""
    handler = getattr(analytics, name)
    with pytest.raises(DirectToolInvocationError) as raised:
        handler()
    assert name in str(raised.value)


# ============================================== they are registered and scoped


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_a_new_tool_is_in_the_one_registry(name):
    """Unregistered means unreachable: the executor resolves tools by name."""
    from agent_platform.tools.registry import default_registry

    assert default_registry().get(name) is not None


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_a_new_tool_is_a_low_risk_read(name):
    from agent_platform.models import Capability, RiskLevel
    from agent_platform.tools.registry import default_registry

    definition = default_registry().get(name)
    assert definition.capability is Capability.READ_DATA
    assert definition.risk_level is RiskLevel.LOW
    assert not definition.requires_confirmation


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_a_new_tool_is_not_offered_to_the_router(name):
    """PL002. The router classifies intent and executes nothing."""
    from agent_platform.models import AgentName
    from agent_platform.tools.registry import default_registry

    assert AgentName.ROUTER not in default_registry().get(name).allowed_agents


@pytest.mark.parametrize("name", NEW_TOOLS)
def test_a_new_tool_declares_the_capability_it_uses(name):
    """The matrix is the authorisation, not a label beside it.

    A tool whose `capability` is not granted to the agents in its
    `allowed_agents` would be denied at run time for every caller -- a tool
    that exists and can never run.
    """
    from agent_platform.guardrails import AGENT_CAPABILITIES
    from agent_platform.tools.registry import default_registry

    definition = default_registry().get(name)
    assert definition.allowed_agents, f"{name} is offered to nobody"
    for agent in definition.allowed_agents:
        assert definition.capability in AGENT_CAPABILITIES[agent], (
            f"{agent} may call {name} but does not hold {definition.capability}"
        )
