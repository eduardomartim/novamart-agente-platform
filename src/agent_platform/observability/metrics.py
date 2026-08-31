"""Metric collection.

Every figure here is derived from persisted rows. When there is no data the
result is zero or ``None`` -- never a placeholder. The dashboard renders these
values directly, so inventing one here would mean inventing one on screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from ..persistence.repository import Repository


@dataclass(frozen=True, slots=True)
class PlatformMetrics:
    """Operational counters for the whole platform."""

    requests: int
    succeeded: int
    blocked: int
    retries: int
    success_rate: float
    avg_latency_ms: float
    tool_calls: int
    tool_failures: int
    policy_denials: int
    confirmations: int
    injection_flags: int
    sensitive_inputs: int
    output_redactions: int
    total_tokens: int
    llm_calls: int
    estimated_cost_usd: Decimal
    generated_at: str

    @property
    def failure_rate(self) -> float:
        return 1.0 - self.success_rate if self.requests else 0.0

    @property
    def tool_failure_rate(self) -> float:
        return (self.tool_failures / self.tool_calls) if self.tool_calls else 0.0

    @property
    def cost_per_request(self) -> Decimal:
        if not self.requests:
            return Decimal(0)
        return self.estimated_cost_usd / Decimal(self.requests)

    @property
    def has_data(self) -> bool:
        return self.requests > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "requests": self.requests,
            "succeeded": self.succeeded,
            "blocked": self.blocked,
            "retries": self.retries,
            "success_rate": self.success_rate,
            "failure_rate": self.failure_rate,
            "avg_latency_ms": self.avg_latency_ms,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "tool_failure_rate": self.tool_failure_rate,
            "policy_denials": self.policy_denials,
            "confirmations": self.confirmations,
            "injection_flags": self.injection_flags,
            "sensitive_inputs": self.sensitive_inputs,
            "output_redactions": self.output_redactions,
            "total_tokens": self.total_tokens,
            "llm_calls": self.llm_calls,
            "estimated_cost_usd": float(self.estimated_cost_usd),
            "cost_per_request": float(self.cost_per_request),
            "generated_at": self.generated_at,
        }


def collect_metrics(repository: Repository) -> PlatformMetrics:
    """Read aggregate counters from storage."""
    summary = repository.metrics_summary()
    return PlatformMetrics(
        requests=int(summary["requests"]),
        succeeded=int(summary["succeeded"]),
        blocked=int(summary["blocked"]),
        retries=int(summary["retries"]),
        success_rate=float(summary["success_rate"]),
        avg_latency_ms=float(summary["avg_latency_ms"]),
        tool_calls=int(summary["tool_calls"]),
        tool_failures=int(summary["tool_failures"]),
        policy_denials=int(summary["policy_denials"]),
        confirmations=int(summary["confirmations"]),
        injection_flags=int(summary["injection_flags"]),
        sensitive_inputs=int(summary.get("sensitive_inputs", 0)),
        output_redactions=int(summary["output_redactions"]),
        total_tokens=int(summary["total_tokens"]),
        llm_calls=int(summary["llm_calls"]),
        estimated_cost_usd=Decimal(str(summary["estimated_cost_usd"])),
        generated_at=str(summary["generated_at"]),
    )
