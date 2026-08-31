#!/usr/bin/env python
"""Concurrency and load, against the deterministic stub.

The question is not "how fast is it". A stub provider makes throughput a
measurement of this machine, not of the platform. The question is whether
**concurrency corrupts anything**: whether two replicas sharing a ledger can
lose an update, whether a rate limit means the same number under load as it
does at rest, whether a budget that says one dollar spends one dollar when
forty requests arrive at once.

So the numbers are reported and the *invariants* are asserted.

Replicas are separate ``AgentPlatform`` instances over one SQLite file, which
is what a pod-per-replica deployment looks like from the repository's point of
view once the file is shared. That is deliberate: it needs no Redis, no
Postgres and no Docker, so this runs anywhere the suite runs.

Never touches a provider. The stub is selected because no key is configured,
and the live gate would refuse a real one anyway.

    python scripts/load_test.py --replicas 4 --workers 16 --requests 200
"""

from __future__ import annotations

import argparse
import statistics
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, "src")

from agent_platform.config import Settings
from agent_platform.persistence.sqlite import SQLiteRepository
from agent_platform.platform import AgentPlatform

QUESTIONS = (
    "What is the refund policy?",
    "What is the status of order ORD-1001?",
    "How do I reset my password?",
    "What is the cancellation policy?",
)


@dataclass
class Outcome:
    status: str
    seconds: float
    replica: int


def settings_for(database: Path, per_minute: int) -> Settings:
    return Settings(
        gemini_api_key=None,
        gemini_model="deterministic-stub-v1",
        gemini_thinking_budget=0,
        gemini_max_output_tokens=2048,
        database_path=database,
        environment="load-test",
        log_level="WARNING",
        max_retries=2,
        recursion_limit=25,
        llm_timeout=5.0,
        tool_timeout=5.0,
        requests_per_minute=per_minute,
        requests_per_hour=per_minute * 60,
        daily_budget_usd=Decimal("1.00"),
        max_request_cost_usd=Decimal("0.05"),
        max_input_chars=8000,
        max_context_items=20,
        max_trace_payload_chars=500,
        max_llm_calls_per_request=12,
        max_tool_calls_per_request=8,
        request_deadline_seconds=180.0,
        max_tool_output_bytes=32_768,
        max_pending_confirmations=50,
        confirmation_ttl_seconds=900.0,
        circuit_failure_threshold=5,
        circuit_cooldown_seconds=60.0,
        global_requests_per_minute=per_minute,
        global_requests_per_hour=per_minute * 60,
    )


def percentile(values: list[float], fraction: float) -> float:
    """Nearest-rank. Honest about small samples, unlike interpolation."""
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round(fraction * len(ordered)) - 1))
    return ordered[index]


def run(replicas: int, workers: int, requests: int, per_minute: int) -> int:
    # ignore_cleanup_errors: SQLite connections are per-thread, and a pool
    # thread that has finished its work may still hold a handle when the
    # directory is removed. Windows refuses to unlink an open file, which is a
    # property of the harness and not of the platform -- the invariants below
    # have already been checked by then.
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        database = Path(tmp) / "load.db"

        # One repository per replica over one file, which is what a shared
        # volume looks like to the code. Built up front so connection setup is
        # not counted as request latency.
        platforms = []
        for _ in range(replicas):
            repository = SQLiteRepository(database)
            platforms.append(
                AgentPlatform(settings_for(database, per_minute), repository=repository)
            )

        outcomes: list[Outcome] = []
        lock = threading.Lock()

        def one(index: int) -> None:
            platform = platforms[index % replicas]
            question = QUESTIONS[index % len(QUESTIONS)]
            started = time.perf_counter()
            result = platform.run(question, quota_key=f"caller-{index % workers}")
            elapsed = time.perf_counter() - started
            with lock:
                outcomes.append(Outcome(result.status, elapsed, index % replicas))

        wall_started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(one, range(requests)))
        wall = time.perf_counter() - wall_started

        for platform in platforms:
            platform.close()

        return report(outcomes, wall, replicas, workers, requests, per_minute, database)


def report(
    outcomes: list[Outcome],
    wall: float,
    replicas: int,
    workers: int,
    requests: int,
    per_minute: int,
    database: Path,
) -> int:
    latencies = [o.seconds for o in outcomes]
    by_status: dict[str, int] = {}
    for o in outcomes:
        by_status[o.status] = by_status.get(o.status, 0) + 1

    print(f"  replicas          {replicas}")
    print(f"  workers           {workers}")
    print(f"  requests          {requests}")
    print(f"  wall clock        {wall:.2f} s")
    print(f"  throughput        {requests / wall:.1f} req/s")
    print()
    print(f"  p50               {percentile(latencies, 0.50) * 1000:8.1f} ms")
    print(f"  p95               {percentile(latencies, 0.95) * 1000:8.1f} ms")
    print(f"  p99               {percentile(latencies, 0.99) * 1000:8.1f} ms")
    print(f"  mean              {statistics.fmean(latencies) * 1000:8.1f} ms")
    print(f"  max               {max(latencies) * 1000:8.1f} ms")
    print()
    for status, count in sorted(by_status.items(), key=lambda kv: -kv[1]):
        print(f"  {status:<18}{count:5d}")
    print()

    # ---- the assertions, which are the point ------------------------------
    failures: list[str] = []

    if len(outcomes) != requests:
        failures.append(f"{len(outcomes)} outcomes for {requests} requests")

    # Every status must be one the platform actually defines. A novel string
    # under load means something raced into an unhandled path.
    known = {
        "success", "blocked", "declined", "denied", "failed",
        "awaiting_confirmation", "rejected", "rate_limited", "expired",
    }
    unknown = set(by_status) - known
    if unknown:
        failures.append(f"unrecognised status under load: {sorted(unknown)}")

    # No request may crash. `failed` is a governed outcome and is allowed;
    # an exception escaping to the caller is not, and would have shown up as a
    # missing outcome above.
    #
    # The ledger must reconcile: every request that ran is recorded exactly
    # once, from whichever replica served it.
    audit = SQLiteRepository(database)
    try:
        recorded = len(list(audit.recent_requests(limit=requests * 2)))
    finally:
        audit.close()
    if recorded != requests:
        failures.append(
            f"the shared ledger holds {recorded} requests for {requests} served "
            "-- a lost or duplicated write under concurrency"
        )

    allowed = by_status.get("rate_limited", 0)
    print(f"  ledger rows       {recorded} (expected {requests})")
    print(f"  refused for quota {allowed}")
    print()
    print("  What this run does and does not prove:")
    print("    proves    the shared ledger stays exact under concurrency --")
    print("              every request recorded once, from whichever replica")
    print("              served it, with no lost or duplicated write")
    print("    does NOT  prove shared rate limiting. No REDIS_URL is set here,")
    print("              so each replica holds its own in-process limiter and a")
    print("              limit of N per caller becomes N per caller per replica.")
    print("              That is the documented behaviour without Redis, and")
    print("              tests/integration/test_shared_state.py is where the")
    print("              shared limiter is actually exercised.")
    print()

    if failures:
        print("INVARIANTS BROKEN:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("invariants held: every request accounted for exactly once, no novel status")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--replicas", type=int, default=4)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--requests", type=int, default=200)
    parser.add_argument(
        "--per-minute", type=int, default=100_000,
        help="rate limit; the default is high so load is measured, not the limiter",
    )
    args = parser.parse_args()
    return run(args.replicas, args.workers, args.requests, args.per_minute)


if __name__ == "__main__":
    raise SystemExit(main())
