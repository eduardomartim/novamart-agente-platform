"""The execution boundary, exercised as a boundary.

Everything here talks to a **real MCP server in a real subprocess** over stdio.
That is the point: the property under test is that authorisation survives
leaving the process, and a mocked server shares memory with the test, which is
precisely the condition the grant exists to stop relying on.

The negative cases matter most. A boundary that runs authorised calls is easy;
one that refuses a grant issued for a different tool, or for different
arguments, or one already used, is the only kind worth building.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from agent_platform.execution import grant as grants
from agent_platform.execution.transport import McpToolTransport, subprocess_environment
from agent_platform.mcp_server import ToolServer

REPO = Path(__file__).resolve().parents[2]
SECRET = "integration-test-signing-secret-not-real"
REDIS_URL = os.getenv("TEST_REDIS_URL", "redis://127.0.0.1:16379/0")


def _redis_up() -> bool:
    try:
        from agent_platform.state.backend import redis_backend_from_url

        redis_backend_from_url(REDIS_URL)
    except Exception:
        return False
    return True


REDIS_AVAILABLE = _redis_up()
needs_redis = pytest.mark.skipif(not REDIS_AVAILABLE, reason="no reachable Redis")


@pytest.fixture(scope="module")
def transport():
    """A real subprocess running the tool server, shut down afterwards."""
    env = subprocess_environment(
        {**os.environ, "EXECUTION_GRANT_SECRET": SECRET, "PYTHONPATH": str(REPO / "src")}
    )
    t = McpToolTransport(
        command=sys.executable, args=["-m", "agent_platform.mcp_server"], env=env
    )
    try:
        t.start()
        yield t
    finally:
        t.close()


def grant_for(tool: str, arguments: dict, **kw) -> str:
    token, _ = grants.issue(
        secret=SECRET,
        execution_id=f"req-{uuid.uuid4().hex[:8]}:1",
        tool=tool,
        arguments=arguments,
        **kw,
    )
    return token


def call(transport, tool: str, arguments: dict, token: str):
    return transport.call(tool, {"grant": token, "arguments": arguments})


# ================================================== the subprocess is genuine


def test_the_server_runs_in_a_separate_process(transport):
    """Not a mock, and not this interpreter."""
    result = call(
        transport, "get_order", {"order_id": "ORD-1001"},
        grant_for("get_order", {"order_id": "ORD-1001"}),
    )
    assert result["found"] is True
    assert result["order"]["order_id"] == "ORD-1001"


def test_the_subprocess_never_receives_the_provider_credential():
    """The tool server runs simulated tools; it has no use for a real key, and
    what it cannot see it cannot leak."""
    env = subprocess_environment(
        {
            "EXECUTION_GRANT_SECRET": SECRET,
            "GEMINI_API_KEY": "a-value-that-must-not-be-forwarded",
            "GOOGLE_API_KEY": "also-not-forwarded",
            "PATH": "/usr/bin",
        }
    )
    assert "GEMINI_API_KEY" not in env
    assert "GOOGLE_API_KEY" not in env
    assert env["EXECUTION_GRANT_SECRET"] == SECRET
    assert "a-value-that-must-not-be-forwarded" not in json.dumps(env)


def test_the_secret_is_not_visible_in_the_subprocess_command_line():
    """A secret in argv is a secret in every process listing on the machine."""
    env = subprocess_environment({**os.environ, "EXECUTION_GRANT_SECRET": SECRET})
    args = [sys.executable, "-m", "agent_platform.mcp_server"]
    assert all(SECRET not in part for part in args)
    assert env["EXECUTION_GRANT_SECRET"] == SECRET, "it travels by environment"


# ========================================================= authorised calls


@pytest.mark.parametrize(
    ("tool", "arguments", "check"),
    [
        ("get_order", {"order_id": "ORD-1001"}, lambda r: r["order"]["status"] == "shipped"),
        ("get_customer", {"customer_id": "CUS-2001"}, lambda r: r["found"] is True),
        ("search", {"query": "refund policy"}, lambda r: len(r["results"]) > 0),
    ],
)
def test_a_valid_grant_executes_the_tool(transport, tool, arguments, check):
    result = call(transport, tool, arguments, grant_for(tool, arguments))
    assert check(result), f"unexpected result for {tool}: {result}"


# ================================================================= refusals


def test_a_grant_for_another_tool_is_refused(transport):
    """Tool confusion, across the boundary."""
    token = grant_for("get_order", {"customer_id": "CUS-2001"})
    with pytest.raises(PermissionError):
        call(transport, "get_customer", {"customer_id": "CUS-2001"}, token)


def test_swapped_arguments_are_refused(transport):
    """Argument confusion: a grant for one record must not reach another."""
    token = grant_for("get_order", {"order_id": "ORD-1001"})
    with pytest.raises(PermissionError):
        call(transport, "get_order", {"order_id": "ORD-1003"}, token)


def test_a_forged_grant_is_refused(transport):
    forged, _ = grants.issue(
        secret="a-completely-different-secret-value",
        execution_id="req-x:1",
        tool="get_order",
        arguments={"order_id": "ORD-1001"},
    )
    with pytest.raises(PermissionError):
        call(transport, "get_order", {"order_id": "ORD-1001"}, forged)


def test_an_expired_grant_is_refused(transport):
    token = grant_for(
        "get_order", {"order_id": "ORD-1001"}, ttl_seconds=1.0, now=time.time() - 120
    )
    with pytest.raises(PermissionError):
        call(transport, "get_order", {"order_id": "ORD-1001"}, token)


def test_a_replayed_grant_is_refused(transport):
    """First use executes; the second must not."""
    arguments = {"order_id": "ORD-1002"}
    token = grant_for("get_order", arguments)

    assert call(transport, "get_order", arguments, token)["found"] is True
    with pytest.raises(PermissionError):
        call(transport, "get_order", arguments, token)


def test_a_missing_grant_is_refused(transport):
    with pytest.raises(PermissionError):
        transport.call("get_order", {"grant": "", "arguments": {"order_id": "ORD-1001"}})


def test_an_unregistered_tool_cannot_be_invented(transport):
    """A name the caller made up must not resolve to anything."""
    token = grant_for("drop_database", {})
    # PermissionError specifically: the server reports an unknown tool the same
    # way it reports any other refusal, so a probe cannot distinguish "no such
    # tool" from "not authorised" and use that to enumerate the registry.
    with pytest.raises(PermissionError):
        call(transport, "drop_database", {}, token)


def test_invalid_arguments_are_refused_before_execution(transport):
    """Schema validation happens on the server, against the tool's own model."""
    arguments = {"order_id": {"nested": "not-a-string"}}
    with pytest.raises(PermissionError):
        call(transport, "get_order", arguments, grant_for("get_order", arguments))


