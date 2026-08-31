#!/usr/bin/env python
"""The HTTPS path, proved against a running cluster.

Complements ``scripts/cluster_proofs.py`` rather than replacing it. That script
talks to the pods directly over HTTP, which is the *internal* path -- pod to
pod, inside the NetworkPolicy, where nothing crosses a trust boundary and
terminating TLS twice would buy nothing. Those proofs stay exactly as they are.

This one enters the way an outside client does: through the ingress controller,
over TLS, at the documented hostname.

The distinction it exists to demonstrate: **TLS is not authentication.** Every
check below arrives over HTTPS, and the unauthenticated ones still get 401. The
encryption changed what a network observer sees; it changed nothing about who
the platform believes the caller is.

Requires a cluster with the ingress controller installed
(``scripts/k8s_ingress_up.sh``). Exit 0 when every proof holds.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request

CONTROLLER_NS = "ingress-nginx"
HOST = os.environ.get("TLS_HOST", "agent-platform.local")

results: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label))
    print(f"  {'ok ' if ok else '!! '}{label}{('  -> ' + detail) if detail else ''}")


def call(port: int, path: str, token: str | None = None, scheme: str = "https"):
    """Reach the Ingress at 127.0.0.1 while presenting the documented hostname.

    The certificate is self-signed, so verification is disabled *here* -- this
    script is proving that TLS terminates and routes, and it is the only place
    in the project that skips a certificate check. A client sending a real
    credential to a real host must not do this.
    """
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE

    headers = {"Host": HOST}
    if token:
        headers["authorization"] = f"Bearer {token}"
    url = f"{scheme}://127.0.0.1:{port}{path}"
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - literal scheme

    # Redirects are the thing under test, not something to follow. The
    # controller answers plain HTTP with a 308 to https://agent-platform.local,
    # a hostname that does not resolve -- so following it fails on DNS and the
    # assertion never sees the status it came to check.
    opener = urllib.request.build_opener(
        _NoRedirect,
        urllib.request.HTTPSHandler(context=context),
    )
    try:
        with opener.open(request, timeout=60) as response:
            raw = response.read()
            body = json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode()
            return response.status, body, response.headers
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw), exc.headers
        except ValueError:
            return exc.code, raw.decode(), exc.headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Return the redirect instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def peer_certificate(port: int) -> dict:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    with (
        socket.create_connection(("127.0.0.1", port), timeout=30) as raw,
        context.wrap_socket(raw, server_hostname=HOST) as tls,
    ):
        return {
            "der": tls.getpeercert(binary_form=True),
            "cipher": tls.cipher(),
            "version": tls.version(),
        }


def san_hosts(der: bytes) -> list[str]:
    """Pull subjectAltName out with openssl rather than a parser dependency."""
    text = subprocess.run(
        ["openssl", "x509", "-inform", "DER", "-noout", "-text"],
        input=der, capture_output=True, timeout=60,
    ).stdout.decode(errors="replace")
    match = re.search(r"X509v3 Subject Alternative Name:\s*\n\s*(.+)", text)
    return re.findall(r"DNS:([^,\s]+)", match.group(1)) if match else []


def main() -> int:
    tokens_path = os.environ.get("TOKENS_OUT", "")
    if not tokens_path or not pathlib.Path(tokens_path).is_file():
        print("set TOKENS_OUT to the file scripts/k8s_up.sh wrote")
        return 1
    tokens = json.loads(pathlib.Path(tokens_path).read_text(encoding="utf-8"))

    https_port = int(os.environ.get("TLS_PORT", "18443"))
    http_port = int(os.environ.get("PLAIN_PORT", "18080"))

    forwards = [
        subprocess.Popen(  # noqa: S603 - fixed argv
            ["kubectl", "-n", CONTROLLER_NS, "port-forward",
             "svc/ingress-nginx-controller", f"{https_port}:443", f"{http_port}:80"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    ]
    try:
        deadline = time.time() + 90
        while time.time() < deadline:
            try:
                peer_certificate(https_port)
                break
            except (OSError, ssl.SSLError):
                time.sleep(1)
        else:
            print("the ingress controller never answered on the forwarded port")
            return 1

        print("THE CERTIFICATE")
        peer = peer_certificate(https_port)
        names = san_hosts(peer["der"])
        check(HOST in names, "the certificate covers the documented hostname", ", ".join(names))
        check(
            peer["version"] in ("TLSv1.2", "TLSv1.3"),
            "the negotiated protocol is current",
            str(peer["version"]),
        )

        print("\nHTTP IS NOT SERVED")
        status, _body, headers = call(http_port, "/health", scheme="http")
        location = (headers or {}).get("Location", "")
        check(status in (301, 307, 308), "plain HTTP is redirected, not answered", str(status))
        check(location.startswith("https://"), "the redirect target is HTTPS", location[:60])

        print("\nHTTPS REACHES THE APPLICATION")
        status, body, _ = call(https_port, "/health")
        check(status == 200 and body == {"status": "ok"}, "/health answers over TLS")
        status, body, _ = call(https_port, "/ready")
        check(status == 200 and body.get("auth_mode") == "enforced", "/ready answers over TLS")

        print("\nTLS IS NOT AUTHENTICATION")
        status, _body, _ = call(https_port, "/metrics")
        check(status == 401, "no credential over HTTPS is still 401", str(status))
        status, _body, _ = call(https_port, "/runs", token=tokens["scraper"])
        check(status in (403, 405), "wrong scope over HTTPS is still refused", str(status))
        status, body, _ = call(https_port, "/metrics", token=tokens["scraper"])
        check(
            status == 200 and "agent_build_info" in str(body),
            "the right scope over HTTPS is served",
        )

        print("\nNO CREDENTIAL LEAKS THROUGH THE EDGE")
        _s, body, _h = call(https_port, "/metrics", token=tokens["scraper"])
        text = str(body)
        check(
            not any(t.split("_", 2)[2] in text for t in tokens.values()),
            "no credential secret appears in the exposition",
        )
    finally:
        for forward in forwards:
            forward.terminate()

    print("\n" + "=" * 70)
    failed = [label for ok, label in results if not ok]
    print(f"{len(results) - len(failed)}/{len(results)} TLS proofs held")
    for label in failed:
        print(f"  FAILED: {label}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
