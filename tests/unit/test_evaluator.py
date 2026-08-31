"""Unit tests for the evaluator, judge policy and drift monitor."""

from __future__ import annotations

import pytest

from agent_platform.drift import DriftMonitor, DriftThresholds, snapshot
from agent_platform.evaluation.evaluator import (
    CATEGORIES,
    CaseScore,
    EvalCase,
    Evaluator,
    load_all,
    load_dataset,
    summarise,
)
from agent_platform.evaluation.judges import LLMJudge
from agent_platform.llm.stub import StubProvider
from agent_platform.observability.metrics import collect_metrics
from agent_platform.platform import RunResult

#: Baselines are provider- and model-scoped; every drift call names its scope.
STUB_SCOPE = {"provider": "stub", "model": "deterministic-stub-v1"}
LIVE_SCOPE = {"provider": "gemini", "model": "gemini-3.5-flash-lite"}

# ------------------------------------------------------------------- datasets


@pytest.mark.parametrize("category", CATEGORIES)
def test_every_dataset_loads(category):
    cases = load_dataset(category)
    assert cases, f"{category} dataset is empty"
    for case in cases:
        assert case.id
        assert case.input
        assert case.expected_behavior, f"{case.id} has no documented expected behaviour"


def test_case_ids_are_unique():
    ids = [case.id for case in load_all()]
    assert len(ids) == len(set(ids))


def test_adversarial_cases_assert_something_meaningful():
    for case in load_dataset("adversarial"):
        assert case.must_block or case.forbidden_tools or case.forbidden_output, (
            f"{case.id} asserts nothing"
        )


# -------------------------------------------------------------------- scoring


def _result(**kwargs) -> RunResult:
    defaults = dict(
        request_id="req-1", trace_id="t-1", status="success", response="ok", route="researcher"
    )
    defaults.update(kwargs)
    return RunResult(**defaults)  # type: ignore[arg-type]


class _FakeEvaluator(Evaluator):
    """Evaluator with tool usage stubbed, so scoring can be tested alone."""

    def __init__(self, platform, tools_used):
        super().__init__(platform)
        self._tools = tools_used

    def _tools_used(self, request_id: str):
        return self._tools


def test_forbidden_tool_execution_fails_safety(platform):
    evaluator = _FakeEvaluator(platform, ["delete_record"])
    case = EvalCase(
        id="c", input="x", category="adversarial", forbidden_tools=("delete_record",)
    )
    score = evaluator.score_case(case, _result())
    assert score.safety == 0.0
    assert score.passed is False


def test_must_block_case_fails_when_it_succeeds(platform):
    evaluator = _FakeEvaluator(platform, [])
    case = EvalCase(id="c", input="x", category="adversarial", must_block=True)
    assert evaluator.score_case(case, _result(status="success")).passed is False
    assert evaluator.score_case(case, _result(status="blocked")).passed is True


def test_forbidden_output_is_detected(platform):
    evaluator = _FakeEvaluator(platform, [])
    case = EvalCase(
        id="c", input="x", category="adversarial", forbidden_output=("AIza",)
    )
    score = evaluator.score_case(case, _result(response="your key is AIzaXYZ"))
    assert score.safety == 0.0


def test_route_mismatch_fails_correctness(platform):
    evaluator = _FakeEvaluator(platform, [])
    case = EvalCase(id="c", input="x", category="normal", expected_route="executor")
    score = evaluator.score_case(case, _result(route="researcher"))
    assert score.correctness == 0.0
    assert score.passed is False


def test_unasserted_dimensions_are_none_not_zero(platform):
    """A dimension a case does not assert must not drag the average down."""
    evaluator = _FakeEvaluator(platform, [])
    case = EvalCase(id="c", input="x", category="normal")
    score = evaluator.score_case(case, _result())
    assert score.correctness is None
    assert score.tool_accuracy is None
    assert score.safety == 1.0
    assert score.overall == 1.0


def test_overall_ignores_missing_dimensions():
    score = CaseScore(
        case_id="c", category="normal", passed=True, safety=1.0, correctness=0.5
    )
    assert score.overall == 0.75


def test_summarise_reports_per_category():
    scores = [
        CaseScore(case_id="a", category="normal", passed=True, safety=1.0),
        CaseScore(case_id="b", category="adversarial", passed=False, safety=0.0),
    ]
    report = summarise(scores)
    assert report["cases"] == 2
    assert report["passed"] == 1
    assert report["by_category"]["adversarial"]["safety"] == 0.0


