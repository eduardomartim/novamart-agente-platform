"""Simulated back-office tools.

Every tool here operates on the in-memory dataset in :mod:`.dataset`. Nothing
in this module opens a socket, writes a file, or sends a message.
``send_email`` returns a record describing the message it *would* have sent.

This is a deliberate safety property, not a shortcut: it lets the platform
demonstrate a complete authorisation flow for high-risk and destructive actions
without any action being genuinely destructive.
"""

from __future__ import annotations

import copy
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field

from ..models import AgentName, Capability, RiskLevel
from ..retrieval import strategy as retrieval_strategy
from .dataset import (
    CUSTOMERS,
    KB_ARTICLES,  # noqa: F401  re-exported: callers reach it as fake_tools.KB_ARTICLES
    ORDERS,
    TICKETS,
    shipment_for_order,
)
from .execution import require_gateway
from .models import ToolDefinition

SIMULATED_EMAIL_MARKER: Final[str] = "SIMULATED EMAIL - no message was sent"

#: Mutable working copies. Write tools mutate these, never the source dataset,
#: so ``reset_dataset()`` always restores a known-good state.
_orders: dict[str, dict[str, Any]] = copy.deepcopy(ORDERS)
_customers: dict[str, dict[str, Any]] = copy.deepcopy(CUSTOMERS)
_tickets: dict[str, dict[str, Any]] = copy.deepcopy(TICKETS)


def reset_dataset() -> None:
    """Restore the simulated dataset. Used between tests and demo runs."""
    global _orders, _customers, _tickets
    _orders = copy.deepcopy(ORDERS)
    _customers = copy.deepcopy(CUSTOMERS)
    _tickets = copy.deepcopy(TICKETS)


# --------------------------------------------------------------------- schemas


class SearchArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=500)


class GetOrderArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    order_id: str = Field(pattern=r"^ORD-\d{3,8}$")


#: A name lookup is the one read whose result size the caller does not control,
#: so it is bounded here rather than left to the caller's good manners.
MAX_CUSTOMER_MATCHES: Final[int] = 5


class FindCustomerArgs(BaseModel):
    """Arguments for a name-based customer lookup."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(
        min_length=1,
        max_length=80,
        description="Full or partial customer name, e.g. 'Ana' or 'Ana Ribeiro'.",
    )


class GetCustomerArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^CUS-\d{3,8}$")


class ListCustomerOrdersArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: str = Field(pattern=r"^CUS-\d{3,8}$")


class GetTicketArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ticket_id: str = Field(pattern=r"^TKT-\d{3,8}$")


class UpdateRecordArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(pattern=r"^ORD-\d{3,8}$")
    field: str = Field(min_length=1, max_length=40)
    value: str = Field(max_length=200)


class SendEmailArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")
    subject: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class DeleteRecordArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(pattern=r"^ORD-\d{3,8}$")


# -------------------------------------------------------------------- handlers


def search(query: str) -> dict[str, Any]:
    """Rank the knowledge base, using whichever strategy this request can use.

    The handler does not choose. It asks ``retrieval.strategy``, which returns
    BM25 when no provider is bound to the request and hybrid when one is --
    demo mode simply binds nothing. Keeping the choice out of here is what lets
    the tool contract stay still while retrieval changes underneath it: the
    return shape is identical either way, and no caller can tell which ranker
    produced a document.

    Read-only in the strongest sense available: every retriever behind this
    offers no verb that writes. ``result_count`` counts what was returned;
    ``total_matches`` carries the number that used to be in that field.
    """
    require_gateway("search")
    results = retrieval_strategy.retrieve(query)
    return {
        "query": query,
        "result_count": len(results),
        "total_matches": retrieval_strategy.total_matches(query),
        "results": [
            {
                "doc_id": result.doc_id,
                "title": result.title,
                "body": result.body,
                "score": round(result.score, 4),
            }
            for result in results
        ],
    }


#: Order and ticket vocabulary as a reader says it. The dataset stores the
#: English key; only the sentence changes, never the stored value.
_ORDER_STATUS_PT: Final[dict[str, str]] = {
    "processing": "em processamento",
    "shipped": "enviado",
    "delivered": "entregue",
    "cancelled": "cancelado",
    "returned": "devolvido",
}
_TICKET_STATUS_PT: Final[dict[str, str]] = {
    "open": "aberto",
    "escalated": "escalado",
    "resolved": "resolvido",
}
_PRIORITY_PT: Final[dict[str, str]] = {
    "high": "alta",
    "normal": "normal",
    "low": "baixa",
}
_TIER_PT: Final[dict[str, str]] = {
    "platinum": "Platinum",
    "gold": "Ouro",
    "standard": "Standard",
}


def _money(value: float) -> str:
    """Brazilian currency, written the way a Brazilian reader expects."""
    return "R$ " + f"{value:,.2f}".replace(",", "@").replace(".", ",").replace("@", ".")


def _customer_name(customer_id: str) -> str:
    """A person's name, falling back to the identifier only if it is unknown."""
    customer = _customers.get(customer_id)
    return customer["name"] if customer else customer_id


