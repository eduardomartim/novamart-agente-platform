#!/usr/bin/env python
"""Re-enact the 72-call incident, and assert it is now refused.

The command that caused it::

    pytest -m "not docker"

``addopts`` in pyproject.toml carries ``-m "not live"``. A ``-m`` on the command
line **replaces** that value rather than combining with it -- pytest stores a
single ``markexpr`` -- so this expression silently made every live test
eligible. It must now fail at collection, having executed nothing.

Collection only. Even if every layer of the gate were removed, this script
would still not run a test, so it cannot itself spend quota.

Exit 0 when the incident command is refused, 1 otherwise.
"""

from __future__ import annotations

import os
import subprocess
import sys

USAGE_ERROR = 4


def collect(markers: str) -> subprocess.CompletedProcess[str]:
    environment = {**os.environ}
    environment.pop("AGENT_PLATFORM_LIVE", None)
    return subprocess.run(  # noqa: S603 - fixed argv, literals only
        [
            sys.executable, "-m", "pytest", "--collect-only", "-q",
            "-p", "no:cacheprovider", "-m", markers,
        ],
        capture_output=True, text=True, timeout=900, env=environment,
    )


def main() -> int:
    failures: list[str] = []

    incident = collect("not docker")
    output = incident.stdout + incident.stderr
    if incident.returncode != USAGE_ERROR:
        failures.append(
            f'pytest -m "not docker" exited {incident.returncode}, expected '
            f"{USAGE_ERROR} (usage error). Live tests were not refused."
        )
    if "not authorised" not in output:
        failures.append("the refusal did not explain that authorisation is missing")

    direct = collect("live")
    if direct.returncode != USAGE_ERROR:
        failures.append(
            f'pytest -m "live" exited {direct.returncode}, expected {USAGE_ERROR}'
        )

    # The complement. A gate that refused everything would pass the checks
    # above while making the project unusable, and would be deleted by the
    # first person it inconvenienced.
    ordinary = collect("not live and not docker")
    if ordinary.returncode != 0:
        failures.append(
            f"the ordinary offline filter exited {ordinary.returncode}; the gate "
            "must not fire on a normal run"
        )

    if failures:
        print("THE LIVE GATE DID NOT HOLD:")
        for failure in failures:
            print(f"  - {failure}")
        return 1

    print('ok: pytest -m "not docker" is refused at collection (exit 4)')
    print('ok: pytest -m "live" is refused at collection (exit 4)')
    print('ok: pytest -m "not live and not docker" collects normally')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
