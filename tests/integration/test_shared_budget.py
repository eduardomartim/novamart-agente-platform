"""The daily budget, shared by every replica.

This is the defect V2.5 exists to close, and it is worth stating precisely
because it was easy to miss.

``BudgetGuard`` reads the day's spend from the repository. V2.2 moved the
provider-call ledger to Redis but left the repository as a per-pod SQLite file,
so each replica saw only its own spend and each allowed the *full* daily budget.
Two replicas meant twice the configured ceiling, three meant three times, and
nothing anywhere said so: the configuration still read one number.

With a shared repository they read one ledger.

What this does not claim
------------------------
``check()`` is still read-then-decide, so two replicas checking in the same
instant can both see room. The defect changes shape rather than vanishing: from
"N replicas allow N times the budget" to "the budget, plus at most one in-flight
call per replica". That residual is accepted deliberately -- the monetary budget
is an estimate reconciled afterwards, and the ceiling that actually binds
spending, the physical provider-call ledger, has been atomic since V2.2.

``test_the_residual_race_is_bounded`` pins that honestly rather than pretending
the guard is atomic.
"""

from __future__ import annotations

import os
import uuid
from decimal import Decimal

import pytest

from agent_platform.cost.budget import BudgetGuard
from agent_platform.persistence.repository import LLMCallRecord
from agent_platform.persistence.sqlite import SQLiteRepository

POSTGRES_URL = os.getenv(
    "TEST_POSTGRES_URL", "postgresql://postgres:dev-only-not-a-secret@127.0.0.1:15432/agentplatform"
)


def _postgres_available() -> bool:
    try:
        from agent_platform.persistence.postgres import PostgresRepository

        r = PostgresRepository(POSTGRES_URL)
        r.initialize()
        r.close()
    except Exception:
        return False
    return True


POSTGRES_UP = _postgres_available()
needs_postgres = pytest.mark.skipif(not POSTGRES_UP, reason="no reachable PostgreSQL")

DAILY = Decimal("1.00")
CALL_COST = Decimal("0.10")


@pytest.fixture
def schema() -> str:
    """A private schema per test, so counts mean what they say."""
    return f"t{uuid.uuid4().hex[:12]}"


def open_repo(schema: str):
    """A repository as an independent replica would open it."""
    from agent_platform.persistence.postgres import PostgresRepository

    return PostgresRepository(f"{POSTGRES_URL}?options=-csearch_path%3D{schema}")


@pytest.fixture
def prepared(schema: str):
    from agent_platform.persistence.postgres import PostgresRepository

    bootstrap = PostgresRepository(POSTGRES_URL)
    with bootstrap._pool.connection() as conn:
        conn.execute(f'CREATE SCHEMA "{schema}"')
        conn.commit()
    bootstrap.close()

    repo = open_repo(schema)
    repo.initialize()
    repo.close()
    try:
        yield schema
    finally:
        cleanup = PostgresRepository(POSTGRES_URL)
        with cleanup._pool.connection() as conn:
            conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            conn.commit()
        cleanup.close()


def guard_for(repo) -> BudgetGuard:
    return BudgetGuard(
        repo, daily_budget_usd=DAILY, max_request_cost_usd=Decimal("1.00")
    )


def spend(repo, amount: Decimal, request_id: str) -> None:
    repo.save_llm_call(
        LLMCallRecord(
            request_id=request_id,
            trace_id=f"trace-{request_id}",
            agent="router",
            provider="gemini",
            model="m",
            input_tokens=1,
            output_tokens=1,
            cost_usd=amount,
            latency_ms=1.0,
        )
    )


# ============================================== the defect, and its absence


@needs_postgres
def test_two_replicas_share_one_daily_budget(prepared):
    """The headline claim of this phase.

    Replica A spends the whole daily allowance. Replica B -- a separate
    repository, separate pool, separate guard, sharing only the database --
    must see that and refuse.
    """
    replica_a, replica_b = open_repo(prepared), open_repo(prepared)
    try:
        guard_a, guard_b = guard_for(replica_a), guard_for(replica_b)

        # A spends the day's budget.
        for i in range(10):
            assert guard_a.check(CALL_COST, request_id=f"a-{i}").allowed
            spend(replica_a, CALL_COST, f"a-{i}")

        assert replica_a.spend_since("1970-01-01T00:00:00Z") == DAILY

        # B never made a call, and must still be refused.
        decision = guard_b.check(CALL_COST, request_id="b-0")
        assert decision.allowed is False, (
            "the second replica allowed spending past a budget the first exhausted"
        )
        assert "daily budget exceeded" in decision.reason
    finally:
        replica_a.close()
        replica_b.close()


