"""The HTTP boundary.

The question these tests exist to answer is not "does the route return 200".
It is **whether the transport created a second way to do anything**. A boundary
that quietly skipped the policy engine, or reached a tool directly, or let a
caller approve an action that was never proposed, would pass a naive route test
and destroy the property the whole project is built on.

So the assertions below check the platform's invariants *through* HTTP: that a
denied action stays denied, that a tool still runs inside the gateway, that the
budget still binds, and that the API holds no authority of its own.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")  # starlette.testclient needs it

from starlette.applications import Starlette
from starlette.testclient import TestClient

from agent_platform.api import create_app
from agent_platform.api.schemas import MAX_INPUT_CHARS
from agent_platform.config import Settings
from agent_platform.llm.provider import Purpose
from agent_platform.llm.stub import StubProvider
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.tools.dataset import dataset_digest
from agent_platform.tools.execution import is_inside_gateway
from tests.conftest import bearer

DIGEST = "db512de8207f751e"


@pytest.fixture
def settings(tmp_path, monkeypatch, api_credentials) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    return Settings.from_env(load_dotenv_file=False)


@pytest.fixture
def platform(settings) -> AgentPlatform:
    p = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        yield p
    finally:
        p.close()


@pytest.fixture
def app(platform) -> Starlette:
    return create_app(platform)


@pytest.fixture
def client(app, api_credentials):
    """An authenticated caller.

    The subject of this file is whether the transport created a second way to
    do anything -- not the credential model, which has its own file. So the
    client here carries an identity that may both submit and approve, and every
    assertion below is about what the platform does once it knows who is
    asking.

    The unauthenticated case is not absent, it is elsewhere and deliberate:
    ``test_every_route_declares_its_authority`` at the bottom of this file
    drives every route with no credential at all.
    """
    with TestClient(app, headers=bearer(api_credentials.operator)) as c:
        yield c


def events(platform):
    return list(platform.repository.events)


# ===================================================== health and readiness


def test_health_is_200_and_touches_nothing(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_does_not_consult_the_provider_or_repository(settings):
    """A liveness probe that checks dependencies restarts every replica at once
    when a dependency blips. This one must answer with both broken."""

    class ExplodingRepo(InMemoryRepository):
        def metrics_summary(self):
            raise RuntimeError("repository is down")

    class ExplodingProvider(StubProvider):
        @property
        def name(self) -> str:
            raise RuntimeError("provider is down")

    p = AgentPlatform(settings, repository=ExplodingRepo())
    p.provider = ExplodingProvider()
    try:
        with TestClient(create_app(p)) as c:
            assert c.get("/health").status_code == 200
    finally:
        p.repository = InMemoryRepository()
        p.close()


def test_ready_reports_the_checks_it_actually_made(client, api_credentials):
    r = client.get("/ready", headers=bearer(api_credentials.scraper))
    assert r.status_code == 200
    body = r.json()
    assert body["ready"] is True
    assert body["checks"] == {"repository": True, "registry": True}
    assert body["provider"] == "stub"
    assert body["circuit"] == "closed"


def test_ready_is_503_when_the_repository_cannot_answer(settings, api_credentials):
    class ExplodingRepo(InMemoryRepository):
        def metrics_summary(self):
            raise RuntimeError("repository is down")

    p = AgentPlatform(settings, repository=ExplodingRepo())
    try:
        with TestClient(create_app(p)) as c:
            r = c.get("/ready", headers=bearer(api_credentials.scraper))
        assert r.status_code == 503
        assert r.json()["ready"] is False
        assert r.json()["checks"]["repository"] is False
    finally:
        p.repository = InMemoryRepository()
        p.close()


def test_ready_reports_provider_trouble_without_failing_readiness(
    client, platform, api_credentials
):
    """Every replica shares one provider. Failing readiness on an open circuit
    would remove them all at once, replacing governed answers with an
    unreachable service."""
    for _ in range(platform.settings.circuit_failure_threshold):
        platform.circuit.record_failure()
    assert platform.circuit.state == "open"

    r = client.get("/ready", headers=bearer(api_credentials.scraper))
    assert r.status_code == 200
    assert r.json()["ready"] is True
    assert r.json()["circuit"] == "open"


# ================================================================ contracts


def test_a_valid_request_returns_the_response_schema(client):
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
    assert r.status_code == 200
    body = r.json()

    for field in (
        "request_id", "trace_id", "status", "response",
        "route", "provider", "latency_ms", "retry_count", "errors",
    ):
        assert field in body, f"{field} missing from the response contract"

    assert body["status"] == "success"
    assert body["provider"] == "stub"
    assert "ORD-1001" in body["response"]


def test_internals_are_not_exposed(client):
    """``tool_result`` is raw tool output captured before output security masks
    PII and blocks secrets. It must not cross the network."""
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
    body = r.json()
    assert "tool_result" not in body
    assert "validation" not in body


@pytest.mark.parametrize(
    ("payload", "because"),
    [
        ({}, "input is required"),
        ({"input": ""}, "empty input is not a request"),
        ({"input": 42}, "input must be a string"),
        ({"input": None}, "null is not a string"),
        ({"input": "hi", "extra": "x"}, "unknown fields are refused"),
        ({"input": "x" * (MAX_INPUT_CHARS + 1)}, "over the structural ceiling"),
    ],
)
def test_invalid_requests_are_400(client, payload, because):
    r = client.post("/runs", json=payload)
    assert r.status_code == 400, because
    body = r.json()
    assert body["error"] == "bad_request"
    assert body["detail"]


def test_malformed_json_is_400(client):
    r = client.post(
        "/runs", content=b"{not json", headers={"content-type": "application/json"}
    )
    assert r.status_code == 400
    assert r.json()["error"] == "bad_request"


def test_a_json_array_body_is_400(client):
    r = client.post("/runs", content=b"[1,2,3]", headers={"content-type": "application/json"})
    assert r.status_code == 400


def test_input_security_rejection_is_422_not_400(client, platform):
    """The platform's input-security layer is the authority on what input is
    acceptable. The API must surface its refusal, not pre-empt it: 400 means
    'never became a request', 422 means 'the platform considered and refused'."""
    oversized = "x" * (platform.settings.max_input_chars + 100)
    assert len(oversized) < MAX_INPUT_CHARS, "must be under the API's structural cap"

    r = client.post("/runs", json={"input": oversized})
    assert r.status_code == 422
    assert r.json()["status"] == "rejected"


def test_an_unknown_platform_status_becomes_500(client, platform, monkeypatch):
    """A status the mapping has never seen must not be presented as normal."""
    from agent_platform.platform import RunResult

    def weird(_user_input):
        return RunResult(
            request_id="r", trace_id="t", status="something_new", response="?"
        )

    monkeypatch.setattr(platform, "run", weird)
    r = client.post("/runs", json={"input": "hello"})
    assert r.status_code == 500


def test_an_internal_error_is_a_sanitised_500(client, platform, monkeypatch):
    def explode(_user_input):
        raise RuntimeError(r"secret-value at C:\Users\someone\app.py")

    monkeypatch.setattr(platform, "run", explode)
    r = client.post("/runs", json={"input": "hello"})

    assert r.status_code == 500
    body = json.dumps(r.json())
    assert "secret-value" not in body
    assert "C:\\Users" not in body
    assert "Traceback" not in body
    assert r.json()["error"] == "internal_error"


def test_a_provider_failure_is_reported_not_hidden(client, platform):
    """A provider outage is a recorded run in which no tool executed. It comes
    back as a result with ``status: failed``, not as a transport error."""
    from agent_platform.llm.provider import LLMUnavailableError

    class DeadProvider(StubProvider):
        def generate(self, prompt, **kwargs):
            raise LLMUnavailableError("provider is unreachable")

    platform.provider = DeadProvider()
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})

    assert r.status_code == 200
    assert r.json()["status"] == "failed"

    executed = [
        e for e in events(platform) if e.event_type == "tool_call" and e.status == "success"
    ]
    assert executed == [], "a provider failure must never execute a tool"


# =========================================== the boundary is not a second path


def test_http_reaches_a_tool_only_through_the_gateway(client, platform):
    """HTTP -> API -> Platform -> Gateway -> Tool.

    Proven by the event stream: a tool_call event is only ever written by the
    gateway, so its presence is evidence the gateway ran it.
    """
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
    assert r.status_code == 200

    stream = events(platform)
    tool_calls = [e for e in stream if e.event_type == "tool_call"]
    assert tool_calls, "no tool ran; this test is not measuring what it claims"
    assert tool_calls[0].tool == "get_order"

    kinds = [e.event_type for e in stream]
    assert "policy_decision" in kinds, "a tool ran without a recorded policy decision"
    assert kinds.index("policy_decision") < kinds.index("tool_call"), (
        "the tool ran before policy evaluated it"
    )


def test_the_api_never_executes_a_tool_itself(client):
    """The API must not hold gateway authorisation of its own."""
    assert not is_inside_gateway()
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
    assert r.status_code == 200
    assert not is_inside_gateway(), "the API leaked gateway authorisation"


@pytest.mark.parametrize(
    "question",
    [
        "What is the status of order ORD-1001?",
        "What is the refund policy?",
        "Tell me about customer CUS-2001",
    ],
)
def test_every_tool_invocation_over_http_was_made_by_the_gateway(
    settings, question, api_credentials
):
    """The real bypass test: **count handler calls, not events.**

    An earlier version of this test watched the event stream, and a deliberately
    injected direct tool call in the API handler sailed straight through it --
    because only ``ToolGateway.submit`` writes a ``tool_call`` event, so calling
    a handler behind the gateway's back leaves the stream looking untouched.

    This counts every invocation of every tool handler, by wrapping the handlers
    themselves, and requires that number to equal the number of invocations the
    gateway recorded. A handler that ran without the gateway recording it is a
    bypass, whichever path called it.
    """
    import dataclasses

    from agent_platform.tools import fake_tools, registry
    from agent_platform.tools.registry import ToolRegistry

    invocations: list[str] = []

    def counting(name, fn):
        def wrapper(**kwargs):
            invocations.append(name)
            return fn(**kwargs)

        return wrapper

    wrapped = tuple(
        dataclasses.replace(d, handler=counting(d.name, d.handler))
        for d in registry.TOOL_DEFINITIONS
    )
    # Patch the module attributes too, so a call that reaches the function
    # directly rather than through the registry is still counted.
    originals = {d.name: getattr(fake_tools, d.name) for d in wrapped}
    for d in wrapped:
        setattr(fake_tools, d.name, d.handler)

    p = AgentPlatform(
        settings, repository=InMemoryRepository(), registry=ToolRegistry(wrapped)
    )
    try:
        with TestClient(
                create_app(p), headers=bearer(api_credentials.operator)
            ) as c:
            assert c.post("/runs", json={"input": question}).status_code == 200
        recorded = [e.tool for e in events(p) if e.event_type == "tool_call"]
    finally:
        for name, fn in originals.items():
            setattr(fake_tools, name, fn)
        p.close()

    assert sorted(invocations) == sorted(recorded), (
        f"tool handlers ran {sorted(invocations)} but the gateway recorded "
        f"{sorted(recorded)}; the difference executed outside the gateway"
    )


def test_a_policy_denial_over_http_executes_nothing(client, platform):
    r = client.post("/runs", json={"input": "Delete customer record CUS-2001"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "blocked"
    assert body["policy_decision"]["decision"] == "deny"

    executed = [
        e for e in events(platform) if e.event_type == "tool_call" and e.status == "success"
    ]
    assert executed == [], "a denied action executed anyway"


def test_authorization_still_binds_over_http(client, platform):
    """``delete_record`` is held by no agent. HTTP must not become the caller
    that finally reaches it."""
    client.post("/runs", json={"input": "Delete customer record CUS-2001"})
    ran = [e.tool for e in events(platform) if e.event_type == "tool_call"]
    assert "delete_record" not in ran


def test_the_budget_still_binds_over_http(client, platform):
    from agent_platform.cost.budget import BudgetDecision
    from agent_platform.cost.tracker import BudgetExceededError

    def refuse(*args, **kwargs):
        status = platform.budget_guard.status()
        raise BudgetExceededError(
            BudgetDecision(allowed=False, reason="daily budget exhausted", status=status)
        )

    platform.budget_guard.check = refuse  # type: ignore[method-assign]
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})

    assert r.status_code == 200
    assert r.json()["status"] == "blocked"


def test_rate_limiting_binds_over_http(client, platform):
    from agent_platform.security.rate_limit import RateLimitResult

    platform.rate_limiter.acquire = lambda: RateLimitResult(  # type: ignore[method-assign]
        allowed=False, reason="too many requests", retry_after_seconds=30.0
    )
    r = client.post("/runs", json={"input": "hello"})

    assert r.status_code == 429
    assert r.json()["status"] == "rate_limited"


# ============================================================= confirmations


def test_a_high_risk_action_suspends_over_http(client):
    r = client.post("/runs", json={"input": "Send a message to customer CUS-2001"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "awaiting_confirmation"
    pending = body["awaiting_confirmation"]
    assert pending is not None
    assert pending["tool"] == "send_email"
    assert pending["risk_level"]


def test_confirming_an_unknown_request_is_404(client):
    r = client.post("/runs/does-not-exist/confirm", json={"approved": True})
    assert r.status_code == 404
    assert r.json()["error"] == "not_found"


def test_an_unconfirmed_action_executes_nothing(client, platform):
    """Read-only context gathering before the proposal is expected and allowed.
    What must not happen is the *suspended* action running unapproved."""
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()
    pending_tool = created["awaiting_confirmation"]["tool"]

    executed = [
        e.tool
        for e in events(platform)
        if e.event_type == "tool_call" and e.status == "success"
    ]
    assert pending_tool not in executed, "a suspended action ran without approval"


def test_approving_over_http_resumes_the_same_action(client, platform):
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()
    request_id = created["request_id"]

    r = client.post(f"/runs/{request_id}/confirm", json={"approved": True})
    assert r.status_code == 200
    assert r.json()["status"] == "success"

    executed = [
        e.tool
        for e in events(platform)
        if e.event_type == "tool_call" and e.status == "success"
    ]
    assert "send_email" in executed


def test_declining_over_http_executes_nothing(client, platform):
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()

    pending_tool = created["awaiting_confirmation"]["tool"]
    r = client.post(f"/runs/{created['request_id']}/confirm", json={"approved": False})
    assert r.status_code == 200

    executed = [
        e.tool
        for e in events(platform)
        if e.event_type == "tool_call" and e.status == "success"
    ]
    assert pending_tool not in executed, "a declined action executed"


def test_a_confirmation_cannot_be_replayed(client, platform):
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()
    request_id = created["request_id"]

    assert client.post(f"/runs/{request_id}/confirm", json={"approved": True}).status_code == 200
    replay = client.post(f"/runs/{request_id}/confirm", json={"approved": True})

    assert replay.status_code == 404, "a consumed confirmation was resumable again"
    sends = [
        e
        for e in events(platform)
        if e.event_type == "tool_call" and e.tool == "send_email" and e.status == "success"
    ]
    assert len(sends) == 1, "the action executed twice"


def test_the_confirm_body_cannot_name_the_action(client):
    """The decision is the entire body. Naming a tool must not redirect it."""
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()

    r = client.post(
        f"/runs/{created['request_id']}/confirm",
        json={"approved": True, "tool": "delete_record"},
    )
    assert r.status_code == 400, "the API accepted an action name in a confirmation"


@pytest.mark.parametrize("payload", [{}, {"approved": "yes"}, {"approved": None}])
def test_invalid_confirmations_are_400(client, payload):
    created = client.post(
        "/runs", json={"input": "Send a message to customer CUS-2001"}
    ).json()
    r = client.post(f"/runs/{created['request_id']}/confirm", json=payload)
    assert r.status_code == 400


# ================================================== parity with the CLI path


@pytest.mark.parametrize(
    "question",
    [
        "What is the status of order ORD-1001?",
        "What is the refund policy?",
        "How do I reset my password?",
        "Delete customer record CUS-2001",
    ],
)
def test_http_and_direct_calls_agree(settings, question, api_credentials):
    """The same intent through the transport and through the object must reach
    the same outcome. Byte-identical is not required -- ids and timings differ --
    but status, route and the governing decision must not."""
    direct_platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        direct = direct_platform.run(question)
    finally:
        direct_platform.close()

    http_platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
                create_app(http_platform), headers=bearer(api_credentials.operator)
            ) as c:
            body = c.post("/runs", json={"input": question}).json()
    finally:
        http_platform.close()

    assert body["status"] == direct.status
    assert body["route"] == direct.route
    assert body["response"] == direct.response
    assert (body["policy_decision"] or {}).get("decision") == (
        direct.policy_decision or {}
    ).get("decision")


def test_the_stub_still_needs_no_network_or_key(client):
    r = client.post("/runs", json={"input": "What is the refund policy?"})
    assert r.status_code == 200
    assert r.json()["provider"] == "stub"


def test_the_dataset_is_unchanged():
    assert dataset_digest() == DIGEST


# ============================================================== cost of path


def test_the_api_adds_no_model_calls(settings, api_credentials):
    """The transport must not cost a call. Same question, same count."""

    class Counting(StubProvider):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[str] = []

        def generate(self, prompt, **kwargs):
            purpose = kwargs.get("purpose")
            self.calls.append(
                purpose.value if isinstance(purpose, Purpose) else str(purpose)
            )
            return super().generate(prompt, **kwargs)

    question = "What is the status of order ORD-1001?"

    direct_platform = AgentPlatform(settings, repository=InMemoryRepository())
    direct_provider = Counting()
    direct_platform.provider = direct_provider
    try:
        direct_platform.run(question)
    finally:
        direct_platform.close()

    http_platform = AgentPlatform(settings, repository=InMemoryRepository())
    http_provider = Counting()
    http_platform.provider = http_provider
    try:
        with TestClient(
            create_app(http_platform), headers=bearer(api_credentials.operator)
        ) as c:
            c.post("/runs", json={"input": question})
    finally:
        http_platform.close()

    assert http_provider.calls == direct_provider.calls


def test_every_status_the_platform_can_produce_is_mapped():
    """The HTTP mapping must cover the platform's whole vocabulary.

    This test exists because it caught a real omission: ``declined`` was
    missing, so declining a confirmation over HTTP returned 500. Rather than
    fix that one entry and move on, the statuses are read back out of the
    source, so a status added in a later phase fails here instead of silently
    becoming an error to every API caller.
    """
    import re
    from pathlib import Path

    from agent_platform.api.schemas import STATUS_TO_HTTP

    src = Path(__file__).resolve().parents[2] / "src" / "agent_platform"
    produced = {"success"}  # the default in platform._invoke
    produced |= set(
        re.findall(r'status="([a-z_]+)"', (src / "platform.py").read_text(encoding="utf-8"))
    )
    produced |= set(
        re.findall(
            r'"status":\s*"([a-z_]+)"',
            (src / "orchestration" / "graph.py").read_text(encoding="utf-8"),
        )
    )

    unmapped = produced - set(STATUS_TO_HTTP)
    assert not unmapped, f"these statuses would fall through to 500: {sorted(unmapped)}"


# ============================================================ route authority


def test_every_route_declares_its_authority(app):
    """No route reaches the application without a decision about who may call it.

    The Starlette route table and the middleware's path matcher are built from
    one literal, so this checks the thing that literal is for: that every route
    which exists carries either a scope or an explicit ``PUBLIC``, and that the
    two tables describe the same set of paths.

    A route added later without a scope is a ``TypeError`` at import. This is
    the second lock: it catches a route registered by some other means.
    """
    from agent_platform.api.app import PUBLIC
    from agent_platform.security.api_auth import KNOWN_SCOPES

    specs = app.state.route_specs
    declared = {spec.path for spec in specs}
    routed = {route.path for route in app.routes}
    assert declared == routed, "the route table and the scope table disagree"

    for spec in specs:
        assert spec.scope == PUBLIC or spec.scope in KNOWN_SCOPES, (
            f"{spec.path} declares {spec.scope!r}, which is neither PUBLIC nor a known scope"
        )


@pytest.mark.parametrize(
    ("method", "path", "public"),
    [
        ("GET", "/health", True),
        ("GET", "/ready", True),
        ("GET", "/metrics", False),
        ("POST", "/runs", False),
        ("POST", "/runs/whatever/confirm", False),
    ],
)
def test_no_credential_reaches_only_the_public_routes(app, method, path, public):
    """Driven with no credential at all, against every route that exists."""
    with TestClient(app) as anonymous:
        response = anonymous.request(method, path, json={"input": "x", "approved": True})
    if public:
        assert response.status_code == 200
    else:
        assert response.status_code == 401
        assert response.headers["WWW-Authenticate"] == "Bearer"
