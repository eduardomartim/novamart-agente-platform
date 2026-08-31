"""The demo's promises must be true.

A recruiter clicks an example question and expects an answer. If the example
was written by hand and never run, it can reference an order that does not
exist or phrase something the router cannot classify -- and the first thing the
visitor sees is a failure. That is the worst possible first impression, and it
is entirely preventable: run every example.

These also pin the narrative to the data. The company overview is *derived*
from the dataset rather than duplicated, so a board claiming "1 high-priority
ticket" cannot drift away from what the tools actually return.

Everything runs on the deterministic stub. No provider is contacted.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

import demo_content as demo  # noqa: E402

from agent_platform.persistence.memory import InMemoryRepository  # noqa: E402
from agent_platform.platform import AgentPlatform  # noqa: E402
from agent_platform.tools import dataset as data  # noqa: E402


def _run(settings, question: str):
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        return platform.run(question), list(platform.repository.events)
    finally:
        platform.close()


# ------------------------------------------------ every example must answer


@pytest.mark.slow
@pytest.mark.parametrize("question,_hint", demo.READ_ONLY_EXAMPLES)
def test_read_only_examples_return_an_answer(settings, question, _hint):
    """A lookup example must succeed and say something."""
    result, _ = _run(settings, question)
    assert result.status == "success", (
        f"example {question!r} ended as {result.status}, not success"
    )
    assert result.response and result.response.strip(), "no response text"
    assert "no matching articles" not in result.response.lower(), (
        f"example {question!r} found nothing; it would look broken to a visitor"
    )


@pytest.mark.slow
@pytest.mark.parametrize("question,_hint", demo.ACTION_EXAMPLES)
def test_action_examples_stop_for_approval(settings, question, _hint):
    """An action example must demonstrate the confirmation gate, not run."""
    result, events = _run(settings, question)
    assert result.status == "awaiting_confirmation", (
        f"example {question!r} ended as {result.status}; the demo promises a "
        "confirmation prompt"
    )
    assert result.awaiting_confirmation is not None
    executed = [
        e.tool for e in events if e.event_type == "tool_call" and e.status == "success"
    ]
    assert "update_record" not in executed
    assert "send_email" not in executed


@pytest.mark.slow
@pytest.mark.parametrize("question,_hint", demo.SECURITY_EXAMPLES)
def test_security_examples_are_refused(settings, question, _hint):
    """A security example must actually be refused, not merely answered oddly."""
    result, events = _run(settings, question)
    assert result.status == "blocked", (
        f"security example {question!r} ended as {result.status}; the demo "
        "promises a refusal"
    )
    executed = [
        e.tool for e in events if e.event_type == "tool_call" and e.status == "success"
    ]
    for destructive in ("delete_record", "update_record", "send_email"):
        assert destructive not in executed


@pytest.mark.slow
@pytest.mark.parametrize("scenario", demo.SCENARIOS, ids=lambda s: s["title"][:18])
def test_each_scenario_produces_the_outcome_it_advertises(settings, scenario):
    """A scenario that describes the wrong outcome teaches the wrong lesson."""
    result, _ = _run(settings, scenario["ask"])
    assert result.status == scenario["outcome"], (
        f"scenario {scenario['title']!r} advertises {scenario['outcome']!r} "
        f"but produced {result.status!r}"
    )


# ------------------------------------------- the narrative matches the data


def test_every_referenced_id_exists_in_the_dataset():
    """No example may name a record that is not there."""
    blob = " ".join(
        [q for q, _ in demo.all_examples()]
        + [s["ask"] for s in demo.SCENARIOS]
        + [s["expect"] for s in demo.SCENARIOS]
        + [s["watch"] for s in demo.SCENARIOS]
    )
    import re

    for order_id in set(re.findall(r"ORD-\d+", blob)):
        assert order_id in data.ORDERS, f"{order_id} is referenced but does not exist"
    for customer_id in set(re.findall(r"CUS-\d+", blob)):
        assert customer_id in data.CUSTOMERS, f"{customer_id} does not exist"
    for ticket_id in set(re.findall(r"TKT-\d+", blob)):
        assert ticket_id in data.TICKETS, f"{ticket_id} does not exist"


def test_company_totals_match_the_dataset():
    """The overview board is derived, not asserted independently."""
    totals = demo.company_totals()
    assert totals["Customers"] == len(data.CUSTOMERS)
    assert totals["Orders"] == len(data.ORDERS)
    assert totals["Products"] == len(data.PRODUCTS)
    assert totals["Open tickets"] == sum(
        1 for t in data.TICKETS.values() if t["status"] == "open"
    )


def test_operations_tables_reference_real_records():
    for row in demo.open_tickets():
        assert row["Ticket"] in data.TICKETS
    for row in demo.orders_in_transit():
        assert row["Order"] in data.ORDERS
    for row in demo.returned_shipments():
        assert row["Order"] in data.ORDERS


def test_explorer_tables_cover_the_whole_dataset():
    assert len(demo.customers_table()) == len(data.CUSTOMERS)
    assert len(demo.products_table()) == len(data.PRODUCTS)
    assert len(demo.tickets_table()) == len(data.TICKETS)


def test_agent_roles_describe_only_agents_that_exist():
    """The demo must not invent an agent the architecture does not have."""
    from agent_platform.models import AgentName

    real = {a.value for a in AgentName}
    described = {role["name"] for role in demo.AGENT_ROLES}
    assert described == real, (
        f"described agents {described} do not match the real ones {real}"
    )


def test_event_labels_do_not_expose_internals():
    """Plain-English labels must not leak prompt or reasoning vocabulary."""
    forbidden = ("prompt", "chain", "thought", "scratchpad", "system message")
    for label in demo.EVENT_LABELS.values():
        lowered = label.lower()
        for word in forbidden:
            if word == "prompt" and "credentials stripped" in lowered:
                continue  # egress label names the control, not the content
            assert word not in lowered, f"label {label!r} leaks internal vocabulary"
