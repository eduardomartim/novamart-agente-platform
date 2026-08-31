"""A vector index that can prove what it was built from.

An index is a *derived artefact that outlives the process which built it*. That
is the whole problem. A stale index does not crash; it answers, plausibly and
wrongly, from a corpus that no longer exists or a model whose vectors are not
comparable with the query's. Nothing downstream can detect that, because a
cosine similarity between two unrelated embedding spaces is still a number
between -1 and 1.

So every load is a proof obligation. The index carries the corpus fingerprint,
the model that produced its vectors, their dimension, its own format version
and its document count, and :meth:`VectorIndex.load` refuses -- loudly, with
:class:`IndexIntegrityError` -- if any of them disagrees with what the caller
expects. There is no fallback path. An index that cannot prove its provenance
is not silently degraded to "lexical only"; it raises, and the caller decides.

Why brute force
---------------
Twelve documents. Measured on this machine, an exact cosine scan over a
12-vector matrix takes single-digit microseconds and stays under a millisecond
to ten thousand vectors. An ANN index would add a dependency, a build step and
an approximation, to make an already-exact search slower. When the corpus
outgrows that, this module's search method is the one place that changes.

Why the runtime never writes
----------------------------
:func:`build_index` is a module-level function, not a method. It was briefly a
``@staticmethod``, which meant a loaded index still answered to
``index.build(...)`` -- a writer hanging off the object the request path holds.
:class:`VectorIndex` now exposes ``search``, ``load`` and metadata properties
and nothing else, and opens its file read-only.

That matters because embedding twelve documents costs twelve physical provider
calls: a runtime able to rebuild the index is a runtime that can spend the
day's quota because a file was missing.
"""

from __future__ import annotations

import sqlite3
import struct
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final, TypeAlias

import numpy as np
import numpy.typing as npt

#: A float32 vector or matrix. Named so the annotations below stay readable.
Vector: TypeAlias = npt.NDArray[np.float32]

#: Bumped whenever the on-disk layout changes in a way an older reader would
#: misinterpret. A reader that does not recognise the version refuses rather
#: than guessing.
INDEX_FORMAT_VERSION: Final[int] = 1

#: Vectors are stored little-endian float32. Fixed explicitly rather than left
#: to the platform, so an index built on one machine reads correctly on another.
_DTYPE: Final[str] = "<f4"
_BYTES_PER_VALUE: Final[int] = 4

#: Below this norm a vector carries no direction, so cosine similarity is
#: undefined rather than merely small. Rejected at build time.
_MIN_NORM: Final[float] = 1e-9


class IndexIntegrityError(RuntimeError):
    """The index cannot be trusted to answer for the corpus it was asked about."""


@dataclass(frozen=True, slots=True)
class IndexMetadata:
    """What an index knows about its own provenance."""

    format_version: int
    model_id: str
    dimension: int
    corpus_fingerprint: str
    document_count: int
    built_at: str


@dataclass(frozen=True, slots=True)
class VectorHit:
    doc_id: str
    score: float
    rank: int


def _normalise(vector: Vector) -> Vector:
    """Scale to unit length so cosine similarity is a dot product.

    Done once at build time rather than per query: the arithmetic is identical
    and the alternative is dividing by the same norms on every search.
    """
    norm = float(np.linalg.norm(vector))
    if not np.isfinite(norm) or norm < _MIN_NORM:
        raise IndexIntegrityError(
            "a zero or non-finite vector has no direction, so its cosine "
            "similarity is undefined; refusing to index it"
        )
    unit_vector: Vector = (vector / norm).astype(np.float32)
    return unit_vector


