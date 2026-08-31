"""The knowledge base as addressable, retrievable units.

Why this module exists at all
-----------------------------
``KB_ARTICLES`` is a tuple of ``{"title", "body"}`` dicts with **no identity**.
A document is addressed by its position in that tuple and by nothing else. That
is fine for a substring scan that returns whole dicts, and unworkable for an
index: an index stores references, and a reference into a positional list
silently repoints the moment anyone reorders, inserts or deletes an entry.

So this module assigns identity -- and assigns it *derived from the content*
rather than stored beside it.

Why the IDs are derived and not stored
--------------------------------------
``dataset_digest()`` hashes ``KB_ARTICLES`` itself. Adding an ``"id"`` key to
those dicts would change the digest, and the digest is a frozen guarantee that
the evaluation corpus has not moved under the measurements. Deriving the ID
from the title keeps the dataset byte-identical while still giving every
document a stable name.

The ID is a slug of the title, not an ordinal. ``KB-001`` would be stable only
as long as nobody touches the tuple's order, which is exactly the failure this
module exists to prevent. ``KB-refund-policy`` survives reordering, reads
sensibly in a trace, and collides loudly rather than quietly.

Why there is no chunking
------------------------
Measured over the real corpus: twelve documents, 92 to 165 characters, mean
134, 1 611 characters in total. The longest document is shorter than this
paragraph. Splitting a 22-word article yields fragments shorter than the
questions asked of them, so **one document is one chunk**, and the ``ordinal``
field exists only so that the interface does not have to change if that stops
being true. :func:`oversized_documents` is the tripwire: it is asserted empty
by the test suite, so a corpus that outgrows this assumption fails a test
instead of silently retrieving truncated text.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Final

#: Prefix for every knowledge-base document identifier.
ID_PREFIX: Final[str] = "KB-"

#: The length beyond which a single document should no longer be treated as one
#: chunk. Set with deliberate headroom: the longest real document is 165
#: characters, so this is roughly six times the observed maximum. It is a
#: tripwire for a future dataset, not a limit anything trims against today.
CHUNK_MAX_CHARS: Final[int] = 1_000


@dataclass(frozen=True, slots=True)
class Chunk:
    """One retrievable unit: today, one whole knowledge-base article."""

    doc_id: str
    title: str
    body: str
    #: Position within its source document. Always 0 while one document is one
    #: chunk; present so that ceasing to be true is not a breaking change.
    ordinal: int = 0

    @property
    def embed_text(self) -> str:
        """The text an embedding is computed over.

        Title and body together, because the titles carry real signal in this
        corpus -- "Damaged items" and "Address changes" are close to the way a
        customer would phrase the question, in a way their bodies are not.
        """
        return f"{self.title}\n{self.body}"


def _slug(title: str) -> str:
    """Lowercase ASCII words joined by hyphens."""
    folded = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii")
    return "-".join(re.findall(r"[a-z0-9]+", folded.lower()))


def document_id(title: str) -> str:
    """The stable identifier for the article called *title*.

    A pure function of the title, so it can be recomputed anywhere -- by the
    index builder, by the retriever, by a test -- without a lookup table to
    fall out of step.
    """
    slug = _slug(title)
    if not slug:
        raise ValueError(f"title {title!r} produces an empty identifier")
    return f"{ID_PREFIX}{slug}"


def _corpus() -> tuple[dict[str, Any], ...]:
    """The knowledge base, resolved on call rather than at import.

    Imported inside the function on purpose. ``tools.fake_tools`` reaches this
    package for the retriever, so a module-level ``from ..tools.dataset import
    KB_ARTICLES`` closes a cycle: importing ``agent_platform.retrieval`` first
    lands in a partially initialised ``tools`` package and fails. Resolving
    late keeps the dependency one-directional -- retrieval knows nothing about
    the tools package until something actually asks it for the corpus.
    """
    from ..tools.dataset import KB_ARTICLES

    return KB_ARTICLES


def build_chunks(articles: tuple[dict[str, Any], ...] | None = None) -> tuple[Chunk, ...]:
    """Turn the corpus into chunks, ordered by identifier.

    Sorted by ``doc_id`` rather than by tuple position, so the corpus order is
    a property of the content and not of the source file. Reordering
    ``KB_ARTICLES`` therefore cannot change the index, the fingerprint, or any
    tie-break that falls back to document order.
    """
    source = _corpus() if articles is None else articles
    chunks: list[Chunk] = []
    seen: dict[str, str] = {}

    for article in source:
        title = str(article["title"])
        body = str(article["body"])
        if not title.strip() or not body.strip():
            raise ValueError(f"article {title!r} has an empty title or body")

        doc_id = document_id(title)
        if doc_id in seen:
            raise ValueError(
                f"identifier {doc_id!r} is claimed by both {seen[doc_id]!r} and {title!r}; "
                "two articles cannot share a slug"
            )
        seen[doc_id] = title
        chunks.append(Chunk(doc_id=doc_id, title=title, body=body))

    return tuple(sorted(chunks, key=lambda chunk: chunk.doc_id))


def oversized_documents(
    articles: tuple[dict[str, Any], ...] | None = None,
    *,
    limit: int = CHUNK_MAX_CHARS,
) -> tuple[str, ...]:
    """Titles of documents too long to remain a single chunk.

    Empty today, and asserted empty by the suite. When it stops being empty,
    the one-document-one-chunk decision needs revisiting -- which is a design
    decision, so it should surface as a failing test and not as a silent
    truncation inside a retriever.
    """
    return tuple(
        str(article["title"])
        for article in (_corpus() if articles is None else articles)
        if len(f"{article['title']}\n{article['body']}") > limit
    )


def corpus_fingerprint(chunks: tuple[Chunk, ...]) -> str:
    """Content hash of a chunk set, used to stamp a built index.

    An index is a derived artefact that outlives the process which built it. It
    must be able to prove it was built from *this* corpus, so that a stale
    index is refused rather than served. Order-independent by construction,
    since :func:`build_chunks` sorts.
    """
    payload = "\n".join(
        f"{chunk.doc_id}\x1f{chunk.ordinal}\x1f{chunk.embed_text}"
        for chunk in sorted(chunks, key=lambda chunk: (chunk.doc_id, chunk.ordinal))
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
