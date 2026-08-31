"""Lexical retrieval over the knowledge base, using SQLite FTS5 and BM25.

What this replaces, and why
---------------------------
The previous implementation was a substring scan::

    terms = [t for t in query.lower().split() if len(t) > 2]
    hits = [doc for doc in KB_ARTICLES if any(term in doc["body"].lower() ...)]
    return {..., "results": hits[:3]}

Measured over 25 questions it scored 38% hit-rate@3 with an MRR of 0.27, and
the reasons were mechanical rather than subtle:

* **No ranking at all.** ``hits[:3]`` returns the first three matches in
  *declaration order*, so "Refund policy" -- which happens to be written first
  in the tuple -- was returned for 13 of 25 queries, including "What is the
  CEO's salary?".
* **No stopword handling.** ``"the"`` is longer than two characters, so it was
  a search term, and it appears in eight of the twelve articles. Any question
  containing "the" retrieved two thirds of the corpus.
* **Punctuation was never stripped.** ``"times?"`` matched nothing, so the last
  word of every question ending in a question mark was silently discarded.
* **Substring, not token, matching.** ``"you"`` matched "Tracking *your*
  order", which is the entire reason "Do you sell cars?" returned a result.

BM25 fixes all four as a side effect of being a real ranking function: rare
terms outweigh common ones, so ``"the"`` contributes almost nothing without
anyone maintaining a stopword list.

Configuration chosen by measurement
-----------------------------------
Both decisions below were made by running the same 25 questions against each
candidate, not by preference:

* ``tokenize='porter unicode61'`` -- Porter stemming scored 14/21 against
  unicode61's 13/21, with MRR 0.61 against 0.53. It earns its place; it was not
  adopted because stemming is conventional.
* Column weights ``title=2.0, body=1.0`` -- identical hit-rate to 1:1 but a
  better MRR (0.61 against 0.59), because titles in this corpus are close to
  the way a customer phrases the question. 5:1 measured the same as 2:1, so the
  smaller weighting is used.

The index is not a second source of truth
-----------------------------------------
It is derived, in memory, from ``KB_ARTICLES -> build_chunks() -> FTS5``, and
is stamped with the corpus fingerprint. If the corpus changes, the fingerprint
changes and the index is rebuilt rather than serving stale rows. Nothing is
persisted, so there is no file to fall out of step with the dataset.
"""

from __future__ import annotations

import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from typing import Protocol

from .model import Chunk, build_chunks, corpus_fingerprint

#: The most results a caller may ask for. A ceiling rather than a default:
#: retrieval output is interpolated into prompts and trace payloads downstream,
#: so an unbounded k is an unbounded prompt.
MAX_TOP_K = 5

#: What the tool returns when a caller does not say. Three, because that is
#: what the previous implementation returned and what the frozen 25-question
#: baseline was measured at -- changing it would make the comparison invalid.
DEFAULT_TOP_K = 3

#: Independent of ``SearchArgs.query``'s own 500-character cap. Defence in
#: depth: the retriever is a library and must not assume it was called through
#: the schema that validates for it.
MAX_QUERY_CHARS = 500

#: BM25 column weights, in table order: doc_id (unindexed), title, body.
_WEIGHT_DOC_ID = 0.0
_WEIGHT_TITLE = 2.0
_WEIGHT_BODY = 1.0

_TOKEN = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True, slots=True)
class Result:
    """One retrieved document, with the score that placed it."""

    doc_id: str
    title: str
    body: str
    #: Raw BM25. Negative by SQLite's convention, and *more* negative is more
    #: relevant. Deliberately not rescaled into a 0-1 "confidence": it is a
    #: ranking statistic about this corpus, not a probability that the answer
    #: is correct, and presenting it as the latter would be a lie with a
    #: decimal point in it.
    score: float
    rank: int


class Retriever(Protocol):
    """The shape every retrieval strategy offers.

    Deliberately minimal, and deliberately read-only: there is no verb here for
    adding, updating or deleting a document. A retriever that cannot express a
    write cannot be talked into one.
    """

    def retrieve(self, query: str, *, top_k: int = DEFAULT_TOP_K) -> tuple[Result, ...]:
        """Return the best matches for *query*, most relevant first."""
        ...


def normalize_terms(query: str) -> list[str]:
    """Reduce a user query to safe, matchable terms.

    Accents are folded the same way :func:`model.document_id` folds them, so
    "endereço" and "endereco" reach the same tokens.

    This is also the security boundary for FTS5's query language. ``MATCH``
    takes an expression, not a string -- ``NEAR``, ``AND``, ``NOT``, ``*``,
    ``^``, ``:`` and ``"`` are all operators, so passing raw user text would
    let a query rewrite itself, or simply crash with a syntax error on an
    apostrophe. Keeping only ``[a-z0-9]+`` means the terms cannot contain a
    quote and therefore cannot escape the quoting applied below; every operator
    survives only as a literal word.
    """
    folded = unicodedata.normalize("NFKD", query[:MAX_QUERY_CHARS])
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    return _TOKEN.findall(ascii_only.lower())


