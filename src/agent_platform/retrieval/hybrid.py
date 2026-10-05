"""Fusing lexical and vector rankings.

The two rankers disagree about what a score means. BM25 returns an unbounded
negative number whose scale depends on the corpus and the query; cosine
similarity returns a bounded [-1, 1]. Adding them directly would let BM25's
magnitude swamp the vector signal on one query and vanish on the next, so each
list is normalised to [0, 1] within its own result set before they are combined.

The weights are experimental parameters, not constants handed down from a
paper. They are named here so an evaluation can vary them, and whichever pair
is chosen must be chosen by measuring the frozen question set -- see
``docs`` and the 7E checkpoint for what was actually measured.

This module contains no provider call and no I/O. It takes two ranked lists and
returns one, which is what makes it testable without a network or an index.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Equal weighting is the honest default to *start* an evaluation from: it
#: asserts nothing about which signal matters more on this corpus. It is not a
#: tuned value and must not be described as one.
DEFAULT_LEXICAL_WEIGHT = 0.5
DEFAULT_VECTOR_WEIGHT = 0.5


@dataclass(frozen=True, slots=True)
class FusedHit:
    doc_id: str
    score: float
    lexical_score: float | None
    vector_score: float | None
    rank: int


def _min_max(values: dict[str, float]) -> dict[str, float]:
    """Rescale to [0, 1] within this result set.

    A single result normalises to 1.0 rather than to 0: it is the best of what
    was found, and mapping it to zero would make a sole confident match
    indistinguishable from no match at all.
    """
    if not values:
        return {}
    lowest = min(values.values())
    highest = max(values.values())
    if highest - lowest < 1e-12:
        return {key: 1.0 for key in values}
    return {
        key: (value - lowest) / (highest - lowest) for key, value in values.items()
    }


def fuse(
    lexical: dict[str, float],
    vector: dict[str, float],
    *,
    lexical_weight: float = DEFAULT_LEXICAL_WEIGHT,
    vector_weight: float = DEFAULT_VECTOR_WEIGHT,
    top_k: int = 3,
) -> tuple[FusedHit, ...]:
    """Combine two ``doc_id -> score`` maps into one ranking.

    BM25 scores arrive in SQLite's convention, where *more negative* is more
    relevant, so they are negated before normalisation. Documents found by only
    one ranker keep their contribution from that ranker and score zero from the
    other -- they are not dropped, because the whole reason for fusing is that
    each ranker finds things the other misses.

    Ordering is ``(-score, doc_id)``: deterministic, with the identifier
    breaking ties rather than dictionary insertion order.
    """
    lexical_normalised = _min_max({k: -v for k, v in lexical.items()})
    vector_normalised = _min_max(dict(vector))

    fused: dict[str, float] = {}
    for doc_id in set(lexical_normalised) | set(vector_normalised):
        fused[doc_id] = (
            lexical_weight * lexical_normalised.get(doc_id, 0.0)
            + vector_weight * vector_normalised.get(doc_id, 0.0)
        )

    order = sorted(fused, key=lambda doc_id: (-fused[doc_id], doc_id))
    k = max(1, min(int(top_k), len(order)))

    return tuple(
        FusedHit(
            doc_id=doc_id,
            score=fused[doc_id],
            lexical_score=lexical.get(doc_id),
            vector_score=vector.get(doc_id),
            rank=position + 1,
        )
        for position, doc_id in enumerate(order[:k])
    )
