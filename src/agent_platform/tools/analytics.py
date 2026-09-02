"""Aggregate reads over the demo dataset.

Why this module exists, and why it is separate from :mod:`fake_tools`:

Every tool in `fake_tools` answers a question about **one** record, reached by
identifier or by name. That covers "what is the status of ORD-1001?" and
nothing else. A person looking at a back-office dashboard asks a different
kind of question first -- "how many customers do we have?", "which orders are
still open?", "is there anything I should know about?" -- and the platform had
no tool that could answer any of them. Asked anyway, the deterministic
provider fell back to a lookup with a default identifier and reported success,
so an aggregate question came back as a confident sentence about one arbitrary
customer. Correct data, wrong question, and no sign that anything went wrong.

These tools close that gap by reading the same dataset the record tools read.
Nothing here computes anything the data does not already contain:

* counts and sums come from the rows themselves;
* "top" customers are ranked by their own orders;
* "open" means the recorded status, not an inferred one.

Where the dataset genuinely does not hold the answer, the tool is absent
rather than approximate. **There is deliberately no stock tool**: the product
catalogue records sku, name, category, unit price and warranty, and no
inventory level at all. A `low_stock_products` that invented quantities would
be the exact failure this module was written to remove, so the question is
refused instead -- see `StubProvider._unanswerable`.

Each tool carries a ``summary``: one sentence, written for a person, that the
orchestrator renders instead of the raw payload. The structured fields stay in
the output for the trace and for any caller that wants them.

Risk, capability and permissions live in the definitions at the bottom, read
by the policy engine exactly as it reads the record tools. Nothing here is a
side channel: these run through the same gateway, the same capability matrix
and the same audit trail.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..i18n import current_locale, pick
from ..models import AgentName, Capability, RiskLevel
from . import dataset as data
from .execution import require_gateway
from .models import ToolDefinition

#: Order statuses that mean the order has not reached the customer. Taken from
#: the dataset's own vocabulary rather than invented: an order is open because
#: its recorded status says so.
OPEN_ORDER_STATUSES: Final[frozenset[str]] = frozenset({"processing", "shipped"})

#: Ticket statuses that still need someone. `resolved` is the only terminal
#: state the dataset uses.
UNRESOLVED_TICKET_STATUSES: Final[frozenset[str]] = frozenset({"open", "escalated"})

#: Vocabulary, per locale. The dataset stores one English key per value and
#: keeps storing it: only the words a reader sees are translated, so every
#: number, filter and comparison below is computed from the same key in both
#: languages and cannot drift between them.
_LABELS: Final[dict[str, dict[str, dict[str, str]]]] = {
    "tier": {
        "pt": {"platinum": "Platinum", "gold": "Ouro", "standard": "Standard"},
        "en": {"platinum": "Platinum", "gold": "Gold", "standard": "Standard"},
    },
    # So a Portuguese sentence does not report "2 escalated e 7 open".
    "ticket_status": {
        "pt": {"open": "abertos", "escalated": "escalados"},
        "en": {"open": "open", "escalated": "escalated"},
    },
    "category": {
        "pt": {
            "peripherals": "periféricos", "accessories": "acessórios",
            "displays": "monitores", "audio": "áudio",
            "furniture": "mobiliário", "storage": "armazenamento",
        },
        "en": {
            "peripherals": "peripherals", "accessories": "accessories",
            "displays": "displays", "audio": "audio",
            "furniture": "furniture", "storage": "storage",
        },
    },
    "status": {
        "pt": {
            "processing": "em processamento", "shipped": "enviado",
            "delivered": "entregue", "cancelled": "cancelado",
            "returned": "devolvido",
        },
        "en": {
            "processing": "processing", "shipped": "shipped",
            "delivered": "delivered", "cancelled": "cancelled",
            "returned": "returned",
        },
    },
    "priority": {
        "pt": {"high": "alta", "normal": "normal", "low": "baixa"},
        "en": {"high": "high", "normal": "normal", "low": "low"},
    },
}


def _label(table: str, key: str) -> str:
    """One label in the locale in force, falling back to the stored key.

    A missing translation shows the dataset's own word rather than raising:
    these sit on the path of every answer, and a sentence with one English
    word in it beats a request that fails.
    """
    return _LABELS[table][current_locale()].get(key, key)


def _brl(value: float) -> str:
    """Format a number the way a Brazilian reader expects to see money.

    The currency does not change with the locale, and neither does the
    grouping. NovaMart's orders are in reais whoever is reading, so an English
    reader sees ``R$ 33.002,50`` too -- converting it would invent an exchange
    rate, and re-grouping it would make the same figure look like two.
    """
    whole = f"{value:,.2f}"
    return "R$ " + whole.replace(",", "@").replace(".", ",").replace("@", ".")


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


def _n(count: int, pt_one: str, pt_many: str, en_one: str, en_many: str) -> str:
    """A counted noun, agreeing in number, in the locale in force."""
    return pick(_plural(count, pt_one, pt_many), _plural(count, en_one, en_many))


def _customer_of(ticket: dict[str, Any]) -> str:
    """The person a ticket belongs to, by name rather than by identifier."""
    customer = data.CUSTOMERS.get(ticket.get("customer_id", ""))
    return customer["name"] if customer else str(ticket.get("customer_id", "—"))


# ------------------------------------------------------------------ arguments


class NoArgs(BaseModel):
    """A tool that takes nothing still declares a schema, so PL004 can check it."""

    model_config = ConfigDict(extra="forbid")


class ListOrdersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Constrained to the statuses the dataset actually uses. An unknown status
    #: is a validation failure (PL004), not an empty result that reads like
    #: "there are none".
    status: (
        Literal["processing", "shipped", "delivered", "cancelled", "returned", "open"]
        | None
    ) = Field(
        default=None,
        description="Filter by recorded order status. 'open' means not yet delivered.",
    )
    limit: int = Field(default=10, ge=1, le=40)


class TopCustomersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=5, ge=1, le=12)
    by: Literal["revenue", "orders"] = Field(
        default="revenue",
        description="Rank by total value of orders placed, or by order count.",
    )


class ListProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Constrained to the categories the catalogue actually uses, for the same
    #: reason `ListOrdersArgs.status` is: an unknown value is a validation
    #: failure, not an empty list that reads like "we sell none of those".
    category: (
        Literal["peripherals", "accessories", "displays", "audio", "furniture", "storage"]
        | None
    ) = Field(default=None, description="Filter by product category.")
    limit: int = Field(default=15, ge=1, le=15)


class OpenTicketsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: Literal["high", "normal", "low"] | None = Field(
        default=None, description="Filter by ticket priority."
    )


class TopSellingProductsArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=5, ge=1, le=15)
    by: Literal["units", "revenue"] = Field(
        default="units",
        description="Rank by units sold, or by the revenue those units produced.",
    )


# ---------------------------------------------------------------- the handlers


def count_customers() -> dict[str, Any]:
    """How many customers exist, and how they are distributed across tiers."""
    require_gateway("count_customers")
    by_tier: dict[str, int] = {}
    for customer in data.CUSTOMERS.values():
        by_tier[customer["tier"]] = by_tier.get(customer["tier"], 0) + 1

    total = len(data.CUSTOMERS)
    parts = ", ".join(
        f"{count} {_label('tier', tier)}"
        for tier, count in sorted(by_tier.items(), key=lambda kv: -kv[1])
    )
    return {
        "found": True,
        "total": total,
        "by_tier": by_tier,
        "summary": pick(
            f"Temos {total} clientes cadastrados: {parts}.",
            f"We have {total} registered customers: {parts}.",
        ),
    }


def count_products() -> dict[str, Any]:
    """How many products the catalogue holds, and in which categories."""
    require_gateway("count_products")
    by_category: dict[str, int] = {}
    for product in data.PRODUCTS.values():
        by_category[product["category"]] = by_category.get(product["category"], 0) + 1

    total = len(data.PRODUCTS)
    parts = ", ".join(
        f"{count} {pick('em', 'in')} {_label('category', category)}"
        for category, count in sorted(by_category.items(), key=lambda kv: -kv[1])
    )
    return {
        "found": True,
        "total": total,
        "by_category": by_category,
        "summary": pick(
            f"O catálogo tem {total} produtos: {parts}.",
            f"The catalogue holds {total} products: {parts}.",
        ),
    }


def list_products(category: str | None = None, limit: int = 15) -> dict[str, Any]:
    """The catalogue, optionally narrowed to one category.

    Price is the catalogue price. The dataset records no cost and no stock
    level, so neither margin nor availability is reported here -- see the note
    at the top of this module.
    """
    require_gateway("list_products")
    products = sorted(data.PRODUCTS.values(), key=lambda p: p["name"])
    if category:
        selected = [p for p in products if p["category"] == category]
        described = pick(
            f"na categoria {_label('category', category)}",
            f"in {_label('category', category)}",
        )
    else:
        selected = products
        described = pick("no catálogo", "in the catalogue")

    if not selected:
        summary = pick(f"Nenhum produto {described}.", f"No products {described}.")
    else:
        named = "; ".join(
            f"{p['name']} ({_brl(p['unit_price_brl'])})" for p in selected[:limit]
        )
        rest = len(selected) - limit
        more = "" if len(selected) <= limit else pick(f" e mais {rest}", f" and {rest} more")
        noun = _n(len(selected), "produto", "produtos", "product", "products")
        summary = f"{len(selected)} {noun} {described}: {named}{more}."

    return {
        "found": True,
        "category_filter": category,
        "matched": len(selected),
        "products": [
            {
                "name": p["name"],
                "category": p["category"],
                "unit_price_brl": p["unit_price_brl"],
                "warranty_months": p["warranty_months"],
                "sku": p["sku"],
            }
            for p in selected[:limit]
        ],
        "summary": summary,
    }


def product_price_range() -> dict[str, Any]:
    """The cheapest and most expensive products, and the average between them."""
    require_gateway("product_price_range")
    products = list(data.PRODUCTS.values())
    if not products:
        return {
            "found": True,
            "summary": pick("O catálogo está vazio.", "The catalogue is empty."),
        }

    cheapest = min(products, key=lambda p: p["unit_price_brl"])
    dearest = max(products, key=lambda p: p["unit_price_brl"])
    average = sum(p["unit_price_brl"] for p in products) / len(products)
    return {
        "found": True,
        "cheapest": {"name": cheapest["name"], "unit_price_brl": cheapest["unit_price_brl"]},
        "most_expensive": {"name": dearest["name"], "unit_price_brl": dearest["unit_price_brl"]},
        "average_price_brl": round(average, 2),
        "summary": pick(
            f"O produto mais caro é {dearest['name']}, a "
            f"{_brl(dearest['unit_price_brl'])}; o mais barato é "
            f"{cheapest['name']}, a {_brl(cheapest['unit_price_brl'])}. "
            f"O preço médio do catálogo é {_brl(average)}.",
            f"The most expensive product is {dearest['name']}, at "
            f"{_brl(dearest['unit_price_brl'])}; the cheapest is "
            f"{cheapest['name']}, at {_brl(cheapest['unit_price_brl'])}. "
            f"The catalogue's average price is {_brl(average)}.",
        ),
    }


def top_selling_products(limit: int = 5, by: str = "units") -> dict[str, Any]:
    """What actually sold, counted from the order lines themselves.

    Derived from `ORDERS.items`, which is where quantity lives -- the product
    catalogue records no sales figures of its own, so this is a count of what
    happened rather than a stored total.
    """
    require_gateway("top_selling_products")
    tally: dict[str, dict[str, Any]] = {}
    for order in data.ORDERS.values():
        for line in order["items"]:
            entry = tally.setdefault(line["name"], {"units": 0, "revenue": 0.0})
            entry["units"] += line["quantity"]
            entry["revenue"] += line["line_total_brl"]

    if not tally:
        return {
            "found": True,
            "products": [],
            "summary": pick("Nenhum item vendido ainda.", "Nothing has sold yet."),
        }

    ranked = sorted(tally.items(), key=lambda kv: (kv[1][by], kv[1]["units"]), reverse=True)
    rows = [
        {"name": name, "units": stats["units"], "revenue_brl": round(stats["revenue"], 2)}
        for name, stats in ranked[:limit]
    ]
    lead = rows[0]
    if by == "revenue":
        listed = ", ".join(f"{r['name']} ({_brl(r['revenue_brl'])})" for r in rows)
        summary = pick(
            f"{lead['name']} é o produto que mais gerou receita: "
            f"{_brl(lead['revenue_brl'])} em {lead['units']} unidades. "
            f"Os principais por receita: {listed}.",
            f"{lead['name']} produced the most revenue: "
            f"{_brl(lead['revenue_brl'])} across {lead['units']} units. "
            f"Leaders by revenue: {listed}.",
        )
    else:
        listed = ", ".join(
            f"{r['name']} ({r['units']} {pick('un.', 'units')})" for r in rows
        )
        summary = pick(
            f"{lead['name']} é o produto mais vendido, com {lead['units']} "
            f"unidades. Os principais por volume: {listed}.",
            f"{lead['name']} is the best seller, with {lead['units']} "
            f"units. Leaders by volume: {listed}."
        )
    return {"found": True, "ranked_by": by, "products": rows, "summary": summary}


def revenue_total() -> dict[str, Any]:
    """Total booked value, and how much of it was cancelled or returned."""
    require_gateway("revenue_total")
    orders = list(data.ORDERS.values())
    total = sum(order["total_brl"] for order in orders)
    lost = sum(
        order["total_brl"]
        for order in orders
        if order["status"] in ("cancelled", "returned")
    )
    net = total - lost
    # "What is the average order value?" reaches this tool, and the sentence
    # used to report the total without ever stating the average -- a confident
    # reply to a question it had not answered.
    average = total / len(orders) if orders else 0.0
    return {
        "found": True,
        "order_count": len(orders),
        "total_brl": round(total, 2),
        "cancelled_or_returned_brl": round(lost, 2),
        "net_brl": round(net, 2),
        "average_order_brl": round(average, 2),
        "summary": pick(
            f"Os {len(orders)} pedidos somam {_brl(total)}, uma média de "
            f"{_brl(average)} por pedido. Descontando cancelamentos e "
            f"devoluções ({_brl(lost)}), o valor efetivo é {_brl(net)}.",
            f"The {len(orders)} orders total {_brl(total)}, an average of "
            f"{_brl(average)} per order. Net of cancellations and returns "
            f"({_brl(lost)}), the effective value is {_brl(net)}.",
        ),
    }


def list_orders(status: str | None = None, limit: int = 10) -> dict[str, Any]:
    """Orders filtered by recorded status, most recent first.

    ``status="open"`` is the one label that is not a raw dataset value: it
    stands for "not yet delivered", which is the union of `processing` and
    `shipped`. The summary says so rather than leaving the reader to guess.
    """
    require_gateway("list_orders")
    orders = sorted(
        data.ORDERS.values(), key=lambda order: order["placed_on"], reverse=True
    )
    if status == "open":
        selected = [o for o in orders if o["status"] in OPEN_ORDER_STATUSES]
        described = pick("em aberto (ainda não entregues)", "open (not yet delivered)")
    elif status:
        selected = [o for o in orders if o["status"] == status]
        described = _label('status', status)
    else:
        selected = orders
        described = pick("no total", "in total")

    shown = selected[:limit]
    value = sum(order["total_brl"] for order in selected)

    if not selected:
        summary = pick(
            f"Nenhum pedido {described} no conjunto de dados.",
            f"No orders {described} in the dataset.",
        )
    else:
        # The list is newest first, so the sentence names the newest. Naming
        # the oldest read as an odd answer to "show me recent orders".
        newest = max(order["placed_on"] for order in selected)
        noun = _n(len(selected), "pedido", "pedidos", "order", "orders")
        summary = pick(
            f"{len(selected)} {noun} {described}, somando {_brl(value)}. "
            f"O mais recente é de {newest}.",
            f"{len(selected)} {noun} {described}, totalling {_brl(value)}. "
            f"The most recent is from {newest}.",
        )
        if status == "open":
            # The dataset records no promised delivery date, so "late" cannot be
            # computed. Saying which orders are still open is the honest answer
            # to "which orders are delayed?", and this sentence is what stops it
            # from being read as more than that.
            summary += pick(
                " Esta demonstração não registra prazo de entrega, então não é "
                "possível dizer quais estão atrasados — apenas quais seguem "
                "abertos.",
                " This demo records no delivery deadline, so it cannot say "
                "which orders are late — only which are still open.",
            )

    return {
        "found": True,
        "status_filter": status,
        "matched": len(selected),
        "total_brl": round(value, 2),
        "orders": [
            {
                "order_id": order["order_id"],
                "status": order["status"],
                "placed_on": order["placed_on"],
                "total_brl": order["total_brl"],
            }
            for order in shown
        ],
        "summary": summary,
    }


def top_customers(limit: int = 5, by: str = "revenue") -> dict[str, Any]:
    """Rank customers by what they actually bought."""
    require_gateway("top_customers")
    totals: dict[str, dict[str, Any]] = {}
    for order in data.ORDERS.values():
        entry = totals.setdefault(
            order["customer_id"], {"orders": 0, "revenue": 0.0}
        )
        entry["orders"] += 1
        entry["revenue"] += order["total_brl"]

    ranked = sorted(
        totals.items(),
        key=lambda item: (item[1][by], item[1]["orders"]),
        reverse=True,
    )[:limit]

    rows = []
    for customer_id, stats in ranked:
        customer = data.CUSTOMERS.get(customer_id, {})
        rows.append(
            {
                "name": customer.get("name", customer_id),
                "tier": customer.get("tier"),
                "orders": stats["orders"],
                "revenue_brl": round(stats["revenue"], 2),
            }
        )

    if not rows:
        return {
            "found": True,
            "customers": [],
            "summary": pick(
                "Não há pedidos registrados para ranquear clientes.",
                "There are no recorded orders to rank customers by.",
            ),
        }

    lead = rows[0]
    if by == "orders":
        listed = ", ".join(
            f"{row['name']} ({row['orders']} {pick('pedidos', 'orders')})"
            for row in rows
        )
        summary = pick(
            f"{lead['name']} é quem mais comprou, com {lead['orders']} pedidos. "
            f"Os principais por volume: {listed}.",
            f"{lead['name']} placed the most orders, with {lead['orders']}. "
            f"Leaders by volume: {listed}.",
        )
    else:
        listed = ", ".join(
            f"{row['name']} ({_brl(row['revenue_brl'])})" for row in rows
        )
        summary = pick(
            f"{lead['name']} é a cliente de maior valor, com "
            f"{_brl(lead['revenue_brl'])} em {lead['orders']} pedidos. "
            f"Os principais por valor: {listed}.",
            f"{lead['name']} is the highest-value customer, with "
            f"{_brl(lead['revenue_brl'])} across {lead['orders']} orders. "
            f"Leaders by value: {listed}.",
        )

    return {
        "found": True,
        "ranked_by": by,
        "customers": rows,
        "summary": summary,
    }


def open_tickets(priority: str | None = None) -> dict[str, Any]:
    """Support tickets nobody has closed yet, worst first.

    Each row now carries the customer's name. It used to report only the
    subject, which meant "which customers have tickets?" had no tool that could
    answer it even though the link exists in the data: every ticket carries a
    `customer_id`.
    """
    require_gateway("open_tickets")
    rank = {"high": 0, "normal": 1, "low": 2}
    unresolved = [
        ticket
        for ticket in data.TICKETS.values()
        if ticket["status"] in UNRESOLVED_TICKET_STATUSES
    ]
    if priority:
        unresolved = [t for t in unresolved if t["priority"] == priority]
    unresolved.sort(key=lambda t: (rank.get(t["priority"], 3), t["ticket_id"]))
    high = [t for t in unresolved if t["priority"] == "high"]

    if not unresolved:
        summary = (
            pick(
                f"Não há tickets em aberto de prioridade "
                f"{_label('priority', priority)}.",
                f"There are no open {_label('priority', priority)}-priority "
                f"tickets.",
            )
            if priority
            else pick(
                "Não há tickets em aberto no momento.",
                "There are no open tickets right now.",
            )
        )
    elif priority:
        nomes = sorted({_customer_of(t) for t in unresolved})
        who = _n(len(nomes), "cliente", "clientes", "customer", "customers")
        summary = pick(
            f"{len(unresolved)} "
            f"{_plural(len(unresolved), 'ticket', 'tickets')} de prioridade "
            f"{_label('priority', priority)} em aberto, "
            f"de {len(nomes)} {who}: {', '.join(nomes)}.",
            f"{len(unresolved)} open "
            f"{_label('priority', priority)}-priority "
            f"{_plural(len(unresolved), 'ticket', 'tickets')}, "
            f"from {len(nomes)} {who}: {', '.join(nomes)}.",
        )
    else:
        # Says what was counted. The dashboard's own KPI counts only the
        # `open` status, so "9 tickets em aberto" beside a card reading 7 would
        # look like one of the two is wrong when both are right.
        by_status: dict[str, int] = {}
        for ticket in unresolved:
            by_status[ticket["status"]] = by_status.get(ticket["status"], 0) + 1
        breakdown = pick(" e ", " and ").join(
            f"{count} {_label('ticket_status', status)}"
            for status, count in sorted(by_status.items())
        )
        summary = pick(
            f"Existem {len(unresolved)} tickets não resolvidos ({breakdown})",
            f"There are {len(unresolved)} unresolved tickets ({breakdown})",
        )
        if high:
            subjects = "; ".join(t["subject"] for t in high[:3])
            summary += pick(
                f", {len(high)} de alta prioridade. Os mais urgentes tratam de: "
                f"{subjects}.",
                f", {len(high)} of them high priority. The most urgent are "
                f"about: {subjects}.",
            )
        else:
            summary += pick(
                ", nenhum de alta prioridade.", ", none of them high priority."
            )
        # Who they belong to. The names were already in the payload and only
        # the subjects were spoken, so "which customers have tickets?" got a
        # list of complaints and no customer.
        nomes = sorted({_customer_of(t) for t in unresolved})
        who = _n(len(nomes), "cliente", "clientes", "customer", "customers")
        summary += pick(
            f" Os tickets são de {len(nomes)} {who}: {', '.join(nomes)}.",
            f" The tickets belong to {len(nomes)} {who}: {', '.join(nomes)}.",
        )

    return {
        "found": True,
        "priority_filter": priority,
        "open_count": len(unresolved),
        "high_priority_count": len(high),
        "customers": sorted({_customer_of(t) for t in unresolved}),
        "tickets": [
            {
                "customer": _customer_of(ticket),
                "subject": ticket["subject"],
                "priority": ticket["priority"],
                "status": ticket["status"],
                "opened_on": ticket["opened_on"],
            }
            for ticket in unresolved[:8]
        ],
        "summary": summary,
    }


def business_overview() -> dict[str, Any]:
    """What a manager would want flagged, assembled from the same rows.

    Answers "is there anything I should know about?" without editorialising:
    every number below is counted, and the sentence names what was counted.
    """
    require_gateway("business_overview")
    unresolved = [
        t for t in data.TICKETS.values() if t["status"] in UNRESOLVED_TICKET_STATUSES
    ]
    high = [t for t in unresolved if t["priority"] == "high"]
    open_orders = [
        o for o in data.ORDERS.values() if o["status"] in OPEN_ORDER_STATUSES
    ]
    returned = [o for o in data.ORDERS.values() if o["status"] == "returned"]
    cancelled = [o for o in data.ORDERS.values() if o["status"] == "cancelled"]
    stuck = [s for s in data.SHIPMENTS.values() if s["state"] == "returned_to_sender"]

    points = [
        pick(
            f"{len(high)} {_plural(len(high), 'ticket', 'tickets')} de alta "
            f"prioridade em aberto",
            f"{len(high)} open high-priority "
            f"{_plural(len(high), 'ticket', 'tickets')}",
        ),
        pick(
            f"{len(open_orders)} "
            f"{_plural(len(open_orders), 'pedido', 'pedidos')} "
            f"ainda não entregues",
            f"{len(open_orders)} "
            f"{_plural(len(open_orders), 'order', 'orders')} not yet delivered",
        ),
        pick(
            f"{len(cancelled)} "
            f"{_plural(len(cancelled), 'cancelamento', 'cancelamentos')} "
            f"e {len(returned)} "
            f"{_plural(len(returned), 'devolução', 'devoluções')}",
            f"{len(cancelled)} "
            f"{_plural(len(cancelled), 'cancellation', 'cancellations')} "
            f"and {len(returned)} {_plural(len(returned), 'return', 'returns')}",
        ),
        pick(
            f"{len(stuck)} "
            f"{_plural(len(stuck), 'entrega devolvida', 'entregas devolvidas')} "
            f"ao remetente",
            f"{len(stuck)} "
            f"{_plural(len(stuck), 'shipment', 'shipments')} returned to sender",
        ),
    ]
    return {
        "found": True,
        "high_priority_tickets": len(high),
        "open_orders": len(open_orders),
        "cancelled_orders": len(cancelled),
        "returned_orders": len(returned),
        "shipments_returned_to_sender": len(stuck),
        "summary": (
            pick(
                "Pontos que merecem atenção neste conjunto de dados: ",
                "Points worth attention in this dataset: ",
            )
            + "; ".join(points)
            + "."
        ),
    }


# -------------------------------------------------------------- the definitions
#
# Read-only, LOW risk, READ_DATA capability. The researcher and the validator
# may propose them; the executor may too, because an action often needs a count
# before it is proposed. The router holds nothing, as PL002 requires.

_READERS: Final[frozenset[AgentName]] = frozenset(
    {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
)

ANALYTICS_TOOL_DEFINITIONS: Final[tuple[ToolDefinition, ...]] = (
    ToolDefinition(
        name="count_customers",
        description=(
            "Count the customers on record and break them down by tier. Use for "
            "'how many customers do we have?' and similar questions about the "
            "size of the customer base."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=count_customers,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="revenue_total",
        description=(
            "Total value of all orders, with cancellations and returns "
            "separated out. Use for questions about total or overall revenue."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=revenue_total,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="list_orders",
        description=(
            "List orders filtered by status, most recent first. Use for recent "
            "orders, open orders, cancelled orders, and questions about how "
            "many orders are in a given state."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=ListOrdersArgs,
        handler=list_orders,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="top_customers",
        description=(
            "Rank customers by the value or the number of orders they placed. "
            "Use for 'top customers', 'who buys the most', and similar."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=TopCustomersArgs,
        handler=top_customers,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="open_tickets",
        description=(
            "Support tickets that are not resolved, highest priority first, "
            "each with the customer it belongs to. Optionally filtered by "
            "priority. Use for urgent tickets, high-priority tickets, open "
            "support load, and which customers have tickets."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=OpenTicketsArgs,
        handler=open_tickets,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="count_products",
        description=(
            "Count the products in the catalogue and break them down by "
            "category. Use for 'how many products do we have?'."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=count_products,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="list_products",
        description=(
            "List the catalogue with each product's price, optionally filtered "
            "by category. Use for 'which products do we sell?' and 'how much "
            "does each product cost?'. The dataset records no stock level and "
            "no cost, so neither is reported."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=ListProductsArgs,
        handler=list_products,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="product_price_range",
        description=(
            "The cheapest product, the most expensive one, and the catalogue "
            "average. Use for questions about the priciest or cheapest item."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=product_price_range,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="top_selling_products",
        description=(
            "Rank products by units sold or by the revenue they produced, "
            "counted from the order lines. Use for 'which product sells the "
            "most?' and best sellers."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=TopSellingProductsArgs,
        handler=top_selling_products,
        allowed_agents=_READERS,
    ),
    ToolDefinition(
        name="business_overview",
        description=(
            "A counted summary of what needs attention: high-priority tickets, "
            "undelivered orders, cancellations, returns. Use when asked whether "
            "there is any problem a manager should know about."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=business_overview,
        allowed_agents=_READERS,
    ),
)
