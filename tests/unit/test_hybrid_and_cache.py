"""Score fusion and the query-embedding cache.

Both are pure: no provider, no index, no network. Fusion takes two ranked lists
and returns one; the cache is a dict with a lock. Testing them in isolation is
what makes the 7E evaluation interpretable -- if the hybrid numbers move, it is
because the retrieval changed, not because the arithmetic is wrong.
"""

from __future__ import annotations

import math

import pytest

from agent_platform.retrieval.embedding_cache import (
    QueryEmbeddingCache,
    cache_key,
)
from agent_platform.retrieval.hybrid import fuse
from agent_platform.retrieval.lexical import normalize_terms

MODEL = "test-embedding-model"


# ============================================================== score fusion


def test_a_document_found_by_only_the_vector_ranker_can_surface():
    """The entire justification for fusing: each ranker finds what the other misses."""
    fused = fuse({"KB-a": -5.0}, {"KB-z": 0.95}, top_k=3)
    assert {hit.doc_id for hit in fused} == {"KB-a", "KB-z"}


def test_a_document_found_by_only_the_lexical_ranker_survives():
    fused = fuse({"KB-a": -5.0, "KB-b": -1.0}, {}, top_k=3)
    assert [hit.doc_id for hit in fused] == ["KB-a", "KB-b"]


def test_bm25_sign_convention_is_respected():
    """SQLite BM25 is negative, and more negative means more relevant."""
    fused = fuse({"KB-best": -9.0, "KB-worst": -0.5}, {}, top_k=2)
    assert fused[0].doc_id == "KB-best"


def test_a_document_both_rankers_agree_on_outranks_either_alone():
    fused = fuse(
        {"KB-both": -9.0, "KB-lex": -9.0},
        {"KB-both": 0.9, "KB-vec": 0.9},
        top_k=4,
    )
    assert fused[0].doc_id == "KB-both"


def test_weights_shift_the_ranking_as_declared():
    lexical = {"KB-lex": -9.0, "KB-vec": -0.1}
    vector = {"KB-lex": 0.1, "KB-vec": 0.9}

    lexical_heavy = fuse(lexical, vector, lexical_weight=1.0, vector_weight=0.0, top_k=2)
    vector_heavy = fuse(lexical, vector, lexical_weight=0.0, vector_weight=1.0, top_k=2)

    assert lexical_heavy[0].doc_id == "KB-lex"
    assert vector_heavy[0].doc_id == "KB-vec"


def test_a_single_result_normalises_to_the_top_not_to_zero():
    """One confident match must not be indistinguishable from no match."""
    fused = fuse({}, {"KB-only": 0.42}, top_k=3)
    assert math.isclose(fused[0].score, 0.5, abs_tol=1e-9)


def test_identical_scores_do_not_produce_nan():
    fused = fuse({"KB-a": -3.0, "KB-b": -3.0}, {}, top_k=2)
    assert all(math.isfinite(hit.score) for hit in fused)


def test_fusion_is_deterministic():
    lexical = {"KB-a": -4.0, "KB-b": -2.0, "KB-c": -1.0}
    vector = {"KB-b": 0.8, "KB-c": 0.5, "KB-d": 0.9}
    assert fuse(lexical, vector, top_k=4) == fuse(lexical, vector, top_k=4)


def test_ties_are_broken_by_document_id():
    fused = fuse({"KB-z": -1.0, "KB-a": -1.0, "KB-m": -1.0}, {}, top_k=3)
    assert [hit.doc_id for hit in fused] == ["KB-a", "KB-m", "KB-z"]


def test_component_scores_are_preserved_for_inspection():
    """A fused score nobody can decompose is a number nobody can argue with."""
    fused = fuse({"KB-a": -4.0}, {"KB-a": 0.75}, top_k=1)
    assert fused[0].lexical_score == -4.0
    assert fused[0].vector_score == 0.75


def test_a_missing_component_is_reported_as_missing_not_as_zero():
    fused = fuse({"KB-a": -4.0}, {}, top_k=1)
    assert fused[0].vector_score is None


def test_empty_inputs_give_an_empty_ranking():
    assert fuse({}, {}, top_k=3) == ()


def test_ranks_are_sequential():
    fused = fuse({"KB-a": -3.0, "KB-b": -2.0, "KB-c": -1.0}, {}, top_k=3)
    assert [hit.rank for hit in fused] == [1, 2, 3]


# ================================================================== the cache


def test_a_miss_then_a_hit():
    cache = QueryEmbeddingCache()
    assert cache.get("refund policy", MODEL) is None
    cache.put("refund policy", MODEL, (0.1, 0.2))
    assert cache.get("refund policy", MODEL) == (0.1, 0.2)
    assert cache.hits == 1
    assert cache.misses == 1


def test_the_key_includes_the_model():
    """Vectors from different models are not comparable, so keys must differ."""
    assert cache_key("refund policy", "model-a") != cache_key("refund policy", "model-b")


def test_two_models_never_share_an_entry():
    cache = QueryEmbeddingCache()
    cache.put("refund policy", "model-a", (1.0, 0.0))
    assert cache.get("refund policy", "model-b") is None


def test_queries_equivalent_after_normalisation_share_one_key():
    """This is what turns a repeated demo question into zero provider calls."""
    a = " ".join(normalize_terms("What is the REFUND policy?"))
    b = " ".join(normalize_terms("what   is the refund policy"))
    assert a == b
    assert cache_key(a, MODEL) == cache_key(b, MODEL)


def test_the_key_is_a_sha256_hex_digest():
    key = cache_key("refund policy", MODEL)
    assert len(key) == 64
    assert all(c in "0123456789abcdef" for c in key)


def test_the_cache_does_not_grow_without_bound():
    cache = QueryEmbeddingCache(max_entries=3)
    for i in range(10):
        cache.put(f"query {i}", MODEL, (float(i),))
    assert len(cache) == 3


def test_eviction_is_least_recently_used():
    cache = QueryEmbeddingCache(max_entries=2)
    cache.put("a", MODEL, (1.0,))
    cache.put("b", MODEL, (2.0,))
    cache.get("a", MODEL)          # 'a' is now the more recently used
    cache.put("c", MODEL, (3.0,))  # evicts 'b'
    assert cache.get("a", MODEL) == (1.0,)
    assert cache.get("b", MODEL) is None


def test_clearing_resets_counters():
    cache = QueryEmbeddingCache()
    cache.put("a", MODEL, (1.0,))
    cache.get("a", MODEL)
    cache.clear()
    assert len(cache) == 0
    assert cache.hits == 0
    assert cache.misses == 0


def test_max_entries_must_be_positive():
    with pytest.raises(ValueError, match="max_entries"):
        QueryEmbeddingCache(max_entries=0)


def test_the_cache_is_thread_safe():
    import threading

    cache = QueryEmbeddingCache(max_entries=512)
    errors: list[BaseException] = []

    def hammer(start: int) -> None:
        try:
            for i in range(start, start + 100):
                cache.put(f"q{i}", MODEL, (float(i),))
                cache.get(f"q{i}", MODEL)
        except BaseException as exc:  # pragma: no cover - only on failure
            errors.append(exc)

    threads = [threading.Thread(target=hammer, args=(i * 100,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors
