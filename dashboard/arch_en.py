"""The ten staged workflows and the diagram's labels. English.

The mirror of `arch_pt.py`. Same scenario ids, same nodes, same order --
`test_architecture_scene.py` asserts that, because two hand-written lists of
ten workflows drift apart on their own otherwise.

The specialised agents drawn here (Support, Refund, Finance...) are **not**
independent components of this backend. The platform has five agents: router,
researcher, executor, validator and answerer. The diagram represents the
capability of specialisation, and the scene labels itself as a representation.
"""

from __future__ import annotations

from typing import Any, Final

INTEGRATION: Final[str] = "Integration point"

ARCH_NODES: Final[dict[str, dict[str, str]]] = {
    "ev_ticket": {"name": "New ticket", "role": "Event"},
    "ev_order": {"name": "Order placed", "role": "Event"},
    "ev_stock": {"name": "Low stock", "role": "Event"},
    "ev_refund": {"name": "Refund request", "role": "Event"},
    "ev_campaign": {"name": "Scheduled campaign", "role": "Event"},
    "ag_support": {"name": "Support Agent", "role": "Customer service"},
    "ag_order": {"name": "Order Agent", "role": "Orders"},
    "ag_inventory": {"name": "Inventory Agent", "role": "Stock"},
    "ag_refund": {"name": "Refund Agent", "role": "Refunds"},
    "ag_finance": {"name": "Finance Agent", "role": "Finance"},
    "ag_comm": {"name": "Communication Agent", "role": "Messaging"},
    "ag_marketing": {"name": "Marketing Agent", "role": "Marketing"},
    "ag_content": {"name": "Content Agent", "role": "Content"},
    "ag_analytics": {"name": "Analytics Agent", "role": "Analysis"},
    "ag_research": {"name": "Research Agent", "role": "Retrieval"},
    "policy": {"name": "Policy Engine", "role": "Decides before anything runs"},
    "human": {"name": "Human approval", "role": "When policy requires it"},
    "orchestrator": {"name": "NovaMart", "role": "Orchestrator"},
    "gateway": {"name": "Gateway", "role": "The only route to a tool"},
    "validator": {"name": "Validator", "role": "Checks the result"},
    "t_db": {"name": "Database", "role": INTEGRATION},
    "t_mail": {"name": "Email / SMS", "role": INTEGRATION},
    "t_chat": {"name": "Messaging", "role": INTEGRATION},
    "t_erp": {"name": "ERP / Finance", "role": INTEGRATION},
    "t_api": {"name": "External APIs", "role": INTEGRATION},
    "t_social": {"name": "Social media", "role": INTEGRATION},
    "t_store": {"name": "Storage", "role": INTEGRATION},
}

ARCH_HEADINGS: Final[dict[str, str]] = {
    "events": "Events / triggers",
    "agents": "Specialised agents",
    "core": "Orchestration and governance",
    "tools": "Systems (integration)",
    "aria": "Diagram of the NovaMart architecture: events, specialised agents, "
            "orchestrator, Policy Engine, gateway, validator and integration "
            "points.",
}

