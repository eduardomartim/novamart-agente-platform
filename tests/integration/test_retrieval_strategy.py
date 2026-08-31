"""The retrieval strategy: which ranker runs, and what it is allowed to claim.

Read the LIVE half of this file knowing exactly what it proves.

The query vectors in ``tests/fixtures/query_vectors_7e.json`` are **real** --
captured from `gemini-embedding-001` during the 7E live gate on 2026-08-29, 25
of them at 3072 dimensions. Replaying them exercises the genuine persisted
index, genuine cosine arithmetic, genuine fusion and the genuine threshold,
with no network call.

What it does **not** prove is that Gemini would return those same vectors
again, or that it returns good vectors for a query nobody has embedded yet.
These tests validate the wiring, not the embedding provider.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent_platform.llm.provider import Embedding, EmbedTask, LLMUnavailableError
from agent_platform.llm.stub import StubProvider
from agent_platform.retrieval import strategy
from agent_platform.retrieval.index import IndexIntegrityError
from agent_platform.retrieval.lexical import LexicalRetriever
from agent_platform.tools.dataset import dataset_digest

DIGEST = "db512de8207f751e"
FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "query_vectors_7e.json"

INDEX_AVAILABLE = strategy.DEFAULT_INDEX_PATH.exists()
needs_index = pytest.mark.skipif(
    not INDEX_AVAILABLE, reason="the persisted vector index is not present"
)


@pytest.fixture(scope="module")
def captured() -> dict[str, list[float]]:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert payload["model"] == "gemini-embedding-001"
    assert payload["dimension"] == 3072
    return payload["query_vectors"]


@pytest.fixture(autouse=True)
def clean_caches():
    strategy.reset_caches()
    yield
    strategy.reset_caches()


class ReplayProvider(StubProvider):
    """Returns the real vectors captured in 7E. Makes no network call."""

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        super().__init__()
        self._vectors = vectors
        self.embed_calls = 0

    def embed(self, text, *, task=EmbedTask.QUERY, timeout=None):
        self.embed_calls += 1
        if text not in self._vectors:
            raise KeyError(f"no captured vector for {text!r}")
        return Embedding(
            vector=tuple(self._vectors[text]),
            provider="replay",
            model="gemini-embedding-001",
            task=task,
        )


class BrokenEmbedder(StubProvider):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self._error = error
        self.embed_calls = 0

    def embed(self, text, *, task=EmbedTask.QUERY, timeout=None):
        self.embed_calls += 1
        raise self._error


# ========================================================= STRATEGY SELECTION


def test_no_provider_bound_means_lexical():
    assert strategy.active_strategy() == "lexical"
    assert strategy.active_provider() is None


def test_binding_a_provider_selects_hybrid(captured):
    with strategy.retrieval_provider(ReplayProvider(captured)):
        assert strategy.active_strategy() == "hybrid"


def test_the_binding_is_released_on_exit(captured):
    with strategy.retrieval_provider(ReplayProvider(captured)):
        pass
    assert strategy.active_provider() is None


def test_the_binding_is_released_on_exception(captured):
    with pytest.raises(RuntimeError), strategy.retrieval_provider(ReplayProvider(captured)):
        raise RuntimeError("boom")
    assert strategy.active_provider() is None


def test_binding_none_selects_lexical(captured):
    """Demo mode's mechanism: bind nothing, get BM25, attempt no embedding."""
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(None):
        results = strategy.retrieve("How do I request a refund?")
    assert provider.embed_calls == 0
    assert results == LexicalRetriever().retrieve("How do I request a refund?")


