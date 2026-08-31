"""Retrieval quality, measured against a frozen question set.

The 25 questions below were written during the Phase 7 audit, *before* any
retrieval work began, by reading the twelve articles and recording which one
answers each question. They have not been edited since, and they must not be
edited to make a number move: a benchmark you are allowed to rewrite measures
nothing.

Ground truth is a judgement, and it is stated here in full rather than hidden
in a fixture file, so that anyone who disagrees with a label can see it and
argue with it.

Measured on the substring scan this replaced:

    direct              4/8   50%
    paraphrase          3/4   75%
    terminology         1/9   11%
    correctly empty     1/4   25%
    overall answerable  8/21  38%
    MRR@3                     0.27
    precision@3               0.32

n = 25 is small. These are counts, not estimates with confidence intervals,
and no statistical claim is made from them.
"""

from __future__ import annotations

import pytest

from agent_platform.retrieval.lexical import LexicalRetriever
from agent_platform.tools.dataset import KB_ARTICLES, dataset_digest

DIGEST = "db512de8207f751e"

#: (question, titles that genuinely answer it, family)
EVALUATION: tuple[tuple[str, frozenset[str], str], ...] = (
    # direct: the question uses the article's own vocabulary
    ("What is the return policy?", frozenset({"Returns process", "Refund policy"}), "direct"),
    ("How long does delivery take?", frozenset({"Shipping times"}), "direct"),
    ("What happens when an order arrives damaged?", frozenset({"Damaged items"}), "direct"),
    ("How do I request a refund?", frozenset({"Refund policy", "Returns process"}), "direct"),
    ("What are the shipping times?", frozenset({"Shipping times"}), "direct"),
    ("How do I cancel an order?", frozenset({"Cancellation policy"}), "direct"),
    ("What payment methods are accepted?", frozenset({"Payment methods"}), "direct"),
    ("What are the support hours?", frozenset({"Support hours"}), "direct"),
    # paraphrase: same meaning, overlapping vocabulary
    ("Can a customer return an opened product?", frozenset({"Returns process"}), "paraphrase"),
    ("How do I track my order?", frozenset({"Tracking your order"}), "paraphrase"),
    ("Is there a warranty on furniture?", frozenset({"Warranty"}), "paraphrase"),
    ("Can I change my delivery address?", frozenset({"Address changes"}), "paraphrase"),
    # mismatch: same meaning, different words entirely
    ("When will my parcel arrive?", frozenset({"Shipping times"}), "mismatch"),
    ("My package showed up broken", frozenset({"Damaged items"}), "mismatch"),
    ("I want my money back", frozenset({"Refund policy"}), "mismatch"),
    ("Where is my stuff right now?", frozenset({"Tracking your order"}), "mismatch"),
    ("Can I stop an order before it goes out?", frozenset({"Cancellation policy"}), "mismatch"),
    ("How long is the guarantee on a sofa?", frozenset({"Warranty"}), "mismatch"),
    ("I moved house, send it elsewhere", frozenset({"Address changes"}), "mismatch"),
    ("What perks do big spenders get?", frozenset({"Customer tiers"}), "mismatch"),
    ("How do I get a receipt for tax?", frozenset({"Invoices"}), "mismatch"),
    # no-answer: nothing in the corpus covers these
    ("Do you sell cars?", frozenset(), "no-answer"),
    ("How do I reset my password?", frozenset(), "no-answer"),
    ("What is the CEO's salary?", frozenset(), "no-answer"),
    ("Is the warehouse in Sao Paulo?", frozenset(), "no-answer"),
)

#: The floor 7C had to clear, taken from the audit's BM25 measurement.
GATE_HIT_RATE = 0.52
GATE_MRR = 0.47

#: What the substring scan scored on the identical set.
PREVIOUS_HIT_RATE = 8 / 21
PREVIOUS_MRR = 0.27


@pytest.fixture(scope="module")
def outcomes() -> list[tuple[str, frozenset[str], str, list[str]]]:
    retriever = LexicalRetriever()
    return [
        (question, gold, family, [r.title for r in retriever.retrieve(question)])
        for question, gold, family in EVALUATION
    ]


def _answerable(outcomes):
    return [row for row in outcomes if row[1]]