def _order_phrase(order: dict[str, Any]) -> str:
    status = _ORDER_STATUS_PT.get(order["status"], order["status"])
    return f"o pedido de {_money(order['total_brl'])} está {status}"


def get_order(order_id: str) -> dict[str, Any]:
    require_gateway("get_order")
    order = _orders.get(order_id)
    if order is None:
        return {"found": False, "order_id": order_id}
    result = copy.deepcopy(order)
    shipment = shipment_for_order(order_id)
    result["shipment"] = copy.deepcopy(shipment) if shipment else None

    # The sentence a person reads. The record above is untouched and still
    # carries every identifier, so the trace and any caller that wants the
    # structure keep exactly what they had.
    who = _customer_name(order["customer_id"])
    status = _ORDER_STATUS_PT.get(order["status"], order["status"])
    summary = (
        f"O pedido de {who}, no valor de {_money(order['total_brl'])}, "
        f"está {status}."
    )
    if order.get("tracking") and order["status"] in ("shipped", "delivered"):
        summary += f" Código de rastreio: {order['tracking']}."
    return {"found": True, "order": result, "summary": summary}


def get_customer(customer_id: str) -> dict[str, Any]:
    require_gateway("get_customer")
    customer = _customers.get(customer_id)
    if customer is None:
        return {"found": False, "customer_id": customer_id}
    tier = _TIER_PT.get(customer["tier"], customer["tier"])
    return {
        "found": True,
        "customer": copy.deepcopy(customer),
        "summary": (
            f"{customer['name']} é cliente do segmento {tier}, "
            f"de {customer['city']}, desde {customer['since']}."
        ),
    }


def list_customer_orders(customer_id: str) -> dict[str, Any]:
    """Order history for one customer.

    Returns a summary per order rather than full records: the agent usually
    needs to pick one, and returning every line item would bloat the context
    for no benefit.
    """
    require_gateway("list_customer_orders")
    if customer_id not in _customers:
        return {"found": False, "customer_id": customer_id}
    matches = sorted(
        (o for o in _orders.values() if o["customer_id"] == customer_id),
        key=lambda o: o["placed_on"],
        reverse=True,
    )
    who = _customer_name(customer_id)
    if not matches:
        summary = f"{who} não tem pedidos registrados."
    else:
        total = sum(o["total_brl"] for o in matches)
        listed = "; ".join(_order_phrase(o) for o in matches[:3])
        more = "" if len(matches) <= 3 else f" e mais {len(matches) - 3}"
        summary = (
            f"{who} tem {len(matches)} "
            f"{'pedido' if len(matches) == 1 else 'pedidos'}, somando "
            f"{_money(total)}. Os mais recentes: {listed}{more}."
        )
    return {
        "found": True,
        "customer_id": customer_id,
        "order_count": len(matches),
        "summary": summary,
        "orders": [
            {
                "order_id": o["order_id"],
                "status": o["status"],
                "placed_on": o["placed_on"],
                "total_brl": o["total_brl"],
                "item_count": o["item_count"],
            }
            for o in matches
        ],
    }


