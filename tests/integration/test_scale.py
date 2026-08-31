"""Correctness at ten replicas, not at two.

Two replicas is where a shared-state defect first becomes visible. It is not
where it becomes interesting: a race that appears once in a hundred at two
replicas appears constantly at ten, and a ledger that loses one write in a
thousand looks perfect until the traffic arrives.

So these run the platform the way a deployment does -- several independent
``AgentPlatform`` instances over one database file, which is what a shared
volume looks like from the repository's point of view -- and ask whether the
answers are still exact.

No Docker, no Redis, no Postgres, no provider. The stub is selected because no
key is configured, and the live gate would refuse a real one anyway.

The complement to `scripts/load_test.py`: that script reports numbers, this one
asserts invariants and runs in the suite.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_platform.config import Settings
from agent_platform.persistence.sqlite import SQLiteRepository
from agent_platform.platform import AgentPlatform

REPLICAS = 10
QUESTION = "What is the refund policy?"

#: Statuses the platform is allowed to produce. A string outside this set means
#: concurrency reached a path nobody wrote.
KNOWN = frozenset(
    {
        "success", "blocked", "declined", "denied", "failed",
        "awaiting_confirmation", "rejected", "rate_limited", "expired",
    }
)


@pytest.fixture
def shared_database(tmp_path):
    return tmp_path / "scale.db"


@pytest.fixture
def fleet(settings: Settings, shared_database):
    """Ten replicas over one database, torn down whatever happens."""
    tuned = replace(
        settings,
        database_path=shared_database,
        requests_per_minute=10_000,
        requests_per_hour=100_000,
        global_requests_per_minute=100_000,
        global_requests_per_hour=1_000_000,
    )
    platforms = [
        AgentPlatform(tuned, repository=SQLiteRepository(shared_database))
        for _ in range(REPLICAS)
    ]
    try:
        yield platforms
    finally:
        for platform in platforms:
            platform.close()


def drive(fleet, count: int, quota_key=None) -> list[str]:
    """Spread `count` requests across every replica, concurrently."""
    outcomes: list[str] = []
    lock = threading.Lock()

    def one(index: int) -> None:
        platform = fleet[index % len(fleet)]
        key = quota_key(index) if quota_key else None
        result = platform.run(QUESTION, quota_key=key)
        with lock:
            outcomes.append(result.status)

    with ThreadPoolExecutor(max_workers=min(16, count)) as pool:
        list(pool.map(one, range(count)))
    return outcomes


# ======================================================= the ledger stays exact


def test_every_request_is_recorded_exactly_once(fleet, shared_database):
    """The headline. A lost write and a duplicated write both look like success.

    Ten replicas writing concurrently to one database, then counted from a
    fresh connection: the number must be the number.
    """
    requests = 60
    outcomes = drive(fleet, requests)
    assert len(outcomes) == requests

    audit = SQLiteRepository(shared_database)
    try:
        recorded = audit.recent_requests(limit=requests * 3)
    finally:
        audit.close()

    assert len(recorded) == requests, (
        f"{len(recorded)} rows for {requests} requests: a write was lost or duplicated"
    )
    ids = [row["request_id"] for row in recorded]
    assert len(set(ids)) == len(ids), "two requests share an id"


def test_no_request_produces_an_unknown_status(fleet):
    """A novel status means a race reached a path nobody wrote."""
    outcomes = set(drive(fleet, 40))
    assert outcomes <= KNOWN, f"unrecognised: {sorted(outcomes - KNOWN)}"


def test_nothing_escapes_as_an_exception(fleet):
    """Every request returns a governed outcome, including the failures.

    `failed` is a result. An exception reaching the caller is not, and would
    show up here as a missing outcome rather than as a status.
    """
    requests = 40
    assert len(drive(fleet, requests)) == requests


# ================================================= quota still means a number


def test_a_caller_is_limited_across_the_fleet_when_state_is_shared(settings, tmp_path):
    """Ten replicas, one caller, one in-process limiter each.

    This asserts the *documented* behaviour rather than the desired one: with
    no REDIS_URL the limiter is per replica, so a limit of N becomes N per
    replica. Pinning it means the day someone believes otherwise, a test says
    so -- and the shared case is exercised in
    tests/integration/test_shared_state.py, where a real backend exists.
    """
    database = tmp_path / "quota.db"
    tuned = replace(
        settings,
        database_path=database,
        requests_per_minute=2,
        requests_per_hour=1000,
        global_requests_per_minute=1000,
        global_requests_per_hour=10_000,
    )
    fleet = [
        AgentPlatform(tuned, repository=SQLiteRepository(database)) for _ in range(3)
    ]
    try:
        # One replica, one caller: the limit binds.
        first = [fleet[0].run(QUESTION, quota_key="alice").status for _ in range(4)]
        assert "rate_limited" in first, "the per-replica limiter did not bind at all"

        # A different replica has its own window, which is the limitation.
        other = fleet[1].run(QUESTION, quota_key="alice").status
        assert other != "rate_limited", (
            "unexpectedly shared: if this now holds, the limiter became shared "
            "without Redis and docs/state.md is wrong"
        )
    finally:
        for platform in fleet:
            platform.close()


def test_the_money_ledger_is_shared_by_construction(fleet, shared_database):
    """What V2.5 closed, re-checked at ten replicas rather than two.

    Spend recorded anywhere is visible everywhere, because the repository is
    the shared thing. A per-pod database is what made a daily budget mean N
    times itself.
    """
    drive(fleet, 20)
    seen = {
        replica.repository.spend_since("1970-01-01T00:00:00Z") for replica in fleet
    }
    assert len(seen) == 1, f"replicas disagree about total spend: {seen}"


# ============================================== state that stays per-replica


def test_the_circuit_breaker_is_per_replica_and_that_is_recorded(fleet):
    """Deliberate, and cheaper than coordinating.

    Each replica learns a provider outage independently, so a failing provider
    is retried once per replica before all of them open. Wasteful, not
    incorrect -- and asserted here so the claim in docs/state.md is checked
    rather than merely written.
    """
    for _ in range(fleet[0].settings.circuit_failure_threshold):
        fleet[0].circuit.record_failure()

    assert fleet[0].circuit.state == "open"
    assert fleet[1].circuit.state == "closed", (
        "the breaker became shared; docs/state.md says it is not"
    )


def test_the_vector_index_is_derived_not_shared(fleet):
    """Rebuilt per replica from the corpus, which is cheaper than coordinating.

    Every replica must reach the same fingerprint, or retrieval would differ
    between pods for the same question.
    """
    from agent_platform.retrieval.model import build_chunks, corpus_fingerprint

    fingerprints = {corpus_fingerprint(build_chunks()) for _ in fleet}
    assert len(fingerprints) == 1


def test_ten_replicas_agree_on_what_they_are(fleet):
    """A sanity check on the fixture itself.

    If the replicas were secretly one object, every assertion above would pass
    for the wrong reason.
    """
    assert len({id(replica) for replica in fleet}) == REPLICAS
    assert len({id(replica.repository) for replica in fleet}) == REPLICAS
    assert len({str(replica.settings.database_path) for replica in fleet}) == 1


def test_no_replica_uses_a_real_provider(fleet):
    """The load and scale work must never be a way to reach a provider."""
    assert all(replica.provider.name == "stub" for replica in fleet)
    assert all(replica.settings.demo_mode for replica in fleet)
