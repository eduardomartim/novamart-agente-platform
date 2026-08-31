#!/usr/bin/env python
"""Assert that Redis and PostgreSQL are actually reachable.

The integration suites skip cleanly when a service is not there. That is the
right behaviour on a laptop and the wrong outcome in a job created to test
those services: pytest reports ``skipped``, the job goes green, and nothing was
verified.

This runs first and fails loudly instead. It checks reachability the same way
the suites do -- same environment variables, same defaults -- so a pass here
means the suites will exercise the services rather than skip them.

Exit 0 when both answer, 1 otherwise.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "src")

REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:16379/0")
POSTGRES_URL = os.getenv(
    "TEST_POSTGRES_URL", "postgresql://postgres:dev-only-not-a-secret@127.0.0.1:15432/agentplatform"
)


def check_redis() -> str | None:
    try:
        from agent_platform.state import build_shared_state
    except ImportError as exc:
        return f"the redis extra is not installed ({type(exc).__name__})"
    try:
        state = build_shared_state(REDIS_URL)
        if not state.is_shared:
            return "build_shared_state did not return a shared backend"
        if not state.backend.ping():
            return "the backend did not answer ping"
    except Exception as exc:
        # The URL can carry a password, so only the exception type is shown.
        return f"unreachable: {type(exc).__name__}"
    return None


def check_postgres() -> str | None:
    try:
        from agent_platform.persistence.postgres import PostgresRepository
    except ImportError as exc:
        return f"the postgres extra is not installed ({type(exc).__name__})"
    try:
        repository = PostgresRepository(POSTGRES_URL)
        repository.initialize()
        repository.metrics_summary()
        repository.close()
    except Exception as exc:
        return f"unreachable: {type(exc).__name__}"
    return None


def main() -> int:
    results = {"redis": check_redis(), "postgres": check_postgres()}
    for name, problem in results.items():
        print(f"  {name:9} {'ok' if problem is None else 'FAILED - ' + problem}")

    if any(problem is not None for problem in results.values()):
        print()
        print(
            "REFUSING TO CONTINUE: this job exists to exercise these services, and "
            "the suites would skip rather than fail -- a green run proving nothing."
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
