"""Shared state, tested as a contract rather than as an implementation.

Two things are being established here and they are different questions.

**Do the two backends agree?** Almost every test below runs against both the
process-local backend and the Redis one, from one body of assertions. Two
implementations behind one interface that were never checked against the same
expectations are two behaviours wearing one name, and the divergence surfaces in
production rather than here.

**Does sharing actually share?** The Redis-only section builds *separate*
platform instances -- separate objects, separate checkpointers, separate
everything except the URL -- and requires that a confirmation suspended on one
can be approved on the other, exactly once, by exactly one of two racing
threads. "Works in one process" is not evidence for any of that.

Skipped cleanly when no Redis is reachable, so the offline suite stays offline.
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid

import pytest

from agent_platform.config import Settings
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.state import (
    LocalBackend,
    SharedPendingRegistry,
    SharedProviderBudget,
    SharedRateLimiter,
    SharedStateUnavailable,
    SuspendedRun,
    build_shared_state,
)
from agent_platform.state.backend import redis_backend_from_url

REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:16379/0")


def _redis_available() -> bool:
    try:
        redis_backend_from_url(REDIS_URL)
    except Exception:
        return False
    return True


REDIS_UP = _redis_available()
needs_redis = pytest.mark.skipif(not REDIS_UP, reason="no reachable Redis for tests")


@pytest.fixture
def unique() -> str:
    """A per-test namespace so parallel or repeated runs never collide."""
    return f"t{uuid.uuid4().hex[:12]}"


def make_backend(kind: str, prefix: str):
    if kind == "local":
        return LocalBackend()
    from redis import Redis

    from agent_platform.state.backend import RedisBackend

    return RedisBackend(Redis.from_url(REDIS_URL), prefix=prefix)


BACKENDS = ["local"] + (["redis"] if REDIS_UP else [])


@pytest.fixture(params=BACKENDS)
def backend(request, unique):
    return make_backend(request.param, unique)


def run_of(request_id: str, text: str = "hello") -> SuspendedRun:
    return SuspendedRun(
        request_id=request_id, trace_id=f"tr-{request_id}", user_input=text, created_at=0.0
    )


# =================================================== the contract, both backends


def test_an_entry_survives_a_round_trip(backend, unique):
    reg = SharedPendingRegistry(backend, max_entries=4, ttl_seconds=60.0, namespace=unique)
    assert reg.put("r1", run_of("r1", "what is the refund policy?")) is True

    got = reg.get("r1")
    assert got is not None
    assert got.request_id == "r1"
    assert got.user_input == "what is the refund policy?"


def test_taking_an_entry_consumes_it(backend, unique):
    reg = SharedPendingRegistry(backend, max_entries=4, ttl_seconds=60.0, namespace=unique)
    reg.put("r1", run_of("r1"))

    assert reg.take("r1") is not None
    assert reg.take("r1") is None, "a consumed confirmation was resumable a second time"
    assert reg.get("r1") is None


def test_the_store_refuses_rather_than_evicting(backend, unique):
    """Evicting would let a flood of suspensions push a legitimate one out."""
    reg = SharedPendingRegistry(backend, max_entries=2, ttl_seconds=60.0, namespace=unique)
    assert reg.put("r1", run_of("r1")) is True
    assert reg.put("r2", run_of("r2")) is True
    assert reg.put("r3", run_of("r3")) is False

    assert reg.get("r1") is not None
    assert reg.get("r2") is not None


def test_an_expired_entry_is_refused_but_still_distinguishable(backend, unique):
    """The 409-vs-404 distinction the platform already makes.

    An aged-out confirmation must report *expired*, not *not found*, which is
    only answerable while the record physically outlives its logical life.
    """
    clock = {"t": 1_000.0}
    reg = SharedPendingRegistry(
        backend, max_entries=4, ttl_seconds=30.0, namespace=unique,
        clock=lambda: clock["t"],
    )
    reg.put("r1", run_of("r1"))

    clock["t"] += 31.0
    assert reg.get("r1") is None, "an expired confirmation was returned"
    assert reg.is_expired("r1") is True, "expired was reported as never-existed"
    assert reg.take("r1") is None, "an expired confirmation was consumable"

    assert reg.is_expired("never-existed") is False


def test_expired_entries_free_capacity(backend, unique):
    clock = {"t": 1_000.0}
    reg = SharedPendingRegistry(
        backend, max_entries=1, ttl_seconds=30.0, namespace=unique,
        clock=lambda: clock["t"],
    )
    assert reg.put("r1", run_of("r1")) is True
    assert reg.put("r2", run_of("r2")) is False
    clock["t"] += 31.0
    assert reg.put("r2", run_of("r2")) is True


def test_the_budget_stops_at_its_limit(backend, unique):
    budget = SharedProviderBudget(backend, daily_limit=3, prefix=f"{unique}:budget")
    assert [budget.try_consume() for _ in range(5)] == [True, True, True, False, False]
    assert budget.used() == 3
    assert budget.remaining() == 0


def test_the_budget_is_keyed_by_day(backend, unique):
    day = {"d": "2026-01-01"}
    budget = SharedProviderBudget(
        backend, daily_limit=1, today=lambda: day["d"], prefix=f"{unique}:budget"
    )
    assert budget.try_consume() is True
    assert budget.try_consume() is False
    day["d"] = "2026-01-02"
    assert budget.try_consume() is True, "a new day did not reset the ledger"


def test_the_rate_limiter_enforces_both_windows(backend, unique):
    clock = {"t": 10_000.0}
    limiter = SharedRateLimiter(
        backend, requests_per_minute=2, requests_per_hour=3,
        clock=lambda: clock["t"], prefix=f"{unique}:rl",
    )
    assert limiter.acquire().allowed is True
    assert limiter.acquire().allowed is True
    assert limiter.acquire().allowed is False, "per-minute limit not enforced"

    clock["t"] += 61.0
    assert limiter.acquire().allowed is True
    assert limiter.acquire().allowed is False, "per-hour limit not enforced"


# ================================================ Redis only: does it share?


@needs_redis
def test_two_backends_on_one_redis_see_the_same_entry(unique):
    """The minimum claim of this phase, stated directly."""
    writer = make_backend("redis", unique)
    reader = make_backend("redis", unique)
    assert writer is not reader

    SharedPendingRegistry(
        writer, max_entries=4, ttl_seconds=60.0, namespace=unique
    ).put("r1", run_of("r1", "written by the first"))

    seen = SharedPendingRegistry(
        reader, max_entries=4, ttl_seconds=60.0, namespace=unique
    ).get("r1")

    assert seen is not None, "a second connection could not see the entry"
    assert seen.user_input == "written by the first"


@needs_redis
def test_a_local_backend_shares_nothing(unique):
    """The complement, so the test above is not passing for a trivial reason."""
    a = SharedPendingRegistry(
        LocalBackend(), max_entries=4, ttl_seconds=60.0, namespace=unique
    )
    b = SharedPendingRegistry(
        LocalBackend(), max_entries=4, ttl_seconds=60.0, namespace=unique
    )
    a.put("r1", run_of("r1"))
    assert b.get("r1") is None


@needs_redis
def test_exactly_one_of_many_racing_takes_wins(unique):
    """Single-use, under real contention.

    Twenty threads, one confirmation. If consumption were a get-then-delete in
    Python rather than one scripted operation, several would be handed the same
    record and the action would execute more than once.
    """
    SharedPendingRegistry(
        make_backend("redis", unique), max_entries=4, ttl_seconds=60.0, namespace=unique
    ).put("r1", run_of("r1"))

    winners: list[SuspendedRun] = []
    lock = threading.Lock()
    start = threading.Barrier(20)

    def contend() -> None:
        reg = SharedPendingRegistry(
            make_backend("redis", unique), max_entries=4, ttl_seconds=60.0,
            namespace=unique,
        )
        start.wait(timeout=30)
        got = reg.take("r1")
        if got is not None:
            with lock:
                winners.append(got)

    threads = [threading.Thread(target=contend) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(winners) == 1, f"{len(winners)} threads consumed the same confirmation"


@needs_redis
def test_concurrent_charges_never_exceed_the_budget(unique):
    """The ceiling holds under contention, from independent connections.

    Forty threads race for a limit of ten. Read-then-increment would overshoot
    here; the overshoot is the bug this replaces.
    """
    limit = 10
    granted: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(40)

    def charge() -> None:
        budget = SharedProviderBudget(
            make_backend("redis", unique), daily_limit=limit, prefix=f"{unique}:budget"
        )
        start.wait(timeout=30)
        ok = budget.try_consume()
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=charge) for _ in range(40)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sum(granted) == limit, f"{sum(granted)} calls granted against a limit of {limit}"


@needs_redis
def test_concurrent_acquires_never_exceed_the_rate_limit(unique):
    limit = 5
    granted: list[bool] = []
    lock = threading.Lock()
    start = threading.Barrier(30)

    def acquire() -> None:
        limiter = SharedRateLimiter(
            make_backend("redis", unique), requests_per_minute=limit,
            requests_per_hour=1000, prefix=f"{unique}:rl",
        )
        start.wait(timeout=30)
        ok = limiter.acquire().allowed
        with lock:
            granted.append(ok)

    threads = [threading.Thread(target=acquire) for _ in range(30)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert sum(granted) == limit, f"{sum(granted)} admitted against a limit of {limit}"


@needs_redis
def test_state_survives_losing_every_object_that_wrote_it(unique):
    """A restart, as far as the process is concerned.

    Everything that touched Redis is dropped and rebuilt from the URL alone.
    """
    reg = SharedPendingRegistry(
        make_backend("redis", unique), max_entries=4, ttl_seconds=120.0, namespace=unique
    )
    reg.put("survivor", run_of("survivor", "outlives its writer"))
    del reg

    revived = SharedPendingRegistry(
        make_backend("redis", unique), max_entries=4, ttl_seconds=120.0, namespace=unique
    )
    got = revived.get("survivor")
    assert got is not None, "state did not survive the objects that created it"
    assert got.user_input == "outlives its writer"


@needs_redis
def test_a_real_ttl_actually_expires(unique):
    """Not the injected clock -- Redis evicting a key on its own."""
    backend = make_backend("redis", unique)
    assert backend.counter_charge(f"{unique}:ttl", limit=5, ttl_seconds=1.0) is True
    assert backend.counter_value(f"{unique}:ttl") == 1
    time.sleep(1.6)
    assert backend.counter_value(f"{unique}:ttl") == 0, "the key outlived its TTL"


@needs_redis
def test_an_unreachable_backend_fails_loudly(unique):
    """Silently degrading to local state would mean the operator asked for one
    shared limit and got N independent ones, with nothing to say so."""
    with pytest.raises(SharedStateUnavailable):
        build_shared_state("redis://127.0.0.1:1/0")


@needs_redis
def test_an_unreachable_rate_limiter_denies(unique):
    """Fails closed, matching V1."""

    class Broken:
        kind = "redis"

        def window_allow(self, *a, **k):
            raise ConnectionError("redis is gone")

    result = SharedRateLimiter(Broken(), 60, 1000, prefix=unique).acquire()  # type: ignore[arg-type]
    assert result.allowed is False
    assert "unavailable" in result.reason


# ============================================ Redis only: two real platforms


@pytest.fixture
def shared_settings(tmp_path, monkeypatch) -> Settings:
    """Settings pointing at a *clean* shared backend.

    The database is flushed first, and the reason is worth recording: without
    it these tests interfere with each other through the shared rate-limiter
    window -- which is exactly the behaviour under test working correctly.
    State that outlives a test is the point of this phase and the hazard of
    testing it.
    """
    if REDIS_UP:
        from redis import Redis

        Redis.from_url(REDIS_URL).flushdb()
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "shared.db"))
    monkeypatch.setenv("REDIS_URL", REDIS_URL)
    return Settings.from_env(load_dotenv_file=False)


@needs_redis
def test_a_confirmation_suspended_on_one_instance_is_approved_on_another(shared_settings):
    """The headline requirement of this phase.

    Two independently constructed platforms. The first suspends a high-risk
    action; the second -- which never saw the request, holds no graph for it and
    has its own checkpointer object -- approves it and the tool runs.
    """
    first = AgentPlatform(shared_settings, repository=InMemoryRepository())
    second = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        assert first.shared_state.kind == "redis"
        assert second.shared_state.kind == "redis"

        created = first.run("Send a message to customer CUS-2001")
        assert created.status == "awaiting_confirmation"

        resumed = second.confirm(created.request_id, approved=True, actor="operator")
        assert resumed.status == "success", (
            f"second instance could not resume: {resumed.response}"
        )

        executed = [
            e.tool
            for e in second.repository.events
            if e.event_type == "tool_call" and e.status == "success"
        ]
        assert "send_email" in executed, "the approved action did not run on the second instance"
    finally:
        first.close()
        second.close()


@needs_redis
def test_a_confirmation_consumed_on_one_instance_is_dead_on_the_other(shared_settings):
    first = AgentPlatform(shared_settings, repository=InMemoryRepository())
    second = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        created = first.run("Send a message to customer CUS-2001")
        assert first.confirm(created.request_id, approved=True).status == "success"

        replay = second.confirm(created.request_id, approved=True)
        assert replay.status == "failed", "a consumed confirmation was replayable elsewhere"

        sends = [
            e
            for p in (first, second)
            for e in p.repository.events
            if e.event_type == "tool_call" and e.tool == "send_email" and e.status == "success"
        ]
        assert len(sends) == 1, f"the action executed {len(sends)} times"
    finally:
        first.close()
        second.close()


@needs_redis
def test_the_platform_reports_which_backend_it_is_using(shared_settings):
    platform = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        assert platform.shared_state.is_shared is True
        assert platform.shared_state.kind == "redis"
    finally:
        platform.close()


@needs_redis
def test_shared_mode_still_runs_the_offline_stub(shared_settings):
    """Redis must not drag a provider in with it."""
    platform = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        assert platform.provider.name == "stub"
        result = platform.run("What is the status of order ORD-1001?")
        assert result.status == "success"
        assert result.provider == "stub"
        assert "ORD-1001" in result.response
    finally:
        platform.close()


@needs_redis
def test_policy_denial_and_the_gateway_are_unaffected_by_shared_state(shared_settings):
    platform = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        result = platform.run("Delete customer record CUS-2001")
        assert result.status == "blocked"
        assert result.policy_decision["decision"] == "deny"

        executed = [
            e for e in platform.repository.events
            if e.event_type == "tool_call" and e.status == "success"
        ]
        assert executed == [], "a denied action executed under shared state"
    finally:
        platform.close()


# ==================================================== what must not be stored


@needs_redis
def test_no_credential_and_no_raw_tool_output_reaches_the_pending_record(shared_settings):
    """The pending record is facts about *which* run, never its results.

    Raw tool output is captured before the output-security layer masks PII, so
    it must not be what crosses a process boundary.
    """
    platform = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        created = platform.run("Send a message to customer CUS-2001")
        raw = platform.shared_state.backend.entry_peek(
            "pending", created.request_id, now=time.time()
        )
        assert raw is not None

        payload = json.loads(raw)
        # Pinned deliberately. The record is identifiers, the original request
        # and counters -- nothing else may quietly join it, because everything
        # here crosses a process boundary and lands in shared storage.
        assert set(payload) == {
            "request_id", "trace_id", "user_input", "created_at",
            "llm_calls", "tool_calls", "tool_output_bytes", "elapsed_seconds",
        }, f"the pending record grew fields: {sorted(payload)}"
        for forbidden in ("tool_result", "validation", "context", "api_key", "gemini"):
            assert forbidden not in raw.lower(), f"{forbidden!r} reached shared state"
    finally:
        platform.close()


@needs_redis
def test_no_api_key_is_written_anywhere_in_redis(shared_settings, unique):
    """Scan the whole keyspace for credential shapes after real traffic."""
    import re

    from redis import Redis

    platform = AgentPlatform(shared_settings, repository=InMemoryRepository())
    try:
        platform.run("What is the refund policy?")
        platform.run("Send a message to customer CUS-2001")
    finally:
        platform.close()

    client = Redis.from_url(REDIS_URL)
    shapes = re.compile(rb"AQ\.[0-9A-Za-z_\-]{30,}|AIzaSy[0-9A-Za-z_\-]{20,}|sk-ant-")
    offenders: list[str] = []
    for key in client.scan_iter(match="*", count=500):
        try:
            if client.type(key) != b"string":
                continue
            value = client.get(key)
        except Exception:  # noqa: S112 - a key mutating mid-scan is not a finding
            continue
        if value and shapes.search(value):
            offenders.append(key.decode("utf-8", "replace"))

    assert offenders == [], f"credential-shaped values found in redis at: {offenders}"
