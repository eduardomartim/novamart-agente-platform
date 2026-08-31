"""Repository interface and the record types that cross it.

The rest of the platform depends only on this module, never on ``sqlite3``.
Swapping in PostgreSQL means writing one more implementation of
:class:`Repository`; no caller changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

NANO: int = 1_000_000_000


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with a trailing ``Z``, safe to sort as text."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def usd_to_nano(amount: Decimal) -> int:
    """Convert a USD Decimal to integer nano-USD for exact storage and SUM()."""
    return int((amount * NANO).to_integral_value())


def nano_to_usd(nano: int) -> Decimal:
    return Decimal(nano) / Decimal(NANO)


@dataclass(slots=True)
class RequestRecord:
    request_id: str
    trace_id: str
    input_digest: str
    status: str
    created_at: str = field(default_factory=utc_now_iso)
    completed_at: str | None = None
    input_chars: int = 0
    route: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    retry_count: int = 0
    latency_ms: float = 0.0
    provider: str | None = None


@dataclass(slots=True)
class EventRecord:
    request_id: str
    trace_id: str
    sequence: int
    event_type: str
    status: str
    created_at: str = field(default_factory=utc_now_iso)
    agent: str | None = None
    tool: str | None = None
    policy_decision: str | None = None
    risk_level: str | None = None
    rule_ids: tuple[str, ...] = ()
    latency_ms: float = 0.0
    error: str | None = None
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class LLMCallRecord:
    request_id: str
    trace_id: str
    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    created_at: str = field(default_factory=utc_now_iso)
    agent: str | None = None
    currency: str = "USD"
    estimated: bool = True
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(slots=True)
class EvalResultRecord:
    run_id: str
    case_id: str
    category: str
    passed: bool
    correctness: float | None = None
    safety: float | None = None
    tool_accuracy: float | None = None
    relevance: float | None = None
    overall: float | None = None
    failure_reasons: tuple[str, ...] = ()
    created_at: str = field(default_factory=utc_now_iso)


@dataclass(slots=True)
class EvalRunRecord:
    run_id: str
    provider: str
    model: str
    dataset: str
    case_count: int
    passed_count: int
    created_at: str = field(default_factory=utc_now_iso)
    correctness: float | None = None
    safety: float | None = None
    tool_accuracy: float | None = None
    relevance: float | None = None
    overall: float | None = None
    judge_used: bool = False


@dataclass(slots=True)
class BaselineRecord:
    name: str
    metrics: dict[str, float]
    created_at: str = field(default_factory=utc_now_iso)
    source_run_id: str | None = None


@runtime_checkable
class Repository(Protocol):
    """Persistence operations required by the platform."""

    def initialize(self) -> None: ...

    def save_request(self, record: RequestRecord) -> None: ...

    def update_request(self, record: RequestRecord) -> None: ...

    def save_event(self, record: EventRecord) -> None: ...

    def save_llm_call(self, record: LLMCallRecord) -> None: ...

    def save_eval_run(self, run: EvalRunRecord, results: list[EvalResultRecord]) -> None: ...

    def save_baseline(self, record: BaselineRecord) -> None: ...

    def get_baseline(self, name: str) -> BaselineRecord | None: ...

    def spend_since(self, since_iso: str) -> Decimal: ...

    def cost_by_provider(self) -> list[dict[str, Any]]: ...

    def recent_requests(self, limit: int = 50) -> list[dict[str, Any]]: ...

    def events_for_request(self, request_id: str) -> list[dict[str, Any]]: ...

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]: ...

    def latest_eval_run(self) -> dict[str, Any] | None: ...

    def eval_runs(self, limit: int = 20) -> list[dict[str, Any]]: ...

    def metrics_summary(self) -> dict[str, Any]: ...

    def close(self) -> None: ...
