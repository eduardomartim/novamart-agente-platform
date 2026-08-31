"""The corpus as addressable chunks: identity, stability and chunking policy.

The property under test throughout is that a document's identity is a function
of its *content*, never of its position in the source tuple. An index stores
references; a reference that moves when someone reorders a list is a silent
corruption, and this is the layer that must make that impossible.
"""

from __future__ import annotations

import pytest

from agent_platform.retrieval.model import (
    CHUNK_MAX_CHARS,
    Chunk,
    build_chunks,
    corpus_fingerprint,
    document_id,
    oversized_documents,
)
from agent_platform.tools.dataset import KB_ARTICLES, dataset_digest

DIGEST = "db512de8207f751e"


# ------------------------------------------------------------------ identity


def test_identifiers_are_derived_from_the_title():
    assert document_id("Refund policy") == "KB-refund-policy"
    assert document_id("Tracking your order") == "KB-tracking-your-order"


def test_identifiers_ignore_punctuation_case_and_accents():
    assert document_id("Refund Policy!") == document_id("refund   policy")
    assert document_id("Endereço") == "KB-endereco"


def test_a_title_with_no_usable_characters_is_rejected():
    with pytest.raises(ValueError, match="empty identifier"):
        document_id("!!!")


def test_every_article_gets_a_unique_identifier():
    chunks = build_chunks()
    assert len(chunks) == len(KB_ARTICLES)
    assert len({chunk.doc_id for chunk in chunks}) == len(KB_ARTICLES)


def test_two_articles_sharing_a_slug_are_refused_loudly():
    """A collision must fail the build, not quietly overwrite an entry."""
    with pytest.raises(ValueError, match="cannot share a slug"):
        build_chunks(
            (
                {"title": "Refund policy", "body": "one"},
                {"title": "refund  POLICY", "body": "two"},
            )
        )


# ------------------------------------------------------- stability of the set


def test_reordering_the_source_tuple_changes_nothing():
    """The whole point of deriving identity from content."""
    forward = build_chunks(KB_ARTICLES)
    backward = build_chunks(tuple(reversed(KB_ARTICLES)))

    assert [c.doc_id for c in forward] == [c.doc_id for c in backward]
    assert corpus_fingerprint(forward) == corpus_fingerprint(backward)


def test_chunks_are_ordered_by_identifier_not_by_declaration():
    ids = [chunk.doc_id for chunk in build_chunks()]
    assert ids == sorted(ids)
    # The current declaration order starts with "Refund policy"; sorted order
    # must not, or this test is not proving anything.
    assert ids[0] != document_id(KB_ARTICLES[0]["title"])


def test_building_twice_produces_identical_chunks():
    assert build_chunks() == build_chunks()


def test_fingerprint_changes_when_content_changes():
    original = build_chunks()
    edited = build_chunks(
        (
            {"title": "Refund policy", "body": "something else entirely"},
            *(a for a in KB_ARTICLES if a["title"] != "Refund policy"),
        )
    )
    assert corpus_fingerprint(original) != corpus_fingerprint(edited)


def test_malformed_articles_are_refused():
    with pytest.raises(ValueError, match="empty title or body"):
        build_chunks(({"title": "Fine", "body": "   "},))


# ------------------------------------------------------------ chunking policy


def test_one_document_is_one_chunk():
    chunks = build_chunks()
    assert len(chunks) == len(KB_ARTICLES)
    assert all(chunk.ordinal == 0 for chunk in chunks)


def test_no_document_is_large_enough_to_need_splitting():
    """The tripwire. If this fails, the chunking decision must be revisited."""
    assert oversized_documents() == ()


def test_oversized_documents_are_detected_when_they_appear():
    long_article = {"title": "Essay", "body": "x" * (CHUNK_MAX_CHARS + 1)}
    assert oversized_documents((long_article,)) == ("Essay",)


def test_embed_text_carries_the_title():
    """Titles are close to how a customer phrases the question; bodies are not."""
    chunk = Chunk(doc_id="KB-damaged-items", title="Damaged items", body="Report damage.")
    assert chunk.embed_text == "Damaged items\nReport damage."


# ------------------------------------------------------------------ integrity


def test_the_model_layer_does_not_touch_the_dataset():
    """Identity is derived precisely so the frozen digest stays frozen."""
    before = dataset_digest()
    build_chunks()
    corpus_fingerprint(build_chunks())
    oversized_documents()
    assert dataset_digest() == before == DIGEST


def test_articles_gained_no_id_field():
    """If an id were stored rather than derived, the digest would have moved."""
    assert all(set(article) == {"title", "body"} for article in KB_ARTICLES)
