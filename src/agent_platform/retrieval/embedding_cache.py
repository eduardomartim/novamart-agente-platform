"""A process-local cache of query embeddings.

Every cache miss is a physical provider call charged against the same 400/day
ceiling as a completion, so this is a spending control before it is a
performance one. A recruiter clicking the same demo question twice should cost
one call, not two.

Deliberately process-local
--------------------------
A dict and a lock. Not Redis, not a file, not a shared store: under multiple
replicas each process would hold its own cache and the same question could be
charged once per replica. That is a real limitation and it is documented rather
than engineered around, because the alternative is distributed infrastructure
for a twelve-document corpus.

The key includes the model
--------------------------
Vectors from different models are not comparable, so a cache keyed on text
alone would happily return a vector from the wrong space -- which produces a
number, not an error, and therefore a silently wrong ranking.
"""

from __future__ import annotations

import hashlib
import threading
from collections import OrderedDict
from typing import Final

#: Entries are small (a few KB each) and the demo asks a handful of questions,
#: so a modest ceiling is plenty. Least-recently-used eviction keeps a long
#: session from growing without bound.
DEFAULT_MAX_ENTRIES: Final[int] = 256


def cache_key(normalised_query: str, model_id: str) -> str:
    """SHA-256 over the normalised query and the model that would embed it.

    The query is normalised by the retriever's own tokeniser before it gets
    here, so "What is the REFUND policy?" and "what is the refund policy"
    collapse to one key and therefore to one provider call.
    """
    digest = hashlib.sha256()
    digest.update(model_id.encode("utf-8"))
    digest.update(b"\x1f")
    digest.update(normalised_query.encode("utf-8"))
    return digest.hexdigest()


class QueryEmbeddingCache:
    """Thread-safe LRU cache mapping (query, model) to a vector."""

    def __init__(self, *, max_entries: int = DEFAULT_MAX_ENTRIES) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be >= 1")
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, ...]] = OrderedDict()
        self._lock = threading.Lock()
        self.hits = 0
        self.misses = 0

    def get(self, normalised_query: str, model_id: str) -> tuple[float, ...] | None:
        key = cache_key(normalised_query, model_id)
        with self._lock:
            vector = self._entries.get(key)
            if vector is None:
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return vector

    def put(
        self, normalised_query: str, model_id: str, vector: tuple[float, ...]
    ) -> None:
        key = cache_key(normalised_query, model_id)
        with self._lock:
            self._entries[key] = vector
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self.hits = 0
            self.misses = 0

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