def test_a_refusal_reveals_nothing_about_why(transport):
    """Distinguishable refusals help someone iterate towards a forgery."""
    reasons: set[str] = set()
    attempts = [
        (
            "get_order",
            {"order_id": "ORD-1001"},
            grant_for("get_customer", {"order_id": "ORD-1001"}),
        ),
        ("get_order", {"order_id": "ORD-9999"}, grant_for("get_order", {"order_id": "ORD-1001"})),
        ("get_order", {"order_id": "ORD-1001"}, "v1.bogus.bogus"),
    ]
    for tool, args, token in attempts:
        try:
            call(transport, tool, args, token)
        except PermissionError as exc:
            reasons.add(str(exc))
    assert len(reasons) == 1, f"refusals are distinguishable: {reasons}"


# ============================================ the tools still guard themselves


def test_the_server_opens_the_gateway_context_around_the_handler():
    """The ContextVar layer is intact inside the server process.

    Called directly rather than over MCP so the assertion is about the server's
    own behaviour: a handler reached without ``gateway_execution()`` raises.
    """
    server = ToolServer(secret=SECRET)
    arguments = {"order_id": "ORD-1001"}
    token = grant_for("get_order", arguments)
    assert server.execute("get_order", token, arguments)["found"] is True

    from agent_platform.tools import fake_tools
    from agent_platform.tools.execution import DirectToolInvocationError

    with pytest.raises(DirectToolInvocationError):
        fake_tools.get_order(order_id="ORD-1001")


def test_a_grant_hidden_among_the_arguments_is_refused():
    server = ToolServer(secret=SECRET)
    arguments = {"order_id": "ORD-1001", grants.GRANT_ARGUMENT: "smuggled"}
    with pytest.raises(PermissionError):
        server.execute("get_order", grant_for("get_order", arguments), arguments)


# =================================================== cross-process and racing


@needs_redis
def test_a_grant_issued_by_one_instance_is_consumed_by_another():
    """Cross-process, with the nonce in shared state.

    Two ToolServer instances with independent backends over one Redis: the
    first consumes the grant, the second must find it spent. Without shared
    nonces both would consider themselves the first.
    """
    first = ToolServer(secret=SECRET, redis_url=REDIS_URL)
    second = ToolServer(secret=SECRET, redis_url=REDIS_URL)

    arguments = {"order_id": "ORD-1001"}
    token = grant_for("get_order", arguments)

    assert first.execute("get_order", token, arguments)["found"] is True
    with pytest.raises(PermissionError):
        second.execute("get_order", token, arguments)


