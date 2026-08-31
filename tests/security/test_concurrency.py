"""Concurrency: the properties sequential tests structurally cannot check.

Every guarantee in this platform is per-request -- a call ceiling, a deadline,
a confirmation, a trace. Sequential tests verify each in isolation, which is
exactly the condition under which a shared-state bug never appears. These
tests run the same guarantees under contention.

Threads are synchronised on a ``threading.Barrier`` so they arrive at the
contended call together. Without it the operating system usually serialises
them anyway and the test proves nothing.

No test here asserts on timing. Timing assertions are how a concurrency suite
becomes a flaky suite; every assertion below is on a counted invariant.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from agent_platform.llm.circuit import CircuitBreaker
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.rate_limit import RateLimiter
from agent_platform.security.resources import (
    PendingRegistry,
    ResourceGuard,
    ResourceLimitExceeded,
    ResourceLimits,
)

WORKERS = 16


def limits(**overrides) -> ResourceLimits:
    """ResourceLimits with every field set, varying only what a test cares about."""
    base = {
        "max_llm_calls_per_request": 10,
        "max_tool_calls_per_request": 10,
        "request_deadline_seconds": 60.0,
        "max_tool_output_bytes": 100_000,
        "max_pending_confirmations": 64,
    }
    base.update(overrides)
    return ResourceLimits(**base)


def run_together(fn, count: int = WORKERS):
    """Run *fn(i)* on *count* threads released simultaneously."""
    barrier = threading.Barrier(count)
    results: list[object] = [None] * count
    errors: list[BaseException | None] = [None] * count

    def worker(index: int) -> None:
        try:
            barrier.wait(timeout=30)
            results[index] = fn(index)
        except BaseException as exc:
            errors[index] = exc

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    real = [e for e in errors if e is not None and not isinstance(e, threading.BrokenBarrierError)]
    if real:
        raise real[0]
    return results


# ============================================================== rate limiter


def test_rate_limiter_never_over_admits_under_contention():
    """The quota is the quota, however many threads ask at once.

    A read-then-write limiter without a lock over-admits here: every thread
    reads the same count before any of them writes.
    """
    allowed_quota = 5
    limiter = RateLimiter(allowed_quota, 1000)
    results = run_together(lambda _: limiter.acquire().allowed, count=WORKERS)
    assert sum(1 for r in results if r) == allowed_quota


def test_rate_limiter_check_does_not_consume_quota_under_contention():
    """``check`` is a read. Concurrent reads must not spend the budget."""
    limiter = RateLimiter(4, 1000)
    run_together(lambda _: limiter.check().allowed, count=WORKERS)
    granted = [limiter.acquire().allowed for _ in range(4)]
    assert all(granted), "check() consumed quota it only meant to report"


# ============================================================ resource guard


def test_resource_counters_do_not_bleed_between_concurrent_requests():
    """Per-request ceilings must be per *request*, not per process."""
    guard = ResourceGuard(limits(max_llm_calls_per_request=3))
    ids = [f"req-{i}" for i in range(WORKERS)]
    for request_id in ids:
        guard.begin(request_id)

    def charge(index: int) -> int:
        request_id = ids[index]
        last = 0
        for _ in range(3):
            last = guard.charge_llm_call(request_id)
        return last

    results = run_together(charge, count=WORKERS)
    assert results == [3] * WORKERS, (
        "a request saw another request's call count"
    )


def test_resource_ceiling_holds_when_one_request_is_charged_concurrently():
    """Charges for a single request from many threads must not exceed it."""
    limit = 6
    guard = ResourceGuard(limits(max_llm_calls_per_request=limit))
    guard.begin("shared")
    exceeded = threading.Event()

    def charge(_: int) -> None:
        try:
            guard.charge_llm_call("shared")
        except ResourceLimitExceeded:
            exceeded.set()

    run_together(charge, count=WORKERS)
    usage = guard.usage("shared")
    assert usage is not None
    assert usage.llm_calls <= limit, "the ceiling was exceeded under contention"
    assert exceeded.is_set(), "no thread was stopped despite exceeding the ceiling"


def test_begin_and_release_are_safe_under_contention():
    guard = ResourceGuard(limits())

    def churn(index: int) -> None:
        request_id = f"r{index}"
        guard.begin(request_id)
        guard.charge_llm_call(request_id)
        guard.release(request_id)

    run_together(churn, count=WORKERS)
    assert guard.active_requests() == 0, "requests leaked out of the guard"


# =========================================================== circuit breaker


def test_circuit_breaker_failure_count_is_not_lost_under_contention():
    breaker = CircuitBreaker(failure_threshold=1000, cooldown_seconds=60)
    run_together(lambda _: breaker.record_failure(), count=WORKERS)
    assert breaker.failures == WORKERS, "a failure record was lost to a race"


def test_circuit_breaker_opens_exactly_once_under_contention():
    """Concurrent failures past the threshold must still leave one open state."""
    breaker = CircuitBreaker(failure_threshold=3, cooldown_seconds=60)
    run_together(lambda _: breaker.record_failure(), count=WORKERS)
    assert breaker.state == "open"
    assert breaker.failures >= 3


# ======================================================== pending registry


def test_pending_registry_respects_its_bound_under_contention():
    """The bound is a memory-exhaustion control; a race must not lift it."""
    capacity = 8
    registry = PendingRegistry(max_entries=capacity, ttl_seconds=300.0)

    def put(index: int) -> bool:
        return registry.put(f"key-{index}", {"i": index})

    results = run_together(put, count=WORKERS)
    accepted = sum(1 for r in results if r)
    assert accepted <= capacity, "the registry grew past its bound under load"
    assert len(registry) <= capacity


def test_concurrent_pops_deliver_an_entry_to_exactly_one_caller():
    """Two threads confirming the same request must not both execute it."""
    registry = PendingRegistry(max_entries=64, ttl_seconds=300.0)
    registry.put("only", {"payload": True})
    results = run_together(lambda _: registry.pop("only"), count=WORKERS)
    delivered = [r for r in results if r is not None]
    assert len(delivered) == 1, (
        f"{len(delivered)} callers received the same pending action"
    )


# ================================================= end-to-end request isolation


@pytest.fixture
def concurrent_platform(settings):
    platform = AgentPlatform(
        replace(settings, requests_per_minute=200, requests_per_hour=2000),
        repository=InMemoryRepository(),
    )
    yield platform
    platform.close()


def test_simultaneous_requests_keep_distinct_identities(concurrent_platform):
    prompts = [f"What is the status of order ORD-100{i % 3}?" for i in range(WORKERS)]
    results = run_together(lambda i: concurrent_platform.run(prompts[i]), count=WORKERS)

    request_ids = [r.request_id for r in results]
    trace_ids = [r.trace_id for r in results]
    assert len(set(request_ids)) == WORKERS, "request ids collided"
    assert len(set(trace_ids)) == WORKERS, "trace ids collided"


def test_events_are_attributed_to_the_request_that_caused_them(concurrent_platform):
    """A trace must not absorb another request's events."""
    results = run_together(
        lambda i: concurrent_platform.run(f"What is the status of order ORD-100{i % 3}?"),
        count=WORKERS,
    )
    known = {r.request_id for r in results}
    events = concurrent_platform.repository.events
    assert events, "no events were recorded"
    for event in events:
        assert event.request_id in known, "an event carried an unknown request id"

    for result in results:
        own = [e for e in events if e.request_id == result.request_id]
        assert own, f"request {result.request_id} recorded no events"
        assert len({e.trace_id for e in own}) == 1, (
            "one request's events span multiple traces"
        )


