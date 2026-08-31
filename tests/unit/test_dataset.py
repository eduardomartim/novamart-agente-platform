"""Dataset integrity, determinism and privacy properties."""

from __future__ import annotations

import re

from agent_platform.tools import dataset as d

#: Pinned so an accidental data edit fails loudly instead of quietly changing
#: evaluation results. Update deliberately when the dataset is meant to change.
EXPECTED_DIGEST = "db512de8207f751e"


def test_dataset_is_deterministic():
    assert d.dataset_digest() == d.dataset_digest()
    assert d.dataset_digest() == EXPECTED_DIGEST, (
        "the dataset changed; update EXPECTED_DIGEST deliberately if intended"
    )


def test_dataset_has_enough_volume_to_be_interesting():
    summary = d.summary()
    assert summary["customers"] >= 10
    assert summary["orders"] >= 30
    assert summary["tickets"] >= 15
    assert summary["kb_articles"] >= 10


# ----------------------------------------------------------------- integrity


def test_every_order_belongs_to_a_known_customer():
    assert all(o["customer_id"] in d.CUSTOMERS for o in d.ORDERS.values())


def test_every_order_item_references_a_known_product():
    for order in d.ORDERS.values():
        for item in order["items"]:
            assert item["sku"] in d.PRODUCTS


def test_every_shipment_and_ticket_references_a_known_order():
    assert all(s["order_id"] in d.ORDERS for s in d.SHIPMENTS.values())
    assert all(t["order_id"] in d.ORDERS for t in d.TICKETS.values())


def test_ticket_customer_matches_its_order():
    for ticket in d.TICKETS.values():
        assert ticket["customer_id"] == d.ORDERS[ticket["order_id"]]["customer_id"]


def test_unfulfilled_orders_have_neither_tracking_nor_shipment():
    """Consistency the tools rely on when explaining a missing tracking code."""
    for order in d.ORDERS.values():
        if order["status"] in {"processing", "cancelled"}:
            assert order["tracking"] is None
            assert d.shipment_for_order(order["order_id"]) is None


def test_order_totals_match_their_line_items():
    for order in d.ORDERS.values():
        expected = round(sum(i["line_total_brl"] for i in order["items"]), 2)
        assert order["total_brl"] == expected


def test_delivered_shipments_have_a_delivery_date():
    for shipment in d.SHIPMENTS.values():
        if shipment["state"] == "delivered":
            assert shipment["delivered_on"] is not None
        else:
            assert shipment["delivered_on"] is None


def test_several_customers_have_multiple_orders():
    """Multi-hop lookups are only demonstrable if the data supports them."""
    multi = [c for c in d.CUSTOMERS if len(d.orders_for_customer(c)) > 1]
    assert len(multi) >= 8


def test_orders_for_customer_is_sorted_and_filtered():
    orders = d.orders_for_customer("CUS-2001")
    assert {o["customer_id"] for o in orders} == {"CUS-2001"}
    assert [o["placed_on"] for o in orders] == sorted(o["placed_on"] for o in orders)


# -------------------------------------------------------------------- privacy


def test_no_customer_record_carries_unnecessary_identifiers():
    """The dataset stores only what a tool actually consumes."""
    allowed = {"customer_id", "name", "email", "tier", "since", "city", "state"}
    for customer in d.CUSTOMERS.values():
        assert set(customer) == allowed


def test_all_emails_use_the_reserved_domain():
    assert all(c["email"].endswith("@example.com") for c in d.CUSTOMERS.values())


def test_dataset_contains_no_national_id_or_phone_shaped_values():
    """No CPF- or phone-shaped strings anywhere, even incidentally."""
    import json

    blob = json.dumps(
        {"c": d.CUSTOMERS, "o": d.ORDERS, "t": d.TICKETS, "s": d.SHIPMENTS, "k": d.KB_ARTICLES}
    )
    assert not re.search(r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b", blob), "CPF-shaped value present"
    assert not re.search(r"\(\d{2}\)\s?9?\d{4}[-\s]?\d{4}", blob), "phone-shaped value present"


def test_dataset_contains_no_credential_shaped_values():
    import json

    from agent_platform.security.secrets import find_secrets

    blob = json.dumps({"c": d.CUSTOMERS, "o": d.ORDERS, "k": d.KB_ARTICLES})
    assert find_secrets(blob) == []