@needs_redis
def test_concurrent_replays_across_instances_produce_exactly_one_execution():
    """The race the nonce exists for, run for real."""
    arguments = {"order_id": "ORD-1001"}
    token = grant_for("get_order", arguments, ttl_seconds=60.0)

    executed: list[int] = []
    lock = threading.Lock()
    start = threading.Barrier(16)

    def attempt() -> None:
        server = ToolServer(secret=SECRET, redis_url=REDIS_URL)
        start.wait(timeout=30)
        try:
            server.execute("get_order", token, arguments)
        except PermissionError:
            return
        with lock:
            executed.append(1)

    threads = [threading.Thread(target=attempt) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert len(executed) == 1, f"{len(executed)} instances executed the same grant"


# ======================================================= the server standalone


def test_the_server_refuses_to_start_without_a_secret():
    """Fail closed. A server that starts and refuses everything is
    indistinguishable from a misconfigured one."""
    proc = subprocess.run(
        [sys.executable, "-m", "agent_platform.mcp_server"],
        capture_output=True,
        text=True,
        timeout=60,
        env={k: v for k, v in os.environ.items() if k != "EXECUTION_GRANT_SECRET"}
        | {"PYTHONPATH": str(REPO / "src")},
        input="",
    )
    assert proc.returncode != 0
    assert "EXECUTION_GRANT_SECRET" in (proc.stderr + proc.stdout)


def test_the_secret_never_appears_in_server_output():
    proc = subprocess.run(
        [sys.executable, "-c", "import agent_platform.mcp_server as m; m.ToolServer(secret=None)"],
        capture_output=True,
        text=True,
        timeout=60,
        env=subprocess_environment(
            {**os.environ, "EXECUTION_GRANT_SECRET": SECRET, "PYTHONPATH": str(REPO / "src")}
        ),
    )
    assert SECRET not in proc.stdout
    assert SECRET not in proc.stderr


# ========================================= what must not cross the boundary


def test_retrieval_stays_in_process_even_under_mcp_transport():
    """``search`` must not be dispatched over MCP.

    Hybrid retrieval is selected by a provider bound to the request through a
    ContextVar. A separate tool-server process has no such binding, so the same
    search would silently fall back to lexical BM25 and return a worse answer
    with nothing to indicate it -- the failure mode 7G.0 exists to prevent.

    Shipping the provider credential to the tool server to fix that would be
    worse and is forbidden: that process runs simulated tools and must never
    hold a real key.
    """
    from agent_platform.tools.gateway import IN_PROCESS_ONLY

    assert "search" in IN_PROCESS_ONLY


def test_business_tools_are_not_exempted():
    """The carve-out must stay narrow. Everything standing in for an external
    system belongs behind the boundary."""
    from agent_platform.tools.gateway import IN_PROCESS_ONLY
    from agent_platform.tools.registry import default_registry

    exempt = set(IN_PROCESS_ONLY)
    assert exempt == {"search"}, f"the in-process carve-out grew: {sorted(exempt)}"

    for name in default_registry().names():
        if name != "search":
            assert name not in exempt, f"{name} was exempted from the boundary"


def test_mcp_mode_requires_a_secret_to_construct_a_platform():
    """Fail closed at construction, not per call."""
    import tempfile

    from agent_platform.config import Settings
    from agent_platform.execution.grant import GrantConfigurationError
    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    env = {
        "GEMINI_API_KEY": "",
        "DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "t.db"),
        "TOOL_TRANSPORT": "mcp",
        "EXECUTION_GRANT_SECRET": "",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        settings = Settings.from_env(load_dotenv_file=False)
        with pytest.raises(GrantConfigurationError):
            AgentPlatform(settings, repository=InMemoryRepository())
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_inprocess_mode_needs_no_secret_at_all():
    """The default must keep working with nothing configured."""
    import tempfile

    from agent_platform.config import Settings
    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    env = {
        "GEMINI_API_KEY": "",
        "DATABASE_PATH": str(Path(tempfile.mkdtemp()) / "t.db"),
        "TOOL_TRANSPORT": "inprocess",
        "EXECUTION_GRANT_SECRET": "",
    }
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        platform = AgentPlatform(
            Settings.from_env(load_dotenv_file=False), repository=InMemoryRepository()
        )
        try:
            result = platform.run("What is the status of order ORD-1001?")
            assert result.status == "success"
            assert result.response.strip(), "a successful run produced no answer"

            # Evidence that the *right operation actually ran*, taken from the
            # trace rather than from the sentence.
            #
            # This asserted `"ORD-1001" in result.response`, which stopped being
            # true when the answer became a humanised sentence: the tool writes
            # it in the interface language and it names the customer and the
            # total instead of echoing the identifier. That was a deliberate
            # product change, and pinning the prose here was pinning a
            # translation -- the assertion would break again on the next wording
            # or locale without anything about this boundary having moved.
            #
            # The identifier is still a contract, of the tool call rather than
            # of the sentence, which is the layer the rest of this file asserts
            # it at (see test_the_server_runs_in_a_separate_process). Checking it
            # here keeps the property this test needs -- that `inprocess` really
            # executed the lookup, rather than reaching `success` vacuously --
            # and is indifferent to how the answer is phrased.
            calls = [
                event
                for event in platform.repository.events_for_request(result.request_id)
                if event["event_type"] == "tool_call" and event["status"] == "success"
            ]
            lookups = [call for call in calls if call["tool"] == "get_order"]
            assert lookups, f"no successful get_order ran; tool calls were {calls}"
            assert lookups[0]["payload"]["arguments"] == {"order_id": "ORD-1001"}
            assert lookups[0]["payload"]["output"]["order"]["order_id"] == "ORD-1001"
        finally:
            platform.close()
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
