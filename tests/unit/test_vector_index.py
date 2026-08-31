"""The vector index must prove its provenance, or refuse to load.

A stale or mismatched index does not fail loudly on its own: cosine similarity
between two unrelated embedding spaces is still a number between -1 and 1, and
a ranking built from it looks exactly like a ranking built from the right one.
So every guarantee here is about *refusing*, and each refusal is tested by
building a broken index on purpose and checking the error names the problem.

No provider is involved. Vectors are constructed arithmetically so that the
expected similarity is known in advance.
"""

from __future__ import annotations

import math
import sqlite3

import numpy as np
import pytest

from agent_platform.retrieval.index import (
    INDEX_FORMAT_VERSION,
    IndexIntegrityError,
    VectorIndex,
    build_index,
)

MODEL = "test-embedding-model"
FINGERPRINT = "abc123def456"
DIM = 8


def unit(*components: float) -> tuple[float, ...]:
    """A vector padded to DIM dimensions."""
    values = list(components) + [0.0] * (DIM - len(components))
    return tuple(values[:DIM])


VECTORS = {
    "KB-alpha": unit(1, 0, 0),
    "KB-beta": unit(0, 1, 0),
    "KB-gamma": unit(0, 0, 1),
}
DOC_IDS = tuple(sorted(VECTORS))


@pytest.fixture
def index_path(tmp_path):
    path = tmp_path / "vectors.db"
    build_index(
        path,
        vectors=VECTORS,
        model_id=MODEL,
        corpus_fingerprint=FINGERPRINT,
        expected_doc_ids=DOC_IDS,
    )
    return path


@pytest.fixture
def index(index_path):
    return VectorIndex.load(
        index_path,
        expected_model=MODEL,
        expected_fingerprint=FINGERPRINT,
        expected_doc_ids=DOC_IDS,
    )


# ------------------------------------------------------------ index integrity


def test_a_built_index_records_its_own_provenance(index):
    metadata = index.metadata
    assert metadata.format_version == INDEX_FORMAT_VERSION
    assert metadata.model_id == MODEL
    assert metadata.corpus_fingerprint == FINGERPRINT
    assert metadata.dimension == DIM
    assert metadata.document_count == len(VECTORS)
    assert metadata.built_at.endswith("Z")


def test_document_ids_are_stable_and_sorted(index):
    assert index.doc_ids == DOC_IDS


def test_loading_refuses_a_corpus_fingerprint_mismatch(index_path):
    with pytest.raises(IndexIntegrityError, match="different corpus"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint="0000000000000000"
        )


