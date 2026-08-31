"""Knowledge-base retrieval.

Built in layers, each of which must stand on its own:

* :mod:`model` -- the corpus as addressable chunks. Pure data, no I/O.
* :mod:`lexical` -- BM25 ranking over an in-memory SQLite FTS5 index.
* :mod:`index` -- a vector index that proves its own provenance on load.
* :mod:`embedding_cache` -- process-local cache of query embeddings.
* :mod:`hybrid` -- score fusion. No I/O, no provider.

Nothing in this package may call a provider directly; embedding goes through
``LLMProvider.embed`` so that every physical call is charged to the budget.
"""

from __future__ import annotations

from .embedding_cache import QueryEmbeddingCache, cache_key
from .hybrid import FusedHit, fuse
from .index import (
    INDEX_FORMAT_VERSION,
    IndexIntegrityError,
    IndexMetadata,
    VectorHit,
    VectorIndex,
    build_index,
)
from .lexical import (
    DEFAULT_TOP_K,
    MAX_TOP_K,
    LexicalRetriever,
    Result,
    Retriever,
    default_retriever,
    normalize_terms,
)
from .model import (
    Chunk,
    build_chunks,
    corpus_fingerprint,
    document_id,
    oversized_documents,
)

__all__ = [
    "DEFAULT_TOP_K",
    "INDEX_FORMAT_VERSION",
    "MAX_TOP_K",
    "Chunk",
    "FusedHit",
    "IndexIntegrityError",
    "IndexMetadata",
    "LexicalRetriever",
    "QueryEmbeddingCache",
    "Result",
    "Retriever",
    "VectorHit",
    "VectorIndex",
    "build_chunks",
    "build_index",
    "cache_key",
    "corpus_fingerprint",
    "default_retriever",
    "document_id",
    "fuse",
    "normalize_terms",
    "oversized_documents",
]
