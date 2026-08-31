"""One body of assertions, three implementations.

``Repository`` is a ``Protocol`` with three implementations: in-memory for
tests, SQLite for a single process, PostgreSQL for replicas that must share a
ledger. Everything above it -- the budget guard, the tracer, the dashboard, the
drift monitor -- talks to the protocol and cannot tell which one it holds.

That is only true if they actually agree. Three implementations that were never
checked against the same expectations are three behaviours wearing one name, and
the divergence surfaces in production rather than here. So every test below runs
against all three, and none of them knows which it is running against.

The Postgres cases skip cleanly when no server is reachable, so the offline
suite stays offline.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.persistence.repository import (
    BaselineRecord,
    EvalResultRecord,
    EvalRunRecord,
    EventRecord,
    LLMCallRecord,
    RequestRecord,
)
from agent_platform.persistence.sqlite import SQLiteRepository

POSTGRES_URL = os.getenv(
    "TEST_POSTGRES_URL", "postgresql://postgres:dev-only-not-a-secret@127.0.0.1:15432/agentplatform"
)


def _postgres_available() -> bool:
    try:
        from agent_platform.persistence.postgres import PostgresRepository

        repo = PostgresRepository(POSTGRES_URL)
        repo.initialize()
        repo.close()
    except Exception:
        return False
    return True


POSTGRES_UP = _postgres_available()

BACKENDS = ["memory", "sqlite"] + (["postgres"] if POSTGRES_UP else [])


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path):
    """A fresh, empty repository of each kind.

    Postgres is shared, so each test gets its own schema: the assertions below
    count rows, and a leftover row from a previous test would make them lie.
    """
    kind = request.param
    if kind == "memory":
        r = InMemoryRepository()
        r.initialize()
        yield r
        return

    if kind == "sqlite":
        r = SQLiteRepository(tmp_path / "conformance.db")
        r.initialize()
        yield r
        r.close()
        return

    from agent_platform.persistence.postgres import PostgresRepository

    schema = f"t{uuid.uuid4().hex[:12]}"
    r = PostgresRepository(POSTGRES_URL)
    with r._pool.connection() as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.execute(f'SET search_path TO "{schema}"')
        conn.commit()
    # Every pooled connection needs the search_path, not just the one above.
    r._pool.close()
    r = PostgresRepository(f"{POSTGRES_URL}?options=-csearch_path%3D{schema}")
    r.initialize()
    try:
        yield r
    finally:
        with r._pool.connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
        r.close()


def a_request(request_id="req-1", **kw) -> RequestRecord:
    fields = {
        "request_id": request_id,
        "trace_id": f"trace-{request_id}",
        "input_digest": "abc123",
        "input_chars": 42,
        "status": "success",
        "blocked": False,
        "block_reason": None,
        "route": "researcher",
        "retry_count": 0,
        "latency_ms": 12.5,
        "provider": "stub",
    }
    fields.update(kw)
    return RequestRecord(**fields)


def an_event(request_id="req-1", sequence=1, **kw) -> EventRecord:
    fields = {
        "request_id": request_id,
        "trace_id": f"trace-{request_id}",
        "sequence": sequence,
        "agent": "router",
        "event_type": "policy_decision",
        "tool": "get_order",
        "status": "success",
        "policy_decision": "allow",
        "risk_level": "low",
        "rule_ids": ["PL001"],
        "latency_ms": 1.5,
        "error": None,
        "payload": {"note": "hello"},
    }
    fields.update(kw)
    return EventRecord(**fields)


def a_call(request_id="req-1", cost="0.000123", **kw) -> LLMCallRecord:
    fields = {
        "request_id": request_id,
        "trace_id": f"trace-{request_id}",
        "agent": "router",
        "provider": "stub",
        "model": "deterministic-stub-v1",
        "input_tokens": 10,
        "output_tokens": 5,
        "cost_usd": Decimal(cost),
        "latency_ms": 3.0,
    }
    fields.update(kw)
    return LLMCallRecord(**fields)


# ============================================================ empty behaviour


def test_an_empty_repository_reports_zeros_not_placeholders(repo):
    summary = repo.metrics_summary()
    assert summary["requests"] == 0
    assert summary["llm_calls"] == 0
    assert summary["estimated_cost_usd"] == Decimal(0)
    assert repo.recent_requests() == []
    assert repo.recent_events() == []
    assert repo.latest_eval_run() is None
    assert repo.get_baseline("nothing") is None
    assert repo.spend_since("1970-01-01T00:00:00Z") == Decimal(0)


# ================================================================== requests


def test_a_request_round_trips(repo):
    repo.save_request(a_request())
    rows = repo.recent_requests()
    assert len(rows) == 1
    assert rows[0]["request_id"] == "req-1"
    assert rows[0]["status"] == "success"
    assert rows[0]["provider"] == "stub"


def test_updating_a_request_replaces_it_rather_than_duplicating(repo):
    repo.save_request(a_request(status="awaiting_confirmation"))
    repo.update_request(a_request(status="success", latency_ms=99.0))

    rows = repo.recent_requests()
    assert len(rows) == 1, "the update inserted a second row"
    assert rows[0]["status"] == "success"


def test_the_blocked_flag_survives_a_round_trip(repo):
    """Stored as INTEGER in SQLite and BOOLEAN in Postgres -- both must read
    back as something truthy for a blocked request and falsy otherwise."""
    repo.save_request(a_request("req-blocked", blocked=True, block_reason="PL005"))
    repo.save_request(a_request("req-ok", blocked=False))

    by_id = {r["request_id"]: r for r in repo.recent_requests()}
    assert bool(by_id["req-blocked"]["blocked"]) is True
    assert bool(by_id["req-ok"]["blocked"]) is False


# ==================================================================== events


def test_events_come_back_in_sequence_order(repo):
    for seq in (3, 1, 2):
        repo.save_event(an_event(sequence=seq))

    events = repo.events_for_request("req-1")
    assert [e["sequence"] for e in events] == [1, 2, 3]


def test_event_payloads_are_stored_sanitised(repo):
    """The repository is the last stop before data rests on disk."""
    repo.save_event(an_event(payload={"key": "AIzaSyD1234567890123456789012345678901c"}))
    stored = repo.events_for_request("req-1")[0]
    assert "AIzaSyD1234567890123456789012345678901c" not in str(stored["payload"])


def test_recent_events_is_bounded(repo):
    for seq in range(1, 11):
        repo.save_event(an_event(sequence=seq))
    assert len(repo.recent_events(limit=4)) == 4


# ============================================================ money, exactly


def test_spend_is_summed_without_float_drift(repo):
    """Money is stored as integer nano-USD precisely so this holds.

    A thousand sub-cent costs added as binary floats drift; the whole point of
    the nano-USD choice is that they do not.
    """
    for i in range(1000):
        repo.save_llm_call(a_call(request_id=f"req-{i}", cost="0.000001"))

    assert repo.spend_since("1970-01-01T00:00:00Z") == Decimal("0.001000")


def test_spend_since_respects_its_boundary(repo):
    repo.save_llm_call(a_call(request_id="old", created_at="2020-01-01T00:00:00Z"))
    repo.save_llm_call(a_call(request_id="new", created_at="2030-01-01T00:00:00Z"))

    assert repo.spend_since("2025-01-01T00:00:00Z") == Decimal("0.000123")
    assert repo.spend_since("1970-01-01T00:00:00Z") == Decimal("0.000246")


def test_cost_is_grouped_by_provider_and_model(repo):
    repo.save_llm_call(a_call(request_id="a", provider="stub", model="m1"))
    repo.save_llm_call(a_call(request_id="b", provider="stub", model="m1"))
    repo.save_llm_call(a_call(request_id="c", provider="gemini", model="m2", cost="0.5"))

    rows = {(r["provider"], r["model"]): r for r in repo.cost_by_provider()}
    assert rows[("stub", "m1")]["calls"] == 2
    assert rows[("gemini", "m2")]["cost_usd"] == Decimal("0.5")


# ============================================================== evaluations


def test_an_eval_run_and_its_results_round_trip(repo):
    run = EvalRunRecord(
        run_id="run-1",
        provider="stub",
        model="m",
        dataset="normal",
        case_count=2,
        passed_count=1,
        correctness=0.5,
        safety=1.0,
        tool_accuracy=1.0,
        relevance=0.9,
        overall=0.85,
        judge_used=False,
    )
    results = [
        EvalResultRecord(
            run_id="run-1", case_id="c1", category="normal", passed=True,
            correctness=1.0, safety=1.0, tool_accuracy=1.0, relevance=1.0,
            overall=1.0, failure_reasons=[],
        ),
        EvalResultRecord(
            run_id="run-1", case_id="c2", category="normal", passed=False,
            correctness=0.0, safety=1.0, tool_accuracy=1.0, relevance=0.8,
            overall=0.7, failure_reasons=["wrong tool"],
        ),
    ]
    repo.save_eval_run(run, results)

    latest = repo.latest_eval_run()
    assert latest is not None
    assert latest["run_id"] == "run-1"
    assert latest["case_count"] == 2
    assert len(repo.eval_runs()) == 1


def test_saving_a_run_twice_replaces_it(repo):
    run = EvalRunRecord(
        run_id="run-1", provider="stub", model="m", dataset="normal",
        case_count=1, passed_count=1, correctness=1.0, safety=1.0,
        tool_accuracy=1.0, relevance=1.0, overall=1.0, judge_used=False,
    )
    repo.save_eval_run(run, [])
    repo.save_eval_run(run, [])
    assert len(repo.eval_runs()) == 1


# ================================================================= baselines


def test_a_baseline_round_trips_with_its_metrics(repo):
    repo.save_baseline(
        BaselineRecord(name="main", metrics={"overall": 0.9, "safety": 1.0}, source_run_id="run-1")
    )
    got = repo.get_baseline("main")
    assert got is not None
    assert got.metrics["overall"] == 0.9
    assert got.source_run_id == "run-1"


def test_saving_a_baseline_twice_replaces_it(repo):
    repo.save_baseline(BaselineRecord(name="main", metrics={"overall": 0.1}))
    repo.save_baseline(BaselineRecord(name="main", metrics={"overall": 0.9}))
    got = repo.get_baseline("main")
    assert got is not None and got.metrics["overall"] == 0.9


# =========================================================== metrics summary


def test_the_summary_counts_what_actually_happened(repo):
    repo.save_request(a_request("r1", status="success"))
    repo.save_request(a_request("r2", status="blocked", blocked=True))
    repo.save_event(an_event("r1", 1, event_type="tool_call", status="success"))
    repo.save_event(an_event("r1", 2, event_type="tool_call", status="error"))
    repo.save_event(an_event("r1", 3, event_type="policy_decision", policy_decision="deny"))
    repo.save_event(
        an_event("r1", 4, event_type="policy_decision", policy_decision="require_confirmation")
    )
    repo.save_llm_call(a_call("r1"))

    s = repo.metrics_summary()
    assert s["requests"] == 2
    assert s["succeeded"] == 1
    assert s["blocked"] == 1
    assert s["tool_calls"] == 2
    assert s["tool_failures"] == 1
    assert s["policy_denials"] == 1
    assert s["confirmations"] == 1
    assert s["llm_calls"] == 1
    assert s["total_tokens"] == 15


def test_a_policy_verdict_is_counted_once(repo):
    """tool_call and confirmation_requested rows also carry a policy_decision
    column, so an unscoped count would tally the same verdict several times."""
    repo.save_event(an_event("r1", 1, event_type="policy_decision", policy_decision="deny"))
    repo.save_event(an_event("r1", 2, event_type="tool_call", policy_decision="deny"))
    repo.save_event(
        an_event("r1", 3, event_type="confirmation_requested", policy_decision="deny")
    )
    assert repo.metrics_summary()["policy_denials"] == 1


def test_every_implementation_returns_the_same_summary_keys(repo):
    assert set(repo.metrics_summary()) == {
        "requests", "succeeded", "blocked", "retries", "success_rate",
        "avg_latency_ms", "tool_calls", "tool_failures", "policy_denials",
        "confirmations", "injection_flags", "sensitive_inputs",
        "output_redactions", "total_tokens", "llm_calls",
        "estimated_cost_usd", "generated_at",
    }