def _match_expression(terms: list[str]) -> str:
    """Build the FTS5 MATCH expression. Terms are quoted; see above."""
    return " OR ".join(f'"{term}"' for term in terms)


class LexicalRetriever:
    """BM25 retrieval over an in-memory FTS5 index of the knowledge base.

    The index is built once and reused. Queries are ``SELECT`` only -- after
    construction nothing in this class writes to the database, and the class
    exposes no way for a caller to.
    """

    def __init__(self, chunks: tuple[Chunk, ...] | None = None) -> None:
        self._chunks = build_chunks() if chunks is None else chunks
        self._fingerprint = corpus_fingerprint(self._chunks)
        # One connection shared across the gateway's worker threads, guarded by
        # a lock. Queries take microseconds, so serialising them costs nothing
        # measurable and avoids a connection pool nobody needs.
        self._lock = threading.Lock()
        self._connection = self._build(self._chunks)

    @property
    def fingerprint(self) -> str:
        """Fingerprint of the corpus this index was built from."""
        return self._fingerprint

    @property
    def document_count(self) -> int:
        return len(self._chunks)

    @staticmethod
    def _build(chunks: tuple[Chunk, ...]) -> sqlite3.Connection:
        connection = sqlite3.connect(":memory:", check_same_thread=False)
        connection.execute(
            "CREATE VIRTUAL TABLE kb USING fts5("
            "  doc_id UNINDEXED,"
            "  title,"
            "  body,"
            "  tokenize='porter unicode61'"
            ")"
        )
        connection.executemany(
            "INSERT INTO kb (doc_id, title, body) VALUES (?, ?, ?)",
            [(chunk.doc_id, chunk.title, chunk.body) for chunk in chunks],
        )
        connection.commit()
        return connection

    def retrieve(self, query: str, *, top_k: int = DEFAULT_TOP_K) -> tuple[Result, ...]:
        """Rank the corpus against *query* and return the best ``top_k``.

        Ordering is ``(score, doc_id)``: BM25 first, then the document
        identifier to break ties. The tie-break matters more than it looks --
        without it SQLite is free to return equally-scored rows in any order,
        and the previous implementation's worst property was that result order
        depended on where an article happened to sit in the source file.
        """
        k = max(1, min(int(top_k), MAX_TOP_K))
        terms = normalize_terms(query)
        if not terms:
            return ()

        with self._lock:
            rows = self._connection.execute(
                "SELECT doc_id, title, body,"
                "       bm25(kb, ?, ?, ?) AS score"
                "  FROM kb"
                " WHERE kb MATCH ?"
                " ORDER BY score ASC, doc_id ASC"
                " LIMIT ?",
                (
                    _WEIGHT_DOC_ID,
                    _WEIGHT_TITLE,
                    _WEIGHT_BODY,
                    _match_expression(terms),
                    k,
                ),
            ).fetchall()

        return tuple(
            Result(doc_id=row[0], title=row[1], body=row[2], score=float(row[3]), rank=i + 1)
            for i, row in enumerate(rows)
        )

    def total_matches(self, query: str) -> int:
        """How many documents matched at all, before ``top_k`` truncation."""
        terms = normalize_terms(query)
        if not terms:
            return 0
        with self._lock:
            row = self._connection.execute(
                "SELECT count(*) FROM kb WHERE kb MATCH ?", (_match_expression(terms),)
            ).fetchone()
        return int(row[0])


_retriever: LexicalRetriever | None = None
_retriever_lock = threading.Lock()


def default_retriever() -> LexicalRetriever:
    """The process-wide retriever, rebuilt when the corpus changes.

    Cached because building costs about 12 ms and querying costs microseconds,
    so rebuilding per call would make the index the expensive part of a search.

    Keyed on the corpus fingerprint rather than built once and trusted forever:
    the test suite swaps ``KB_ARTICLES`` in and out, and an index that outlived
    such a swap would answer from a corpus that no longer exists. That is
    exactly the "second source of truth" failure this design is meant to avoid,
    so the check is cheap and unconditional.
    """
    global _retriever
    with _retriever_lock:
        current = corpus_fingerprint(build_chunks())
        if _retriever is None or _retriever.fingerprint != current:
            _retriever = LexicalRetriever()
        return _retriever