ARCH_SCENARIOS: Final[list[dict[str, Any]]] = [
    {
        "id": "refund",
        "title": "Refund",
        "trigger": "ev_refund",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Takes the request and identifies the customer."},
            {"node": "ag_order", "state": "running",
             "note": "Finds the order and its value."},
            {"node": "ag_refund", "state": "waiting",
             "note": "Builds the refund proposal.",
             "bubble": "Customer asked for a refund. May I approve it?"},
            {"node": "orchestrator", "state": "running",
             "note": "Sends the proposal for a decision.",
             "bubble": "•••", "bubbleKind": "think"},
            {"node": "policy", "state": "blocked",
             "note": "Rule: refunds above R$ 200 require evidence."},
            {"node": "orchestrator", "state": "blocked",
             "note": "Returns the decision to the agent.",
             "bubble": "I do not approve the refund. Require photos and a video "
                       "of the product.",
             "bubbleKind": "verdict"},
            {"node": "validator", "state": "success",
             "note": "Confirms that nothing ran."},
        ],
        "verdict": {"state": "blocked", "label": "Refund not approved",
                    "why": "Policy requires evidence before returning amounts "
                           "above the threshold. No tool ran."},
    },
    {
        "id": "stock",
        "title": "Critical stock",
        "trigger": "ev_stock",
        "steps": [
            {"node": "ag_inventory", "state": "running",
             "note": "Detects the level below the minimum."},
            {"node": "ag_analytics", "state": "running",
             "note": "Projects demand for the coming weeks."},
            {"node": "ag_finance", "state": "running",
             "note": "Prices the replenishment.",
             "bubble": "Replenishment sits inside the quarter's budget."},
            {"node": "orchestrator", "state": "running",
             "note": "Consolidates and proposes the purchase."},
            {"node": "policy", "state": "success",
             "note": "Within the delegated limit. Allowed."},
            {"node": "gateway", "state": "running",
             "note": "Issues the grant for the tool call."},
            {"node": "t_erp", "state": "success",
             "note": "Integration point: purchase order."},
            {"node": "validator", "state": "success",
             "note": "Quantity and supplier check out."},
        ],
        "verdict": {"state": "success", "label": "Replenishment authorised",
                    "why": "Inside the delegated limit, so policy allowed it "
                           "without asking a person."},
    },
    {
        "id": "ticket",
        "title": "Support ticket",
        "trigger": "ev_ticket",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Classifies what the ticket is about."},
            {"node": "ag_order", "state": "running",
             "note": "Retrieves the order history."},
            {"node": "ag_research", "state": "running",
             "note": "Finds the applicable knowledge base article."},
            {"node": "orchestrator", "state": "running",
             "note": "Drafts the answer from what was retrieved."},
            {"node": "validator", "state": "success",
             "note": "Checks the answer cites only retrieved data."},
            {"node": "ag_comm", "state": "success",
             "note": "Prepares the reply to the customer.",
             "bubble": "Reply ready, with the warranty article attached."},
        ],
        "verdict": {"state": "success", "label": "Ticket answered",
                    "why": "The answer rests on retrieved records; the "
                           "validator rejects anything without a source."},
    },
    {
        "id": "sales",
        "title": "Sales analysis",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Router",
             "note": "Classifies it as a read-only query."},
            {"node": "ag_analytics", "state": "running",
             "note": "Aggregates revenue by category."},
            {"node": "ag_research", "state": "running",
             "note": "Compares against the previous period."},
            {"node": "validator", "state": "success",
             "note": "Numbers match their source."},
            {"node": "orchestrator", "state": "success", "role": "Answerer",
             "note": "Writes the final answer.",
             "bubble": "Three categories hold most of the revenue."},
        ],
        "verdict": {"state": "success", "label": "Query answered",
                    "why": "Read-only path: no write tool was proposed, so "
                           "nothing needed approving."},
    },
    {
        "id": "order",
        "title": "Order processing",
        "trigger": "ev_order",
        "steps": [
            {"node": "ag_order", "state": "running",
             "note": "Validates items and address."},
            {"node": "ag_finance", "state": "running",
             "note": "Confirms the payment."},
            {"node": "orchestrator", "state": "running",
             "note": "Hands it on for execution."},
            {"node": "gateway", "state": "running",
             "note": "Authorises the write, single use."},
            {"node": "t_db", "state": "success",
             "note": "Integration point: order state."},
            {"node": "validator", "state": "success",
             "note": "Final state consistent with the order."},
            {"node": "ag_comm", "state": "success",
             "note": "Confirmation prepared for the customer."},
        ],
        "verdict": {"state": "success", "label": "Order confirmed",
                    "why": "The write went through the gateway on a single-use "
                           "grant, not through the agent."},
    },
    {
        "id": "finance",
        "title": "Financial operation",
        "trigger": "ev_order",
        "steps": [
            {"node": "ag_finance", "state": "running",
             "note": "Proposes a chargeback above the limit.",
             "bubble": "Chargeback of R$ 4,180. Above my limit."},
            {"node": "orchestrator", "state": "running",
             "note": "Sends it for a decision."},
            {"node": "policy", "state": "waiting",
             "note": "The amount requires human confirmation."},
            {"node": "human", "state": "waiting",
             "note": "Waiting for a person to decide.",
             "bubble": "Requires human approval before it runs."},
            {"node": "gateway", "state": "success",
             "note": "Approved: a grant is issued for this action."},
            {"node": "validator", "state": "success",
             "note": "Amount executed equals the amount approved."},
        ],
        "verdict": {"state": "waiting", "label": "Stopped and waited for a person",
                    "why": "Governed autonomy: above the limit, the system does "
                           "not decide on its own."},
    },
    {
        "id": "campaign",
        "title": "Campaign",
        "trigger": "ev_campaign",
        "steps": [
            {"node": "ag_analytics", "state": "running",
             "note": "Selects the audience from purchase history."},
            {"node": "ag_marketing", "state": "running",
             "note": "Sets the offer and the channel."},
            {"node": "ag_content", "state": "running",
             "note": "Writes the pieces.",
             "bubble": "Three variants ready for review."},
            {"node": "validator", "state": "success",
             "note": "Checks claims and prices against the catalogue."},
            {"node": "t_mail", "state": "success",
             "note": "Integration point: delivery."},
        ],
        "verdict": {"state": "success", "label": "Campaign ready",
                    "why": "The validator compares every claim with the "
                           "catalogue before anything is sent."},
    },
    {
        "id": "delivery",
        "title": "Delivery problem",
        "trigger": "ev_ticket",
        "steps": [
            {"node": "ag_support", "state": "running",
             "note": "Customer reports a delay."},
            {"node": "ag_order", "state": "running",
             "note": "Finds the shipment and the carrier."},
            {"node": "ag_research", "state": "running",
             "note": "Checks the delivery-time policy."},
            {"node": "orchestrator", "state": "running",
             "note": "Decides how it is handled."},
            {"node": "ag_comm", "state": "success",
             "note": "Tells the customer the new date.",
             "bubble": "Shipment in transit. New date communicated."},
        ],
        "verdict": {"state": "success", "label": "Customer informed",
                    "why": "No record needed changing, so nothing needed "
                           "authorising."},
    },
    {
        "id": "business",
        "title": "Business question",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Router",
             "note": "Classifies the intent of the question."},
            {"node": "ag_research", "state": "running",
             "note": "Retrieves the relevant records."},
            {"node": "ag_analytics", "state": "running",
             "note": "Aggregates what was retrieved."},
            {"node": "validator", "state": "success",
             "note": "Every number has a traceable source."},
            {"node": "orchestrator", "state": "success", "role": "Answerer",
             "note": "Answers in plain language."},
        ],
        "verdict": {"state": "success", "label": "Answered from the data",
                    "why": "This is the path the Orchestrator page actually "
                           "runs, against the real backend."},
    },
    {
        "id": "blocked",
        "title": "Blocked operation",
        "trigger": None,
        "steps": [
            {"node": "orchestrator", "state": "running", "role": "Executor",
             "note": "Proposes deleting records.",
             "bubble": "Delete last quarter's orders."},
            {"node": "policy", "state": "blocked",
             "note": "Destructive tool: DENY, no exception."},
            {"node": "gateway", "state": "blocked",
             "note": "No grant issued."},
            {"node": "validator", "state": "success",
             "note": "Confirms the data is still intact."},
        ],
        "verdict": {"state": "blocked", "label": "Refused before running",
                    "why": "The decision comes from the tool's metadata, not "
                           "from the wording of the request — there is nothing "
                           "to talk round."},
    },
]