class VectorIndex:
    """An immutable, self-describing set of document vectors."""

    def __init__(
        self,
        metadata: IndexMetadata,
        doc_ids: tuple[str, ...],
        matrix: Vector,
    ) -> None:
        self._metadata = metadata
        self._doc_ids = doc_ids
        self._matrix = matrix

    # ---------------------------------------------------------------- reading

    @property
    def metadata(self) -> IndexMetadata:
        return self._metadata

    @property
    def doc_ids(self) -> tuple[str, ...]:
        return self._doc_ids

    @property
    def dimension(self) -> int:
        return self._metadata.dimension

    @property
    def model_id(self) -> str:
        return self._metadata.model_id

    def search(self, query_vector: tuple[float, ...], *, top_k: int = 3) -> tuple[VectorHit, ...]:
        """Exact cosine similarity, most similar first.

        Ordering is ``(-score, doc_id)``. The tie-break is not decoration: two
        documents can score identically, and without it the order would fall
        back to however the rows happen to sit in the matrix, which is the
        insertion-order dependency this project already removed once from
        lexical search.
        """
        if len(query_vector) != self.dimension:
            raise IndexIntegrityError(
                f"query vector has {len(query_vector)} dimensions, but this "
                f"index holds {self.dimension}-dimensional vectors; they were "
                "produced by different models and cannot be compared"
            )

        query: Vector = np.asarray(query_vector, dtype=np.float32)
        norm = float(np.linalg.norm(query))
        if not np.isfinite(norm) or norm < _MIN_NORM:
            raise IndexIntegrityError(
                "the query vector is zero or non-finite; cosine similarity is undefined"
            )

        scores = self._matrix @ (query / norm)
        order = sorted(
            range(len(self._doc_ids)),
            key=lambda i: (-float(scores[i]), self._doc_ids[i]),
        )
        k = max(1, min(int(top_k), len(self._doc_ids)))
        return tuple(
            VectorHit(doc_id=self._doc_ids[i], score=float(scores[i]), rank=position + 1)
            for position, i in enumerate(order[:k])
        )


    # ---------------------------------------------------------------- loading

    @classmethod
    def load(
        cls,
        path: Path | str,
        *,
        expected_model: str,
        expected_fingerprint: str,
        expected_doc_ids: tuple[str, ...] | None = None,
    ) -> VectorIndex:
        """Open an index, proving it answers for this corpus and this model.

        Every mismatch raises. There is deliberately no ``strict=False``: a
        caller who wants lexical-only retrieval should ask for lexical
        retrieval, not receive it by accident from a vector index that quietly
        decided it could not do its job.
        """
        target = Path(path)
        if not target.exists():
            raise IndexIntegrityError(f"no vector index at {target.name}")

        # Read-only. The runtime holds this handle, and a runtime that can
        # write to the index is a runtime that can be made to poison it.
        uri = f"file:{target.as_posix()}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.OperationalError as exc:
            raise IndexIntegrityError(f"could not open the vector index: {exc}") from exc

        try:
            try:
                rows = dict(connection.execute("SELECT key, value FROM index_metadata"))
            except sqlite3.DatabaseError as exc:
                raise IndexIntegrityError(
                    f"the vector index is unreadable or not an index: {exc}"
                ) from exc

            metadata = cls._read_metadata(rows)

            if metadata.format_version != INDEX_FORMAT_VERSION:
                raise IndexIntegrityError(
                    f"index format version {metadata.format_version} is not "
                    f"version {INDEX_FORMAT_VERSION}; rebuild the index"
                )
            if metadata.model_id != expected_model:
                raise IndexIntegrityError(
                    f"index was built with model {metadata.model_id!r}, but "
                    f"{expected_model!r} is configured; vectors from different "
                    "models are not comparable"
                )
            if metadata.corpus_fingerprint != expected_fingerprint:
                raise IndexIntegrityError(
                    "index was built from a different corpus "
                    f"({metadata.corpus_fingerprint} != {expected_fingerprint}); "
                    "rebuild it rather than serving stale documents"
                )

            stored = connection.execute(
                "SELECT doc_id, vector FROM document_vectors ORDER BY doc_id"
            ).fetchall()
        finally:
            connection.close()

        if len(stored) != metadata.document_count:
            raise IndexIntegrityError(
                f"index declares {metadata.document_count} documents but holds {len(stored)}"
            )

        doc_ids: list[str] = []
        vectors: list[Vector] = []
        expected_bytes = metadata.dimension * _BYTES_PER_VALUE

        for doc_id, blob in stored:
            if len(blob) != expected_bytes:
                raise IndexIntegrityError(
                    f"vector for {doc_id!r} is {len(blob)} bytes, expected "
                    f"{expected_bytes} for {metadata.dimension} dimensions"
                )
            try:
                values: Vector = np.frombuffer(blob, dtype=_DTYPE)
            except (ValueError, struct.error) as exc:
                raise IndexIntegrityError(
                    f"vector for {doc_id!r} is corrupt: {exc}"
                ) from exc
            if not np.all(np.isfinite(values)):
                raise IndexIntegrityError(f"vector for {doc_id!r} contains NaN or infinity")
            doc_ids.append(doc_id)
            vectors.append(values)

        if expected_doc_ids is not None and set(doc_ids) != set(expected_doc_ids):
            missing = sorted(set(expected_doc_ids) - set(doc_ids))
            unknown = sorted(set(doc_ids) - set(expected_doc_ids))
            raise IndexIntegrityError(
                f"index does not match the corpus: missing {missing}, unknown {unknown}"
            )

        matrix = np.vstack(vectors).astype(np.float32) if vectors else np.empty((0, 0))
        return cls(metadata, tuple(doc_ids), matrix)

    @staticmethod
    def _read_metadata(rows: dict[str, str]) -> IndexMetadata:
        required = {
            "format_version",
            "model_id",
            "dimension",
            "corpus_fingerprint",
            "document_count",
            "built_at",
        }
        missing = required - set(rows)
        if missing:
            raise IndexIntegrityError(
                f"index metadata is incomplete; missing {sorted(missing)}"
            )
        try:
            return IndexMetadata(
                format_version=int(rows["format_version"]),
                model_id=rows["model_id"],
                dimension=int(rows["dimension"]),
                corpus_fingerprint=rows["corpus_fingerprint"],
                document_count=int(rows["document_count"]),
                built_at=rows["built_at"],
            )
        except ValueError as exc:
            raise IndexIntegrityError(f"index metadata is malformed: {exc}") from exc