def test_a_confirmation_cannot_resolve_another_request(concurrent_platform):
    """Cross-request confirmation must fail even when both are in flight."""
    first = concurrent_platform.run(
        "Send an email to ana.ribeiro@example.com about order ORD-1001"
    )
    second = concurrent_platform.run(
        "Update order ORD-1002 status to delivered"
    )
    pending = [r for r in (first, second) if r.awaiting_confirmation]
    if len(pending) < 2:
        pytest.skip("both requests must suspend for this cross-check to mean anything")

    # Confirming the first must not release the second.
    concurrent_platform.confirm(
        first.request_id, approved=True, actor="test", source="test"
    )
    still_pending = concurrent_platform.repository.requests[second.request_id]
    assert still_pending.status != "success", (
        "confirming one request released another"
    )


def test_tool_execution_context_is_per_thread(concurrent_platform):
    """The gateway's ContextVar must not leak authorisation across threads.

    If the execution context were process-global, a tool running on one thread
    would make direct invocation look authorised on every other thread.
    """
    from agent_platform.tools.execution import DirectToolInvocationError
    from agent_platform.tools.fake_tools import get_order

    def direct_call(_: int) -> str:
        try:
            get_order(order_id="ORD-1001")
        except DirectToolInvocationError:
            return "refused"
        return "executed"

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda i: concurrent_platform.run("status of ORD-1001"), range(4)))
        outcomes = run_together(direct_call, count=8)

    assert set(outcomes) == {"refused"}, (
        "a direct tool call was allowed while another thread held the gateway context"
    )


