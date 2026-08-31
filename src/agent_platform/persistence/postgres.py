"""The repository, on PostgreSQL.

V2.2 moved the state that has to be *the same* on every replica into Redis --
pending confirmations, the provider-call ledger, the rate-limiter window. What
it left behind was the durable state: events, requests and, most consequentially,
recorded spend. Those stayed in SQLite, which in Kubernetes means a file in a
pod-local volume.

The consequence was a real defect rather than an inconvenience. ``BudgetGuard``
reads the day's spend from the repository, so with a per-pod database two
replicas each saw their own spend and each allowed the full daily budget: the
configured ceiling quietly multiplied by the replica count. This class closes
that.

What it does not close, stated plainly
--------------------------------------
``BudgetGuard.check()`` is still read-then-decide. Two replicas checking at the
same instant can both see room and both proceed. Postgres makes the *data*
shared; it does not make the decision atomic.

So the defect changes shape rather than disappearing: from "N replicas allow N
times the daily budget" to "the daily budget, plus at most one in-flight call
per replica". That residual is accepted deliberately -- the monetary budget is
an estimate-based pre-check reconciled afterwards by ``record_spend``, and the
ceiling that actually binds spending, the physical provider-call ledger, has
been atomic since V2.2. Serialising every model call behind a lock to tighten an
estimate would cost more than it buys.

Kept close to the SQLite implementation
---------------------------------------
Same schema shape, same nano-USD integers, same ISO-8601 text timestamps. The
conformance suite runs one body of assertions against all three implementations,
and that is only meaningful if they are trying to be the same thing.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

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

if TYPE_CHECKING:  # pragma: no cover - typing only
    from psycopg_pool import ConnectionPool

_SCHEMA_PATH = Path(__file__).with_name("schema_postgres.sql")

#: Pool bounds. The gateway runs tool handlers on a four-worker thread pool and
#: the ASGI layer adds its own, so a handful of connections covers the real
#: concurrency. Sized explicitly rather than left to default: an unbounded pool
#: against a single-instance Postgres is a way to exhaust the server's
#: connection limit under load.
DEFAULT_MIN_SIZE = 1
DEFAULT_MAX_SIZE = 10


class PostgresUnavailable(RuntimeError):
    """The configured database could not be reached.

    Raised rather than falling back to SQLite. A silent downgrade would mean an
    operator who asked for one shared ledger got one per replica, with nothing
    in the logs to say the budget had quietly multiplied.
    """


class PostgresRepository:
    """Durable storage shared by every replica."""

    def __init__(
        self,
        conninfo: str,
        *,
        min_size: int = DEFAULT_MIN_SIZE,
        max_size: int = DEFAULT_MAX_SIZE,
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:  # pragma: no cover - depends on the extra
            raise PostgresUnavailable(
                'DATABASE_URL is set but psycopg is not installed. '
                'Install the postgres extra: pip install -e ".[postgres]"'
            ) from exc

        try:
            self._pool: ConnectionPool = ConnectionPool(
                conninfo, min_size=min_size, max_size=max_size, open=True, timeout=10.0
            )
            self._pool.wait(timeout=15.0)
        except Exception as exc:
            # The connection string carries a password; only the failure type
            # is reported.
            raise PostgresUnavailable(
                f"could not connect to the database: {type(exc).__name__}"
            ) from exc
        self._initialized = False

    # ---------------------------------------------------------------- plumbing

    def initialize(self) -> None:
        """Create the schema if it is absent.

        Idempotent DDL, matching the SQLite implementation. There is no
        versioned migration tool here and that is a known limitation, not an
        oversight -- adding one is a separate decision with its own operational
        weight.
        """
        sql = _SCHEMA_PATH.read_text(encoding="utf-8")
        with self._pool.connection() as conn:
            conn.execute(sql)
            conn.commit()
        self._initialized = True

    def _ensure(self) -> None:
        if not self._initialized:
            self.initialize()

    def close(self) -> None:
        self._pool.close()

    # ------------------------------------------------------------------ writes

    def save_request(self, record: RequestRecord) -> None:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO requests (
                    request_id, trace_id, created_at, completed_at, input_digest,
                    input_chars, route, status, blocked, block_reason, retry_count,
                    latency_ms, provider
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (request_id) DO UPDATE SET
                    trace_id     = EXCLUDED.trace_id,
                    created_at   = EXCLUDED.created_at,
                    completed_at = EXCLUDED.completed_at,
                    input_digest = EXCLUDED.input_digest,
                    input_chars  = EXCLUDED.input_chars,
                    route        = EXCLUDED.route,
                    status       = EXCLUDED.status,
                    blocked      = EXCLUDED.blocked,
                    block_reason = EXCLUDED.block_reason,
                    retry_count  = EXCLUDED.retry_count,
                    latency_ms   = EXCLUDED.latency_ms,
                    provider     = EXCLUDED.provider
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
                    bool(record.blocked),
                    record.block_reason,
                    record.retry_count,
                    record.latency_ms,
                    record.provider,
                ),
            )
            conn.commit()

    #: Updating a request is a full replace; the record carries complete state.
    update_request = save_request

    def save_event(self, record: EventRecord) -> None:
        self._ensure()
        safe_payload, _ = sanitize(record.payload)
        safe_error = record.error
        if record.error:
            safe_error, _ = sanitize(record.error)

        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO events (
                    request_id, trace_id, created_at, sequence, agent, event_type,
                    tool, status, policy_decision, risk_level, rule_ids,
                    latency_ms, error, payload
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
            conn.commit()

    def save_llm_call(self, record: LLMCallRecord) -> None:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO llm_calls (
                    request_id, trace_id, created_at, agent, provider, model,
                    input_tokens, output_tokens, total_tokens, cost_nano_usd,
                    currency, estimated, latency_ms
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
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
                    bool(record.estimated),
                    record.latency_ms,
                ),
            )
            conn.commit()

    def save_eval_run(self, run: EvalRunRecord, results: list[EvalResultRecord]) -> None:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO eval_runs (
                    run_id, created_at, provider, model, dataset, case_count,
                    passed_count, correctness, safety, tool_accuracy, relevance,
                    overall, judge_used
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (run_id) DO UPDATE SET
                    created_at    = EXCLUDED.created_at,
                    provider      = EXCLUDED.provider,
                    model         = EXCLUDED.model,
                    dataset       = EXCLUDED.dataset,
                    case_count    = EXCLUDED.case_count,
                    passed_count  = EXCLUDED.passed_count,
                    correctness   = EXCLUDED.correctness,
                    safety        = EXCLUDED.safety,
                    tool_accuracy = EXCLUDED.tool_accuracy,
                    relevance     = EXCLUDED.relevance,
                    overall       = EXCLUDED.overall,
                    judge_used    = EXCLUDED.judge_used
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
                    bool(run.judge_used),
                ),
            )
            for result in results:
                conn.execute(
                    """
                    INSERT INTO eval_results (
                        run_id, case_id, category, passed, correctness, safety,
                        tool_accuracy, relevance, overall, failure_reasons, created_at
                    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                    """,
                    (
                        result.run_id,
                        result.case_id,
                        result.category,
                        bool(result.passed),
                        result.correctness,
                        result.safety,
                        result.tool_accuracy,
                        result.relevance,
                        result.overall,
                        json.dumps(list(result.failure_reasons or [])),
                        result.created_at,
                    ),
                )
            conn.commit()

    def save_baseline(self, record: BaselineRecord) -> None:
        self._ensure()
        with self._pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO drift_baselines (name, created_at, source_run_id, metrics)
                VALUES (%s,%s,%s,%s)
                ON CONFLICT (name) DO UPDATE SET
                    created_at    = EXCLUDED.created_at,
                    source_run_id = EXCLUDED.source_run_id,
                    metrics       = EXCLUDED.metrics
                """,
                (
                    record.name,
                    record.created_at,
                    record.source_run_id,
                    json.dumps(record.metrics),
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------- reads

    def _query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        from psycopg.rows import dict_row

        self._ensure()
        with (
            self._pool.connection() as conn,
            conn.cursor(row_factory=dict_row) as cur,
        ):
            cur.execute(sql, params)
            return list(cur.fetchall())

    def _one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self._query(sql, params)
        return rows[0] if rows else None

    def get_baseline(self, name: str) -> BaselineRecord | None:
        row = self._one("SELECT * FROM drift_baselines WHERE name = %s", (name,))
        if row is None:
            return None
        return BaselineRecord(
            name=row["name"],
            metrics=json.loads(row["metrics"]),
            created_at=row["created_at"],
            source_run_id=row["source_run_id"],
        )

    def spend_since(self, since_iso: str) -> Decimal:
        """Recorded spend since an ISO-8601 UTC instant.

        The comparison is lexicographic on a text column, which is exactly what
        the SQLite implementation does. With a fixed UTC format that ordering is
        chronological, and keeping it identical is what lets one conformance
        suite cover both.
        """
        row = self._one(
            "SELECT COALESCE(SUM(cost_nano_usd), 0) AS total "
            "FROM llm_calls WHERE created_at >= %s",
            (since_iso,),
        )
        return nano_to_usd(int(row["total"]) if row else 0)

    def cost_by_provider(self) -> list[dict[str, Any]]:
        rows = self._query(
            """
            SELECT provider, model,
                   COUNT(*)                                       AS calls,
                   COALESCE(SUM(total_tokens), 0)                 AS tokens,
                   COALESCE(SUM(cost_nano_usd), 0)                AS cost_nano,
                   COUNT(*) FILTER (WHERE estimated)              AS estimated_calls
            FROM llm_calls
            GROUP BY provider, model
            ORDER BY cost_nano DESC
            """
        )
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
        return self._query(
            "SELECT * FROM requests ORDER BY created_at DESC LIMIT %s", (limit,)
        )

    def events_for_request(self, request_id: str) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM events WHERE request_id = %s ORDER BY sequence ASC",
            (request_id,),
        )

    def recent_events(self, limit: int = 200) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM events ORDER BY id DESC LIMIT %s", (limit,)
        )

    def latest_eval_run(self) -> dict[str, Any] | None:
        return self._one("SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT 1")

    def eval_runs(self, limit: int = 20) -> list[dict[str, Any]]:
        return self._query(
            "SELECT * FROM eval_runs ORDER BY created_at DESC LIMIT %s", (limit,)
        )

    def metrics_summary(self) -> dict[str, Any]:
        """Aggregate counters backing the dashboard Overview page.

        Every number is computed from stored rows; an empty database yields
        zeros rather than placeholders. The ``FILTER`` clauses replace SQLite's
        ``SUM(condition)`` idiom, which is not valid here -- Postgres has no
        implicit boolean-to-integer coercion.
        """
        requests = self._one(
            """
            SELECT
                COUNT(*)                                       AS total,
                COUNT(*) FILTER (WHERE status = 'success')     AS succeeded,
                COUNT(*) FILTER (WHERE blocked)                AS blocked,
                COALESCE(SUM(retry_count), 0)                  AS retries,
                COALESCE(AVG(latency_ms), 0)                   AS avg_latency_ms
            FROM requests
            """
        ) or {}
        tools = self._one(
            """
            SELECT
                COUNT(*) FILTER (WHERE event_type = 'tool_call')          AS tool_calls,
                COUNT(*) FILTER (WHERE event_type = 'tool_call'
                                 AND status <> 'success')                 AS tool_failures,
                -- Scoped to policy_decision events. tool_call and
                -- confirmation_requested rows also carry a policy_decision
                -- column, so an unscoped count would tally the same verdict
                -- two or three times.
                COUNT(*) FILTER (WHERE event_type = 'policy_decision'
                                 AND policy_decision = 'deny')            AS policy_denials,
                COUNT(*) FILTER (WHERE event_type = 'policy_decision'
                                 AND policy_decision = 'require_confirmation')
                                                                          AS confirmations,
                COUNT(*) FILTER (WHERE event_type = 'input_flagged')      AS injection_flags,
                COUNT(*) FILTER (WHERE event_type = 'input_sensitive')    AS sensitive_inputs,
                COUNT(*) FILTER (WHERE event_type = 'output_redacted')    AS output_redactions
            FROM events
            """
        ) or {}
        cost = self._one(
            """
            SELECT
                COUNT(*)                          AS llm_calls,
                COALESCE(SUM(total_tokens), 0)    AS total_tokens,
                COALESCE(SUM(cost_nano_usd), 0)   AS cost_nano
            FROM llm_calls
            """
        ) or {}

        total = int(requests.get("total", 0) or 0)
        succeeded = int(requests.get("succeeded", 0) or 0)
        return {
            "requests": total,
            "succeeded": succeeded,
            "blocked": int(requests.get("blocked", 0) or 0),
            "retries": int(requests.get("retries", 0) or 0),
            "success_rate": (succeeded / total) if total else 0.0,
            "avg_latency_ms": float(requests.get("avg_latency_ms", 0) or 0),
            "tool_calls": int(tools.get("tool_calls", 0) or 0),
            "tool_failures": int(tools.get("tool_failures", 0) or 0),
            "policy_denials": int(tools.get("policy_denials", 0) or 0),
            "confirmations": int(tools.get("confirmations", 0) or 0),
            "injection_flags": int(tools.get("injection_flags", 0) or 0),
            "sensitive_inputs": int(tools.get("sensitive_inputs", 0) or 0),
            "output_redactions": int(tools.get("output_redactions", 0) or 0),
            "total_tokens": int(cost.get("total_tokens", 0) or 0),
            "llm_calls": int(cost.get("llm_calls", 0) or 0),
            "estimated_cost_usd": nano_to_usd(int(cost.get("cost_nano", 0) or 0)),
            "generated_at": utc_now_iso(),
        }


__all__ = [
    "DEFAULT_MAX_SIZE",
    "DEFAULT_MIN_SIZE",
    "PostgresRepository",
    "PostgresUnavailable",
]