def find_customer(name: str) -> dict[str, Any]:
    """Find customers by name, with a summary of each one's orders.

    Deliberately narrow. It answers "who is this and what have they ordered",
    which is the question a person actually asks, and nothing else. It is not a
    query interface: the term must be a name fragment, the result set is capped
    at :data:`MAX_CUSTOMER_MATCHES`, and a blank term matches nothing rather
    than everything.

    Orders are included because the orchestration graph performs a single
    research hop. Returning only the customer id would resolve the name and
    leave the actual question unanswered.
    """
    require_gateway("find_customer")

    term = (name or "").strip().casefold()
    # A blank or punctuation-only term is a mistake, not a request for the
    # whole table.
    if not term or not any(ch.isalnum() for ch in term):
        return {"found": False, "query": name, "matches": []}

    matches: list[dict[str, Any]] = []
    for customer in _customers.values():
        if term not in customer["name"].casefold():
            continue
        record = copy.deepcopy(customer)
        record["orders"] = [
            {
                "order_id": order["order_id"],
                "status": order["status"],
                "placed_on": order["placed_on"],
                "total_brl": round(
                    sum(item["line_total_brl"] for item in order["items"]), 2
                ),
            }
            for order in sorted(
                (o for o in _orders.values() if o["customer_id"] == customer["customer_id"]),
                key=lambda o: o["order_id"],
            )
        ]
        matches.append(record)
        if len(matches) >= MAX_CUSTOMER_MATCHES:
            break

    if not matches:
        summary = f"Nenhum cliente encontrado com o nome “{name}”."
    else:
        first = matches[0]
        tier = _TIER_PT.get(first["tier"], first["tier"])
        summary = (
            f"{first['name']} é cliente do segmento {tier}, de {first['city']}."
        )
        orders = first.get("orders") or []
        if orders:
            total = sum(o["total_brl"] for o in orders)
            listed = "; ".join(_order_phrase(o) for o in orders[:3])
            more = "" if len(orders) <= 3 else f" e mais {len(orders) - 3}"
            summary += (
                f" Tem {len(orders)} "
                f"{'pedido' if len(orders) == 1 else 'pedidos'}, somando "
                f"{_money(total)}: {listed}{more}."
            )
        else:
            summary += " Ainda não tem pedidos registrados."
        if len(matches) > 1:
            summary += f" Outros {len(matches) - 1} cliente(s) também correspondem."

    return {
        "found": bool(matches),
        "query": name,
        "matches": matches,
        "summary": summary,
    }


def get_ticket(ticket_id: str) -> dict[str, Any]:
    require_gateway("get_ticket")
    ticket = _tickets.get(ticket_id)
    if ticket is None:
        return {"found": False, "ticket_id": ticket_id}
    status = _TICKET_STATUS_PT.get(ticket["status"], ticket["status"])
    priority = _PRIORITY_PT.get(ticket["priority"], ticket["priority"])
    summary = (
        f"Chamado de {_customer_name(ticket['customer_id'])} sobre "
        f"\"{ticket['subject']}\": está {status}, prioridade {priority}, "
        f"aberto em {ticket['opened_on']}."
    )
    if ticket.get("resolution"):
        summary += f" Resolução: {ticket['resolution']}"
    return {"found": True, "ticket": copy.deepcopy(ticket), "summary": summary}


def update_record(record_id: str, field: str, value: str) -> dict[str, Any]:
    require_gateway("update_record")
    order = _orders.get(record_id)
    if order is None:
        return {"updated": False, "reason": "record not found", "record_id": record_id}
    if field not in order:
        return {
            "updated": False,
            "reason": f"unknown field {field!r}",
            "allowed_fields": sorted(k for k in order if not isinstance(order[k], (list, dict))),
        }
    previous = order[field]
    order[field] = value
    return {
        "updated": True,
        "record_id": record_id,
        "field": field,
        "previous_value": previous,
        "new_value": value,
        "simulated": True,
    }


