"""The same question, asked the way different people would ask it.

The tools were never the bottleneck. Six of the twenty audited questions were
declined by a platform that already held a tool capable of answering them --
`top_customers` existed and "which customer bought the most?" was refused,
`business_overview` existed and "give me an overview" was refused. What failed
was the phrase table between the question and the tool, and a table tested with
one phrase per tool reports full coverage while nineteen out of twenty
rephrasings fall off it.

So every family below is asserted through **several** wordings, in both
languages the demo is used in, including the ones a person reaches for first:
imperatives with no question mark, contractions, and the plain-spoken version
that uses none of the vocabulary the code was written around.

**Route and tool are asserted together**, because they fail independently.
"Give me an overview of HDstore" picked the right tool and was refused anyway:
the router saw no question mark and no lookup noun, classified it as small
talk, and the tool choice was never consulted. A test that checked only the
tool would have passed while the visitor got a refusal.

Two families must *not* resolve. Refunds and stock levels are not in the
dataset -- no refund entity, no inventory field -- so every phrasing of those
has to reach the refusal, and the near-misses that share their vocabulary
("customers", "how much does this cost") must not.

Nothing here calls a provider: the stub is the component under test.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.config import Settings
from agent_platform.llm.stub import StubProvider
from agent_platform.platform import AgentPlatform

# --------------------------------------------------------------------------
# Each family: the tool that must answer it, and the wordings that must reach
# that tool. One entry per way a person actually types the question.
# --------------------------------------------------------------------------

FAMILIES: dict[str, tuple[str, ...]] = {
    "count_products": (
        "How many products do we have?",
        "How many products do we sell?",
        "What is the number of products in the catalogue?",
        "Quantos produtos temos?",
        "Qual a quantidade de produtos no catálogo?",
    ),
    "list_products": (
        "Which products do we sell?",
        "What products are in the catalogue?",
        "List products.",
        "How much does each product cost?",
        "Quais produtos vendemos?",
        "Quanto custa cada produto?",
    ),
    "product_price_range": (
        "What is the most expensive product?",
        "What is the cheapest product?",
        "Which product has the highest price?",
        "Qual o produto mais caro?",
        "Qual o produto mais barato?",
    ),
    "top_selling_products": (
        "Which product sells the most?",
        "What is our best seller?",
        "Which is the most sold product?",
        "Qual o produto mais vendido?",
    ),
    "count_customers": (
        "How many customers do we have?",
        "What is the number of customers?",
        "How big is our customer base?",
        "Quantos clientes temos?",
    ),
    "top_customers": (
        "Which customer bought the most?",
        "Who are our top customers?",
        "Who is our biggest customer?",
        "Which customer spends the most?",
        "Qual cliente mais comprou?",
        "Quem são os melhores clientes?",
    ),
    "revenue_total": (
        "How much have we sold in total?",
        "What is the total value of all orders?",
        "What is the average order value?",
        "What is our total revenue?",
        "Qual o faturamento total?",
        "Qual o valor médio dos pedidos?",
    ),
    "list_orders": (
        "How many orders do we have?",
        "Which orders are delivered?",
        "Which orders are pending?",
        "Are there delayed orders?",
        "Show me recent orders.",
        "Quantos pedidos temos?",
        "Quais pedidos estão em aberto?",
    ),
    "open_tickets": (
        "How many tickets are open?",
        "Are there any urgent tickets?",
        "How many high priority tickets?",
        "Which customers have tickets?",
        "Quantos tickets estão abertos?",
        "Há tickets de alta prioridade?",
    ),
    "business_overview": (
        "Give me an overview of HDstore.",
        "Give me an overview of the business.",
        "What should I know as a manager?",
        "Is there anything wrong I should be aware of?",
        "Me dê um panorama do negócio.",
        "O que precisa de atenção?",
    ),
}

#: Questions about things the dataset does not record. Every one must be
#: refused; none may be served by a tool that reads something adjacent.
UNANSWERABLE: tuple[str, ...] = (
    "How many orders were refunded?",
    "What is the total of refunds?",
    "How many refunds did we process?",
    "Quantos reembolsos tivemos?",
    "Qual o total de reembolsos?",
    "Which products have low stock?",
    "Do we have this in stock?",
    "What is our inventory level?",
    "Qual o estoque atual?",
    "What is our profit margin?",
    "Qual a margem de lucro?",
)

#: Answerable questions whose words overlap the unanswerable ones. Each was a
#: real regression: a substring test for "custo" matched "customers" and
#: refused three questions that three different tools answer.
NEAR_MISSES: tuple[tuple[str, str], ...] = (
    ("How many customers do we have?", "count_customers"),
    ("Which customer bought the most?", "top_customers"),
    ("Which customers have tickets?", "open_tickets"),
    ("How much does each product cost?", "list_products"),
    ("Quanto custa cada produto?", "list_products"),
)


def _phrasings() -> list[tuple[str, str]]:
    return [(tool, phrase) for tool, phrases in FAMILIES.items() for phrase in phrases]


# ============================================== every phrasing reaches the tool


@pytest.mark.parametrize(("tool", "phrase"), _phrasings())
def test_a_phrasing_is_routed_and_served(tool, phrase):
    """Both halves, because either one alone lets a refusal through."""
    provider = StubProvider()

    assert provider._servable(phrase), f"declared unanswerable: {phrase!r}"
    assert provider._choose_route(phrase) == "researcher", (
        f"{phrase!r} never reaches a tool; the router classified it away"
    )
    chosen, _arguments = provider._choose_tool(phrase)
    assert chosen == tool, f"{phrase!r} -> {chosen}, expected {tool}"


@pytest.mark.parametrize("phrase", UNANSWERABLE)
def test_an_unanswerable_question_is_refused_whatever_the_wording(phrase):
    """No refund entity, no stock field, no cost field. So: no answer.

    The route matters as much as the refusal. `direct_response` is what
    reaches the orchestrator's honest "I cannot answer that"; the researcher
    route would produce a confident sentence from whichever tool came closest.
    """
    provider = StubProvider()

    assert not provider._servable(phrase), f"claimed answerable: {phrase!r}"
    assert provider._choose_route(phrase) == "direct_response", phrase


@pytest.mark.parametrize(("phrase", "tool"), NEAR_MISSES)
def test_a_question_that_merely_sounds_unanswerable_is_still_answered(phrase, tool):
    """The other side of the refusal, and the more expensive one to get wrong.

    Over-refusing is quieter than fabricating and just as wrong: the data is
    there, the tool is there, and the visitor is told the platform cannot do
    something it does.
    """
    provider = StubProvider()

    assert provider._servable(phrase), f"wrongly refused: {phrase!r}"
    assert provider._choose_tool(phrase)[0] == tool


# =========================================== the arguments the wording implies


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Which orders are delivered?", {"status": "delivered"}),
        ("Which orders are pending?", {"status": "processing"}),
        ("Which orders were cancelled?", {"status": "cancelled"}),
        ("Are there delayed orders?", {"status": "open"}),
        ("Quais pedidos foram entregues?", {"status": "delivered"}),
        ("How many orders do we have?", {}),
    ],
)
def test_a_status_in_the_question_becomes_a_filter(phrase, expected):
    """Without this the filter was dropped and the reply changed meaning.

    "Which orders are delivered?" answered "40 orders in total" -- a true
    sentence about the dataset, and the wrong answer to the question, reported
    as a success.
    """
    tool, arguments = StubProvider()._choose_tool(phrase)
    assert tool == "list_orders"
    assert arguments == expected


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("How many high priority tickets?", {"priority": "high"}),
        ("Há tickets de alta prioridade?", {"priority": "high"}),
        ("How many tickets are open?", {}),
    ],
)
def test_a_priority_in_the_question_becomes_a_filter(phrase, expected):
    tool, arguments = StubProvider()._choose_tool(phrase)
    assert tool == "open_tickets"
    assert arguments == expected


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("Which product generated the most revenue?", "revenue"),
        ("Qual produto gerou mais receita?", "revenue"),
        ("Which product sells the most?", "units"),
    ],
)
def test_asking_for_money_ranks_by_money(phrase, expected):
    """Units and revenue lead with different products, so this is not cosmetic."""
    tool, arguments = StubProvider()._choose_tool(phrase)
    assert tool == "top_selling_products"
    assert arguments["by"] == expected


# ================================================ and the whole platform agrees


@pytest.fixture(scope="module")
def platform(tmp_path_factory):
    """One platform for the end-to-end sample, with room for many requests."""
    tuned = replace(
        Settings.from_env(load_dotenv_file=False),
        database_path=tmp_path_factory.mktemp("phrasings") / "p.db",
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


def _ask(platform: AgentPlatform, question: str) -> tuple[str, str, list[str]]:
    result = platform.run(question)
    events = platform.repository.events_for_request(result.request_id)
    tools = [
        str(event.get("tool"))
        for event in events
        if event.get("event_type") == "tool_call"
    ]
    return result.response or "", result.status, tools


#: One wording per family, run through the real graph. The parametrised tests
#: above cover breadth against the stub; these prove the wiring underneath
#: them, which is where the "right tool, still refused" bug lived.
END_TO_END = tuple((tool, phrases[0]) for tool, phrases in FAMILIES.items())


@pytest.mark.slow
@pytest.mark.parametrize(("tool", "phrase"), END_TO_END)
def test_the_platform_answers_the_question_with_that_tool(platform, tool, phrase):
    answer, status, tools = _ask(platform, phrase)

    assert status == "success", f"{phrase!r} -> {status}: {answer}"
    assert tools == [tool], f"{phrase!r} ran {tools}"
    assert answer.strip(), "a successful run produced no answer"
    # Aggregate answers must not carry the record identifiers the stub used to
    # invent when it had no aggregate tool to reach for.
    assert not any(
        marker in answer for marker in ("CUS-2001", "ORD-1001", "TKT-4001")
    ), answer


@pytest.mark.slow
@pytest.mark.parametrize("phrase", ["How many orders were refunded?",
                                    "Qual o total de reembolsos?",
                                    "Which products have low stock?"])
def test_the_platform_refuses_what_it_cannot_answer(platform, phrase):
    answer, status, tools = _ask(platform, phrase)

    assert status == "declined", f"{phrase!r} -> {status}"
    assert tools == [], f"a tool ran for an unanswerable question: {tools}"
    assert "não consigo responder" in answer.lower(), answer


@pytest.mark.slow
def test_the_refusal_says_what_it_can_do_instead(platform):
    """An honest refusal that leaves the visitor with nowhere to go is a
    dead end; the message names the entities that do exist."""
    answer, _status, _tools = _ask(platform, "How many orders were refunded?")
    lowered = answer.lower()
    assert any(word in lowered for word in ("clientes", "pedidos", "tickets"))


# ============================================ and a write is still a write


@pytest.mark.parametrize(
    "phrase",
    [
        "Cancel order ORD-1001",
        "Cancele o pedido ORD-1001",
        "Delete order ORD-1001",
        "Please cancel my order",
        "Update order ORD-1003 status",
    ],
)
def test_an_instruction_to_change_something_is_not_a_read(phrase):
    """The risk the phrase table above creates, pinned.

    Answering more questions means matching more words, and the words that
    describe a cancelled order are the words that ask for one. A bare "cancel"
    stem among the `list_orders` keywords would have turned "cancel order
    ORD-1001" into a lookup -- reporting success for a destructive request
    that was never carried out, and never refused in public either. The
    keywords added for this family are passive and plural for that reason.
    """
    assert StubProvider._choose_route(phrase) == "executor", phrase


# ================================== a written rule is not a missing record


#: The line the refusal has to draw. HDstore has no refund *transactions* --
#: nothing in `ORDERS` records one -- but the knowledge base holds a "Refund
#: policy" article and a "Returns process" one. So the rule is answerable and
#: the count is not, from the same word.
DOCUMENTED: tuple[str, ...] = (
    "What is the refund policy?",
    "Qual a política de reembolso?",
    "How do I request a refund?",
    "What is the returns process?",
)


@pytest.mark.parametrize("phrase", DOCUMENTED)
def test_a_policy_question_is_answerable_from_the_knowledge_base(phrase):
    """The regression a blanket refusal caused, pinned in the open.

    Refusing every sentence containing "refund" broke this question and the
    twelve security and retrieval tests built on it. The absent thing is the
    transaction, not the document.
    """
    provider = StubProvider()
    assert provider._servable(phrase), f"wrongly refused: {phrase!r}"
    assert provider._choose_route(phrase) == "researcher", phrase


def test_the_knowledge_base_actually_holds_those_articles():
    """Guards the test above: it is only right while the articles exist."""
    from agent_platform.tools import dataset as data

    titles = " ".join(str(article.get("title", "")) for article in data.KB_ARTICLES)
    assert "Refund policy" in titles
    assert "Returns process" in titles


@pytest.mark.parametrize(
    "phrase",
    ["How many orders were refunded?", "What is the total of refunds?"],
)
def test_counting_refunds_is_still_refused(phrase):
    """The other half. A policy article cannot supply a number of refunds."""
    assert not StubProvider()._servable(phrase), phrase