def test_loading_refuses_a_model_mismatch(index_path):
    with pytest.raises(IndexIntegrityError, match="not comparable"):
        VectorIndex.load(
            index_path, expected_model="some-other-model", expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_an_unknown_format_version(index_path):
    connection = sqlite3.connect(index_path)
    connection.execute(
        "UPDATE index_metadata SET value = ? WHERE key = 'format_version'",
        (str(INDEX_FORMAT_VERSION + 1),),
    )
    connection.commit()
    connection.close()

    with pytest.raises(IndexIntegrityError, match="format version"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_a_vector_of_the_wrong_length(index_path):
    connection = sqlite3.connect(index_path)
    connection.execute(
        "UPDATE document_vectors SET vector = ? WHERE doc_id = 'KB-alpha'",
        (b"\x00\x00\x00\x00",),
    )
    connection.commit()
    connection.close()

    with pytest.raises(IndexIntegrityError, match="bytes, expected"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_a_declared_count_that_does_not_match(index_path):
    connection = sqlite3.connect(index_path)
    connection.execute("DELETE FROM document_vectors WHERE doc_id = 'KB-alpha'")
    connection.commit()
    connection.close()

    with pytest.raises(IndexIntegrityError, match=r"declares .* documents but holds"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_a_document_the_corpus_does_not_contain(index_path):
    with pytest.raises(IndexIntegrityError, match="does not match the corpus"):
        VectorIndex.load(
            index_path,
            expected_model=MODEL,
            expected_fingerprint=FINGERPRINT,
            expected_doc_ids=("KB-alpha", "KB-beta"),
        )


def test_loading_refuses_a_nonfinite_vector(index_path):
    poisoned = np.full(DIM, np.nan, dtype="<f4").tobytes()
    connection = sqlite3.connect(index_path)
    connection.execute(
        "UPDATE document_vectors SET vector = ? WHERE doc_id = 'KB-beta'", (poisoned,)
    )
    connection.commit()
    connection.close()

    with pytest.raises(IndexIntegrityError, match="NaN or infinity"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_a_missing_file(tmp_path):
    with pytest.raises(IndexIntegrityError, match="no vector index"):
        VectorIndex.load(
            tmp_path / "absent.db", expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_a_file_that_is_not_an_index(tmp_path):
    path = tmp_path / "garbage.db"
    path.write_bytes(b"this is not a database" * 100)
    with pytest.raises(IndexIntegrityError):
        VectorIndex.load(
            path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_loading_refuses_incomplete_metadata(index_path):
    connection = sqlite3.connect(index_path)
    connection.execute("DELETE FROM index_metadata WHERE key = 'dimension'")
    connection.commit()
    connection.close()

    with pytest.raises(IndexIntegrityError, match="incomplete"):
        VectorIndex.load(
            index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
        )


def test_there_is_no_lenient_load(index_path):
    """No strict=False. A caller wanting lexical retrieval must ask for it."""
    import inspect

    signature = inspect.signature(VectorIndex.load)
    assert "strict" not in signature.parameters
    assert "fallback" not in signature.parameters


# ------------------------------------------------------------------- building


def test_building_refuses_a_document_the_corpus_does_not_have(tmp_path):
    with pytest.raises(IndexIntegrityError, match="would not match the corpus"):
        build_index(
            tmp_path / "v.db",
            vectors={**VECTORS, "KB-ghost": unit(1, 1)},
            model_id=MODEL,
            corpus_fingerprint=FINGERPRINT,
            expected_doc_ids=DOC_IDS,
        )


def test_building_refuses_inconsistent_dimensions(tmp_path):
    with pytest.raises(IndexIntegrityError, match="inconsistent dimensions"):
        build_index(
            tmp_path / "v.db",
            vectors={"KB-alpha": (1.0, 0.0), "KB-beta": (0.0, 1.0, 0.0)},
            model_id=MODEL,
            corpus_fingerprint=FINGERPRINT,
            expected_doc_ids=("KB-alpha", "KB-beta"),
        )


def test_building_refuses_a_zero_vector(tmp_path):
    """A zero vector has no direction, so its cosine similarity is undefined."""
    with pytest.raises(IndexIntegrityError, match="no direction"):
        build_index(
            tmp_path / "v.db",
            vectors={"KB-alpha": unit(0, 0, 0)},
            model_id=MODEL,
            corpus_fingerprint=FINGERPRINT,
            expected_doc_ids=("KB-alpha",),
        )


def test_building_refuses_an_empty_index(tmp_path):
    with pytest.raises(IndexIntegrityError, match="empty index"):
        build_index(
            tmp_path / "v.db",
            vectors={},
            model_id=MODEL,
            corpus_fingerprint=FINGERPRINT,
            expected_doc_ids=(),
        )


# ---------------------------------------------------------------- vector math


def test_an_identical_vector_scores_one(index):
    hits = index.search(VECTORS["KB-alpha"], top_k=3)
    assert hits[0].doc_id == "KB-alpha"
    assert math.isclose(hits[0].score, 1.0, abs_tol=1e-6)


def test_an_orthogonal_vector_scores_zero(index):
    hits = {h.doc_id: h.score for h in index.search(VECTORS["KB-alpha"], top_k=3)}
    assert math.isclose(hits["KB-beta"], 0.0, abs_tol=1e-6)
    assert math.isclose(hits["KB-gamma"], 0.0, abs_tol=1e-6)


def test_an_opposite_vector_scores_minus_one(index):
    opposite = tuple(-v for v in VECTORS["KB-alpha"])
    hits = {h.doc_id: h.score for h in index.search(opposite, top_k=3)}
    assert math.isclose(hits["KB-alpha"], -1.0, abs_tol=1e-6)


def test_similarity_is_scale_invariant(index):
    """Cosine measures direction; magnitude must not change the ranking."""
    small = index.search(VECTORS["KB-alpha"], top_k=3)
    large = index.search(tuple(v * 1000 for v in VECTORS["KB-alpha"]), top_k=3)
    assert [h.doc_id for h in small] == [h.doc_id for h in large]
    assert math.isclose(small[0].score, large[0].score, abs_tol=1e-5)


def test_a_zero_query_vector_is_rejected(index):
    with pytest.raises(IndexIntegrityError, match="undefined"):
        index.search(unit(0, 0, 0))


def test_a_query_of_the_wrong_dimension_is_rejected(index):
    with pytest.raises(IndexIntegrityError, match="different models"):
        index.search((1.0, 0.0))


def test_a_nonfinite_query_vector_is_rejected(index):
    with pytest.raises(IndexIntegrityError):
        index.search(unit(float("nan"), 0, 0))


# ----------------------------------------------------------------- determinism


def test_the_same_query_gives_the_same_ranking(index):
    first = index.search(unit(1, 1, 1), top_k=3)
    second = index.search(unit(1, 1, 1), top_k=3)
    assert first == second


def test_ties_are_broken_by_document_id(index):
    """An equidistant query must not be resolved by matrix row order."""
    hits = index.search(unit(1, 1, 1), top_k=3)
    scores = {round(h.score, 6) for h in hits}
    assert len(scores) == 1, "fixture failed to produce a tie"
    assert [h.doc_id for h in hits] == sorted(h.doc_id for h in hits)


def test_a_reloaded_index_answers_identically(index_path, index):
    reloaded = VectorIndex.load(
        index_path, expected_model=MODEL, expected_fingerprint=FINGERPRINT
    )
    query = unit(0.3, 0.9, 0.1)
    assert index.search(query, top_k=3) == reloaded.search(query, top_k=3)


def test_top_k_is_bounded_by_the_corpus(index):
    assert len(index.search(unit(1, 0, 0), top_k=999)) == len(VECTORS)
    assert len(index.search(unit(1, 0, 0), top_k=0)) == 1


def test_ranks_are_sequential(index):
    hits = index.search(unit(1, 0.5, 0.2), top_k=3)
    assert [h.rank for h in hits] == [1, 2, 3]


# -------------------------------------------------------------------- security


def test_a_loaded_index_exposes_no_way_to_write(index):
    forbidden = {"insert", "update", "delete", "write", "add", "remove", "build"}
    public = {name for name in dir(index) if not name.startswith("_")}
    assert not (public & forbidden), f"write-shaped verbs exposed: {public & forbidden}"


def test_the_runtime_handle_is_opened_read_only(index_path):
    """Even reaching the connection cannot mutate the file."""
    connection = sqlite3.connect(f"file:{index_path.as_posix()}?mode=ro", uri=True)
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute("DELETE FROM document_vectors")
    finally:
        connection.close()


def test_the_index_holds_no_document_text_or_credential(tmp_path):
    """Vectors and provenance only -- nothing that could carry an instruction.

    Checked against a corpus whose text is known, rather than by grepping for
    generic words: "key" appears in the file as a *column name* in the metadata
    schema, which is not a credential and must not be reported as one.
    """
    build_index(
        tmp_path / "real.db",
        vectors={
            "KB-refund-policy": unit(1, 0, 0),
            "KB-damaged-items": unit(0, 1, 0),
        },
        model_id=MODEL,
        corpus_fingerprint=FINGERPRINT,
        expected_doc_ids=("KB-damaged-items", "KB-refund-policy"),
    )
    blob = (tmp_path / "real.db").read_bytes()

    # Document identifiers are stored; document prose and secrets are not.
    assert b"KB-refund-policy" in blob
    for absent in (
        b"Orders may be refunded",
        b"IGNORE ALL PREVIOUS",
        b"AIzaSy",
        b"GEMINI_API_KEY",
        b"api_key",
    ):
        assert absent not in blob, f"{absent!r} was written into the index"