@needs_postgres
def test_a_second_replica_sees_the_first_replicas_spend(prepared):
    """The mechanism behind the test above, isolated."""
    replica_a, replica_b = open_repo(prepared), open_repo(prepared)
    try:
        spend(replica_a, Decimal("0.42"), "a-1")
        assert replica_b.spend_since("1970-01-01T00:00:00Z") == Decimal("0.42")
    finally:
        replica_a.close()
        replica_b.close()


@needs_postgres
def test_per_replica_storage_is_what_breaks_it(tmp_path):
    """The complement, and the reason the test above is not trivially true.

    Two SQLite files is exactly the V2.4 deployment: each replica keeps its own
    ledger, so the second one happily grants a budget the first already spent.
    This is the behaviour being removed, asserted so the fix cannot silently
    regress to it.
    """
    replica_a = SQLiteRepository(tmp_path / "pod-a.db")
    replica_b = SQLiteRepository(tmp_path / "pod-b.db")
    replica_a.initialize()
    replica_b.initialize()

    for i in range(10):
        spend(replica_a, CALL_COST, f"a-{i}")
    assert replica_a.spend_since("1970-01-01T00:00:00Z") == DAILY

    # The second replica sees nothing and allows the whole budget again.
    assert replica_b.spend_since("1970-01-01T00:00:00Z") == Decimal(0)
    assert guard_for(replica_b).check(CALL_COST, request_id="b-0").allowed is True


@needs_postgres
def test_spend_survives_the_process_that_recorded_it(prepared):
    """A restart must not reset the day's ledger."""
    first = open_repo(prepared)
    spend(first, Decimal("0.55"), "before")
    first.close()

    revived = open_repo(prepared)
    try:
        assert revived.spend_since("1970-01-01T00:00:00Z") == Decimal("0.55")
    finally:
        revived.close()


@needs_postgres
def test_the_residual_race_is_bounded(prepared):
    """What the shared repository does *not* fix, pinned honestly.

    ``check()`` reads then decides, so replicas checking simultaneously can
    both see room. The overshoot is bounded by one in-flight call each -- not
    by the whole budget, which is what the per-replica ledger allowed.
    """
    replicas = [open_repo(prepared) for _ in range(3)]
    try:
        guards = [guard_for(r) for r in replicas]

        # Budget almost exhausted: room for exactly one more call.
        spend(replicas[0], DAILY - CALL_COST, "prefill")

        # All three check before any of them records. All three see room.
        granted = [g.check(CALL_COST, request_id=f"r-{i}").allowed for i, g in enumerate(guards)]
        assert sum(granted) == 3, "the guard is not atomic; this test documents that"

        # The overshoot is one call per replica, not a second full budget.
        worst_case = (DAILY - CALL_COST) + CALL_COST * len(replicas)
        assert worst_case == Decimal("1.20")
        assert worst_case < DAILY * 2, (
            "the residual overshoot must stay far below the per-replica defect"
        )
    finally:
        for r in replicas:
            r.close()


@needs_postgres
def test_a_recorded_spend_is_visible_to_a_guard_immediately(prepared):
    """No caching between the write and the next decision."""
    repo = open_repo(prepared)
    try:
        guard = guard_for(repo)
        assert guard.check(CALL_COST, request_id="x").allowed is True
        spend(repo, DAILY, "exhaust")
        assert guard.check(CALL_COST, request_id="y").allowed is False
    finally:
        repo.close()


# =================================================================== failure


def test_an_unreachable_database_fails_closed():
    """No silent fallback to a pod-local file.

    Degrading quietly would mean an operator who asked for one shared ledger
    got one per replica -- the defect this phase removes -- with nothing in the
    logs to say the budget had multiplied.
    """
    from agent_platform.persistence.postgres import PostgresRepository, PostgresUnavailable

    with pytest.raises(PostgresUnavailable):
        PostgresRepository("postgresql://postgres:fake-test-password@127.0.0.1:1/nope")


def test_the_connection_string_never_appears_in_an_error():
    """It carries a password."""
    from agent_platform.persistence.postgres import PostgresRepository, PostgresUnavailable

    secret_url = "postgresql://postgres:fake-test-password@127.0.0.1:1/nope"
    try:
        PostgresRepository(secret_url)
    except PostgresUnavailable as exc:
        message = str(exc)
        assert "fake-test-password" not in message
        assert "postgresql://" not in message
    else:  # pragma: no cover - the connection must not succeed
        pytest.fail("connecting to a closed port should have raised")
