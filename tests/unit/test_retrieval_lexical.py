"""BM25 lexical retrieval: correctness, determinism and query safety.

The properties worth pinning here are the ones whose absence caused the
measured 38% baseline: results were unranked, they depended on where an article
sat in the source tuple, punctuation silently destroyed query terms, and
substring matching invented relevance that was not there.
"""

from __future__ import annotations

import sqlite3

import pytest

from agent_platform.retrieval.lexical import (
    DEFAULT_TOP_K,
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    LexicalRetriever,
    default_retriever,
    normalize_terms,
)
from agent_platform.retrieval.model import build_chunks, document_id
from agent_platform.tools.dataset import KB_ARTICLES, dataset_digest

DIGEST = "db512de8207f751e"


@pytest.fixture(scope="module")
def retriever() -> LexicalRetriever:
    return LexicalRetriever()


def titles(results) -> list[str]:
    return [r.title for r in results]


# --------------------------------------------------------------- environment


def test_the_bundled_sqlite_actually_has_fts5():
    """If this fails, nothing else in the module means anything."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
    connection.close()


# ------------------------------------------------------ retrieval correctness


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("What is the refund policy?", "Refund policy"),
        ("How do I cancel an order?", "Cancellation policy"),
        ("What payment methods are accepted?", "Payment methods"),
        ("What are the support hours?", "Support hours"),
        ("Is there a warranty on furniture?", "Warranty"),
        ("Can I change my delivery address?", "Address changes"),
    ],
)
def test_direct_questions_rank_the_right_article_first(retriever, query, expected):
    assert titles(retriever.retrieve(query))[0] == expected


def test_a_paraphrase_still_finds_the_article(retriever):
    assert "Damaged items" in titles(
        retriever.retrieve("What happens when an order arrives damaged?")
    )


def test_punctuation_no_longer_destroys_the_final_term(retriever):
    """The old scan searched for 'times?' literally and matched nothing."""
    assert normalize_terms("What are the shipping times?")[-1] == "times"
    assert "Shipping times" in titles(retriever.retrieve("What are the shipping times?"))


def test_an_irrelevant_query_returns_nothing(retriever):
    assert retriever.retrieve("quantum chromodynamics") == ()


def test_a_query_with_no_usable_terms_returns_nothing(retriever):
    for query in ("!!!", "***", "   ", "?"):
        assert retriever.retrieve(query) == ()


def test_substring_matches_are_no_longer_invented(retriever):
    """'you' used to match 'Tracking your order'. Tokens are not substrings."""
    assert "Tracking your order" not in titles(retriever.retrieve("you"))


def test_stopwords_no_longer_drag_in_the_whole_corpus(retriever):
    """'the' appears in 7 of 12 articles; BM25 gives it almost no weight."""
    results = retriever.retrieve("the")
    assert all(result.score > -0.5 for result in results), (
        "a stopword produced a confident-looking score"
    )


# ------------------------------------------------------------------- ranking


def test_results_are_ordered_by_score(retriever):
    results = retriever.retrieve("refund policy", top_k=MAX_TOP_K)
    scores = [r.score for r in results]
    assert scores == sorted(scores), "results are not in BM25 order"


def test_rank_matches_position(retriever):
    results = retriever.retrieve("refund", top_k=MAX_TOP_K)
    assert [r.rank for r in results] == list(range(1, len(results) + 1))


def test_ranking_does_not_follow_declaration_order(retriever):
    """The old implementation returned the first-declared article for 13 of 25
    queries. 'Refund policy' is declared first; a query about support hours
    must not surface it."""
    first = titles(retriever.retrieve("What are the support hours?"))[0]
    assert first == "Support hours"
    assert first != KB_ARTICLES[0]["title"]


# ---------------------------------------------------------------------- top_k


def test_default_top_k_is_three(retriever):
    assert DEFAULT_TOP_K == 3
    assert len(retriever.retrieve("order")) <= 3


def test_top_k_is_capped_at_the_maximum(retriever):
    assert len(retriever.retrieve("order", top_k=999)) <= MAX_TOP_K


def test_top_k_below_one_is_clamped_not_crashing(retriever):
    for k in (0, -5):
        assert len(retriever.retrieve("order", top_k=k)) == 1


# ----------------------------------------------------------------- determinism


def test_the_same_query_twice_gives_the_same_answer(retriever):
    a = retriever.retrieve("refund policy", top_k=MAX_TOP_K)
    b = retriever.retrieve("refund policy", top_k=MAX_TOP_K)
    assert a == b


def test_repeating_a_query_many_times_never_reorders(retriever):
    reference = titles(retriever.retrieve("order shipping", top_k=MAX_TOP_K))
    for _ in range(25):
        assert titles(retriever.retrieve("order shipping", top_k=MAX_TOP_K)) == reference


def test_reordering_the_dataset_changes_nothing():
    """The single most important property. The old scan failed it completely."""
    forward = LexicalRetriever(build_chunks(KB_ARTICLES))
    backward = LexicalRetriever(build_chunks(tuple(reversed(KB_ARTICLES))))

    for query in ("refund policy", "damaged", "shipping times", "warranty sofa"):
        assert titles(forward.retrieve(query, top_k=MAX_TOP_K)) == titles(
            backward.retrieve(query, top_k=MAX_TOP_K)
        ), f"result order depends on dataset declaration order for {query!r}"


def test_a_rebuilt_index_answers_identically():
    first = LexicalRetriever()
    second = LexicalRetriever()
    assert first.fingerprint == second.fingerprint
    for query in ("refund", "order status", "damaged item"):
        assert first.retrieve(query, top_k=MAX_TOP_K) == second.retrieve(
            query, top_k=MAX_TOP_K
        )


def test_ties_are_broken_by_document_id():
    """Equal scores must not be left to SQLite's discretion."""
    chunks = build_chunks(
        (
            {"title": "Zebra topic", "body": "identical wording here"},
            {"title": "Alpha topic", "body": "identical wording here"},
            {"title": "Middle topic", "body": "identical wording here"},
        )
    )
    results = LexicalRetriever(chunks).retrieve("identical wording", top_k=MAX_TOP_K)
    scores = {round(r.score, 6) for r in results}
    assert len(scores) == 1, "fixture failed to produce a tie"
    assert [r.doc_id for r in results] == sorted(r.doc_id for r in results)


