"""Per-visitor quota keys, and the Vercel entrypoint that shares the same app.

The property under test throughout is that a quota key is derived from what the
*connection* says, never from what the page sends. A browser that could name
its own bucket could ask for a fresh allowance on every request, which is the
whole control gone.

Which header is authoritative is the part worth pinning, and it changed. The
dashboard is deployed on Railway, whose edge writes the client address into
`X-Real-IP`, so that header -- and only that header -- names the bucket.
`X-Forwarded-For` is no longer read at all: it is a chain whose correct
interpretation depends on how many proxies are in front, and with one named
platform there is no reason to keep a control whose correctness rests on a
count nobody re-checks. Several tests below fail if `X-Forwarded-For`, or any
header a browser can set, ever regains authority.

The second property pinned here is canonicalisation. The bucket is a hash of an
address *string*, so two spellings of one host used to be two buckets and the
limit silently halved: `203.0.113.9` and `203.0.113.9:44321` were different
visitors, and one IPv6 host in four legal spellings was four. Every spelling now
resolves through `ipaddress` to one form, or is refused outright.

No network, no provider, no Streamlit runtime: the derivation is a pure
function and is tested as one.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD = ROOT / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

pytest.importorskip("streamlit")

# Importing the dashboard reads `.env` into `os.environ` -- it is a Streamlit
# app and that is how it finds its configuration. At collection time that would
# hand a GEMINI_API_KEY to every later test that builds a platform, and the live
# gate would then refuse each one. The environment is put back exactly as it
# was, so importing this file changes nothing for anybody else.
_ENV_BEFORE_IMPORT = dict(os.environ)
import app as dashboard  # noqa: E402

os.environ.clear()
os.environ.update(_ENV_BEFORE_IMPORT)

IP_A = "203.0.113.7"
IP_B = "198.51.100.4"


REAL_IP = "X-Real-IP"


def key(headers=None, ip=None, session=None) -> str:
    return dashboard.visitor_key(headers, ip, session)


def canonical(raw):
    return dashboard._canonical_ip(raw)


# ============================================= A/B. one visitor, one bucket


def test_two_questions_from_one_visitor_share_a_key():
    """A: the same address is the same identity."""
    first = key({REAL_IP: IP_A}, None, "session-1")
    second = key({REAL_IP: IP_A}, None, "session-1")
    assert first == second


def test_a_second_tab_does_not_buy_a_second_allowance():
    """A new tab is a new Streamlit session and the same connection.

    Keying on the session instead would hand out a fresh bucket per tab, which
    is the bypass this derivation exists to close.
    """
    tab_one = key({REAL_IP: IP_A}, None, "session-1")
    tab_two = key({REAL_IP: IP_A}, None, "session-2")
    assert tab_one == tab_two


def test_different_visitors_get_different_keys():
    """B."""
    assert key({REAL_IP: IP_A}, None, "s") != key({REAL_IP: IP_B}, None, "s")


def test_the_header_name_is_matched_case_insensitively():
    """The casing a proxy uses is not a promise."""
    assert key({"x-real-ip": IP_A}, None, "s") == key({REAL_IP: IP_A}, None, "s")
    assert key({"X-REAL-IP": IP_A}, None, "s") == key({REAL_IP: IP_A}, None, "s")


def test_repeated_derivation_is_deterministic():
    """R: nothing about the derivation varies between calls in one process."""
    produced = {key({REAL_IP: IP_A}, None, "s") for _ in range(50)}
    assert len(produced) == 1


def test_concurrent_derivation_agrees_with_itself():
    """R, under threads: the function holds no state that could interleave."""
    import threading

    results: list[str] = []
    lock = threading.Lock()
    barrier = threading.Barrier(16)

    def derive() -> None:
        barrier.wait()
        produced = key({REAL_IP: IP_A}, None, "s")
        with lock:
            results.append(produced)

    threads = [threading.Thread(target=derive) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1 and len(results) == 16


# ============================================ C/D/E/F. canonicalisation


def test_ipv4_is_canonical():
    """C."""
    assert canonical("203.0.113.7") == "203.0.113.7"


@pytest.mark.parametrize(
    "spelling",
    ["2001:db8::1", "2001:DB8::1", "2001:db8:0:0:0:0:0:1", "[2001:db8::1]"],
)
def test_every_spelling_of_one_ipv6_host_is_one_identity(spelling):
    """D: the F-01 defect. Four legal spellings used to be four buckets."""
    assert canonical(spelling) == "2001:db8::1"
    assert key({REAL_IP: spelling}, None, "s") == key({REAL_IP: "2001:db8::1"}, None, "s")


def test_an_ipv4_mapped_ipv6_address_is_the_ipv4_host():
    """E: the same host reached over a v4-mapped socket is the same visitor."""
    assert canonical("::ffff:203.0.113.7") == "203.0.113.7"
    assert key({REAL_IP: "::ffff:203.0.113.7"}, None, "s") == key(
        {REAL_IP: "203.0.113.7"}, None, "s"
    )


@pytest.mark.parametrize("padded", [" 203.0.113.7", "203.0.113.7 ", "\t203.0.113.7\n"])
def test_surrounding_whitespace_is_stripped_and_nothing_else_is(padded):
    """F: unambiguous padding folds; whitespace *inside* a value does not."""
    assert canonical(padded) == IP_A
    assert canonical("203.0.113.7 198.51.100.4") is None


@pytest.mark.parametrize(
    "with_port,expected",
    [
        ("203.0.113.7:44321", "203.0.113.7"),
        ("203.0.113.7:1", "203.0.113.7"),
        ("[2001:db8::1]:443", "2001:db8::1"),
        ("[2001:db8::1]", "2001:db8::1"),
    ],
)
def test_a_port_is_handled_explicitly_and_does_not_split_the_bucket(with_port, expected):
    """The other half of the F-01 defect.

    An ephemeral source port changes on every connection, so an address used
    verbatim made every request a new visitor and the per-visitor limit did
    nothing at all -- silently, which is the worst way for a limit to fail.
    These two forms are stripped deliberately; any other port shape is refused
    rather than guessed at.
    """
    assert canonical(with_port) == expected


def test_ports_do_not_mint_buckets():
    keys = {key({REAL_IP: f"203.0.113.7:{port}"}, None, "s") for port in range(4000, 4010)}
    assert len(keys) == 1
    assert keys == {key({REAL_IP: IP_A}, None, "s")}


# ==================================== G/H/I/J. everything ambiguous is refused


@pytest.mark.parametrize(
    "rejected",
    [
        "1.2.3.4, 5.6.7.8",  # H: a forwarding chain where one address belongs
        "1.2.3.4,5.6.7.8",  # G: no space either
        "203.0.113.7, 203.0.113.7",  # G: even when the entries agree
        "example.com",  # I: hostname
        "localhost",  # I
        "not an ip",  # J
        "",  # J
        "   ",  # J
        "999.1.1.1",  # J: out of range
        "203.0.113",  # J: truncated
        "203.0.113.0/24",  # J: a network, not a host
        "203.0.113.7:notaport",  # J: a colon that is not a port
        "203.0.113.7:",  # J
        "[2001:db8::1]:notaport",  # J
        "0x7f000001",  # J: no permissive integer forms
        "2130706433",  # J
        "1.2.3.4 5.6.7.8",  # F: internal whitespace
        None,  # J
    ],
)
def test_an_ambiguous_value_is_never_an_identity(rejected):
    """An ambiguous value coerced into an identity is one somebody can choose."""
    assert canonical(rejected) is None


def test_a_rejected_header_falls_back_rather_than_inventing_a_bucket():
    """G/H/J end to end: a bad header is not an identity, it is no identity."""
    for bad in ("1.2.3.4, 5.6.7.8", "example.com", "", "999.1.1.1"):
        assert key({REAL_IP: bad}, None, "s") == key({}, None, "s")


# ================================================================= spoofing


@pytest.mark.parametrize(
    "header",
    [
        "X-Visitor-Id",
        "X-Visitor-IP",
        "X-Client-IP",
        "X-Forwarded-For",
        "Forwarded",
        "True-Client-IP",
        "CF-Connecting-IP",
        "X-Original-Forwarded-For",
    ],
)
def test_no_other_header_can_name_the_bucket(header):
    """M/N/O: only `X-Real-IP` is read, whatever else the request carries.

    `X-Forwarded-For` is in this list deliberately. It used to be the
    authority; on Railway the edge writes `X-Real-IP` and a chain read is a
    control whose correctness depends on a hop count nobody re-checks, so it is
    now just another header a browser can set.
    """
    baseline = key({REAL_IP: IP_A}, None, "s")
    assert key({REAL_IP: IP_A, header: IP_B}, None, "s") == baseline


def test_a_visitor_cannot_mint_buckets_by_varying_headers_they_control():
    """M/N/O: vary every browser-settable header; the identity must not move."""
    keys = {
        key(
            {
                REAL_IP: IP_A,
                "X-Forwarded-For": f"{forged}, 10.0.0.1",
                "X-Visitor-IP": forged,
                "X-Client-IP": forged,
                "X-Visitor-Id": forged,
                "Forwarded": f"for={forged}",
            },
            None,
            "s",
        )
        for forged in ("10.0.0.1", "10.0.0.2", "evil", "", "203.0.113.99")
    }
    assert len(keys) == 1, "a client-controlled header changed the bucket"


def test_x_forwarded_for_alone_is_not_an_authority():
    """L: with no `X-Real-IP`, a forwarded chain does not name the bucket.

    It falls through to the same fallback as no header at all, so a visitor who
    supplies one gains nothing.
    """
    assert key({"X-Forwarded-For": IP_A}, None, "s") == key({}, None, "s")
    assert key({"X-Forwarded-For": f"{IP_A}, {IP_B}"}, None, "s") == key({}, None, "s")
    # And it cannot be used to mint buckets either.
    keys = {key({"X-Forwarded-For": forged}, None, "s") for forged in (IP_A, IP_B, "evil")}
    assert len(keys) == 1


# ================================================================ the digest


def test_the_raw_address_never_appears_in_the_key():
    """P: the address goes in, only a digest comes out."""
    produced = key({REAL_IP: IP_A}, None, "s")
    assert IP_A not in produced
    for octet in IP_A.split("."):
        assert f".{octet}." not in produced
    assert produced.startswith("visitor:")


def test_the_key_is_salted():
    """Without a salt a hash of an IPv4 address is not an anonymisation: the
    whole space is four billion entries."""
    import hashlib

    produced = key({REAL_IP: IP_A}, None, "s")
    unsalted = hashlib.sha256(f"ip:{IP_A}".encode()).hexdigest()[:32]
    assert produced != f"visitor:{unsalted}"
    assert dashboard._VISITOR_SALT, "no salt was established"


def test_the_key_is_opaque_and_bounded():
    produced = key({REAL_IP: IP_A}, None, "s")
    assert produced == f"visitor:{produced.split(':', 1)[1]}"
    assert len(produced) == len("visitor:") + 32
    assert all(char in "0123456789abcdef" for char in produced.split(":", 1)[1])


# =============================================================== fallbacks


def test_no_header_falls_back_to_the_socket_peer():
    """K: only meaningful unproxied.

    Streamlit's `ip_address` is the socket peer, which behind Railway's edge is
    the edge -- so it groups every visitor together and can never be the
    primary source. It is the fallback for running without the edge in front,
    and it is canonicalised by the same function as the header.
    """
    assert key({}, IP_A, "s") == key(None, IP_A, "s")
    assert key({}, IP_A, "s") != key({}, IP_B, "s")
    assert key({}, "::ffff:203.0.113.7", "s") == key({}, IP_A, "s")
    assert key({}, "garbage", "s") == key({}, None, "s")


def test_the_real_ip_header_wins_over_the_socket_peer():
    """11: the peer behind the edge is the edge. The header is the visitor."""
    assert key({REAL_IP: IP_A}, "10.0.0.9", "s") == key({REAL_IP: IP_A}, None, "s")
    assert key({REAL_IP: IP_A}, "10.0.0.9", "s") != key({}, "10.0.0.9", "s")


def test_no_address_at_all_separates_by_session():
    """12: a conservative fallback, deliberately kept weak and kept last.

    With no address at all -- a laptop, or an edge that strips everything -- a
    per-session key still separates visitors. It is not a strong identity: a
    new tab is a new session and therefore a new bucket, which is why it must
    never be reachable while an address is available. The global backstop still
    bounds the deployment underneath it.
    """
    assert key({}, None, "session-1") != key({}, None, "session-2")
    assert key({}, None, "session-1") == key({}, None, "session-1")


def test_nothing_identifying_yields_one_conservative_bucket():
    """A shared bucket can only refuse more than intended, never less."""
    assert key({}, None, None) == key(None, None, "")
    assert key(None, None, None).startswith("visitor:")


def test_an_empty_or_whitespace_header_is_not_an_identity():
    """K: present but unusable is the same as absent, never a bucket of its own."""
    assert key({REAL_IP: ""}, None, "s") == key({}, None, "s")
    assert key({REAL_IP: "   "}, None, "s") == key({}, None, "s")
    assert key({REAL_IP: " , , "}, None, "s") == key({}, None, "s")


def test_an_unusable_header_still_lets_the_socket_peer_answer():
    """K: a broken header must not blind the fallback behind it."""
    assert key({REAL_IP: "garbage"}, IP_A, "s") == key({}, IP_A, "s")


# ============================================ the dashboard actually passes it


def test_the_dashboard_passes_a_quota_key_to_run(monkeypatch):
    """Behavioural: `_run_question` must hand `platform.run` a visitor bucket.

    Driven through the real function with a recording platform, so it fails if
    the argument is dropped rather than merely if a string moves in the source.
    """
    seen: dict[str, object] = {}

    class RecordingResult:
        request_id = "req-1"
        status = "success"
        route = "researcher"
        response = "ok"
        latency_ms = 1.0
        awaiting_confirmation = None

    class RecordingRepository:
        def events_for_request(self, _request_id):
            return []

    class RecordingPlatform:
        repository = RecordingRepository()

        def run(self, question, *, quota_key=None, on_event=None):
            seen["question"] = question
            seen["quota_key"] = quota_key
            return RecordingResult()

    class FakeStatus:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def update(self, **kwargs):
            return None

    monkeypatch.setattr(dashboard.st, "status", lambda *a, **k: FakeStatus())
    monkeypatch.setattr(dashboard.st, "session_state", {}, raising=False)

    dashboard._run_question(RecordingPlatform(), "What is the status of ORD-1001?")

    assert seen["quota_key"], "platform.run was called without a quota key"
    assert str(seen["quota_key"]).startswith("visitor:")


def test_the_visitor_key_helper_survives_no_streamlit_context():
    """`_visitor_key()` runs outside a browser session in tests and scripts."""
    produced = dashboard._visitor_key()
    assert produced.startswith("visitor:")


# ================================================= the quota mechanism itself


def test_the_key_reaches_the_rate_limiter_and_separates_buckets(settings):
    """End to end through the real platform: two visitors, two allowances."""
    from dataclasses import replace

    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    tight = replace(settings, requests_per_minute=1, requests_per_hour=10)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        first = platform.run("What is the status of order ORD-1001?", quota_key="v-a")
        assert first.status != "rate_limited"

        again = platform.run("What is the status of order ORD-1001?", quota_key="v-a")
        assert again.status == "rate_limited", "one visitor got a second allowance"

        other = platform.run("What is the status of order ORD-1001?", quota_key="v-b")
        assert other.status != "rate_limited", (
            "a second visitor was refused by the first visitor's spending"
        )
    finally:
        platform.close()


def test_the_configured_limits_are_still_what_binds(settings):
    """Nothing here replaces the existing limiter or its settings."""
    assert settings.requests_per_minute == 1000
    assert settings.requests_per_hour == 10000
    assert settings.effective_global_requests_per_minute > 0


# ===================================================== the Vercel entrypoint


@pytest.fixture
def entrypoint(monkeypatch):
    """Import `api/index.py` the way a cold start would.

    No key, so the stub provider is built: `create_app()` constructs the
    platform eagerly and `build_provider` refuses a configured key without
    `AGENT_PLATFORM_LIVE`. That refusal is the gate working, and it is why the
    deployment must set that variable -- but a test must not.
    """
    import importlib
    import os

    # The whole environment, saved and restored. Importing the entrypoint runs
    # `create_app()` -> `Settings.from_env()` -> `load_dotenv()`, which reads
    # `.env` into `os.environ` for the rest of the process. `monkeypatch` cannot
    # undo that -- it only knows the keys it set -- and a leaked GEMINI_API_KEY
    # makes every later test that builds a platform fail on the live gate.
    saved = dict(os.environ)

    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("API_AUTH_MODE", "disabled")
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_PATH", ":memory:")
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules.pop("api.index", None)
    module = importlib.import_module("api.index")
    try:
        yield module
    finally:
        platform = getattr(module.app.state, "platform", None)
        if platform is not None:
            platform.close()
        sys.modules.pop("api.index", None)
        os.environ.clear()
        os.environ.update(saved)


def test_the_entrypoint_exposes_a_starlette_app(entrypoint):
    """Vercel serves the module-level `app`; the name is required."""
    from starlette.applications import Starlette

    assert hasattr(entrypoint, "app"), "Vercel's runtime looks for `app`"
    assert isinstance(entrypoint.app, Starlette)


def test_the_entrypoint_serves_the_same_routes_as_the_api(entrypoint):
    paths = {route.path for route in entrypoint.app.routes}
    assert {"/health", "/ready", "/metrics", "/runs"} <= paths
    assert "/runs/{request_id}/confirm" in paths


def test_the_entrypoint_builds_no_second_application():
    """Structural, over the parsed module rather than its prose.

    An earlier version of this test searched the file as text and failed on
    its own docstring; what matters is which calls the code makes.
    """
    import ast

    tree = ast.parse((ROOT / "api" / "index.py").read_text(encoding="utf-8"))
    called = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "create_app" in called, "the entrypoint does not reuse create_app"
    for forbidden in ("Starlette", "AgentPlatform", "build_keyring", "Middleware"):
        assert forbidden not in called, (
            f"the entrypoint constructs {forbidden} instead of reusing create_app"
        )


def test_vercel_json_points_at_the_entrypoint():
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    assert config["rewrites"][0]["destination"] == "/api/index"
    assert "api/index.py" in config["functions"]


def test_requirements_resolve_to_the_hashed_api_lock():
    """One source of truth for dependencies, and it keeps its hashes.

    `requirements.txt` includes `requirements-api.txt` rather than restating
    it. Restating was the first attempt and it was wrong: a second list of
    unpinned ranges drifts from the lock silently and drops the hashes that
    `--require-hashes` relies on, so the function would install versions the
    image was never tested with.
    """
    lines = [
        line.split("#", 1)[0].strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    ]
    directives = [line for line in lines if line]
    assert directives == ["-r requirements-api.txt"], (
        "requirements.txt should include the lock, not restate dependencies"
    )


def test_the_resolved_lock_carries_the_api_path_and_not_the_dashboard():
    """Streamlit, pandas, altair and pyarrow are ~170MB the function never
    imports, and the lock is compiled without the dashboard extra."""
    lock = (ROOT / "requirements-api.txt").read_text(encoding="utf-8")
    pinned = " ".join(
        line.split("#", 1)[0].strip()
        for line in lock.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ).lower()
    for needed in ("langgraph", "google-genai", "starlette", "redis", "psycopg"):
        assert needed in pinned, f"the lock is missing {needed}"
    for unwanted in ("streamlit==", "pandas==", "altair==", "pyarrow=="):
        assert unwanted not in pinned, f"the lock ships {unwanted}"
    assert "--hash=sha256:" in lock, "the lock lost its hashes"


# ============================================================ no secrets out


def test_no_secret_is_rendered_by_the_dashboard_module():
    """The salt is server-side configuration and never reaches the page.

    Checked over the parsed module: any call to a Streamlit renderer must not
    carry the salt, by name or by value.
    """
    import ast

    tree = ast.parse((DASHBOARD / "app.py").read_text(encoding="utf-8"))
    renderers = {"write", "code", "json", "markdown", "caption", "text", "title"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        name = target.attr if isinstance(target, ast.Attribute) else None
        if name not in renderers:
            continue
        rendered = ast.dump(node)
        assert "_VISITOR_SALT" not in rendered, f"the salt is rendered at line {node.lineno}"
        assert "VISITOR_ID_SALT" not in rendered


def test_the_salt_is_read_from_the_environment_only():
    source = (DASHBOARD / "app.py").read_text(encoding="utf-8")
    assert 'os.getenv("VISITOR_ID_SALT"' in source


def test_the_visitor_key_is_the_only_thing_derived_from_the_address():
    """The raw address must not be stored, logged or put in session state."""
    import ast

    tree = ast.parse((DASHBOARD / "app.py").read_text(encoding="utf-8"))
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "visitor_key"
    )
    returns = [n for n in ast.walk(helper) if isinstance(n, ast.Return)]
    assert returns, "visitor_key returns nothing"
    for node in returns:
        assert "hexdigest" in ast.dump(node) or "digest" in ast.dump(node), (
            "visitor_key returns something that is not a digest"
        )


# ============================== P/Q/10. the digest, the salt, and what escapes


def test_the_raw_address_reaches_no_event_trace_or_stored_row(settings):
    """P, behaviourally: run a real request under a real address and grep.

    The key-level test above proves the digest does not contain the address.
    This one proves nothing *downstream* of it does either -- the events the
    request emits, the row the repository stores, and the result handed back.
    """
    import json
    from dataclasses import replace

    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    address = "203.0.113.211"
    quota_key = key({REAL_IP: address}, None, "s")
    assert address not in quota_key

    platform = AgentPlatform(replace(settings), repository=InMemoryRepository())
    try:
        result = platform.run("What is the status of order ORD-1001?", quota_key=quota_key)
        haystack = "\n".join(
            [
                json.dumps(platform.repository.events_for_request(result.request_id), default=str),
                json.dumps(platform.repository.recent_requests(10), default=str),
                str(result.response),
                str(result.request_id),
            ]
        )
        assert address not in haystack
        for octet in address.split("."):
            assert f".{octet}." not in haystack
    finally:
        platform.close()


def test_a_different_salt_yields_a_different_bucket(monkeypatch):
    """Q: the salt is what makes the digest an anonymisation rather than a lookup."""
    monkeypatch.setattr(dashboard, "_VISITOR_SALT", "salt-one")
    with_one = key({REAL_IP: IP_A}, None, "s")
    monkeypatch.setattr(dashboard, "_VISITOR_SALT", "salt-two")
    with_two = key({REAL_IP: IP_A}, None, "s")

    assert with_one != with_two
    assert with_one.startswith("visitor:") and with_two.startswith("visitor:")


def test_an_unset_salt_is_per_process_and_not_a_constant():
    """10: state the fallback rather than let it be assumed.

    With `VISITOR_ID_SALT` unset the app generates one per process. That keeps
    the anonymisation real -- an unsalted digest of an IPv4 address is a lookup
    against four billion entries -- but it deliberately does **not** survive a
    restart, so quota windows begin again on every deploy. A deployment that
    wants windows to persist has to set the variable; nothing here fakes that.
    """
    import re
    import subprocess

    source = "\n".join(
        [
            "import os, sys",
            "os.environ.pop('VISITOR_ID_SALT', None)",
            f"sys.path.insert(0, {str(DASHBOARD)!r})",
            f"sys.path.insert(0, {str(ROOT / 'src')!r})",
            "import app",
            "print(app._VISITOR_SALT)",
        ]
    )
    salts = []
    for _ in range(2):
        completed = subprocess.run(  # noqa: S603 - fixed argv, literals built above
            [sys.executable, "-c", source], capture_output=True, text=True, cwd=str(ROOT)
        )
        assert completed.returncode == 0, completed.stderr[-400:]
        salts.append(completed.stdout.strip().splitlines()[-1])

    assert all(re.fullmatch(r"[0-9a-f]{32}", salt) for salt in salts), salts
    assert salts[0] != salts[1], (
        "two processes produced the same unset-salt value; the fallback must be "
        "per-process, not a constant"
    )


# ================================= the trust boundary, pinned in the source


def test_only_x_real_ip_is_consulted_by_the_derivation():
    """L, structurally: no forwarding chain is read anywhere in the derivation.

    A behavioural test proves the current inputs produce the current answer.
    This proves the code has no branch that *could* read a chain, so a future
    edit that reintroduces one fails here rather than in production.

    Executable code only. The docstrings name the rejected headers on purpose,
    to say why they are rejected, and matching on prose would make this test
    fail for the documentation rather than for the behaviour -- so the AST is
    re-emitted with the docstrings dropped and the comments already gone.
    """
    import ast
    import inspect

    code = []
    for function in (dashboard._client_address, dashboard._canonical_ip):
        tree = ast.parse(inspect.getsource(function))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and ast.get_docstring(node):
                node.body = node.body[1:]
        code.append(ast.unparse(tree))
    lowered = "\n".join(code).lower()

    assert "_client_ip_header" in lowered, "the derivation names no trusted header"
    for forbidden in ("x-forwarded-for", "x-visitor-ip", "x-client-ip", "forwarded"):
        assert forbidden not in lowered, f"the derivation reads {forbidden}"
    # And the one header it does trust is the one Railway writes.
    assert dashboard._CLIENT_IP_HEADER == "x-real-ip"


def test_the_trusted_hops_setting_is_gone_rather_than_silently_inert():
    """An env var that no longer does anything is worse than one that never was.

    `VISITOR_TRUSTED_PROXY_HOPS` configured the chain read. With no chain read
    there is nothing for it to configure, and leaving it in place would let an
    operator set it, see no error, and believe they had tuned something.
    """
    source = (DASHBOARD / "app.py").read_text(encoding="utf-8")
    assert "VISITOR_TRUSTED_PROXY_HOPS" not in source
    assert not hasattr(dashboard, "_TRUSTED_PROXY_HOPS")
    assert "VISITOR_TRUSTED_PROXY_HOPS" not in (ROOT / ".env.example").read_text(
        encoding="utf-8"
    )