def hit_rate(outcomes, family: str | None = None) -> tuple[int, int]:
    rows = [r for r in _answerable(outcomes) if family is None or r[2] == family]
    return sum(1 for _, gold, _, got in rows if gold & set(got)), len(rows)


def mrr(outcomes) -> float:
    scores = []
    for _, gold, _, got in _answerable(outcomes):
        rank = next((i + 1 for i, title in enumerate(got) if title in gold), 0)
        scores.append(1 / rank if rank else 0.0)
    return sum(scores) / len(scores)


def precision(outcomes) -> float:
    scores = [
        len([t for t in got if t in gold]) / len(got)
        for _, gold, _, got in _answerable(outcomes)
        if got
    ]
    return sum(scores) / len(scores)


# ------------------------------------------------------------ acceptance gates


def test_overall_hit_rate_clears_the_gate(outcomes):
    hits, total = hit_rate(outcomes)
    assert hits / total >= GATE_HIT_RATE, (
        f"hit-rate@3 regressed to {hits}/{total} = {hits / total:.0%}, "
        f"below the {GATE_HIT_RATE:.0%} gate"
    )


def test_mean_reciprocal_rank_clears_the_gate(outcomes):
    assert mrr(outcomes) >= GATE_MRR, f"MRR@3 regressed to {mrr(outcomes):.2f}"


def test_every_category_improved_or_held_against_the_substring_scan(outcomes):
    """No category may be sacrificed to lift the average."""
    previous = {"direct": 4, "paraphrase": 3, "mismatch": 1}
    for family, before in previous.items():
        hits, _ = hit_rate(outcomes, family)
        assert hits >= before, f"{family} regressed from {before} to {hits}"


def test_it_beats_what_it_replaced(outcomes):
    hits, total = hit_rate(outcomes)
    assert hits / total > PREVIOUS_HIT_RATE
    assert mrr(outcomes) > PREVIOUS_MRR


# ------------------------------------------------------------- per-category


def test_direct_questions(outcomes):
    hits, total = hit_rate(outcomes, "direct")
    assert hits >= 7, f"direct: {hits}/{total}"


def test_paraphrases(outcomes):
    hits, total = hit_rate(outcomes, "paraphrase")
    assert hits >= 3, f"paraphrase: {hits}/{total}"


def test_terminology_mismatch_remains_the_known_weakness(outcomes):
    """Recorded, not celebrated.

    BM25 matches words. When the question and the article share no vocabulary
    -- "I want my money back" against "Refund policy" -- there is nothing for a
    lexical ranker to match on, and no amount of tuning changes that. This is
    the gap a vector layer would have to close, and pinning the number here is
    what will make that claim checkable rather than assumed.
    """
    hits, total = hit_rate(outcomes, "mismatch")
    assert hits >= 1
    assert hits <= 4, (
        f"mismatch scored {hits}/{total}: better than lexical retrieval should "
        "manage. Verify the ground truth before believing it."
    )


def test_unanswerable_questions_mostly_return_nothing(outcomes):
    """Two of the four still return results, and that is recorded honestly.

    Both are driven purely by stopwords ("is", "the") and BM25 scores them at
    -0.000 -- it ranks them last, but nothing drops them, because there is no
    minimum-score threshold yet. No-answer behaviour belongs to the answer
    node, so the threshold is deferred rather than smuggled in here.
    """
    empty = [row for row in outcomes if not row[1]]
    correct = sum(1 for _, _, _, got in empty if not got)
    assert correct >= 2, f"correctly-empty regressed to {correct}/{len(empty)}"


# ------------------------------------------------------------- dataset integrity


def test_the_dataset_did_not_move_under_the_measurement():
    assert dataset_digest() == DIGEST


def test_articles_are_still_only_title_and_body():
    assert all(set(article) == {"title", "body"} for article in KB_ARTICLES)


def test_the_question_set_is_the_one_the_audit_froze():
    assert len(EVALUATION) == 25
    assert len(_answerable([(q, g, f, []) for q, g, f in EVALUATION])) == 21
    families = {family for _, _, family in EVALUATION}
    assert families == {"direct", "paraphrase", "mismatch", "no-answer"}


def test_every_gold_label_names_a_real_article():
    """A typo in the ground truth would silently depress every score."""
    real = {article["title"] for article in KB_ARTICLES}
    for question, gold, _ in EVALUATION:
        assert gold <= real, f"{question!r} references an article that does not exist"
