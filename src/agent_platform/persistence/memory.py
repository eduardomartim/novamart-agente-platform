"""In-memory repository, used by tests and by transient demo runs.

Not simply a convenience: SQLite's ``:memory:`` database is per-connection, and
:class:`~.sqlite.SQLiteRepository` deliberately opens one connection per
operation, so the two are incompatible. This implementation gives tests a fast
backend with the same semantics.
"""

from __future__ import annotations

from dataclasses import asdict
from decimal import Decimal
from typing import Any

from ..security.sanitization import sanitize
from .repository import (
    BaselineRecord,
    EvalResultRecord,
    EvalRunRecord,
    EventRecord,
    LLMCallRecord,
    RequestRecord,
    utc_now_iso,
)


class InMemoryRepository:
    """Dictionary-backed implementation of the repository protocol."""

    def __init__(self) -> None:
        self.requests: dict[str, RequestRecord] = {}
        self.events: list[EventRecord] = []
        self.llm_calls: list[LLMCallRecord] = []
        self.eval_results: list[EvalResultRecord] = []
        self.baselines: dict[str, BaselineRecord] = {}
        #: Underscored because ``eval_runs`` is a method on the repository
        #: protocol; the storage and the accessor must not collide.
        self._eval_runs: dict[str, EvalRunRecord] = {}

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    # ------------------------------------------------------------------ writes

    def save_request(self, record: RequestRecord) -> None:
        self.requests[record.request_id] = record

    update_request = save_request

    def save_event(self, record: EventRecord) -> None:
        # Same final sanitisation gate as the SQLite backend, so tests exercise
        # the real guarantee rather than a weaker one.
        safe_payload, _ = sanitize(record.payload)
        record.payload = safe_payload if isinstance(safe_payload, dict) else {"value": safe_payload}
        if record.error:
            safe_error, _ = sanitize(record.error)
            record.error = str(safe_error)
        self.events.append(record)

    def save_llm_call(self, record: LLMCallRecord) -> None:
        self.llm_calls.append(record)

    def save_eval_run(self, run: EvalRunRecord, results: list[EvalResultRecord]) -> None:
        self._eval_runs[run.run_id] = run
        self.eval_results.extend(results)

    def save_baseline(self, record: BaselineRecord) -> None:
        self.baselines[record.name] = record

    # ------------------------------------------------------------------- reads

    def get_baseline(self, name: str) -> BaselineRecord | None:
        return self.baselines.get(name)

    def spend_since(self, since_iso: str) -> Decimal:
        return sum(
            (c.cost_usd for c in self.llm_calls if c.created_at >= since_iso),
            Decimal(0),
        )

    def cost_by_provider(self) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        for call in self.llm_calls:
            key = (call.provider, call.model)
            entry = grouped.setdefault(
                key,
                {
                    "provider": call.provider,
                    "model": call.model,
                    "calls": 0,
                    "tokens": 0,
                    "cost_usd": Decimal(0),
                    "estimated_calls": 0,
                },
            )
            entry["calls"] += 1
            entry["tokens"] += call.total_tokens
            entry["cost_usd"] += call.cost_usd
            entry["estimated_calls"] += int(call.estimated)
        return sorted(grouped.values(), key=lambda e: e["cost_usd"], reverse=True)

    def recent_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        ordered = sorted(self.requests.values(), key=lambda r: r.created_at, reverse=True)
        return [asdict(r) for r in ordered[:limit]]

    def events_for_request(self, request_id: str) -> list[dict[str, Any]]:
        selected = [e for e in self.events if e.request_id == request_id]
        return [asdict(e) for e in sorted(selected, key=lambda e: e.sequence)]

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        return [asdict(e) for e in list(reversed(self.events))[:limit]]

    def latest_eval_run(self) -> dict[str, Any] | None:
        if not self._eval_runs:
            return None
        newest = max(self._eval_runs.values(), key=lambda r: r.created_at)
        return asdict(newest)

    def eval_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        ordered = sorted(self._eval_runs.values(), key=lambda r: r.created_at, reverse=True)
        return [asdict(r) for r in ordered[:limit]]

    def metrics_summary(self) -> dict[str, Any]:
        total = len(self.requests)
        succeeded = sum(1 for r in self.requests.values() if r.status == "success")
        blocked = sum(1 for r in self.requests.values() if r.blocked)
        tool_calls = [e for e in self.events if e.event_type == "tool_call"]
        return {
            "requests": total,
            "succeeded": succeeded,
            "blocked": blocked,
            "retries": sum(r.retry_count for r in self.requests.values()),
            "success_rate": (succeeded / total) if total else 0.0,
            "avg_latency_ms": (
                sum(r.latency_ms for r in self.requests.values()) / total if total else 0.0
            ),
            "tool_calls": len(tool_calls),
            "tool_failures": sum(1 for e in tool_calls if e.status != "success"),
            # Scoped to policy_decision events for the same reason as the SQL
            # backend: other event types carry the verdict too.
            "policy_denials": sum(
                1
                for e in self.events
                if e.event_type == "policy_decision" and e.policy_decision == "deny"
            ),
            "confirmations": sum(
                1
                for e in self.events
                if e.event_type == "policy_decision"
                and e.policy_decision == "require_confirmation"
            ),
            "injection_flags": sum(1 for e in self.events if e.event_type == "input_flagged"),
            "sensitive_inputs": sum(1 for e in self.events if e.event_type == "input_sensitive"),
            "output_redactions": sum(
                1 for e in self.events if e.event_type == "output_redacted"
            ),
            "total_tokens": sum(c.total_tokens for c in self.llm_calls),
            "llm_calls": len(self.llm_calls),
            "estimated_cost_usd": sum((c.cost_usd for c in self.llm_calls), Decimal(0)),
            "generated_at": utc_now_iso(),
        }
