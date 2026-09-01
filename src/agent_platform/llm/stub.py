"""Deterministic provider used for demo mode, tests and CI.

This provider runs no model. It exists so the platform can be exercised
end to end without an API key, and so the evaluation suite is reproducible.

It identifies itself as ``stub`` in every response, trace and cost record.
Nothing it produces is ever attributed to Gemini.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from .provider import Embedding, EmbedTask, LLMResponse, Purpose, estimate_tokens

STUB_PROVIDER_NAME = "stub"
STUB_MODEL_NAME = "deterministic-stub-v1"

_ORDER_ID = re.compile(r"\b(?:ord[-_]?|order\s+(?:id\s+)?#?)(\d{3,8})\b", re.I)
_CUSTOMER_ID = re.compile(r"\b(?:cus[-_]?|customer\s+(?:id\s+)?#?)(\d{3,8})\b", re.I)
_TICKET_ID = re.compile(r"\b(?:tkt[-_]?|ticket\s+(?:id\s+)?#?)(\d{3,8})\b", re.I)
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

#: Prompts fence the end user's text. Keyword matching must run against that
#: text alone -- matching the whole prompt would let the tool catalogue in the
#: prompt (which contains words like "send" and "delete") drive the choice.
_FENCED = re.compile(
    r"<<<UNTRUSTED_USER_CONTENT\s*(.*?)\s*UNTRUSTED_USER_CONTENT>>>", re.S
)
#: Tool names offered in the prompt, so the stub only proposes tools the
#: calling agent is actually permitted to use.
_OFFERED_TOOL = re.compile(r'"name":\s*"([a-z_]+)"')


def _user_text(prompt: str) -> str:
    """Extract the end user's request from an assembled prompt."""
    match = _FENCED.search(prompt)
    return match.group(1) if match else prompt


def _offered_tools(prompt: str) -> set[str]:
    return set(_OFFERED_TOOL.findall(prompt))

#: Ordered most-specific-first: "delete the order" must map to delete_record,
#: not get_order.
#: Portuguese entries are verb *stems* ("apag" rather than "apagar") so that
#: conjugations such as "apague" and "apagou" are recognised. Matching only the
#: infinitive let a destructive Portuguese request fall through to a harmless
#: read, which then reported success for an action never performed.
#: Aggregate intents, checked *before* the record tools. These questions are
#: about the set rather than about a row, and matching them here is what stops
#: "how many customers do we have?" reaching `get_customer`, which can only
#: answer about one.
#:
#: Ordered most-specific-first for the same reason as the table below: "which
#: customer placed the most orders" is a ranking, not an order listing.
_AGGREGATE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "business_overview",
        (
            "needs attention", "need attention", "should i know", "should i be aware",
            "any problem", "anything wrong", "manager", "deveria conhecer",
            "precisa de atencao", "precisa de atenção", "algum problema",
            "gestor", "resumo do negocio", "resumo do negócio",
        ),
    ),
    (
        "top_customers",
        (
            "top customer", "best customer", "biggest customer", "most orders",
            "most purchases", "most valuable", "who buys", "buys the most",
            "principais client", "melhores client", "maiores client",
            "mais pedidos", "mais compras", "mais comprou",
        ),
    ),
    (
        "count_customers",
        (
            "how many customer", "number of customers", "customer count",
            "customer base", "quantos client", "quantidade de client",
            "total de client",
        ),
    ),
    (
        "revenue_total",
        (
            "total value", "total revenue", "total sales", "how much revenue",
            "valor total", "faturamento", "receita total", "total em pedidos",
        ),
    ),
    (
        "open_tickets",
        (
            "urgent ticket", "open ticket", "unresolved ticket", "any ticket",
            "support load", "tickets abertos", "ticket urgente",
            "chamados abertos", "tickets em aberto",
        ),
    ),
    (
        "list_orders",
        (
            # Bare stems, not phrases. "delayed order" missed "which orders
            # are delayed?" -- the words are in the other order -- and the
            # question was declined even though `list_orders` answers it.
            "delayed", "atrasad", "overdue", "em atraso",
            "recent order", "latest order", "open orders", "orders are open",
            "how many orders", "pedidos recentes", "pedidos em aberto",
            "quantos pedidos", "ultimos pedidos", "últimos pedidos",
        ),
    ),
)


_ACTION_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "delete_record",
        # Stems, for the same reason as the router's write words: "deleting"
        # must select the tool that "delete" selects.
        ("delet", "remov", "eras", "drop", "wipe", "purg", "apag", "exclu"),
    ),
    (
        "send_email",
        ("send", "email", "e-mail", "notify", "message", "enviar", "notific", "avis"),
    ),
    ("update_record", ("updat", "chang", "modif", "set ", "edit", "atualiz", "alter")),
    ("get_ticket", ("ticket", "chamado", "case number", "support case")),
    (
        "list_customer_orders",
        (
            "order history", "all orders", "orders for", "list orders",
            "past orders", "previous orders", "every order",
            "historico de pedidos", "todos os pedidos",
        ),
    ),
    ("get_order", ("order", "pedido", "shipment", "tracking")),
    ("get_customer", ("customer", "cliente", "account holder", "profile")),
)