def test_concurrent_contexts_do_not_share_a_provider(captured):
    """A ContextVar, not a module global. Threads must not see each other's."""
    import threading

    seen: dict[str, str] = {}

    def worker(name: str, provider) -> None:
        with strategy.retrieval_provider(provider):
            seen[name] = strategy.active_strategy()

    threads = [
        threading.Thread(target=worker, args=("hybrid", ReplayProvider(captured))),
        threading.Thread(target=worker, args=("lexical", None)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert seen == {"hybrid": "hybrid", "lexical": "lexical"}
    assert strategy.active_provider() is None


# =============================================== STUB: BM25, ZERO EMBEDDINGS


def test_lexical_results_are_identical_to_the_bare_retriever():
    """7G must not have changed what BM25 returns."""
    for query in ("How do I request a refund?", "damaged item", "shipping times"):
        assert strategy.retrieve(query) == LexicalRetriever().retrieve(query)


def test_lexical_total_matches_is_unchanged():
    assert strategy.total_matches("refund policy") == LexicalRetriever().total_matches(
        "refund policy"
    )


# ================================================ LIVE WIRING, REPLAYED VECTORS


@needs_index
def test_hybrid_retrieves_using_the_real_index(captured):
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(provider):
        results = strategy.retrieve("How do I request a refund?")

    assert provider.embed_calls == 1
    assert results
    assert "Refund policy" in [r.title for r in results]


@needs_index
def test_hybrid_answers_a_question_bm25_cannot(captured):
    """The measured gap 7E identified: no shared vocabulary with any article."""
    question = "I want my money back"
    assert LexicalRetriever().retrieve(question) == (), "BM25 unexpectedly found something"

    with strategy.retrieval_provider(ReplayProvider(captured)):
        results = strategy.retrieve(question)

    assert results, "hybrid found nothing either"
    assert "Refund policy" in [r.title for r in results]


@needs_index
def test_the_result_shape_is_identical_across_strategies(captured):
    lexical = strategy.retrieve("How do I request a refund?")
    with strategy.retrieval_provider(ReplayProvider(captured)):
        hybrid = strategy.retrieve("How do I request a refund?")

    assert {type(r) for r in lexical} == {type(r) for r in hybrid}
    for result in hybrid:
        assert result.doc_id and result.title and result.body
        assert isinstance(result.score, float)


@needs_index
def test_hybrid_ranking_is_deterministic(captured):
    with strategy.retrieval_provider(ReplayProvider(captured)):
        first = strategy.retrieve("How do I request a refund?")
        second = strategy.retrieve("How do I request a refund?")
    assert first == second


# ============================================================ CACHE BEHAVIOUR


@needs_index
def test_a_repeated_query_costs_no_second_embedding(captured):
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(provider):
        strategy.retrieve("How do I request a refund?")
        assert provider.embed_calls == 1
        strategy.retrieve("How do I request a refund?")
        assert provider.embed_calls == 1, "a cache hit still called the provider"


@needs_index
def test_a_different_query_is_embedded_separately(captured):
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(provider):
        strategy.retrieve("How do I request a refund?")
        strategy.retrieve("How do I track my order?")
    assert provider.embed_calls == 2


@needs_index
def test_two_queries_do_not_share_an_embedding(captured):
    """A cache key collision would silently rank one question by another's vector."""
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(provider):
        refunds = strategy.retrieve("How do I request a refund?")
        tracking = strategy.retrieve("How do I track my order?")

    assert [r.doc_id for r in refunds] != [r.doc_id for r in tracking]


# ================================================================= THRESHOLD


@needs_index
def test_the_threshold_is_a_named_constant():
    assert strategy.MIN_COSINE_SIMILARITY == 0.67


@needs_index
def test_a_question_the_corpus_cannot_answer_is_admitted_by_nothing(captured):
    """7F.1 measured every unanswerable question's top cosine below 0.67."""
    with strategy.retrieval_provider(ReplayProvider(captured)):
        assert strategy.retrieve("What is the CEO's salary?") == ()
        assert strategy.retrieve("Is the warehouse in Sao Paulo?") == ()


@needs_index
def test_raising_the_threshold_admits_nothing(captured, monkeypatch):
    """Non-vacuity: the threshold is genuinely load-bearing, not decorative."""
    monkeypatch.setattr(strategy, "MIN_COSINE_SIMILARITY", 0.99)
    with strategy.retrieval_provider(ReplayProvider(captured)):
        assert strategy.retrieve("How do I request a refund?") == ()


@needs_index
def test_removing_the_threshold_admits_the_unanswerable(captured, monkeypatch):
    """The complement: with no threshold, the bad questions come back.

    This is what proves the earlier assertion is not passing because those
    questions simply retrieve nothing.
    """
    monkeypatch.setattr(strategy, "MIN_COSINE_SIMILARITY", -1.0)
    with strategy.retrieval_provider(ReplayProvider(captured)):
        assert strategy.retrieve("What is the CEO's salary?") != ()


@needs_index
def test_the_threshold_is_applied_to_cosine_not_to_the_fused_score(captured):
    """``fuse`` min-max normalises, so its top score is always 1.0 for a
    non-empty set. If the threshold read that, nothing could ever be rejected
    -- and the unanswerable questions above would all pass."""
    import inspect

    source = inspect.getsource(strategy._hybrid)
    admission = source.split("admitted = {")[1].split("}")[0]
    assert "vector_scores" in admission, "admission does not read the cosine component"
    assert "fused" not in admission, "admission reads the fused score"


# ============================================================ FAILURE MODES


@needs_index
def test_an_embedding_outage_propagates_rather_than_degrading(captured):
    """Silently returning BM25 results would hide the outage entirely."""
    provider = BrokenEmbedder(LLMUnavailableError("provider down"))
    with strategy.retrieval_provider(provider), pytest.raises(LLMUnavailableError):
        strategy.retrieve("How do I request a refund?")
    assert provider.embed_calls == 1


@needs_index
def test_an_embedding_outage_does_not_return_zero_documents(captured):
    """The specific substitution 7G.0 forbade, asserted at the retrieval layer."""
    provider = BrokenEmbedder(LLMUnavailableError("down"))
    with strategy.retrieval_provider(provider):
        try:
            results = strategy.retrieve("How do I request a refund?")
        except LLMUnavailableError:
            return  # correct: it raised rather than returning an empty result
    pytest.fail(f"an outage produced {len(results)} results instead of raising")


def test_a_missing_index_raises_rather_than_returning_nothing(captured, monkeypatch):
    monkeypatch.setattr(strategy, "DEFAULT_INDEX_PATH", Path("does-not-exist.db"))
    strategy.reset_caches()
    with strategy.retrieval_provider(ReplayProvider(captured)), pytest.raises(
        IndexIntegrityError
    ):
        strategy.retrieve("How do I request a refund?")


def test_an_empty_query_returns_nothing_without_embedding(captured):
    provider = ReplayProvider(captured)
    with strategy.retrieval_provider(provider):
        assert strategy.retrieve("!!!") == ()
    assert provider.embed_calls == 0, "an unusable query still cost an embedding"


# ================================================================= INTEGRITY


def test_the_strategy_never_writes_to_the_index():
    import inspect

    source = inspect.getsource(strategy)
    for forbidden in ("build_index(", "INSERT INTO", "UPDATE ", "DELETE FROM"):
        assert forbidden not in source, f"the strategy layer writes: {forbidden}"


def test_the_dataset_is_unchanged():
    assert dataset_digest() == DIGEST