# ---------------------------------------------------------------- query safety


@pytest.mark.parametrize(
    "hostile",
    [
        'refund" OR kb MATCH "policy',
        "NEAR(refund policy)",
        "refund AND NOT policy",
        "refund OR policy",
        "*",
        'refund"',
        "col:refund",
        "^refund",
        "refund*",
        "'; DROP TABLE kb; --",
    ],
)
def test_fts5_operators_survive_only_as_literal_words(retriever, hostile):
    """MATCH takes an expression, not a string.

    Raw user text would let a query rewrite itself or crash on an apostrophe.
    Only [a-z0-9]+ survives normalisation, so no term can contain the quote
    that would let it escape its own quoting.
    """
    for term in normalize_terms(hostile):
        assert term.isalnum(), f"{term!r} escaped normalisation"
    retriever.retrieve(hostile)  # must not raise


def test_the_index_survives_a_drop_table_attempt(retriever):
    retriever.retrieve("'; DROP TABLE kb; --")
    assert titles(retriever.retrieve("refund policy"))[0] == "Refund policy"


def test_an_over_long_query_is_truncated_not_rejected(retriever):
    terms = normalize_terms("refund " * 10_000)
    assert len(" ".join(terms)) <= MAX_QUERY_CHARS + 8


def test_accents_are_folded_the_same_way_identifiers_fold_them():
    assert normalize_terms("endereço") == ["endereco"]
    assert document_id("Endereço") == "KB-endereco"


# ------------------------------------------------------------------- integrity


def test_the_index_is_derived_not_a_second_source_of_truth():
    retriever = LexicalRetriever()
    assert retriever.document_count == len(KB_ARTICLES)
    from agent_platform.retrieval.model import corpus_fingerprint

    assert retriever.fingerprint == corpus_fingerprint(build_chunks())


def test_the_cached_retriever_is_reused_while_the_corpus_is_unchanged():
    assert default_retriever() is default_retriever()


def test_retrieval_does_not_touch_the_dataset(retriever):
    before = dataset_digest()
    for query in ("refund", "order", "'; DROP TABLE kb; --", "damaged"):
        retriever.retrieve(query)
    assert dataset_digest() == before == DIGEST


def test_the_retriever_exposes_no_way_to_write():
    """A retriever that cannot express a write cannot be talked into one."""
    forbidden = {"insert", "update", "delete", "write", "add", "remove", "index_document"}
    public = {name for name in dir(LexicalRetriever) if not name.startswith("_")}
    assert not (public & forbidden), f"write-shaped verbs exposed: {public & forbidden}"


def test_the_retrieval_package_imports_standalone():
    """Regression: retrieval must not depend on the tools package at import.

    ``fake_tools`` imports the retriever, so a module-level import of
    ``KB_ARTICLES`` in ``retrieval.model`` closed a cycle. It went unnoticed
    because the suite always imported ``agent_platform.tools`` first; importing
    ``agent_platform.retrieval`` on its own raised ImportError from a partially
    initialised module.
    """
    import os
    import subprocess
    import sys

    # A fresh interpreter is the only way to test import order; it must be able
    # to find the package, so the current search path is handed to it.
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)

    result = subprocess.run(
        [sys.executable, "-c", "import agent_platform.retrieval as r; r.build_chunks()"],
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
