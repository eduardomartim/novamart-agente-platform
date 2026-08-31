"""The shape of a grounded answer, and the rules that keep it honest.

Types only. Nothing here calls a model, reads a corpus or touches a provider --
that belongs to the agent built on top of it. Keeping the contract separate is
what makes the invariants below testable without a network, a corpus or a
prompt.

Five outcomes, deliberately distinct
------------------------------------
A retrieval-backed answer can end in five genuinely different ways, and
collapsing any of them into another is a lie of a specific kind:

``GROUNDED``
    The model returned the structured answer it was asked for, and its support
    was checked against the documents actually retrieved.
``INSUFFICIENT_EVIDENCE``
    Retrieval ran, and what came back does not answer the question. This is a
    *result*, not a failure.
``UNVERIFIABLE``
    The model answered, but not in the structure the check requires -- prose
    instead of the schema, or citing only documents that were never retrieved.
    There may well be a correct answer inside that text. The system simply
    cannot say so, and refuses to imply otherwise.
``PROVIDER_ERROR``
    The model could not be reached or produced nothing usable.
``RESOURCE_BLOCKED``
    A ceiling stopped the call -- the per-request LLM budget, the daily
    provider budget, an open circuit.

The distinctions matter individually. "I could not find that in the documents"
when the truth is "the provider was down" is a confident statement about the
corpus derived from an outage. "Here is a grounded answer" when the truth is
"the model wrote prose we could not check" is a claim of verification that
never happened. Neither is available to be made by accident: the invariants
below make the contradictory states unconstructible.

The two error outcomes are **never built inside the answering agent by
swallowing an exception**: the existing taxonomy propagates, and only a caller
that has caught a specific exception may build them, via :func:`provider_error`
and :func:`resource_blocked`.

Citations are derived, never accepted
-------------------------------------
A model asked to cite its sources will cheerfully cite a document that does not
exist. :func:`validate_citations` intersects whatever was claimed with what was
actually retrieved, so a fabricated identifier is dropped by code rather than
discouraged by a prompt. Nothing downstream ever sees it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class AnswerOutcome(StrEnum):
    """How an answer attempt ended. See the module docstring."""

    GROUNDED = "grounded"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNVERIFIABLE = "unverifiable"
    PROVIDER_ERROR = "provider_error"
    RESOURCE_BLOCKED = "resource_blocked"


@dataclass(frozen=True, slots=True)
class Citation:
    """One document offered as support for an answer.

    Both fields are copied from a retrieved document by code. Neither is taken
    from model output, which is the point: a citation is a claim about what the
    system read, and only the system knows that.
    """

    doc_id: str
    title: str


@dataclass(frozen=True, slots=True)
class RetrievedDocument:
    """A document as the answering layer sees it: untrusted text with a name."""

    doc_id: str
    title: str
    body: str
    #: The tool that produced it, for tracing. Never an authorisation input.
    source: str = "unknown"


@dataclass(frozen=True, slots=True)
class Answer:
    """A proposed answer plus the evidence behind it.

    Invariants are enforced here rather than trusted to callers, because every
    one of them is a way the answer could quietly overstate itself:

    * a ``GROUNDED`` answer must cite at least one document -- "grounded" with
      nothing to point at is exactly the claim this type exists to prevent;
    * a ``GROUNDED`` answer must carry ``metadata["structured"] is True``. The
      support of an unstructured answer was never checked, so calling it
      grounded asserts a verification that did not happen. Enforcing it here
      makes that state unconstructible rather than merely discouraged -- it was
      briefly reachable in 7F.5, and a comment would not have stopped it coming
      back;
    * any other outcome must carry no citations, so a refusal cannot arrive
      wearing the costume of evidence;
    * any other outcome must give a reason, so "no answer" is never silent.
    """

    text: str
    outcome: AnswerOutcome
    citations: tuple[Citation, ...] = ()
    reason: str | None = None
    #: Non-authoritative detail for traces and tests: document count, whether a
    #: model was consulted. Never read to make a decision.
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome is AnswerOutcome.GROUNDED:
            if not self.citations:
                raise ValueError(
                    "a grounded answer must cite at least one retrieved document"
                )
            if not self.text.strip():
                raise ValueError("a grounded answer must have text")
            if self.metadata.get("structured") is not True:
                raise ValueError(
                    "a grounded answer must come from a structured response; an "
                    "unstructured answer was never verified, so calling it "
                    "grounded claims a check that did not happen -- use "
                    "AnswerOutcome.UNVERIFIABLE instead"
                )
        else:
            if self.citations:
                raise ValueError(
                    f"an answer with outcome {self.outcome.value!r} must not carry "
                    "citations; only a grounded answer has evidence behind it"
                )
            if not (self.reason or "").strip():
                raise ValueError(
                    f"an answer with outcome {self.outcome.value!r} must state why"
                )

    @property
    def grounded(self) -> bool:
        return self.outcome is AnswerOutcome.GROUNDED

    @property
    def cited_ids(self) -> tuple[str, ...]:
        return tuple(citation.doc_id for citation in self.citations)


# --------------------------------------------------------------- constructors


def insufficient_evidence(reason: str, *, text: str | None = None, **metadata: Any) -> Answer:
    """The documents do not answer the question. A result, not an error."""
    return Answer(
        text=text
        or (
            "I could not find enough information in the available documents to "
            "answer that."
        ),
        outcome=AnswerOutcome.INSUFFICIENT_EVIDENCE,
        reason=reason,
        metadata=dict(metadata),
    )


def unverifiable(reason: str, *, text: str | None = None, **metadata: Any) -> Answer:
    """The model answered, but not in a form whose support can be checked.

    Not an error -- the provider worked. Not an absence of evidence -- documents
    were retrieved. The specific thing that is missing is *verification*, so
    that is what the outcome says.

    The model's text may be passed in and is kept for the trace, but it carries
    no citations: attributing an unchecked answer to the documents that
    happened to be nearby would manufacture the very evidence this outcome
    exists to say is absent.
    """
    return Answer(
        text=text
        or (
            "An answer was produced but could not be verified against the "
            "retrieved documents, so it is not being presented as one."
        ),
        outcome=AnswerOutcome.UNVERIFIABLE,
        reason=reason,
        metadata=dict(metadata),
    )


def provider_error(reason: str, **metadata: Any) -> Answer:
    """The model could not be reached or returned nothing usable.

    Only for a caller that has caught a specific provider exception. Never call
    this from inside a broad ``except``: that is how an outage becomes a
    confident statement about the knowledge base.
    """
    return Answer(
        text="The answering service is unavailable, so no answer was produced.",
        outcome=AnswerOutcome.PROVIDER_ERROR,
        reason=reason,
        metadata=dict(metadata),
    )


def resource_blocked(reason: str, **metadata: Any) -> Answer:
    """A ceiling stopped the call before it happened."""
    return Answer(
        text="No answer was generated because a usage limit was reached.",
        outcome=AnswerOutcome.RESOURCE_BLOCKED,
        reason=reason,
        metadata=dict(metadata),
    )


# ------------------------------------------------------------------- parsing


def documents_from_context(context: list[dict[str, Any]]) -> tuple[RetrievedDocument, ...]:
    """Pull retrieved documents out of gathered context.

    Shape-detected rather than keyed on the tool name, matching
    ``base._retrieved_documents``: the caller passes whatever the gateway
    returned, and the wrapper keys differ between a researcher context item and
    a raw tool result.

    Only ``doc_id``, ``title`` and ``body`` are read. An ``authority`` or
    ``policy`` key travelling alongside them is not so much rejected as never
    looked at -- there is no code path that would consult it.
    """
    documents: list[RetrievedDocument] = []

    for item in context:
        source = str(item.get("source") or item.get("tool") or "unknown")
        data = item.get("data", item.get("output"))
        if not isinstance(data, dict):
            continue
        results = data.get("results")
        if not isinstance(results, list):
            continue
        for entry in results:
            if not isinstance(entry, dict):
                continue
            if "title" not in entry or "body" not in entry:
                continue
            documents.append(
                RetrievedDocument(
                    doc_id=str(entry.get("doc_id") or ""),
                    title=str(entry["title"]),
                    body=str(entry["body"]),
                    source=source,
                )
            )

    return tuple(documents)


def validate_citations(
    claimed: object, documents: tuple[RetrievedDocument, ...]
) -> tuple[Citation, ...]:
    """Keep only the claimed identifiers that name a genuinely retrieved document.

    *claimed* is model output and is treated as such: it may be a list, a
    string, nested, or nonsense. Anything that does not match a retrieved
    ``doc_id`` exactly is dropped, and the title is taken from the retrieved
    document rather than from what the model said it was -- otherwise a model
    could cite a real identifier under an invented title.

    Order follows retrieval order, not the order claimed, so the output cannot
    be steered either.
    """
    by_id = {document.doc_id: document for document in documents if document.doc_id}

    if isinstance(claimed, str):
        names = [claimed]
    elif isinstance(claimed, list | tuple | set):
        names = [str(entry) for entry in claimed if isinstance(entry, str | int)]
    else:
        names = []

    wanted = {name.strip() for name in names}
    return tuple(
        Citation(doc_id=document.doc_id, title=document.title)
        for document in documents
        if document.doc_id and document.doc_id in wanted and document.doc_id in by_id
    )


def cite_all(documents: tuple[RetrievedDocument, ...]) -> tuple[Citation, ...]:
    """Cite every retrieved document, in retrieval order.

    The conservative fallback for when a model names no sources but the answer
    is otherwise supported: attributing the answer to everything that was read
    is honest, if imprecise. Inventing a narrower attribution would not be.
    """
    return tuple(
        Citation(doc_id=document.doc_id, title=document.title)
        for document in documents
        if document.doc_id
    )
