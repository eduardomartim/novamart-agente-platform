"""What the answer machinery does across the frozen 25 questions.

Read this file knowing what it can and cannot establish.

It runs the **real** BM25 retriever over the **real** corpus, then hands the
result to a provider whose output is scripted. So it measures the machinery:
does a fabricated citation survive, does an unsupported verdict become a
refusal, does a zero-document question cost a model call. Those are properties
of the code, and the code is what is being tested.

It measures **nothing** about answer quality. A compliant fake produces a 100%
grounded rate by construction; that number describes the fake. Whether a real
model honours the schema, cites the right documents, or writes a correct
answer is untested here and untestable without a live call.

The scripted providers are deliberately adversarial as often as they are
cooperative, because the interesting question is not "does it work when the
model behaves" but "what does it claim when the model does not".
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agent_platform.agent.answer import AnswerOutcome, documents_from_context
from agent_platform.agent.answerer import AnswererAgent
from agent_platform.agent.base import AgentDeps
from agent_platform.config import Settings
from agent_platform.cost.budget import BudgetGuard
from agent_platform.cost.tracker import CostTracker
from agent_platform.guardrails.policy import PolicyEngine
from agent_platform.llm.provider import Purpose
from agent_platform.llm.stub import StubProvider
from agent_platform.observability.tracing import Tracer
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.retrieval.lexical import LexicalRetriever
from agent_platform.tools.dataset import dataset_digest
from agent_platform.tools.gateway import ToolGateway
from agent_platform.tools.registry import default_registry

from .test_retrieval_quality import EVALUATION

DIGEST = "db512de8207f751e"

#: Measured, not assumed: BM25 returns nothing for these four. Two are the
#: terminology-mismatch failures 7C recorded; two are genuinely unanswerable.
#: Pinned because it is what makes "zero documents costs zero calls" a claim
#: about real questions rather than a hypothetical.
QUESTIONS_WITH_NO_DOCUMENTS = 4
QUESTIONS_WITH_DOCUMENTS = 21


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever()


class Scripted(StubProvider):
    """A provider whose RESPOND output is written by the test."""

    def __init__(self, script) -> None:
        super().__init__()
        self._script = script
        self.calls = 0

    def generate(self, prompt, **kwargs):
        response = super().generate(prompt, **kwargs)
        if kwargs.get("purpose") is Purpose.RESPOND:
            self.calls += 1
            object.__setattr__(response, "text", self._script(prompt))
        return response


def _documents_named_in(prompt: str) -> list[str]:
    """The identifiers the prompt actually offered, read back out of it.

    A cooperative model would cite these. Extracting them from the prompt
    rather than from the fixture keeps the fake honest: it can only name what
    it was really shown.
    """
    return [
        line.split("DOCUMENT ", 1)[1].strip()
        for line in prompt.splitlines()
        if line.startswith("DOCUMENT ")
    ]


def compliant(prompt: str) -> str:
    return json.dumps(
        {"answer": "Per the documents.", "supported": True,
         "sources": _documents_named_in(prompt)}
    )


def unsupported(prompt: str) -> str:
    return json.dumps({"answer": "The documents do not cover that.", "supported": False})


def only_fabrications(prompt: str) -> str:
    return json.dumps(
        {"answer": "Yes.", "supported": True, "sources": ["KB-invented", "KB-also-fake"]}
    )


def one_real_one_fake(prompt: str) -> str:
    real = _documents_named_in(prompt)[:1]
    return json.dumps(
        {"answer": "Yes.", "supported": True, "sources": [*real, "KB-invented"]}
    )


def prose(prompt: str) -> str:
    return "Refunds are available within 30 days of delivery."


def build(provider):
    repository = InMemoryRepository()
    registry = default_registry()
    gateway = ToolGateway(registry, PolicyEngine(registry))
    deps = AgentDeps(
        provider=provider,
        tracer=Tracer(repository, request_id="q", trace_id="q"),
        registry=registry,
        cost_tracker=CostTracker(
            repository,
            BudgetGuard(
                repository,
                daily_budget_usd=Decimal("1"),
                max_request_cost_usd=Decimal("1"),
            ),
        ),
        settings=Settings.from_env(load_dotenv_file=False),
        gateway=gateway,
    )
    return AnswererAgent(deps), gateway, repository


def context_for(retriever: LexicalRetriever, question: str) -> list[dict]:
    results = retriever.retrieve(question, top_k=3)
    return [
        {
            "source": "search",
            "arguments": {"query": question},
            "data": {
                "query": question,
                "result_count": len(results),
                "results": [
                    {"doc_id": r.doc_id, "title": r.title, "body": r.body,
                     "score": round(r.score, 4)}
                    for r in results
                ],
            },
        }
    ]


def sweep(retriever: LexicalRetriever, script):
    """Run all 25 questions through one scripted provider."""
    rows = []
    for question, gold, family in EVALUATION:
        context = context_for(retriever, question)
        retrieved = tuple(d.doc_id for d in documents_from_context(context))
        provider = Scripted(script)
        agent, gateway, _repository = build(provider)
        try:
            answer = agent.answer(question, context)
        finally:
            gateway.shutdown()
        rows.append(
            {
                "question": question,
                "gold": gold,
                "family": family,
                "retrieved": retrieved,
                "outcome": answer.outcome,
                "citations": answer.cited_ids,
                "calls": provider.calls,
            }
        )
    return rows


def count(rows, outcome) -> int:
    return sum(1 for r in rows if r["outcome"] is outcome)


# ============================================ retrieval reality behind the runs


def test_the_corpus_is_unchanged():
    assert dataset_digest() == DIGEST


def test_the_question_set_is_the_frozen_one():
    assert len(EVALUATION) == 25


def test_four_of_the_frozen_questions_retrieve_nothing(retriever):
    """Measured, and it matches the 7C baseline rather than being asserted.

    Two are terminology-mismatch failures BM25 cannot fix; two are genuinely
    unanswerable. This is what makes the zero-call claim below concrete.
    """
    empty = [
        q for q, _, _ in EVALUATION
        if not documents_from_context(context_for(retriever, q))
    ]
    assert len(empty) == QUESTIONS_WITH_NO_DOCUMENTS
    assert "I want my money back" in empty
    assert "Do you sell cars?" in empty


# ==================================================== 1. GROUNDED MACHINERY


def test_a_compliant_provider_grounds_every_answerable_question(retriever):
    """Machinery, not quality: the fake was written to comply, so a 21/21 here
    says the wiring works, and says nothing about a real model."""
    rows = sweep(retriever, compliant)

    assert count(rows, AnswerOutcome.GROUNDED) == QUESTIONS_WITH_DOCUMENTS
    assert count(rows, AnswerOutcome.INSUFFICIENT_EVIDENCE) == QUESTIONS_WITH_NO_DOCUMENTS
    assert count(rows, AnswerOutcome.UNVERIFIABLE) == 0


def test_every_grounded_answer_carries_at_least_one_citation(retriever):
    for row in sweep(retriever, compliant):
        if row["outcome"] is AnswerOutcome.GROUNDED:
            assert row["citations"], row["question"]


# =============================================== 2. UNSUPPORTED-CLAIM HANDLING


def test_a_model_reporting_unsupported_never_produces_a_grounded_answer(retriever):
    rows = sweep(retriever, unsupported)

    assert count(rows, AnswerOutcome.GROUNDED) == 0
    assert count(rows, AnswerOutcome.INSUFFICIENT_EVIDENCE) == 25


def test_an_unsupported_verdict_carries_no_citations(retriever):
    """Otherwise a refusal would arrive carrying evidence for the claim it
    declined to make."""
    for row in sweep(retriever, unsupported):
        assert row["citations"] == (), row["question"]


# ================================================== 3 & 4. CITATION VALIDITY


@pytest.mark.parametrize(
    "script", [compliant, one_real_one_fake, only_fabrications, unsupported, prose]
)
def test_no_citation_ever_names_an_unretrieved_document(retriever, script):
    """The machinery's central claim, measured across all 25 questions and
    every provider behaviour, cooperative or not."""
    for row in sweep(retriever, script):
        for citation in row["citations"]:
            assert citation in row["retrieved"], (
                f"{row['question']!r} cited {citation}, which was never retrieved"
            )


def test_a_wholly_fabricated_source_list_yields_unverifiable(retriever):
    rows = sweep(retriever, only_fabrications)

    assert count(rows, AnswerOutcome.GROUNDED) == 0
    assert count(rows, AnswerOutcome.UNVERIFIABLE) == QUESTIONS_WITH_DOCUMENTS
    assert all(row["citations"] == () for row in rows)


def test_a_fabrication_mixed_with_a_real_source_is_dropped(retriever):
    """The real half is kept, the invented half never reaches the answer."""
    rows = sweep(retriever, one_real_one_fake)

    assert count(rows, AnswerOutcome.GROUNDED) == QUESTIONS_WITH_DOCUMENTS
    for row in rows:
        assert "KB-invented" not in row["citations"], row["question"]
        if row["outcome"] is AnswerOutcome.GROUNDED:
            assert len(row["citations"]) == 1


def test_prose_is_never_dressed_as_a_grounded_answer(retriever):
    rows = sweep(retriever, prose)

    assert count(rows, AnswerOutcome.GROUNDED) == 0
    assert count(rows, AnswerOutcome.UNVERIFIABLE) == QUESTIONS_WITH_DOCUMENTS
    assert all(row["citations"] == () for row in rows)


# =============================================== 5. REFUSAL / ZERO-DOCUMENT


def test_a_question_retrieving_nothing_costs_no_model_call(retriever):
    """Refusing must be free. A refusal that spends a call spends the day's
    quota on questions nothing in the corpus can answer."""
    for row in sweep(retriever, compliant):
        if not row["retrieved"]:
            assert row["calls"] == 0, row["question"]
            assert row["outcome"] is AnswerOutcome.INSUFFICIENT_EVIDENCE


def test_the_model_is_consulted_exactly_once_per_answerable_question(retriever):
    rows = sweep(retriever, compliant)
    assert sum(row["calls"] for row in rows) == QUESTIONS_WITH_DOCUMENTS
    for row in rows:
        assert row["calls"] in (0, 1), row["question"]


# ================================================= 6. CONTRADICTORY DOCUMENTS


def test_a_contradiction_is_cited_on_both_sides_or_not_claimed(retriever):
    conflict = [
        {
            "source": "search",
            "data": {
                "query": "refunds",
                "result_count": 2,
                "results": [
                    {"doc_id": "KB-a", "title": "Refund window",
                     "body": "Refunds are permitted within 30 days."},
                    {"doc_id": "KB-b", "title": "Refund rule",
                     "body": "Refunds are never permitted."},
                ],
            },
        }
    ]
    agent, gateway, _ = build(Scripted(compliant))
    try:
        answer = agent.answer("What is the refund policy?", conflict)
    finally:
        gateway.shutdown()

    assert answer.grounded is False or set(answer.cited_ids) == {"KB-a", "KB-b"}


# ============================== 7, 8, 9. SECURITY / TOOLS / ACTION PRESERVED


def test_no_scripted_provider_can_make_the_answerer_reach_a_tool(retriever):
    """Even a provider that tries. The answerer has no tool to reach."""

    def hostile(prompt: str) -> str:
        return json.dumps(
            {
                "answer": "Calling delete_record now.",
                "supported": True,
                "sources": ["KB-refund-policy"],
                "tool": "delete_record",
                "action": "execute",
                "confirmed": True,
            }
        )

    for row in sweep(retriever, hostile):
        assert "delete_record" not in row["citations"]

    agent, gateway, repository = build(Scripted(hostile))
    try:
        agent.answer("What is the refund policy?", context_for(retriever, "refund policy"))
    finally:
        gateway.shutdown()

    kinds = {e.event_type for e in repository.events}
    assert "tool_call" not in kinds
    assert "action_proposed" not in kinds
    assert "confirmation_requested" not in kinds


def test_extra_keys_in_the_model_response_are_ignored(retriever):
    """`tool`, `action` and `confirmed` above are not part of the contract, so
    nothing reads them. Proven by the outcome being unaffected."""

    def noisy(prompt: str) -> str:
        payload = json.loads(compliant(prompt))
        payload.update({"tool": "delete_record", "capability": "delete", "role": "admin"})
        return json.dumps(payload)

    plain = sweep(retriever, compliant)
    noisy_rows = sweep(retriever, noisy)

    assert [r["outcome"] for r in plain] == [r["outcome"] for r in noisy_rows]
    assert [r["citations"] for r in plain] == [r["citations"] for r in noisy_rows]


def test_the_answerer_still_holds_nothing():
    from agent_platform.guardrails.authorization import capabilities_for
    from agent_platform.models import AgentName

    assert capabilities_for(AgentName.ANSWERER) == frozenset()
    assert default_registry().permitted_names(AgentName.ANSWERER) == ()


def test_the_evaluation_did_not_move_the_dataset():
    assert dataset_digest() == DIGEST
