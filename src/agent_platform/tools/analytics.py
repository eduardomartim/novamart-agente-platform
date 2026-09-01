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

#: Tier labels as a reader would say them. The dataset stores the English key.
TIER_LABELS: Final[dict[str, str]] = {
    "platinum": "Platinum",
    "gold": "Ouro",
    "standard": "Standard",
}

#: Ticket-status labels, so a Portuguese sentence does not report "2 escalated
#: e 7 open".
TICKET_STATUS_LABELS: Final[dict[str, str]] = {
    "open": "abertos",
    "escalated": "escalados",
}

#: Order-status labels for the same reason.
STATUS_LABELS: Final[dict[str, str]] = {
    "processing": "em processamento",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
    "returned": "devolvido",
}


def _brl(value: float) -> str:
    """Format a number the way a Brazilian reader expects to see money."""
    whole = f"{value:,.2f}"
    return "R$ " + whole.replace(",", "@").replace(".", ",").replace("@", ".")


def _plural(count: int, singular: str, plural: str) -> str:
    return singular if count == 1 else plural


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


# ---------------------------------------------------------------- the handlers


def count_customers() -> dict[str, Any]:
    """How many customers exist, and how they are distributed across tiers."""
    require_gateway("count_customers")
    by_tier: dict[str, int] = {}
    for customer in data.CUSTOMERS.values():
        by_tier[customer["tier"]] = by_tier.get(customer["tier"], 0) + 1

    total = len(data.CUSTOMERS)
    parts = ", ".join(
        f"{count} {TIER_LABELS.get(tier, tier)}"
        for tier, count in sorted(by_tier.items(), key=lambda kv: -kv[1])
    )
    return {
        "found": True,
        "total": total,
        "by_tier": by_tier,
        "summary": (
            f"Temos {total} clientes cadastrados: {parts}."
        ),
    }


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
    return {
        "found": True,
        "order_count": len(orders),
        "total_brl": round(total, 2),
        "cancelled_or_returned_brl": round(lost, 2),
        "net_brl": round(net, 2),
        "summary": (
            f"Os {len(orders)} pedidos somam {_brl(total)}. Descontando "
            f"cancelamentos e devoluções ({_brl(lost)}), o valor efetivo é "
            f"{_brl(net)}."
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
        described = "em aberto (ainda não entregues)"
    elif status:
        selected = [o for o in orders if o["status"] == status]
        described = STATUS_LABELS.get(status, status)
    else:
        selected = orders
        described = "no total"

    shown = selected[:limit]
    value = sum(order["total_brl"] for order in selected)

    if not selected:
        summary = f"Nenhum pedido {described} no conjunto de dados."
    else:
        # The list is newest first, so the sentence names the newest. Naming
        # the oldest read as an odd answer to "show me recent orders".
        newest = max(order["placed_on"] for order in selected)
        summary = (
            f"{len(selected)} {_plural(len(selected), 'pedido', 'pedidos')} "
            f"{described}, somando {_brl(value)}. O mais recente é de {newest}."
        )
        if status == "open":
            # The dataset records no promised delivery date, so "late" cannot be
            # computed. Saying which orders are still open is the honest answer
            # to "which orders are delayed?", and this sentence is what stops it
            # from being read as more than that.
            summary += (
                " Esta demonstração não registra prazo de entrega, então não é "
                "possível dizer quais estão atrasados — apenas quais seguem "
                "abertos."
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
            "summary": "Não há pedidos registrados para ranquear clientes.",
        }

    lead = rows[0]
    if by == "orders":
        listed = ", ".join(
            f"{row['name']} ({row['orders']} pedidos)" for row in rows
        )
        summary = (
            f"{lead['name']} é quem mais comprou, com {lead['orders']} pedidos. "
            f"Os principais por volume: {listed}."
        )
    else:
        listed = ", ".join(
            f"{row['name']} ({_brl(row['revenue_brl'])})" for row in rows
        )
        summary = (
            f"{lead['name']} é a cliente de maior valor, com "
            f"{_brl(lead['revenue_brl'])} em {lead['orders']} pedidos. "
            f"Os principais por valor: {listed}."
        )

    return {
        "found": True,
        "ranked_by": by,
        "customers": rows,
        "summary": summary,
    }


def open_tickets() -> dict[str, Any]:
    """Support tickets nobody has closed yet, worst first."""
    require_gateway("open_tickets")
    rank = {"high": 0, "normal": 1, "low": 2}
    unresolved = [
        ticket
        for ticket in data.TICKETS.values()
        if ticket["status"] in UNRESOLVED_TICKET_STATUSES
    ]
    unresolved.sort(key=lambda t: (rank.get(t["priority"], 3), t["ticket_id"]))
    high = [t for t in unresolved if t["priority"] == "high"]

    if not unresolved:
        summary = "Não há tickets em aberto no momento."
    else:
        # Says what was counted. The dashboard's own KPI counts only the
        # `open` status, so "9 tickets em aberto" beside a card reading 7 would
        # look like one of the two is wrong when both are right.
        by_status: dict[str, int] = {}
        for ticket in unresolved:
            by_status[ticket["status"]] = by_status.get(ticket["status"], 0) + 1
        breakdown = " e ".join(
            f"{count} {TICKET_STATUS_LABELS.get(status, status)}"
            for status, count in sorted(by_status.items())
        )
        summary = (
            f"Existem {len(unresolved)} tickets não resolvidos ({breakdown})"
        )
        if high:
            subjects = "; ".join(t["subject"] for t in high[:3])
            summary += (
                f", {len(high)} de alta prioridade. Os mais urgentes tratam de: "
                f"{subjects}."
            )
        else:
            summary += ", nenhum de alta prioridade."

    return {
        "found": True,
        "open_count": len(unresolved),
        "high_priority_count": len(high),
        "tickets": [
            {
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
        f"{len(high)} {_plural(len(high), 'ticket', 'tickets')} de alta prioridade "
        f"em aberto",
        f"{len(open_orders)} {_plural(len(open_orders), 'pedido', 'pedidos')} "
        f"ainda não entregues",
        f"{len(cancelled)} {_plural(len(cancelled), 'cancelamento', 'cancelamentos')} "
        f"e {len(returned)} {_plural(len(returned), 'devolução', 'devoluções')}",
        f"{len(stuck)} {_plural(len(stuck), 'entrega devolvida', 'entregas devolvidas')} "
        f"ao remetente",
    ]
    return {
        "found": True,
        "high_priority_tickets": len(high),
        "open_orders": len(open_orders),
        "cancelled_orders": len(cancelled),
        "returned_orders": len(returned),
        "shipments_returned_to_sender": len(stuck),
        "summary": (
            "Pontos que merecem atenção neste conjunto de dados: "
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
            "Support tickets that are not resolved, highest priority first. Use "
            "for urgent tickets and open support load."
        ),
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=NoArgs,
        handler=open_tickets,
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