def test_summarise_of_nothing_is_none_not_zero():
    assert summarise([])["safety"] is None


# ---------------------------------------------------------------------- judge


def test_stub_judge_declines_to_score():
    """The stub must not emit a fixed number that looks like a measurement."""
    judge = LLMJudge(StubProvider())
    assert judge.available is False
    assert judge.score(user_input="x", response="y") is None


def test_relevance_is_absent_under_the_stub(platform):
    evaluator = _FakeEvaluator(platform, [])
    case = EvalCase(id="c", input="x", category="normal")
    assert evaluator.score_case(case, _result()).relevance is None


# ---------------------------------------------------------------------- drift


def test_no_baseline_returns_none(repository):
    assert DriftMonitor(repository).compare({"safety": 1.0}, **STUB_SCOPE) is None


def test_regression_is_detected(repository):
    monitor = DriftMonitor(repository)
    monitor.capture_baseline({"quality": 0.9, "safety": 1.0}, **STUB_SCOPE)
    report = monitor.compare({"quality": 0.5, "safety": 1.0}, **STUB_SCOPE)
    assert report is not None
    assert report.has_regression is True
    assert [d.name for d in report.regressions] == ["quality"]


def test_safety_has_zero_tolerance(repository):
    monitor = DriftMonitor(repository)
    monitor.capture_baseline({"safety": 1.0}, **STUB_SCOPE)
    report = monitor.compare({"safety": 0.99}, **STUB_SCOPE)
    assert report is not None
    assert report.safety_regressed is True


def test_lower_is_better_dimensions_are_read_correctly(repository):
    """A latency improvement must not be reported as a regression."""
    monitor = DriftMonitor(repository)
    monitor.capture_baseline({"latency_ms": 400.0}, **STUB_SCOPE)
    faster = monitor.compare({"latency_ms": 100.0}, **STUB_SCOPE)
    slower = monitor.compare({"latency_ms": 900.0}, **STUB_SCOPE)
    assert faster is not None and slower is not None
    assert faster.has_regression is False
    assert faster.dimensions[0].improved is True
    assert slower.has_regression is True


def test_equal_movements_are_classified_consistently(repository):
    """Two dimensions dropping by the same amount must agree.

    Guards the floating-point boundary: 0.92-0.87 and 0.96-0.91 are not the
    same float, so a naive comparison classified them differently.
    """
    monitor = DriftMonitor(repository, DriftThresholds(quality=0.05, tool_accuracy=0.05))
    monitor.capture_baseline({"quality": 0.92, "tool_accuracy": 0.96}, **STUB_SCOPE)
    report = monitor.compare({"quality": 0.87, "tool_accuracy": 0.91}, **STUB_SCOPE)
    assert report is not None
    assert len({d.regressed for d in report.dimensions}) == 1


def test_snapshot_only_includes_measured_scores(repository):
    metrics = collect_metrics(repository)
    values = snapshot(metrics)
    assert "quality" not in values
    assert "success_rate" in values
    assert "quality" in snapshot(metrics, quality=0.9)


def test_missing_dimensions_are_skipped_not_zeroed(repository):
    monitor = DriftMonitor(repository)
    monitor.capture_baseline({"quality": 0.9, "safety": 1.0}, **STUB_SCOPE)
    report = monitor.compare({"safety": 1.0}, **STUB_SCOPE)
    assert report is not None
    assert [d.name for d in report.dimensions] == ["safety"]


def test_baseline_from_one_provider_is_not_visible_to_another(repository):
    """A stub baseline must not be comparable against live results.

    The two differ in quality and latency by construction, so the comparison
    would report a dramatic regression that describes neither.
    """
    monitor = DriftMonitor(repository)
    monitor.capture_baseline({"quality": 0.95, "safety": 1.0}, **STUB_SCOPE)

    assert monitor.compare({"quality": 0.95, "safety": 1.0}, **STUB_SCOPE) is not None
    assert monitor.compare({"quality": 0.95, "safety": 1.0}, **LIVE_SCOPE) is None


def test_baseline_key_round_trips():
    from agent_platform.drift import baseline_key, parse_baseline_key

    key = baseline_key("default", "gemini", "gemini-3.5-flash-lite")
    assert parse_baseline_key(key) == ("default", "gemini", "gemini-3.5-flash-lite")


def test_legacy_unscoped_key_parses_without_raising():
    from agent_platform.drift import parse_baseline_key

    assert parse_baseline_key("default") == ("default", "unknown", "unknown")
