#!/usr/bin/env python
"""Assert that no credential reached the pod logs.

Runs after the cluster proofs, which exercise authentication hard enough to
produce refusals on every replica. Those refusals are logged, and the whole
question is whether the log lines carry the reason or the credential.

Reads the token file rather than a pattern, so this checks for the exact values
in play -- not merely for something that looks like a token. Nothing is ever
printed: the report says which pod and how many lines, never what matched.

Exit 0 when the logs are clean.
"""

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys

NAMESPACE = "agent-platform"


def main() -> int:
    tokens_path = os.environ.get("TOKENS_OUT") or (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not tokens_path or not pathlib.Path(tokens_path).is_file():
        print("no token file to check against; set TOKENS_OUT")
        return 1
    tokens = json.loads(pathlib.Path(tokens_path).read_text(encoding="utf-8"))

    pods = subprocess.run(  # noqa: S603 - fixed argv
        ["kubectl", "-n", NAMESPACE, "get", "pods",
         "-l", "app.kubernetes.io/component=api",
         "-o", "jsonpath={.items[*].metadata.name}"],
        capture_output=True, text=True, timeout=120,
    ).stdout.split()

    if not pods:
        print("no API pods found")
        return 1

    leaked = 0
    for pod in pods:
        logs = subprocess.run(  # noqa: S603 - fixed argv
            ["kubectl", "-n", NAMESPACE, "logs", pod],
            capture_output=True, text=True, timeout=180,
        ).stdout
        found = [
            name
            for name, token in tokens.items()
            # Both the whole token and its secret half: a partial write is
            # still a disclosure.
            if token in logs or token.split("_", 2)[2] in logs
        ]
        if found:
            leaked += len(found)
            print(f"  {pod}: LEAKED the credential of {', '.join(sorted(found))}")
        else:
            print(f"  {pod}: {len(logs.splitlines())} lines, no credential")

    if leaked:
        print(f"\n{leaked} credential(s) reached the logs")
        return 1
    print("\nok: no credential in any pod log")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
