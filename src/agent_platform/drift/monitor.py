"""Drift monitoring: baseline capture and comparison.

An important limitation, stated plainly because it is easy to oversell: drift
detection reports that behaviour *changed*. It does not explain why, and it
does not establish that a change was caused by the model, the data, or the
code. A breached threshold is a prompt to investigate, not a diagnosis.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal

from ..observability.metrics import PlatformMetrics
from ..persistence.repository import BaselineRecord, Repository, utc_now_iso

DEFAULT_BASELINE: Final[str] = "default"

#: Separator for the composite baseline key. Baselines are scoped by provider
#: and model because comparing across them is meaningless: the deterministic
#: stub and a live model produce different quality and latency by construction,
#: so a stub baseline measured against live results would report a catastrophic
#: regression that says nothing about either. Scoping the key makes that
#: comparison structurally impossible rather than merely discouraged.
SCOPE_SEPARATOR: Final[str] = "::"


def baseline_key(name: str, provider: str, model: str) -> str:
    """Compose the storage key for a provider- and model-scoped baseline."""
    return f"{name}{SCOPE_SEPARATOR}{provider}{SCOPE_SEPARATOR}{model}"


def parse_baseline_key(key: str) -> tuple[str, str, str]:
    """Split a composite key back into ``(name, provider, model)``.

    Tolerates a bare legacy name so a database written before scoping existed
    still reads back rather than raising.
    """
    parts = key.split(SCOPE_SEPARATOR)
    if len(parts) == 3:
        return parts[0], parts[1], parts[2]
    return key, "unknown", "unknown"

Direction = Literal["higher_is_better", "lower_is_better"]

#: Comparisons are made with a small slack so that a movement of exactly the
#: tolerance is treated consistently. Without it, two dimensions that both drop
#: by 0.05 can land on opposite sides of the threshold purely because of binary
#: floating-point representation (0.92 - 0.87 and 0.96 - 0.91 do not produce
#: the same value). Tolerance is inclusive: moving exactly by it is not a
#: regression.
_EPSILON: Final[float] = 1e-9

#: Which way each dimension should move. Getting this wrong would report a
#: latency improvement as a regression.
DIMENSION_DIRECTION: Final[dict[str, Direction]] = {
    "quality": "higher_is_better",
    "safety": "higher_is_better",
    "tool_accuracy": "higher_is_better",
    "success_rate": "higher_is_better",
    "latency_ms": "lower_is_better",
    "cost_per_request": "lower_is_better",
    "failure_rate": "lower_is_better",
}


@dataclass(frozen=True, slots=True)
class DriftThresholds:
    """How much movement is tolerated before a dimension is flagged.

    Absolute for rates (a 0.05 drop in safety), relative for latency and cost
    (a 50% increase), because the acceptable range of those depends on the
    baseline value rather than on a fixed number of milliseconds.
    """

    quality: float = 0.05
    safety: float = 0.0
    tool_accuracy: float = 0.05
    success_rate: float = 0.05
    failure_rate: float = 0.05
    latency_relative: float = 0.50
    cost_relative: float = 0.50

    def tolerance_for(self, dimension: str, baseline: float) -> float:
        if dimension == "latency_ms":
            return abs(baseline) * self.latency_relative
        if dimension == "cost_per_request":
            return abs(baseline) * self.cost_relative
        return {
            "quality": self.quality,
            "safety": self.safety,
            "tool_accuracy": self.tool_accuracy,
            "success_rate": self.success_rate,
            "failure_rate": self.failure_rate,
        }.get(dimension, 0.05)


@dataclass(frozen=True, slots=True)
class DriftDimension:
    name: str
    baseline: float
    current: float
    tolerance: float

    @property
    def delta(self) -> float:
        return self.current - self.baseline

    @property
    def direction(self) -> Direction:
        return DIMENSION_DIRECTION.get(self.name, "higher_is_better")

    @property
    def regressed(self) -> bool:
        """True when the dimension moved the wrong way by more than tolerance."""
        limit = self.tolerance + _EPSILON
        if self.direction == "higher_is_better":
            return self.delta < -limit
        return self.delta > limit

    @property
    def improved(self) -> bool:
        limit = self.tolerance + _EPSILON
        if self.direction == "higher_is_better":
            return self.delta > limit
        return self.delta < -limit

    def as_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.name,
            "baseline": self.baseline,
            "current": self.current,
            "delta": self.delta,
            "tolerance": self.tolerance,
            "direction": self.direction,
            "regressed": self.regressed,
            "improved": self.improved,
        }


@dataclass(slots=True)
class DriftReport:
    baseline_name: str
    dimensions: list[DriftDimension] = field(default_factory=list)
    baseline_created_at: str = ""
    generated_at: str = field(default_factory=utc_now_iso)

    @property
    def regressions(self) -> list[DriftDimension]:
        return [d for d in self.dimensions if d.regressed]

    @property
    def has_regression(self) -> bool:
        return bool(self.regressions)

    @property
    def safety_regressed(self) -> bool:
        """Safety is the one dimension with zero tolerance."""
        return any(d.name == "safety" and d.regressed for d in self.dimensions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline_name": self.baseline_name,
            "baseline_created_at": self.baseline_created_at,
            "generated_at": self.generated_at,
            "has_regression": self.has_regression,
            "safety_regressed": self.safety_regressed,
            "dimensions": [d.as_dict() for d in self.dimensions],
        }


def snapshot(
    metrics: PlatformMetrics,
    *,
    quality: float | None = None,
    safety: float | None = None,
    tool_accuracy: float | None = None,
) -> dict[str, float]:
    """Build a comparable metric snapshot.

    Evaluation scores are passed in rather than read from storage so that a
    snapshot always pairs operational metrics with the evaluation run that
    produced them.
    """
    values: dict[str, float] = {
        "success_rate": metrics.success_rate,
        "failure_rate": metrics.failure_rate,
        "latency_ms": metrics.avg_latency_ms,
        "cost_per_request": float(metrics.cost_per_request),
    }
    if quality is not None:
        values["quality"] = quality
    if safety is not None:
        values["safety"] = safety
    if tool_accuracy is not None:
        values["tool_accuracy"] = tool_accuracy
    return values


class DriftMonitor:
    """Captures baselines and compares later snapshots against them."""

    def __init__(
        self, repository: Repository, thresholds: DriftThresholds | None = None
    ) -> None:
        self._repository = repository
        self.thresholds = thresholds or DriftThresholds()

    def capture_baseline(
        self,
        metrics: dict[str, float],
        *,
        name: str = DEFAULT_BASELINE,
        provider: str,
        model: str,
        source_run_id: str | None = None,
    ) -> BaselineRecord:
        """Store a baseline scoped to the provider and model that produced it."""
        record = BaselineRecord(
            name=baseline_key(name, provider, model),
            metrics=dict(metrics),
            source_run_id=source_run_id,
        )
        self._repository.save_baseline(record)
        return record

    def baseline(
        self, name: str = DEFAULT_BASELINE, *, provider: str, model: str
    ) -> BaselineRecord | None:
        return self._repository.get_baseline(baseline_key(name, provider, model))

    def compare(
        self,
        current: dict[str, float],
        *,
        name: str = DEFAULT_BASELINE,
        provider: str,
        model: str,
    ) -> DriftReport | None:
        """Compare *current* against the baseline for this provider and model.

        Returns ``None`` when no matching baseline exists -- including when a
        baseline exists for a *different* provider. Callers report that as "no
        baseline captured for this provider" rather than comparing against
        something incomparable or against zero, either of which would render
        every dimension as a catastrophic regression.
        """
        record = self.baseline(name, provider=provider, model=model)
        if record is None:
            return None

        dimensions: list[DriftDimension] = []
        for dimension, baseline_value in record.metrics.items():
            if dimension not in current:
                continue
            dimensions.append(
                DriftDimension(
                    name=dimension,
                    baseline=float(baseline_value),
                    current=float(current[dimension]),
                    tolerance=self.thresholds.tolerance_for(dimension, float(baseline_value)),
                )
            )

        return DriftReport(
            baseline_name=record.name,
            dimensions=sorted(dimensions, key=lambda d: d.name),
            baseline_created_at=record.created_at,
        )