def build_index(
    path: Path | str,
    *,
    vectors: dict[str, tuple[float, ...]],
    model_id: str,
    corpus_fingerprint: str,
    expected_doc_ids: tuple[str, ...],
) -> IndexMetadata:
    """Write an index. Offline only -- never reachable from the request path.

    A module-level function rather than a method, deliberately. As a
    ``@staticmethod`` it was still reachable as ``loaded_index.build(...)``,
    which put a writer on the object the runtime holds. :class:`VectorIndex` is
    now purely a reader: it exposes ``search`` and metadata, and nothing else.

    *expected_doc_ids* is the corpus as ``build_chunks`` sees it. Passing it
    makes the build assert that it embedded exactly the documents that exist,
    so a missing or unknown identifier is a build error rather than an index
    that is quietly short one document.
    """
    if not vectors:
        raise IndexIntegrityError("refusing to build an empty index")

    missing = set(expected_doc_ids) - set(vectors)
    unknown = set(vectors) - set(expected_doc_ids)
    if missing or unknown:
        raise IndexIntegrityError(
            f"index would not match the corpus: missing {sorted(missing)}, "
            f"unknown {sorted(unknown)}"
        )

    dimensions = {len(v) for v in vectors.values()}
    if len(dimensions) != 1:
        raise IndexIntegrityError(
            f"vectors have inconsistent dimensions {sorted(dimensions)}; "
            "they cannot have come from one model"
        )
    dimension = dimensions.pop()
    if dimension < 1:
        raise IndexIntegrityError("vectors must have at least one dimension")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        target.unlink()

    connection = sqlite3.connect(target)
    try:
        connection.execute(
            "CREATE TABLE index_metadata ("
            "  key TEXT PRIMARY KEY,"
            "  value TEXT NOT NULL"
            ")"
        )
        connection.execute(
            "CREATE TABLE document_vectors ("
            "  doc_id TEXT PRIMARY KEY,"
            "  vector BLOB NOT NULL"
            ")"
        )
        metadata = IndexMetadata(
            format_version=INDEX_FORMAT_VERSION,
            model_id=model_id,
            dimension=dimension,
            corpus_fingerprint=corpus_fingerprint,
            document_count=len(vectors),
            built_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
        connection.executemany(
            "INSERT INTO index_metadata (key, value) VALUES (?, ?)",
            [
                ("format_version", str(metadata.format_version)),
                ("model_id", metadata.model_id),
                ("dimension", str(metadata.dimension)),
                ("corpus_fingerprint", metadata.corpus_fingerprint),
                ("document_count", str(metadata.document_count)),
                ("built_at", metadata.built_at),
            ],
        )
        connection.executemany(
            "INSERT INTO document_vectors (doc_id, vector) VALUES (?, ?)",
            [
                (
                    doc_id,
                    _normalise(np.asarray(values, dtype=np.float32))
                    .astype(_DTYPE)
                    .tobytes(),
                )
                for doc_id, values in sorted(vectors.items())
            ],
        )
        connection.commit()
    finally:
        connection.close()

    return metadata