#: A person's name, written the way people write one: "Ana Ribeiro", or
#: "Ana's" in the possessive. Matching a proper noun rather than consulting the
#: dataset keeps the stub free of a dependency on the data it is standing in
#: for -- a real model would not have the customer list memorised either.
_PERSON_NAME = re.compile(
    r"\b([A-Z][a-z]{2,})(?:\s+([A-Z][a-z]{2,}))?(?:'s|\u2019s)?\b"
)

#: Words that look like names but introduce a sentence rather than a person.
_NOT_A_NAME = frozenset(
    {
        "What", "Which", "Show", "Tell", "Give", "List", "Find", "Where",
        "When", "Why", "How", "Who", "Does", "Did", "Can", "Could", "Please",
        "Update", "Delete", "Send", "Ignore", "The", "This", "That", "Order",
        "Customer", "Ticket", "Product", "Status", "All", "Every", "Refund",
        "Qual", "Quais", "Mostre", "Onde", "Como", "Porque", "Pedido",
        "Cliente", "Chamado",
    }
)


def _person_name(text: str) -> str | None:
    """Return a name the request is about, if it names a person at all."""
    for match in _PERSON_NAME.finditer(text):
        first, second = match.group(1), match.group(2)
        if first in _NOT_A_NAME:
            continue
        if second and second in _NOT_A_NAME:
            second = None
        return f"{first} {second}" if second else first
    return None


def _first_group(pattern: re.Pattern[str], text: str, default: str) -> str:
    match = pattern.search(text)
    return match.group(1) if match else default