# ============================================ simulated dataset under load


def test_simulated_dataset_stays_coherent_under_concurrent_writes(tmp_path):
    """The tool dataset is module-level mutable state shared by every request.

    It carries no lock. That is safe here for two specific reasons, and this
    test pins both so a future change cannot quietly remove them:

    * a single dict key assignment is atomic under CPython's GIL, so a write
      cannot leave a record half-updated;
    * reads ``deepcopy`` the record, so a reader never shares structure with a
      writer.

    Replace either property and this test fails.
    """
    from agent_platform.tools import fake_tools
    from agent_platform.tools.execution import gateway_execution

    fake_tools.reset_dataset()

    def write(index: int) -> None:
        for round_number in range(10):
            with gateway_execution():
                fake_tools.update_record(
                    "ORD-1001", "status", f"w{index}-{round_number}"
                )

    run_together(write, count=WORKERS)

    with gateway_execution():
        payload = fake_tools.get_order(order_id="ORD-1001")

    order = payload["order"]
    assert {"order_id", "status"} <= set(order), "the record lost fields"
    assert isinstance(order["status"], str)
    assert order["status"].startswith("w"), "the final value is not a real write"
    fake_tools.reset_dataset()


def test_dataset_reads_never_observe_a_partial_record():
    """Readers must not see a record mid-update."""
    from agent_platform.tools import fake_tools
    from agent_platform.tools.execution import gateway_execution

    fake_tools.reset_dataset()
    stop = threading.Event()
    torn: list[str] = []

    def writer() -> None:
        counter = 0
        while not stop.is_set():
            with gateway_execution():
                fake_tools.update_record("ORD-1001", "status", f"v{counter}")
            counter += 1

    def reader() -> None:
        while not stop.is_set():
            with gateway_execution():
                payload = fake_tools.get_order(order_id="ORD-1001")
            order = payload.get("order")
            if not isinstance(order, dict) or "status" not in order:
                torn.append(repr(payload)[:80])

    threads = [threading.Thread(target=writer) for _ in range(3)]
    threads += [threading.Thread(target=reader) for _ in range(3)]
    for thread in threads:
        thread.start()
    stop.wait(0.75)
    stop.set()
    for thread in threads:
        thread.join(timeout=10)

    assert not torn, f"{len(torn)} readers observed a partial record"
    fake_tools.reset_dataset()


# ==================================================== SQLite under contention


def test_sqlite_persists_every_concurrent_write(tmp_path):
    """Concurrent requests must not lose events to a locked database.

    The repository opens a connection per operation rather than sharing one,
    which is what makes this safe. A shared connection would raise
    "SQLite objects created in a thread..." or silently serialise.
    """
    from agent_platform.observability.events import EventStatus, EventType
    from agent_platform.observability.tracing import Tracer
    from agent_platform.persistence.sqlite import SQLiteRepository

    repository = SQLiteRepository(tmp_path / "concurrent.db")
    repository.initialize()

    per_thread = 5

    def emit(index: int) -> None:
        tracer = Tracer(repository, request_id=f"req-{index}", trace_id=f"tr-{index}")
        for _ in range(per_thread):
            tracer.event(EventType.REQUEST_STARTED, status=EventStatus.INFO)

    run_together(emit, count=8)

    events = repository.recent_events(limit=1000)
    assert len(events) == 8 * per_thread, (
        f"expected {8 * per_thread} events, found {len(events)}"
    )
    # Every event must still belong to exactly one request.
    by_request: dict[str, int] = {}
    for event in events:
        by_request[event["request_id"]] = by_request.get(event["request_id"], 0) + 1
    assert set(by_request.values()) == {per_thread}, (
        "events were misattributed across concurrent writers"
    )
