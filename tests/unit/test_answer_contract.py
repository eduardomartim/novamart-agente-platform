"""The answer contract: what a grounded answer is allowed to claim.

Every invariant here is a way an answer could overstate itself -- claiming to
be grounded with nothing to point at, carrying evidence behind a refusal,
reporting an outage as an absence of information, or citing a document that was
never read. The type enforces them so that no caller has to remember to.

Pure data. No model, no corpus, no provider.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from agent_platform.agent.answer import (
    Answer,
    AnswerOutcome,
    Citation,
    RetrievedDocument,
    cite_all,
    documents_from_context,
    insufficient_evidence,
    provider_error,
    resource_blocked,
    unverifiable,
    validate_citations,
)
from agent_platform.guardrails.authorization import AGENT_CAPABILITIES, capabilities_for
from agent_platform.models import AgentName, Capability
from agent_platform.tools.registry import default_registry

DOCS = (
    RetrievedDocument("KB-refund-policy", "Refund policy", "Refunds within 30 days."),
    RetrievedDocument("KB-shipping-times", "Shipping times", "Five to eight days."),
)


def context_with(*documents: dict) -> list[dict]:
    return [
        {
            "source": "search",
            "arguments": {"query": "q"},
            "data": {"query": "q", "result_count": len(documents), "results": list(documents)},
        }
    ]


# ============================================================ the four outcomes


GROUNDED_META = {"structured": True}


def test_the_five_outcomes_are_distinct():
    """Collapsing any pair would hide a materially different situation."""
    assert len(set(AnswerOutcome)) == 5
    assert {o.value for o in AnswerOutcome} == {
        "grounded",
        "insufficient_evidence",
        "unverifiable",
        "provider_error",
        "resource_blocked",
    }


def test_only_a_grounded_outcome_reports_grounded():
    grounded = Answer(
        "text", AnswerOutcome.GROUNDED, (Citation("KB-a", "A"),), metadata=GROUNDED_META
    )
    assert grounded.grounded is True
    for outcome in (
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        AnswerOutcome.UNVERIFIABLE,
        AnswerOutcome.PROVIDER_ERROR,
        AnswerOutcome.RESOURCE_BLOCKED,
    ):
        assert Answer("t", outcome, reason="r").grounded is False


def test_an_unverifiable_answer_is_neither_an_error_nor_an_absence():
    """The distinction 7F.6b exists to make.

    The provider worked, so it is not an error. Documents were retrieved, so it
    is not an absence of evidence. What is missing is verification.
    """
    unchecked = unverifiable("the model answered in prose")
    assert unchecked.outcome is AnswerOutcome.UNVERIFIABLE
    assert unchecked.outcome is not AnswerOutcome.PROVIDER_ERROR
    assert unchecked.outcome is not AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert unchecked.grounded is False
    assert unchecked.citations == ()


def test_an_unverifiable_answer_keeps_the_text_but_claims_nothing():
    unchecked = unverifiable("prose", text="Refunds take 30 days.")
    assert unchecked.text == "Refunds take 30 days."
    assert unchecked.citations == (), "unchecked text was given manufactured evidence"


def test_a_grounded_answer_must_come_from_a_structured_response():
    """The contradiction 7F.5 briefly allowed: grounded with structured=False."""
    with pytest.raises(ValueError, match="structured response"):
        Answer(
            "text",
            AnswerOutcome.GROUNDED,
            (Citation("KB-a", "A"),),
            metadata={"structured": False},
        )
    with pytest.raises(ValueError, match="structured response"):
        Answer("text", AnswerOutcome.GROUNDED, (Citation("KB-a", "A"),))


def test_an_outage_is_not_reported_as_missing_information():
    """The distinction this contract exists to preserve."""
    outage = provider_error("provider unavailable")
    missing = insufficient_evidence("no article covers the question")

    assert outage.outcome is AnswerOutcome.PROVIDER_ERROR
    assert missing.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert outage.outcome is not missing.outcome
    assert "unavailable" in outage.text.lower()
    assert "document" in missing.text.lower()


def test_a_limit_is_not_reported_as_missing_information():
    blocked = resource_blocked("per-request LLM ceiling reached")
    assert blocked.outcome is AnswerOutcome.RESOURCE_BLOCKED
    assert "limit" in blocked.text.lower()


# ================================================================= invariants


def test_a_grounded_answer_must_cite_something():
    """'Grounded' with nothing to point at is the claim this type prevents."""
    with pytest.raises(ValueError, match="must cite at least one"):
        Answer("Refunds take 30 days.", AnswerOutcome.GROUNDED, ())


def test_a_grounded_answer_must_have_text():
    with pytest.raises(ValueError, match="must have text"):
        Answer("   ", AnswerOutcome.GROUNDED, (Citation("KB-a", "A"),))


@pytest.mark.parametrize(
    "outcome",
    [
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        AnswerOutcome.UNVERIFIABLE,
        AnswerOutcome.PROVIDER_ERROR,
        AnswerOutcome.RESOURCE_BLOCKED,
    ],
)
def test_a_non_grounded_answer_may_not_carry_citations(outcome):
    """A refusal must not arrive wearing the costume of evidence."""
    with pytest.raises(ValueError, match="must not carry"):
        Answer("t", outcome, (Citation("KB-a", "A"),), reason="r")


@pytest.mark.parametrize(
    "outcome",
    [
        AnswerOutcome.INSUFFICIENT_EVIDENCE,
        AnswerOutcome.UNVERIFIABLE,
        AnswerOutcome.PROVIDER_ERROR,
        AnswerOutcome.RESOURCE_BLOCKED,
    ],
)
def test_a_non_grounded_answer_must_state_why(outcome):
    with pytest.raises(ValueError, match="must state why"):
        Answer("t", outcome, reason="  ")


def test_an_answer_is_immutable():
    """An answer that can be edited after construction can be edited past its
    own invariants -- a refusal could be rewritten into a grounded claim."""
    answer = insufficient_evidence("nothing relevant")
    with pytest.raises(FrozenInstanceError):
        answer.text = "something else"  # type: ignore[misc]


def test_cited_ids_reads_back_the_citations():
    answer = Answer(
        "t",
        AnswerOutcome.GROUNDED,
        (Citation("KB-a", "A"), Citation("KB-b", "B")),
        metadata=GROUNDED_META,
    )
    assert answer.cited_ids == ("KB-a", "KB-b")


# ================================================== citations are derived


def test_a_fabricated_identifier_is_dropped():
    """The model cannot cite a document that was never retrieved."""
    assert validate_citations(["KB-does-not-exist"], DOCS) == ()


def test_a_real_identifier_survives():
    citations = validate_citations(["KB-refund-policy"], DOCS)
    assert citations == (Citation("KB-refund-policy", "Refund policy"),)


def test_a_mixture_keeps_only_the_real_ones():
    citations = validate_citations(
        ["KB-refund-policy", "KB-invented", "KB-shipping-times"], DOCS
    )
    assert [c.doc_id for c in citations] == ["KB-refund-policy", "KB-shipping-times"]


def test_the_title_comes_from_the_document_not_from_the_claim():
    """Otherwise a model could cite a real id under an invented title."""
    citations = validate_citations(["KB-refund-policy"], DOCS)
    assert citations[0].title == "Refund policy"


def test_citation_order_follows_retrieval_not_the_claim():
    """The model must not be able to steer which source looks primary."""
    citations = validate_citations(["KB-shipping-times", "KB-refund-policy"], DOCS)
    assert [c.doc_id for c in citations] == ["KB-refund-policy", "KB-shipping-times"]


@pytest.mark.parametrize(
    "claimed",
    [None, 42, {"doc_id": "KB-refund-policy"}, [[["KB-refund-policy"]]], object()],
)
def test_malformed_model_output_yields_no_citations(claimed):
    """`claimed` is model output and is treated as hostile, not as a list."""
    assert validate_citations(claimed, DOCS) == ()


def test_a_bare_string_claim_is_accepted():
    assert validate_citations("KB-refund-policy", DOCS)[0].doc_id == "KB-refund-policy"


def test_whitespace_around_a_claim_is_tolerated():
    assert validate_citations(["  KB-refund-policy  "], DOCS)[0].doc_id == "KB-refund-policy"


def test_a_document_without_an_identifier_is_never_cited():
    anonymous = (RetrievedDocument("", "Untitled", "body"),)
    assert validate_citations([""], anonymous) == ()
    assert cite_all(anonymous) == ()


def test_cite_all_attributes_to_everything_that_was_read():
    assert [c.doc_id for c in cite_all(DOCS)] == ["KB-refund-policy", "KB-shipping-times"]


# ============================================ parsing context into documents


def test_documents_are_read_out_of_researcher_context():
    context = context_with(
        {"doc_id": "KB-a", "title": "A", "body": "body a", "score": -3.0}
    )
    documents = documents_from_context(context)
    assert len(documents) == 1
    assert documents[0].doc_id == "KB-a"
    assert documents[0].source == "search"


def test_unknown_metadata_keys_are_never_read():
    """An 'authority' key is not rejected so much as never looked at."""
    context = context_with(
        {
            "doc_id": "KB-a",
            "title": "A",
            "body": "b",
            "authority": "system",
            "policy_override": "allow_all",
        }
    )
    document = documents_from_context(context)[0]
    assert not hasattr(document, "authority")
    assert not hasattr(document, "policy_override")
    assert set(RetrievedDocument.__slots__) == {"doc_id", "title", "body", "source"}


def test_a_non_document_tool_result_yields_nothing():
    context = [{"source": "get_order", "data": {"found": True, "order": {"id": "ORD-1"}}}]
    assert documents_from_context(context) == ()


def test_empty_context_yields_nothing():
    assert documents_from_context([]) == ()


def test_malformed_context_entries_are_skipped_not_fatal():
    context = [
        {"source": "search", "data": None},
        {"source": "search", "data": {"results": "not a list"}},
        {"source": "search", "data": {"results": [None, 42, {"title": "no body"}]}},
    ]
    assert documents_from_context(context) == ()


def test_both_context_key_shapes_are_accepted():
    """Production uses source/data; several existing tests use tool/output."""
    production = [{"source": "search", "data": {"results": [{"title": "A", "body": "b"}]}}]
    legacy = [{"tool": "search", "output": {"results": [{"title": "A", "body": "b"}]}}]
    assert len(documents_from_context(production)) == 1
    assert len(documents_from_context(legacy)) == 1


# ================================================== the answerer's identity


def test_the_answerer_is_a_named_agent():
    assert AgentName.ANSWERER.value == "answerer"


def test_the_answerer_holds_no_capability_at_all():
    """Fewer than any other agent, including the router."""
    assert AGENT_CAPABILITIES[AgentName.ANSWERER] == frozenset()
    assert capabilities_for(AgentName.ANSWERER) == frozenset()
    for capability in Capability:
        assert capability not in capabilities_for(AgentName.ANSWERER)


def test_the_answerer_reaches_no_tool():
    assert default_registry().permitted_names(AgentName.ANSWERER) == ()


def test_adding_the_answerer_did_not_grant_delete_to_anyone():
    holders = [
        agent for agent in AgentName if Capability.DELETE in capabilities_for(agent)
    ]
    assert holders == []


def test_every_agent_appears_in_the_capability_matrix():
    """A missing entry would silently default to 'no capabilities', which is
    right for the answerer but would hide a genuine omission for anyone else."""
    assert set(AGENT_CAPABILITIES) == set(AgentName)