class StubProvider:
    """A rule-based stand-in for a real LLM.

    Deterministic by construction: the same prompt always yields the same
    output, which is what makes the regression suite meaningful.
    """

    def __init__(self, *, latency_ms: float = 0.0) -> None:
        self._latency_ms = latency_ms

    @property
    def name(self) -> str:
        return STUB_PROVIDER_NAME

    @property
    def model(self) -> str:
        return STUB_MODEL_NAME

    # ------------------------------------------------------------------ routing

    @staticmethod
    def _servable(request: str) -> bool:
        """Could any registered tool actually answer this request?

        Asked before routing, so a question nothing can serve is declined
        instead of being sent to the researcher to produce something. The three
        ways a request is answerable:

        * it matches an aggregate intent -- those tools need no identifier;
        * it carries an identifier, so a record lookup applies;
        * it names a person, which `find_customer` resolves;
        * or it is a knowledge-base question, which `search` always accepts.
        """
        lowered = request.lower()
        if any(
            keyword in lowered
            for _tool, keywords in _AGGREGATE_KEYWORDS
            for keyword in keywords
        ):
            return True
        if (
            _ORDER_ID.search(request)
            or _CUSTOMER_ID.search(request)
            or _TICKET_ID.search(request)
        ):
            return True
        name = _person_name(request)
        if name is not None:
            # Two capitalised words read as somebody's full name, and a name
            # lookup is the obvious tool. One word could be anything -- a city,
            # a product, a country -- so "what is the weather in Tokyo?" used to
            # become a search for a customer named Tokyo and report success on
            # finding none. A single name needs either a domain word beside it,
            # or to be the entire request, which is how a visitor types a name.
            if " " in name:
                return True
            if request.strip().rstrip("?.!").strip() == name:
                return True
            return any(
                word in lowered
                for word in (
                    "customer", "client", "cliente", "order", "pedido",
                    "ticket", "chamado", "about", "sobre", "conta", "account",
                )
            )
        # A documentation question. `search` takes free text, so it can always
        # be attempted -- and when the corpus holds nothing, the answer node
        # says so rather than inventing coverage.
        return any(
            word in lowered
            for word in (
                "policy", "policies", "how do i", "how long", "process",
                "refund", "return", "shipping", "warranty", "politica",
                "política", "prazo", "devoluc", "devoluç", "garantia",
                "como funciona",
            )
        )

    @staticmethod
    def _choose_route(prompt: str) -> str:
        """Classify intent.

        Interrogative phrasing wins over action keywords, so "what is the
        refund policy?" is a lookup rather than a refund request. Where the two
        genuinely conflict ("can you delete order 1001?") the read-only route
        wins: an ambiguous request should not default to the path that changes
        things.
        """
        lowered = prompt.lower().strip()
        question_openers = (
            "what", "who", "when", "where", "which", "how", "why", "is ", "are ",
            "can ", "could ", "do ", "does ", "qual", "quais", "quando", "onde",
            "como", "por que", "quanto",
        )
        # Stems, not whole words. The Portuguese entries were already stems;
        # the English ones were not, so "deleting" and "updating" matched
        # nothing and an imperative to change something was read as a lookup.
        write_words = (
            "delet", "remov", "send", "email", "updat", "chang", "creat",
            "cancel", "set ", "notif", "inform", "apag", "exclu", "enviar",
            "atualiz", "alter", "avis",
        )
        lookup_words = (
            "status", "find", "look up", "search", "show", "list", "order",
            "customer", "policy", "buscar", "consultar", "pedido", "cliente",
            "ticket", "chamado", "history",
            # "Tell me about X" is how a person asks for a lookup without
            # phrasing it as a question. Checked after the write words, so an
            # instruction to change something is still an action.
            "tell me", "me fale", "fale sobre",
        )

        is_question = lowered.endswith("?") or lowered.startswith(question_openers)
        has_write = any(word in lowered for word in write_words)

        if has_write and not is_question:
            return "executor"
        if is_question or any(word in lowered for word in lookup_words):
            # Only if something can actually answer it. Routing an unanswerable
            # question to the researcher is what produced a confident answer to
            # a question nobody could serve; `direct_response` reaches the
            # orchestrator's honest refusal instead.
            return "researcher" if StubProvider._servable(prompt) else "direct_response"
        return "direct_response"

    @staticmethod
    def _choose_tool(prompt: str) -> tuple[str, dict[str, Any]]:
        """Pick a tool for the request.

        Two competing concerns are balanced here:

        * proposing a tool the calling agent was never offered generates a
          guaranteed policy denial and pollutes the metrics with noise, so
          benign intents fall back to something the agent actually has; but
        * a genuinely destructive request must still surface as a proposal, so
          that the platform is seen to *refuse* it. Quietly downgrading "delete
          order 1001" into a harmless lookup would report success for a request
          that was never carried out -- worse than an honest refusal.
        """
        request = _user_text(prompt)
        lowered = request.lower()
        offered = _offered_tools(prompt)

        matched: str | None = None

        # Aggregate intents first. A question about the whole set must never be
        # served by a tool that reads one row: that is how "how many customers
        # do we have?" used to come back as a sentence about one customer.
        for tool, keywords in _AGGREGATE_KEYWORDS:
            if any(keyword in lowered for keyword in keywords):
                matched = tool
                break
        if matched is not None and (not offered or matched in offered):
            return matched, StubProvider._arguments_for(matched, request)
        matched = None

        # Plural "orders" alongside a customer reference is a history lookup,
        # not a single-order lookup. Without this, "which orders does customer
        # 2005 have?" matched the generic "order" keyword and answered about an
        # unrelated order -- confidently, and about the wrong customer.
        if "orders" in lowered and any(
            marker in lowered for marker in ("customer", "cus-", "cliente")
        ):
            matched = "list_customer_orders"
        elif (
            not _CUSTOMER_ID.search(request)
            and not _ORDER_ID.search(request)
            and not _TICKET_ID.search(request)
            and _person_name(request) is not None
            and not any(
                word in lowered
                for word in ("delete", "remove", "update", "change", "send", "email")
            )
        ):
            # The request names a person and carries no identifier, so the only
            # read that can answer it is the one that resolves a name.
            matched = "find_customer"
        else:
            for tool, keywords in _ACTION_KEYWORDS:
                if any(keyword in lowered for keyword in keywords):
                    matched = tool
                    break

        if matched is not None and (
            not offered or matched in offered or matched == "delete_record"
        ):
            return matched, StubProvider._arguments_for(matched, request)

        if not offered or "search" in offered:
            return "search", {"query": request[:200]}
        fallback = sorted(offered)[0]
        return fallback, StubProvider._arguments_for(fallback, request)

    @staticmethod
    def _identifier(pattern: re.Pattern[str], text: str) -> str | None:
        """The identifier the request actually carries, or nothing.

        There used to be a default here -- `ORD-1001`, `CUS-2001`, `TKT-4001`.
        It meant a question with no identifier still produced a lookup, so
        "which orders are delayed?" was answered with a confident sentence
        about one arbitrary order. A tool that needs an identifier the request
        never gave is not a tool that can answer it.
        """
        match = pattern.search(text)
        return match.group(1) if match else None

    @staticmethod
    def _arguments_for(tool: str, prompt: str) -> dict[str, Any]:
        if tool == "list_orders":
            lowered = prompt.lower()
            if any(
                word in lowered
                for word in ("delayed", "late", "overdue", "open", "atrasad", "aberto")
            ):
                return {"status": "open"}
            if "cancel" in lowered:
                return {"status": "cancelled"}
            return {}
        if tool in ("count_customers", "revenue_total", "open_tickets",
                    "business_overview"):
            return {}
        if tool == "top_customers":
            lowered = prompt.lower()
            by_orders = any(
                word in lowered
                for word in ("most orders", "mais pedidos", "order count")
            )
            return {"by": "orders"} if by_orders else {"by": "revenue"}
        if tool == "get_order":
            found = StubProvider._identifier(_ORDER_ID, prompt)
            return {"order_id": f"ORD-{found}"} if found else {}
        if tool == "find_customer":
            return {"name": _person_name(prompt) or prompt[:80]}
        if tool in ("get_customer", "list_customer_orders"):
            found = StubProvider._identifier(_CUSTOMER_ID, prompt)
            return {"customer_id": f"CUS-{found}"} if found else {}
        if tool == "get_ticket":
            found = StubProvider._identifier(_TICKET_ID, prompt)
            return {"ticket_id": f"TKT-{found}"} if found else {}
        if tool == "update_record":
            return {
                "record_id": f"ORD-{_first_group(_ORDER_ID, prompt, '1001')}",
                "field": "status",
                "value": "updated",
            }
        if tool == "delete_record":
            return {"record_id": f"ORD-{_first_group(_ORDER_ID, prompt, '1001')}"}
        if tool == "send_email":
            match = _EMAIL.search(prompt)
            return {
                "to": match.group(0) if match else "customer@example.com",
                "subject": "Update on your request",
                "body": "This is a simulated message generated by the demo platform.",
            }
        return {"query": prompt[:200]}

    # ----------------------------------------------------------------- dispatch

    def _payload_for(self, prompt: str, purpose: Purpose) -> str:
        if purpose is Purpose.ROUTE:
            return json.dumps({"route": self._choose_route(_user_text(prompt))})

        if purpose in (Purpose.PROPOSE_ACTION, Purpose.SELECT_TOOL):
            tool, arguments = self._choose_tool(prompt)
            return json.dumps({"tool": tool, "arguments": arguments})

        if purpose is Purpose.VALIDATE:
            lowered = prompt.lower()
            failed = any(
                marker in lowered
                for marker in ("status: error", "status: denied", "status: timeout", '"error"')
            )
            return json.dumps(
                {
                    "approved": not failed,
                    "reason": (
                        "Tool reported a failure status."
                        if failed
                        else "Result is well formed and consistent with the request."
                    ),
                }
            )

        if purpose is Purpose.JUDGE:
            # Fixed mid-high scores. The evaluator records judge_used=False for
            # the stub so these are never presented as real model judgements.
            return json.dumps(
                {
                    "relevance": 0.8,
                    "coherence": 0.8,
                    "usefulness": 0.8,
                    "reason": "Deterministic stub judge; no model was consulted.",
                }
            )

        if purpose is Purpose.RESEARCH:
            return (
                "Collected the available context for this request from the "
                "simulated back-office dataset."
            )

        return (
            "Request handled by the demo platform. No live language model was "
            "called; this text was produced by the deterministic stub provider."
        )

    def generate(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        system: str | None = None,
        response_schema: dict[str, Any] | None = None,
        temperature: float = 0.0,
        timeout: float | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        text = self._payload_for(prompt, purpose)
        if self._latency_ms:
            time.sleep(self._latency_ms / 1000.0)
        elapsed_ms = (time.perf_counter() - started) * 1000.0

        return LLMResponse(
            text=text,
            provider=self.name,
            model=self.model,
            purpose=purpose,
            input_tokens=estimate_tokens(prompt) + estimate_tokens(system or ""),
            output_tokens=estimate_tokens(text),
            latency_ms=elapsed_ms,
            finish_reason="stop",
            tokens_estimated=True,
        )

    def embed(
        self,
        text: str,
        *,
        task: EmbedTask = EmbedTask.QUERY,
        timeout: float | None = None,
    ) -> Embedding:
        """Refuse. The stub cannot embed, and must not pretend otherwise.

        Every other stub method returns a canned answer, because a canned route
        or a canned tool proposal is still a *real* decision the platform then
        acts on -- the orchestration being demonstrated is genuine.

        An embedding is different. A hash-derived or zero vector has no
        semantic structure, so similarities computed from it are noise wearing
        the costume of a measurement. Returning one would make STUB mode look
        like it was doing semantic search while it ranked documents at random,
        and every offline number produced from it would be a fabrication.

        So the stub refuses, loudly, and STUB mode uses lexical retrieval
        instead: worse than semantic search, honestly worse, and reproducible.
        """
        raise NotImplementedError(
            "the deterministic stub cannot produce embeddings; a fake vector "
            "would make offline retrieval look semantic while ranking at "
            "random. Use lexical retrieval in demo mode, or configure a real "
            "provider for semantic search."
        )
