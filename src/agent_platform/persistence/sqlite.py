"""SQLite implementation of the Repository protocol.

A fresh connection is opened per operation. At demo volumes the cost is
irrelevant, and it removes an entire class of threading bugs when Streamlit
re-runs a script on a different thread than the one that created a pool.
"""

from __future__ import annotations

import json
import sqlite3
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..security.sanitization import sanitize
from .repository import (
    BaselineRecord,
    EvalResultRecord,
    EvalRunRecord,
    EventRecord,
    LLMCallRecord,
    RequestRecord,
    nano_to_usd,
    usd_to_nano,
    utc_now_iso,
)

_SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class SQLiteRepository:
    """Concrete repository backed by a local SQLite file."""

    def __init__(self, database_path: Path | str) -> None:
        self._path = Path(database_path)
        self._initialized = False

    # ---------------------------------------------------------------- plumbing

    def _connect(self) -> sqlite3.Connection:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> None:
        script = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(script)
        self._initialized = True

    def _ensure(self) -> None:
        if not self._initialized:
            self.initialize()

    def close(self) -> None:
        """No-op: connections are per-operation and closed by their context."""
        return None

    # ------------------------------------------------------------------ writes

    def save_request(self, record: RequestRecord) -> None:
        self._ensure()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO requests (
                    request_id, trace_id, created_at, completed_at, input_digest,
                    input_chars, route, status, blocked, block_reason, retry_count,
                    latency_ms, provider
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.request_id,
                    record.trace_id,
                    record.created_at,
                    record.completed_at,
                    record.input_digest,
                    record.input_chars,
                    record.route,
                    record.status,
                    int(record.blocked),
                    record.block_reason,
                    record.retry_count,
                    record.latency_ms,
                    record.provider,
                ),
            )

    #: Updating a request is a full replace; the record carries complete state.
    update_request = save_request

    def save_event(self, record: EventRecord) -> None:
        self._ensure()
        # Final sanitisation gate. The tracer already sanitises, but persisting
        # is the last irreversible step so it is re-checked here. sanitize() is
        # idempotent, so this costs nothing and closes the hole if a future call
        # site ever writes an event directly.
        safe_payload, _ = sanitize(record.payload)
        safe_error: str | None = None
        if record.error:
            safe_error, _ = sanitize(record.error)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    request_id, trace_id, created_at, sequence, agent, event_type,
                    tool, status, policy_decision, risk_level, rule_ids, latency_ms,
                    error, payload
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.request_id,
                    record.trace_id,
                    record.created_at,
                    record.sequence,
                    record.agent,
                    record.event_type,
                    record.tool,
                    record.status,
                    record.policy_decision,
                    record.risk_level,
                    ",".join(record.rule_ids) if record.rule_ids else None,
                    record.latency_ms,
                    safe_error,
                    json.dumps(safe_payload, ensure_ascii=False, default=str),
                ),
            )

    def save_llm_call(self, record: LLMCallRecord) -> None:
        self._ensure()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (
                    request_id, trace_id, created_at, agent, provider, model,
                    input_tokens, output_tokens, total_tokens, cost_nano_usd,
                    currency, estimated, latency_ms
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    record.request_id,
                    record.trace_id,
                    record.created_at,
                    record.agent,
                    record.provider,
                    record.model,
                    record.input_tokens,
                    record.output_tokens,
                    record.total_tokens,
                    usd_to_nano(record.cost_usd),
                    record.currency,
                    int(record.estimated),
                    record.latency_ms,
                ),
            )

    def save_eval_run(self, run: EvalRunRecord, results: list[EvalResultRecord]) -> None:
        self._ensure()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_runs (
                    run_id, created_at, provider, model, dataset, case_count,
                    passed_count, correctness, safety, tool_accuracy, relevance,
                    overall, judge_used
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    run.run_id,
                    run.created_at,
                    run.provider,
                    run.model,
                    run.dataset,
                    run.case_count,
                    run.passed_count,
                    run.correctness,
                    run.safety,
                    run.tool_accuracy,
                    run.relevance,
                    run.overall,
                    int(run.judge_used),
                ),
            )
            conn.executemany(
                """
                INSERT INTO eval_results (
                    run_id, case_id, category, passed, correctness, safety,
                    tool_accuracy, relevance, overall, failure_reasons, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                [
                    (
                        r.run_id,
                        r.case_id,
                        r.category,
                        int(r.passed),
                        r.correctness,
                        r.safety,
                        r.tool_accuracy,
                        r.relevance,
                        r.overall,
                        " | ".join(r.failure_reasons) if r.failure_reasons else None,
                        r.created_at,
                    )
                    for r in results
                ],
            )

    def save_baseline(self, record: BaselineRecord) -> None:
        self._ensure()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO drift_baselines (name, created_at, source_run_id, metrics)
                VALUES (?,?,?,?)
                """,
                (
                    record.name,
                    record.created_at,
                    record.source_run_id,
                    json.dumps(record.metrics),
                ),
            )

    # ------------------------------------------------------------------- reads

    def get_baseline(self, name: str) -> BaselineRecord | None:
        self._ensure()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM drift_baselines WHERE name = ?", (name,)
            ).fetchone()
        if row is None:
            return None
        return BaselineRecord(
            name=row["name"],
            metrics=json.loads(row["metrics"]),
            created_at=row["created_at"],
            source_run_id=row["source_run_id"],
        )

    def spend_since(self, since_iso: str) -> Decimal:
        self._ensure()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_nano_usd), 0) AS total "
                "FROM llm_calls WHERE created_at >= ?",
                (since_iso,),
            ).fetchone()
        return nano_to_usd(int(row["total"]))

    def cost_by_provider(self) -> list[dict[str, Any]]:
        """Spend grouped by provider and model.

        Kept separate rather than blended into one total: a database holding
        both stub rows (structurally zero) and live rows would otherwise report
        an average cost per request that describes neither.
        """
        self._ensure()
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, model,
                       COUNT(*)                        AS calls,
                       COALESCE(SUM(total_tokens), 0)  AS tokens,
                       COALESCE(SUM(cost_nano_usd), 0) AS cost_nano,
                       COALESCE(SUM(estimated), 0)     AS estimated_calls
                FROM llm_calls
                GROUP BY provider, model
                ORDER BY cost_nano DESC
                """
            ).fetchall()
        return [
            {
                "provider": r["provider"],
                "model": r["model"],
                "calls": int(r["calls"]),
                "tokens": int(r["tokens"]),
                "cost_usd": nano_to_usd(int(r["cost_nano"])),
                "estimated_calls": int(r["estimated_calls"]),
            }
            for r in rows
        ]

    def recent_requests(self, limit: int = 50) -> list[dict[str, Any]]:
        self._ensure()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM requests ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def events_for_request(self, request_id: str) -> list[dict[str, Any]]:
        self._ensure()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE request_id = ? ORDER BY sequence ASC",
                (request_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        self._ensure()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_eval_run(self) -> dict[str, Any] | None:
        self._ensure()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def eval_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        self._ensure()
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def metrics_summary(self) -> dict[str, Any]:
        """Aggregate counters backing the dashboard Overview page.

        Every number here is computed from stored rows. Nothing is synthesised:
        an empty database yields zeros, not placeholder values.
        """
        self._ensure()
        with self._connect() as conn:
            requests = conn.execute(
                """
                SELECT
                    COUNT(*)                                 AS total,
                    COALESCE(SUM(status = 'success'), 0)     AS succeeded,
                    COALESCE(SUM(blocked), 0)                AS blocked,
                    COALESCE(SUM(retry_count), 0)            AS retries,
                    COALESCE(AVG(latency_ms), 0)             AS avg_latency_ms
                FROM requests
                """
            ).fetchone()
            tools = conn.execute(
                """
                SELECT
                    COALESCE(SUM(event_type = 'tool_call'), 0)                 AS tool_calls,
                    COALESCE(SUM(event_type = 'tool_call'
                                 AND status != 'success'), 0)                  AS tool_failures,
                    -- Scoped to policy_decision events. tool_call and
                    -- confirmation_requested events also carry a
                    -- policy_decision column, so an unscoped SUM would count
                    -- the same verdict two or three times.
                    COALESCE(SUM(event_type = 'policy_decision'
                                 AND policy_decision = 'deny'), 0)             AS policy_denials,
                    COALESCE(SUM(event_type = 'policy_decision'
                                 AND policy_decision = 'require_confirmation'), 0)
                                                                               AS confirmations,
                    COALESCE(SUM(event_type = 'input_flagged'), 0)             AS injection_flags,
                    COALESCE(SUM(event_type = 'input_sensitive'), 0)           AS sensitive_inputs,
                    COALESCE(SUM(event_type = 'output_redacted'), 0)           AS output_redactions
                FROM events
                """
            ).fetchone()
            cost = conn.execute(
                """
                SELECT
                    COALESCE(SUM(cost_nano_usd), 0) AS cost_nano,
                    COALESCE(SUM(total_tokens), 0)  AS tokens,
                    COUNT(*)                        AS calls
                FROM llm_calls
                """
            ).fetchone()

        total = int(requests["total"])
        return {
            "requests": total,
            "succeeded": int(requests["succeeded"]),
            "blocked": int(requests["blocked"]),
            "retries": int(requests["retries"]),
            "success_rate": (int(requests["succeeded"]) / total) if total else 0.0,
            "avg_latency_ms": float(requests["avg_latency_ms"]),
            "tool_calls": int(tools["tool_calls"]),
            "tool_failures": int(tools["tool_failures"]),
            "policy_denials": int(tools["policy_denials"]),
            "confirmations": int(tools["confirmations"]),
            "injection_flags": int(tools["injection_flags"]),
            "sensitive_inputs": int(tools["sensitive_inputs"]),
            "output_redactions": int(tools["output_redactions"]),
            "total_tokens": int(cost["tokens"]),
            "llm_calls": int(cost["calls"]),
            "estimated_cost_usd": nano_to_usd(int(cost["cost_nano"])),
            "generated_at": utc_now_iso(),
        }