def send_email(to: str, subject: str, body: str) -> dict[str, Any]:
    require_gateway("send_email")
    # No transport is involved. The returned record is the entire effect.
    return {
        "status": SIMULATED_EMAIL_MARKER,
        "to": to,
        "subject": subject,
        "body_chars": len(body),
        "sent": False,
        "simulated": True,
    }


def delete_record(record_id: str) -> dict[str, Any]:
    require_gateway("delete_record")
    existed = record_id in _orders
    _orders.pop(record_id, None)
    return {"deleted": existed, "record_id": record_id, "simulated": True}


# ----------------------------------------------------------------- definitions

#: The least-privilege matrix from the blueprint, expressed as data.
#: Router holds no tools at all; Validator may only read.
TOOL_DEFINITIONS: Final[tuple[ToolDefinition, ...]] = (
    ToolDefinition(
        name="search",
        description="Search the internal knowledge base for policy and process information.",
        risk_level=RiskLevel.LOW,
        capability=Capability.SEARCH,
        parameters=SearchArgs,
        handler=search,
        allowed_agents=frozenset({AgentName.RESEARCHER, AgentName.EXECUTOR}),
    ),
    ToolDefinition(
        name="get_order",
        description="Look up a single order, including its shipment status, by identifier.",
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=GetOrderArgs,
        handler=get_order,
        allowed_agents=frozenset(
            {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
        ),
    ),
    ToolDefinition(
        name="list_customer_orders",
        description="List the order history for one customer, most recent first.",
        risk_level=RiskLevel.LOW,
        capability=Capability.READ_DATA,
        parameters=ListCustomerOrdersArgs,
        handler=list_customer_orders,
        allowed_agents=frozenset(
            {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
        ),
    ),
    ToolDefinition(
        name="find_customer",
        description=(
            "Find customers by full or partial name, returning each match with "
            "a summary of their orders. Use when the request names a person "
            "rather than an identifier."
        ),
        risk_level=RiskLevel.MEDIUM,
        capability=Capability.READ_DATA,
        parameters=FindCustomerArgs,
        handler=find_customer,
        allowed_agents=frozenset(
            {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
        ),
    ),
    ToolDefinition(
        name="get_customer",
        description="Look up a customer profile by its identifier.",
        risk_level=RiskLevel.MEDIUM,
        capability=Capability.READ_DATA,
        parameters=GetCustomerArgs,
        handler=get_customer,
        allowed_agents=frozenset(
            {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
        ),
    ),
    ToolDefinition(
        name="get_ticket",
        description="Read a support ticket, including the order it relates to.",
        risk_level=RiskLevel.MEDIUM,
        capability=Capability.READ_DATA,
        parameters=GetTicketArgs,
        handler=get_ticket,
        allowed_agents=frozenset(
            {AgentName.RESEARCHER, AgentName.EXECUTOR, AgentName.VALIDATOR}
        ),
    ),
    ToolDefinition(
        name="update_record",
        description="Modify a field on an existing order record.",
        risk_level=RiskLevel.HIGH,
        capability=Capability.WRITE_DATA,
        parameters=UpdateRecordArgs,
        handler=update_record,
        allowed_agents=frozenset({AgentName.EXECUTOR}),
        requires_confirmation=True,
        idempotent=False,
    ),
    ToolDefinition(
        name="send_email",
        description="Send a message to a customer. Simulated: no mail is transmitted.",
        risk_level=RiskLevel.HIGH,
        capability=Capability.SEND_MESSAGE,
        parameters=SendEmailArgs,
        handler=send_email,
        allowed_agents=frozenset({AgentName.EXECUTOR}),
        requires_confirmation=True,
        idempotent=False,
    ),
    ToolDefinition(
        name="delete_record",
        description="Permanently delete an order record.",
        risk_level=RiskLevel.CRITICAL,
        capability=Capability.DELETE,
        parameters=DeleteRecordArgs,
        handler=delete_record,
        # No agent is granted the delete capability. The tool is registered so
        # that the platform can demonstrate a CRITICAL action being refused,
        # rather than merely being absent.
        allowed_agents=frozenset(),
        requires_confirmation=True,
        idempotent=False,
    ),
)
