#!/usr/bin/env python
"""The V2.6 HTTP proofs, run against a live cluster.

These were run by hand when V2.6 was validated. A proof that exists only in a
transcript is a proof nobody can repeat, so it lives here and the nightly
workflow runs it.

Reads the tokens written by ``scripts/k8s_up.sh`` -- a file at mode 600 outside
the repository. Nothing here prints a token, and the final check asserts that
none of them reached the exposition endpoint.

Requires a reachable cluster. Exit 0 when every proof holds.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

NAMESPACE = "agent-platform"
QUESTION = "What is the refund policy?"
HIGH_RISK = "Send a message to customer CUS-2001"

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"  {'ok ' if ok else '!! '}{label}{('  -> ' + detail) if detail else ''}")


def kubectl(*args: str, timeout: int = 120) -> str:
    return subprocess.run(  # noqa: S603 - fixed argv
        ["kubectl", "-n", NAMESPACE, *args],
        capture_output=True, text=True, timeout=timeout,
    ).stdout.strip()


def call(base: str, method: str, path: str, token: str | None = None, body=None):
    headers, data = {}, None
    if body is not None:
        data = json.dumps(body).encode()
        headers["content-type"] = "application/json"
    if token:
        headers["authorization"] = f"Bearer {token}"
    request = urllib.request.Request(  # noqa: S310 - literal scheme, loopback host
        f"{base}{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310
            raw = response.read()
            return response.status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw.decode()


def main() -> int:
    tokens_path = os.environ.get("TOKENS_OUT") or (
        sys.argv[1] if len(sys.argv) > 1 else ""
    )
    if not tokens_path or not pathlib.Path(tokens_path).is_file():
        print("no token file: pass a path or set TOKENS_OUT (see scripts/k8s_up.sh)")
        return 1
    tokens = json.loads(pathlib.Path(tokens_path).read_text(encoding="utf-8"))
    runner, approver, scraper = tokens["runner"], tokens["approver"], tokens["scraper"]

    pods = kubectl(
        "get", "pods", "-l", "app.kubernetes.io/component=api",
        "-o", "jsonpath={.items[*].metadata.name}",
    ).split()
    if len(pods) < 2:
        print(f"expected at least 2 API replicas, found {len(pods)}")
        return 1
    print(f"  replicas: {pods[0]} | {pods[1]}\n")

    forwards, bases = [], []
    for index, pod in enumerate(pods[:2]):
        port = 18800 + index
        forwards.append(
            subprocess.Popen(  # noqa: S603 - fixed argv
                ["kubectl", "-n", NAMESPACE, "port-forward", f"pod/{pod}", f"{port}:8000"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        )
        bases.append(f"http://127.0.0.1:{port}")

    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                if all(call(base, "GET", "/health")[0] == 200 for base in bases):
                    break
            except (OSError, urllib.error.URLError, ValueError):
                # The port-forward is not up yet. Anything else is a real
                # problem and should not be swallowed by a wait loop.
                pass
            time.sleep(1)
        else:
            print("port-forward never answered")
            return 1

        a, b = bases

        print("PUBLIC AND PROTECTED")
        status, body = call(a, "GET", "/health")
        check(status == 200 and body == {"status": "ok"}, "/health needs no credential")
        status, body = call(a, "GET", "/ready")
        check(status == 200, "/ready needs no credential (the kubelet sends none)")
        check(body.get("auth_mode") == "enforced", "/ready reports enforced auth")
        check(
            body.get("provider") == "" and body.get("checks") == {},
            "/ready tells an anonymous caller nothing operational",
        )
        status, body = call(a, "GET", "/ready", scraper)
        check(
            status == 200 and body.get("provider") == "stub",
            "/ready with metrics:read carries the detail",
        )

        print("\nMETRICS IS PROTECTED")
        check(call(a, "GET", "/metrics")[0] == 401, "/metrics anonymous is 401")
        check(call(a, "GET", "/metrics", runner)[0] == 403, "/metrics with runs:write is 403")
        status, text = call(a, "GET", "/metrics", scraper)
        check(
            status == 200 and "agent_build_info" in text,
            "/metrics with metrics:read is 200",
        )

        print("\nSEPARATION OF DUTIES")
        check(
            call(a, "POST", "/runs", approver, {"input": QUESTION})[0] == 403,
            "an approver cannot submit",
        )
        check(
            call(a, "POST", "/runs/x/confirm", runner, {"approved": True})[0] == 403,
            "a submitter cannot approve",
        )
        check(
            call(a, "POST", "/runs", None, {"input": QUESTION})[0] == 401,
            "anonymous cannot submit",
        )
        check(call(a, "GET", "/nope")[0] == 401, "an unknown path answers 401 anonymously")

        print("\nTHE APPROVER COMES FROM THE CREDENTIAL, ACROSS REPLICAS")
        status, created = call(a, "POST", "/runs", runner, {"input": HIGH_RISK})
        check(
            status == 200 and created.get("status") == "awaiting_confirmation",
            "a high-risk action suspends on replica A",
            str(created.get("status")),
        )
        request_id = created.get("request_id", "")
        status, refused = call(
            b, "POST", f"/runs/{request_id}/confirm", approver,
            {"approved": True, "actor": "ceo"},
        )
        check(status == 400, "an actor in the body is refused")
        check(
            "authenticated credential" in str(refused.get("detail", "")),
            "the refusal explains where the approver comes from",
        )
        status, resumed = call(
            b, "POST", f"/runs/{request_id}/confirm", approver, {"approved": True}
        )
        check(
            status == 200 and resumed.get("status") == "success",
            "replica B approves what replica A suspended",
        )

        # The id is minted by the platform, but "it comes from a trusted
        # source" is an argument rather than a control. psql -c takes no bound
        # parameters, so the shape is checked instead: anything that is not the
        # identifier format cannot reach the query.
        if not re.fullmatch(r"req-[0-9a-f]{1,64}", request_id):
            check(False, "the request id has the expected shape", repr(request_id)[:40])
            request_id = ""
        raw = kubectl(
            "exec", "agent-platform-postgres-0", "--", "psql", "-U", "agent",
            "-d", "agentplatform", "-t", "-A", "-c",
            "select payload from events where event_type='confirmation_resolved' "  # noqa: S608 - shape-checked above
            f"and request_id='{request_id}'",
        )
        payload = json.loads(raw) if raw.startswith("{") else {}
        check(
            payload.get("actor") == "approver",
            "the audit record names the authenticated principal",
        )
        check(payload.get("source") == "api", "source=api marks an attested identity")
        check(payload.get("actor") != "ceo", "the name the body tried to inject does not appear")

        print("\nPER-PRINCIPAL QUOTA")
        limit = int(kubectl("get", "cm", "agent-platform-config",
                            "-o", "jsonpath={.data.REQUESTS_PER_MINUTE}") or 10)
        codes = []
        for _ in range(limit + 3):
            code, _body = call(a, "POST", "/runs", runner, {"input": QUESTION})
            codes.append(code)
            if code == 429:
                break
        check(429 in codes, "the runner exhausts its own quota", f"{codes.count(200)} accepted")
        check(
            call(a, "GET", "/metrics", scraper)[0] == 200,
            "another principal is not denied by the runner's traffic",
        )

        body = call(a, "GET", "/metrics", scraper)[1]
        check(
            'agent_auth_refusals_total{reason="missing_scope"}' in body,
            "scope refusals are counted in the exposition",
        )
        check(
            not any(token.split("_", 2)[2] in body for token in tokens.values()),
            "no credential secret appears in /metrics",
        )
    finally:
        for forward in forwards:
            forward.terminate()

    print("\n" + "=" * 70)
    failed = [label for ok, label in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} proofs held")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
