"""Which retriever answers a search, and how the provider reaches it.

Two strategies, chosen by what is available rather than by configuration:

* **lexical** -- BM25 over FTS5. No provider, no network, fully deterministic.
  This is what demo mode runs, and what the entire offline suite exercises.
* **hybrid** -- BM25 fused with exact cosine over the persisted vector index.
  Needs one embedding call per uncached query, so it exists only where a
  provider capable of embedding has been bound to the request.

Why a ContextVar
----------------
The search tool is invoked by the gateway as ``tool.handler(**arguments)``,
where the arguments are the validated schema fields and nothing else. The tools
layer therefore has no route to a provider, and giving it one would mean
changing ``ToolHandler``, ``ToolDefinition`` or the registry -- all of which are
contracts this project deliberately keeps still.

``tools/execution.py`` already solved the identical problem for the gateway
token: a ``ContextVar`` scoped to the dynamic extent of a request, reset in a
``finally``. The same mechanism is used here for the same reason. Being a
``ContextVar`` rather than a module global matters -- the gateway runs handlers
on a thread pool, and two concurrent requests must not see each other's
provider.

Why demo mode simply binds nothing
----------------------------------
The strategy does not ask a provider whether it can embed, and does not catch
``NotImplementedError`` to find out. The composition root already knows: in
demo mode it binds no provider, the ContextVar stays empty, and lexical
retrieval is selected because there is nothing else to select. Capability is
expressed by what was wired, not by probing.

Why a failing embedding is not quietly downgraded
-------------------------------------------------
If the embedding call fails, this module lets the exception out. It does *not*
fall back to lexical and carry on: the search result contract has no field that
could say "these results are degraded", and adding one would change a contract.
Silently returning lexical results as though nothing happened is the class of
lie 7G.0 was written to remove. The exception reaches the gateway, becomes a
tool error, and the answer node reports it as an infrastructure failure rather
than as an absence of evidence.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import TYPE_CHECKING, Final

from .embedding_cache import QueryEmbeddingCache
from .hybrid import fuse
from .index import IndexIntegrityError, VectorIndex
from .lexical import DEFAULT_TOP_K, MAX_TOP_K, Result, default_retriever, normalize_terms
from .model import build_chunks, corpus_fingerprint

if TYPE_CHECKING:  # pragma: no cover - typing only
    from ..llm.provider import LLMProvider

#: Minimum cosine similarity for a document to count as evidence.
#:
#: **Calibrated, not validated.** Measured in 7F.1 on the frozen 25-question
#: set: the lowest top-1 cosine among answerable questions was 0.6875, the
#: highest among unanswerable was 0.6533, giving a clean separating window of
#: (0.6533, 0.6875] with zero overlap. 0.67 is its midpoint.
#:
#: That calibration rests on **four** unanswerable questions. Four points is
#: enough to show a gap exists on this corpus and nowhere near enough to call
#: the number validated, universal, or production-grade. A fifth unanswerable
#: question landing at 0.70 would break it.
#:
#: Applied to the *cosine component only*. Never to the fused score: ``fuse``
#: min-max normalises within each result set, so a lone candidate always scores
#: 1.0 and absolute scale is destroyed by construction. 7F.1 measured that the
#: fused score does not separate the two populations at all.
MIN_COSINE_SIMILARITY: Final[float] = 0.67

#: How many candidates each ranker contributes before fusion. Larger than the
#: final ``top_k`` so a document ranked well by one ranker and poorly by the
#: other still reaches the fusion step.
CANDIDATES: Final[int] = MAX_TOP_K

#: Environment variable naming the persisted vector index explicitly.
#:
#: This is the mechanism a deployment uses. Guessing is a fallback for
#: development, not a deployment strategy: an operator who mounts the index
#: somewhere should say so rather than hope a heuristic finds it.
INDEX_PATH_ENV: Final[str] = "KB_VECTOR_INDEX_PATH"


def _package_index_path() -> Path:
    """The index shipped beside this module, if it was packaged that way."""
    return Path(__file__).resolve().parent / "kb_vectors.db"


def _checkout_index_path() -> Path:
    """``<repo>/data/kb_vectors.db`` for a source checkout.

    ``parents[3]`` walks ``retrieval -> agent_platform -> src -> <repo>``. That
    is correct in a checkout and meaningless once the package is installed,
    where it climbs out of ``site-packages`` entirely -- which is exactly the
    bug this function is one candidate of, rather than the whole answer.
    """
    here = Path(__file__).resolve()
    parents = here.parents
    if len(parents) < 4:  # pragma: no cover - only on pathologically short paths
        return here.parent / "kb_vectors.db"
    return parents[3] / "data" / "kb_vectors.db"


def resolve_index_path() -> Path:
    """Locate the persisted vector index.

    Ordered, and deliberately free of any dependence on the current working
    directory -- a retrieval index that moves when the process is started from
    a different folder is a source of results that change for no stated reason.

    1. ``KB_VECTOR_INDEX_PATH`` if set. Explicit configuration always wins.
    2. Beside the installed package, for a deployment that ships the index as
       package data.
    3. ``<repo>/data/kb_vectors.db``, the source-checkout layout.

    When none exists the checkout path is returned unchanged, so a missing
    index still fails as :class:`IndexIntegrityError` naming the familiar
    location rather than something the reader has never seen.
    """
    configured = os.getenv(INDEX_PATH_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()

    checkout = _checkout_index_path()
    for candidate in (_package_index_path(), checkout):
        if candidate.exists():
            return candidate
    return checkout


#: Resolved once at import, and still a module attribute so tests can replace
#: it. ``_load_index`` reads it through the module global on every call.
DEFAULT_INDEX_PATH: Final[Path] = resolve_index_path()

_PROVIDER: ContextVar[LLMProvider | None] = ContextVar(
    "agent_platform_retrieval_provider", default=None
)

_cache = QueryEmbeddingCache()
_index: VectorIndex | None = None
_index_lock = threading.Lock()


@contextmanager
def retrieval_provider(provider: LLMProvider | None) -> Iterator[None]:
    """Bind *provider* for the dynamic extent of a request.

    Reset in a ``finally`` so an exception cannot leave a provider bound to a
    context that has moved on. Binding ``None`` is meaningful and is what demo
    mode does: it selects lexical retrieval by leaving nothing to select.
    """
    token = _PROVIDER.set(provider)
    try:
        yield
    finally:
        _PROVIDER.reset(token)


def active_provider() -> LLMProvider | None:
    """The provider bound to this request, if any."""
    return _PROVIDER.get()


def active_strategy() -> str:
    """``"hybrid"`` when a provider is bound, otherwise ``"lexical"``."""
    return "hybrid" if _PROVIDER.get() is not None else "lexical"


def _load_index() -> VectorIndex:
    """Open the persisted index, read-only, cached per corpus.

    Never built here. Embedding twelve documents costs twelve physical provider
    calls, so a runtime that could rebuild the index is a runtime that can
    spend the day's quota because a file was missing. A missing or mismatched
    index raises :class:`IndexIntegrityError`, which surfaces as a tool error
    rather than as an empty result.
    """
    global _index
    from ..llm.gemini import DEFAULT_EMBEDDING_MODEL

    chunks = build_chunks()
    fingerprint = corpus_fingerprint(chunks)

    with _index_lock:
        if _index is None or _index.metadata.corpus_fingerprint != fingerprint:
            _index = VectorIndex.load(
                DEFAULT_INDEX_PATH,
                expected_model=DEFAULT_EMBEDDING_MODEL,
                expected_fingerprint=fingerprint,
                expected_doc_ids=tuple(chunk.doc_id for chunk in chunks),
            )
        return _index


def _query_vector(provider: LLMProvider, query: str) -> tuple[float, ...]:
    """Embed *query*, consulting the cache first.

    The cache sits in front of the provider so a repeated question costs
    nothing. It does not sit in front of the *budget*: charging happens inside
    ``GeminiProvider.embed``'s retry loop, where the physical call is, and
    nothing here moves or duplicates it. A cache hit is not a discounted call,
    it is no call.
    """
    from ..llm.gemini import DEFAULT_EMBEDDING_MODEL
    from ..llm.provider import EmbedTask

    normalised = " ".join(normalize_terms(query))
    cached = _cache.get(normalised, DEFAULT_EMBEDDING_MODEL)
    if cached is not None:
        return cached

    embedding = provider.embed(query, task=EmbedTask.QUERY)
    _cache.put(normalised, DEFAULT_EMBEDDING_MODEL, embedding.vector)
    return embedding.vector


def retrieve(query: str, *, top_k: int = DEFAULT_TOP_K) -> tuple[Result, ...]:
    """Rank the knowledge base with whichever strategy this request can use."""
    provider = _PROVIDER.get()
    if provider is None:
        return default_retriever().retrieve(query, top_k=top_k)
    return _hybrid(provider, query, top_k=top_k)


def _hybrid(
    provider: LLMProvider, query: str, *, top_k: int = DEFAULT_TOP_K
) -> tuple[Result, ...]:
    """BM25 fused with exact cosine, filtered by the cosine component.

    Ranking uses the fused score; *admission* uses the raw cosine. Those are
    deliberately different signals. Fusion answers "which of these is most
    relevant to each other", which is only meaningful once you have decided the
    candidates are relevant at all -- and the fused score cannot answer that,
    because normalising within a result set discards the absolute scale that
    would let it.
    """
    lexical_retriever = default_retriever()
    lexical_scores = {
        result.doc_id: result.score
        for result in lexical_retriever.retrieve(query, top_k=CANDIDATES)
    }

    terms = normalize_terms(query)
    if not terms:
        return ()

    index = _load_index()
    vector_hits = index.search(_query_vector(provider, query), top_k=len(index.doc_ids))
    vector_scores = {hit.doc_id: hit.score for hit in vector_hits}

    # Admission first, on the cosine component, before fusion can rescale
    # anything. A document BM25 liked but the query is not actually about does
    # not become evidence by being the best of a bad set.
    admitted = {
        doc_id: score
        for doc_id, score in vector_scores.items()
        if score >= MIN_COSINE_SIMILARITY
    }
    if not admitted:
        return ()

    fused = fuse(
        {k: v for k, v in lexical_scores.items() if k in admitted},
        admitted,
        top_k=top_k,
    )

    by_id = {chunk.doc_id: chunk for chunk in build_chunks()}
    return tuple(
        Result(
            doc_id=hit.doc_id,
            title=by_id[hit.doc_id].title,
            body=by_id[hit.doc_id].body,
            score=hit.score,
            rank=hit.rank,
        )
        for hit in fused
        if hit.doc_id in by_id
    )


def total_matches(query: str) -> int:
    """How many documents matched at all, for the tool's existing field."""
    provider = _PROVIDER.get()
    if provider is None:
        return default_retriever().total_matches(query)
    return len(retrieve(query, top_k=MAX_TOP_K))


def reset_caches() -> None:
    """Drop the cached index and query embeddings. For tests only."""
    global _index
    with _index_lock:
        _index = None
    _cache.clear()


__all__ = [
    "CANDIDATES",
    "MIN_COSINE_SIMILARITY",
    "IndexIntegrityError",
    "active_provider",
    "active_strategy",
    "reset_caches",
    "retrieval_provider",
    "retrieve",
    "total_matches",
]
