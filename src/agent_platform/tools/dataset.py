"""The synthetic back-office dataset.

Entirely fictional and entirely in memory. Separated from ``fake_tools`` so the
data can be reviewed, hashed and tested independently of the tool logic that
reads it.

**Determinism.** No random number generator, no clock, no environment lookup.
Every value is either a literal or derived from one by a pure function, so the
dataset is byte-identical on every import. ``dataset_digest()`` is asserted in
the test suite, which turns an accidental edit into a failing test rather than
a silently different evaluation baseline.

**No real personal data.** Names are generic and fictional, all addresses use
the RFC 2606 reserved ``example.com`` domain, and the records deliberately
carry **no phone numbers, no national ID numbers and no street addresses**. Not
because they would be hard to fake, but because no tool in the registry needs
them -- and a demo dataset is a bad place to practise storing identifiers you
have no use for. The PII detector is exercised against synthetic values in the
test suite, which is where such values belong.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final

# --------------------------------------------------------------------- people

#: (customer_id, name, tier, since, city, state)
_CUSTOMER_ROWS: Final[tuple[tuple[str, str, str, str, str, str], ...]] = (
    ("CUS-2001", "Ana Ribeiro", "gold", "2023-04-11", "Sao Paulo", "SP"),
    ("CUS-2002", "Bruno Carvalho", "standard", "2025-01-30", "Campinas", "SP"),
    ("CUS-2003", "Carla Mendes", "gold", "2022-09-02", "Rio de Janeiro", "RJ"),
    ("CUS-2004", "Diego Fontes", "standard", "2024-06-18", "Belo Horizonte", "MG"),
    ("CUS-2005", "Elisa Prado", "platinum", "2021-03-27", "Curitiba", "PR"),
    ("CUS-2006", "Felipe Nunes", "standard", "2025-07-05", "Porto Alegre", "RS"),
    ("CUS-2007", "Gabriela Souto", "gold", "2023-11-14", "Recife", "PE"),
    ("CUS-2008", "Henrique Vilela", "standard", "2024-02-09", "Salvador", "BA"),
    ("CUS-2009", "Isabela Rocha", "platinum", "2020-08-21", "Florianopolis", "SC"),
    ("CUS-2010", "Joao Peixoto", "standard", "2025-05-16", "Goiania", "GO"),
    ("CUS-2011", "Larissa Amado", "gold", "2022-12-01", "Fortaleza", "CE"),
    ("CUS-2012", "Marcelo Tavares", "standard", "2026-01-08", "Brasilia", "DF"),
)


def _email_for(name: str) -> str:
    """Derive a reserved-domain address from a fictional name."""
    local = name.lower().replace(" ", ".")
    return f"{local}@example.com"


CUSTOMERS: Final[dict[str, dict[str, Any]]] = {
    row[0]: {
        "customer_id": row[0],
        "name": row[1],
        "email": _email_for(row[1]),
        "tier": row[2],
        "since": row[3],
        "city": row[4],
        "state": row[5],
    }
    for row in _CUSTOMER_ROWS
}

# ------------------------------------------------------------------- catalogue

#: (sku, name, category, unit_price_brl, warranty_months)
_PRODUCT_ROWS: Final[tuple[tuple[str, str, str, float, int], ...]] = (
    ("SKU-100", "Mechanical keyboard", "peripherals", 349.90, 12),
    ("SKU-101", "USB-C cable 2m", "accessories", 39.90, 3),
    ("SKU-102", "Laptop stand", "accessories", 89.00, 3),
    ("SKU-103", "27-inch monitor", "displays", 1299.00, 12),
    ("SKU-104", "Wireless mouse", "peripherals", 159.90, 12),
    ("SKU-105", "Noise-cancelling headset", "audio", 799.00, 12),
    ("SKU-106", "Webcam 1080p", "peripherals", 279.00, 12),
    ("SKU-107", "Docking station", "accessories", 649.00, 12),
    ("SKU-108", "Ergonomic chair", "furniture", 1890.00, 24),
    ("SKU-109", "Standing desk", "furniture", 2450.00, 24),
    ("SKU-110", "Monitor arm", "accessories", 329.00, 12),
    ("SKU-111", "External SSD 1TB", "storage", 549.00, 12),
    ("SKU-112", "USB hub 7-port", "accessories", 129.90, 3),
    ("SKU-113", "Desk lamp", "furniture", 199.00, 12),
    ("SKU-114", "Laptop sleeve 14-inch", "accessories", 119.00, 3),
)

PRODUCTS: Final[dict[str, dict[str, Any]]] = {
    row[0]: {
        "sku": row[0],
        "name": row[1],
        "category": row[2],
        "unit_price_brl": row[3],
        "warranty_months": row[4],
    }
    for row in _PRODUCT_ROWS
}

# ---------------------------------------------------------------------- orders

#: (order_id, customer_id, status, placed_on, ((sku, qty), ...))
#: Statuses: processing, shipped, delivered, cancelled, returned.
_ORDER_ROWS: Final[tuple[tuple[str, str, str, str, tuple[tuple[str, int], ...]], ...]] = (
    ("ORD-1001", "CUS-2001", "shipped", "2026-08-14", (("SKU-100", 1), ("SKU-101", 2))),
    ("ORD-1002", "CUS-2002", "processing", "2026-08-20", (("SKU-102", 1),)),
    ("ORD-1003", "CUS-2001", "cancelled", "2026-07-30", (("SKU-103", 1),)),
    ("ORD-1004", "CUS-2003", "delivered", "2026-06-11", (("SKU-105", 1), ("SKU-104", 1))),
    ("ORD-1005", "CUS-2004", "delivered", "2026-05-02", (("SKU-106", 1),)),
    ("ORD-1006", "CUS-2005", "shipped", "2026-08-18", (("SKU-109", 1), ("SKU-108", 1))),
    ("ORD-1007", "CUS-2003", "returned", "2026-04-22", (("SKU-107", 1),)),
    ("ORD-1008", "CUS-2006", "processing", "2026-08-21", (("SKU-111", 2),)),
    ("ORD-1009", "CUS-2007", "delivered", "2026-03-15", (("SKU-100", 1),)),
    ("ORD-1010", "CUS-2005", "delivered", "2026-02-08", (("SKU-113", 3),)),
    ("ORD-1011", "CUS-2008", "shipped", "2026-08-19", (("SKU-112", 1), ("SKU-101", 1))),
    ("ORD-1012", "CUS-2009", "delivered", "2026-01-27", (("SKU-103", 2),)),
    ("ORD-1013", "CUS-2001", "delivered", "2026-05-30", (("SKU-110", 1),)),
    ("ORD-1014", "CUS-2010", "processing", "2026-08-22", (("SKU-114", 1),)),
    ("ORD-1015", "CUS-2011", "shipped", "2026-08-16", (("SKU-105", 1),)),
    ("ORD-1016", "CUS-2003", "delivered", "2026-07-04", (("SKU-102", 2), ("SKU-113", 1))),
    ("ORD-1017", "CUS-2012", "cancelled", "2026-08-10", (("SKU-108", 1),)),
    ("ORD-1018", "CUS-2005", "delivered", "2026-06-19", (("SKU-107", 1), ("SKU-112", 1))),
    ("ORD-1019", "CUS-2002", "returned", "2026-05-25", (("SKU-104", 1),)),
    ("ORD-1020", "CUS-2009", "shipped", "2026-08-17", (("SKU-109", 1),)),
    ("ORD-1021", "CUS-2007", "delivered", "2026-04-09", (("SKU-111", 1),)),
    ("ORD-1022", "CUS-2004", "processing", "2026-08-23", (("SKU-100", 2),)),
    ("ORD-1023", "CUS-2011", "delivered", "2026-03-28", (("SKU-106", 1), ("SKU-101", 3))),
    ("ORD-1024", "CUS-2006", "cancelled", "2026-07-12", (("SKU-103", 1),)),
    ("ORD-1025", "CUS-2001", "delivered", "2026-02-14", (("SKU-105", 1),)),
    ("ORD-1026", "CUS-2008", "delivered", "2026-06-30", (("SKU-114", 2),)),
    ("ORD-1027", "CUS-2010", "shipped", "2026-08-15", (("SKU-110", 1), ("SKU-104", 1))),
    ("ORD-1028", "CUS-2012", "processing", "2026-08-24", (("SKU-112", 1),)),
    ("ORD-1029", "CUS-2009", "delivered", "2026-05-07", (("SKU-108", 1),)),
    ("ORD-1030", "CUS-2003", "shipped", "2026-08-20", (("SKU-100", 1),)),
    ("ORD-1031", "CUS-2005", "returned", "2026-03-03", (("SKU-106", 1),)),
    ("ORD-1032", "CUS-2007", "delivered", "2026-07-21", (("SKU-113", 2),)),
    ("ORD-1033", "CUS-2002", "delivered", "2026-06-05", (("SKU-111", 1), ("SKU-114", 1))),
    ("ORD-1034", "CUS-2011", "processing", "2026-08-25", (("SKU-109", 1),)),
    ("ORD-1035", "CUS-2004", "delivered", "2026-04-16", (("SKU-102", 1),)),
    ("ORD-1036", "CUS-2006", "delivered", "2026-02-26", (("SKU-101", 4),)),
    ("ORD-1037", "CUS-2008", "cancelled", "2026-08-02", (("SKU-105", 1),)),
    ("ORD-1038", "CUS-2010", "delivered", "2026-05-19", (("SKU-107", 1),)),
    ("ORD-1039", "CUS-2012", "shipped", "2026-08-21", (("SKU-104", 2),)),
    ("ORD-1040", "CUS-2009", "delivered", "2026-07-08", (("SKU-110", 1), ("SKU-112", 2))),
)

#: Statuses that imply the order was never fulfilled and carries no shipment.
_UNFULFILLED: Final[frozenset[str]] = frozenset({"processing", "cancelled"})


def _build_order(row: tuple[str, str, str, str, tuple[tuple[str, int], ...]]) -> dict[str, Any]:
    order_id, customer_id, status, placed_on, item_rows = row
    items = [
        {
            "sku": sku,
            "name": PRODUCTS[sku]["name"],
            "quantity": qty,
            "unit_price_brl": PRODUCTS[sku]["unit_price_brl"],
            "line_total_brl": round(PRODUCTS[sku]["unit_price_brl"] * qty, 2),
        }
        for sku, qty in item_rows
    ]
    return {
        "order_id": order_id,
        "customer_id": customer_id,
        "status": status,
        "placed_on": placed_on,
        "items": items,
        "item_count": sum(item["quantity"] for item in items),
        "total_brl": round(sum(item["line_total_brl"] for item in items), 2),
        # Tracking exists only where a shipment does; deriving it here keeps the
        # two consistent by construction rather than by careful data entry.
        "tracking": None if status in _UNFULFILLED else f"BR{order_id[-4:]}0000{order_id[-1]}",
    }


ORDERS: Final[dict[str, dict[str, Any]]] = {
    row[0]: _build_order(row) for row in _ORDER_ROWS
}

# ------------------------------------------------------------------- shipments

#: (shipment_id, order_id, carrier, state, shipped_on, delivered_on|None)
_SHIPMENT_ROWS: Final[tuple[tuple[str, str, str, str, str, str | None], ...]] = (
    ("SHP-3001", "ORD-1001", "Correios", "in_transit", "2026-08-15", None),
    ("SHP-3002", "ORD-1004", "Correios", "delivered", "2026-06-12", "2026-06-17"),
    ("SHP-3003", "ORD-1005", "Jadlog", "delivered", "2026-05-03", "2026-05-09"),
    ("SHP-3004", "ORD-1006", "Correios", "in_transit", "2026-08-19", None),
    ("SHP-3005", "ORD-1007", "Jadlog", "returned_to_sender", "2026-04-23", None),
    ("SHP-3006", "ORD-1009", "Correios", "delivered", "2026-03-16", "2026-03-21"),
    ("SHP-3007", "ORD-1010", "Loggi", "delivered", "2026-02-09", "2026-02-12"),
    ("SHP-3008", "ORD-1011", "Correios", "in_transit", "2026-08-20", None),
    ("SHP-3009", "ORD-1012", "Jadlog", "delivered", "2026-01-28", "2026-02-03"),
    ("SHP-3010", "ORD-1013", "Correios", "delivered", "2026-05-31", "2026-06-04"),
    ("SHP-3011", "ORD-1015", "Loggi", "in_transit", "2026-08-17", None),
    ("SHP-3012", "ORD-1016", "Correios", "delivered", "2026-07-05", "2026-07-11"),
    ("SHP-3013", "ORD-1018", "Jadlog", "delivered", "2026-06-20", "2026-06-25"),
    ("SHP-3014", "ORD-1019", "Correios", "returned_to_sender", "2026-05-26", None),
    ("SHP-3015", "ORD-1020", "Correios", "in_transit", "2026-08-18", None),
    ("SHP-3016", "ORD-1021", "Loggi", "delivered", "2026-04-10", "2026-04-14"),
    ("SHP-3017", "ORD-1023", "Correios", "delivered", "2026-03-29", "2026-04-03"),
    ("SHP-3018", "ORD-1025", "Jadlog", "delivered", "2026-02-15", "2026-02-20"),
    ("SHP-3019", "ORD-1026", "Correios", "delivered", "2026-07-01", "2026-07-06"),
    ("SHP-3020", "ORD-1027", "Loggi", "in_transit", "2026-08-16", None),
    ("SHP-3021", "ORD-1029", "Correios", "delivered", "2026-05-08", "2026-05-13"),
    ("SHP-3022", "ORD-1030", "Correios", "in_transit", "2026-08-21", None),
    ("SHP-3023", "ORD-1031", "Jadlog", "returned_to_sender", "2026-03-04", None),
    ("SHP-3024", "ORD-1032", "Correios", "delivered", "2026-07-22", "2026-07-27"),
    ("SHP-3025", "ORD-1033", "Loggi", "delivered", "2026-06-06", "2026-06-10"),
    ("SHP-3026", "ORD-1035", "Correios", "delivered", "2026-04-17", "2026-04-22"),
    ("SHP-3027", "ORD-1038", "Jadlog", "delivered", "2026-05-20", "2026-05-26"),
    ("SHP-3028", "ORD-1039", "Correios", "in_transit", "2026-08-22", None),
)

SHIPMENTS: Final[dict[str, dict[str, Any]]] = {
    row[0]: {
        "shipment_id": row[0],
        "order_id": row[1],
        "carrier": row[2],
        "state": row[3],
        "shipped_on": row[4],
        "delivered_on": row[5],
    }
    for row in _SHIPMENT_ROWS
}

# --------------------------------------------------------------------- tickets

#: (ticket_id, order_id, subject, status, priority, opened_on, resolution|None)
_TICKET_ROWS: Final[tuple[tuple[str, str, str, str, str, str, str | None], ...]] = (
    ("TKT-4001", "ORD-1001", "Where is my order?", "open", "normal", "2026-08-19", None),
    ("TKT-4002", "ORD-1003", "Refund not received", "open", "high", "2026-08-05", None),
    ("TKT-4003", "ORD-1007", "Item arrived damaged", "resolved", "high", "2026-04-25",
     "Replacement shipped and refund issued."),
    ("TKT-4004", "ORD-1005", "Warranty question", "resolved", "low", "2026-05-12",
     "Explained the 12-month manufacturer warranty."),
    ("TKT-4005", "ORD-1019", "Wrong item received", "resolved", "high", "2026-05-28",
     "Return authorised and correct item dispatched."),
    ("TKT-4006", "ORD-1002", "Change delivery address", "open", "normal", "2026-08-21", None),
    ("TKT-4007", "ORD-1012", "Invoice request", "resolved", "low", "2026-02-05",
     "Invoice re-sent to the registered address."),
    ("TKT-4008", "ORD-1017", "Why was my order cancelled?", "open", "normal", "2026-08-11", None),
    ("TKT-4009", "ORD-1024", "Cancellation refund timing", "resolved", "normal", "2026-07-14",
     "Refund processed within five business days."),
    ("TKT-4010", "ORD-1031", "Return pickup not collected", "escalated", "high", "2026-03-06",
     None),
    ("TKT-4011", "ORD-1006", "Delivery delay", "open", "normal", "2026-08-23", None),
    ("TKT-4012", "ORD-1010", "Bulk order discount", "resolved", "low", "2026-02-11",
     "Applied the standing gold-tier discount."),
    ("TKT-4013", "ORD-1037", "Cancelled before shipping", "resolved", "normal", "2026-08-04",
     "Order cancelled at customer request; no charge applied."),
    ("TKT-4014", "ORD-1015", "Tracking code not updating", "open", "normal", "2026-08-20", None),
    ("TKT-4015", "ORD-1029", "Assembly instructions missing", "resolved", "low", "2026-05-15",
     "Digital manual sent."),
    ("TKT-4016", "ORD-1040", "Missing item in package", "escalated", "high", "2026-07-12", None),
    ("TKT-4017", "ORD-1022", "Expedite my order", "open", "low", "2026-08-24", None),
    ("TKT-4018", "ORD-1013", "Compatibility question", "resolved", "low", "2026-06-08",
     "Confirmed compatibility with the 27-inch monitor."),
)

TICKETS: Final[dict[str, dict[str, Any]]] = {
    row[0]: {
        "ticket_id": row[0],
        "order_id": row[1],
        "customer_id": ORDERS[row[1]]["customer_id"],
        "subject": row[2],
        "status": row[3],
        "priority": row[4],
        "opened_on": row[5],
        "resolution": row[6],
    }
    for row in _TICKET_ROWS
}

# ---------------------------------------------------------------- knowledge base

KB_ARTICLES: Final[tuple[dict[str, str], ...]] = (
    {
        "title": "Refund policy",
        "body": "Orders may be refunded within 30 days of delivery. Cancelled orders are "
        "refunded automatically within 5 business days. Refunds return to the original "
        "payment method.",
    },
    {
        "title": "Shipping times",
        "body": "Standard shipping takes 5 to 8 business days. Express shipping takes 2 "
        "business days and is free for gold and platinum tier customers.",
    },
    {
        "title": "Warranty",
        "body": "Hardware carries a 12-month manufacturer warranty. Furniture carries 24 "
        "months. Accessories carry a 3-month warranty.",
    },
    {
        "title": "Returns process",
        "body": "Request a return from the order page. A carrier collects the item within 3 "
        "business days. Refunds are issued once the item is received and inspected.",
    },
    {
        "title": "Cancellation policy",
        "body": "Orders can be cancelled free of charge while their status is processing. "
        "Once an order has shipped it must be returned instead of cancelled.",
    },
    {
        "title": "Tracking your order",
        "body": "A tracking code is issued when an order ships. Codes can take up to 24 hours "
        "to appear on the carrier site. Orders in processing have no tracking code yet.",
    },
    {
        "title": "Customer tiers",
        "body": "Standard, gold and platinum tiers are assigned by annual spend. Gold and "
        "platinum receive free express shipping and priority support.",
    },
    {
        "title": "Damaged items",
        "body": "Report damage within 7 days of delivery. Damaged items are replaced at no "
        "cost and the damaged unit is collected by the carrier.",
    },
    {
        "title": "Invoices",
        "body": "Invoices are emailed when an order ships and can be re-sent from the order "
        "page at any time.",
    },
    {
        "title": "Payment methods",
        "body": "We accept credit card, debit card and instant bank transfer. Card details "
        "are handled by the payment processor and are never stored by support.",
    },
    {
        "title": "Address changes",
        "body": "Delivery addresses can be changed while an order is processing. Once "
        "shipped, the address is fixed and the parcel must be redirected with the carrier.",
    },
    {
        "title": "Support hours",
        "body": "Support operates 09:00 to 18:00 on business days. Escalated tickets are "
        "reviewed within one business day.",
    },
)


# ----------------------------------------------------------------- integrity

def orders_for_customer(customer_id: str) -> list[dict[str, Any]]:
    """Every order belonging to *customer_id*, oldest first."""
    return sorted(
        (order for order in ORDERS.values() if order["customer_id"] == customer_id),
        key=lambda order: order["placed_on"],
    )


def shipment_for_order(order_id: str) -> dict[str, Any] | None:
    for shipment in SHIPMENTS.values():
        if shipment["order_id"] == order_id:
            return shipment
    return None


def dataset_digest() -> str:
    """Stable digest of the whole dataset.

    Asserted by the test suite so an accidental data edit surfaces immediately
    rather than quietly changing evaluation results.
    """
    payload = json.dumps(
        {
            "customers": CUSTOMERS,
            "products": PRODUCTS,
            "orders": ORDERS,
            "shipments": SHIPMENTS,
            "tickets": TICKETS,
            "kb": KB_ARTICLES,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def summary() -> dict[str, int]:
    return {
        "customers": len(CUSTOMERS),
        "products": len(PRODUCTS),
        "orders": len(ORDERS),
        "order_items": sum(len(o["items"]) for o in ORDERS.values()),
        "shipments": len(SHIPMENTS),
        "tickets": len(TICKETS),
        "kb_articles": len(KB_ARTICLES),
    }
