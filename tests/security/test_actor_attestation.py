"""Who approved it, and whether the record can be believed.

The platform's central claim is that a high-risk action cannot execute without
a human deciding. The audit record of that decision is
``CONFIRMATION_RESOLVED``, and it carries three things: whether it was approved,
which surface it came from, and who the approver was.

Two of those were always trustworthy. The third was a string the caller typed
into the request body, written down next to a cryptographic fingerprint as
though somebody had checked it. An audit trail that records a self-asserted
identity in a field that reads as verified is worse than no trail at all: an
absent trail is known to be absent.

These tests pin the fix and the shape of the fix. The approver comes from the
credential and from nowhere else, and ``source`` -- which already existed --
now carries real information: ``api`` means attested, ``cli`` means asserted.
"""

from __future__ import annotations

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient

from agent_platform.api.app import create_app
from agent_platform.config import Settings
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from tests.conftest import bearer

HIGH_RISK = "Send a message to customer CUS-2001"


@pytest.fixture
def settings(tmp_path, monkeypatch, api_credentials) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "attest.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    return Settings.from_env(load_dotenv_file=False)


@pytest.fixture
def platform(settings):
    instance = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        yield instance
    finally:
        instance.close()


@pytest.fixture
def client(platform, settings, api_credentials):
    with TestClient(
        create_app(platform, settings=settings), headers=bearer(api_credentials.operator)
    ) as c:
        yield c


def resolutions(platform) -> list[dict]:
    return [
        e.payload
        for e in platform.repository.events
        if e.event_type == "confirmation_resolved"
    ]


def suspend(client) -> str:
    body = client.post("/runs", json={"input": HIGH_RISK}).json()
    assert body["status"] == "awaiting_confirmation", body["status"]
    return body["request_id"]


# ============================================== the body cannot name a person


def test_an_actor_in_the_body_is_rejected(client):
    request_id = suspend(client)
    r = client.post(
        f"/runs/{request_id}/confirm", json={"approved": True, "actor": "compliance-officer"}
    )
    assert r.status_code == 400


def test_the_rejection_explains_itself(client):
    """This field was published contract until it turned out to be a hole.

    "Extra inputs are not permitted" tells an integrator that something changed
    but not what to do about it.
    """
    request_id = suspend(client)
    detail = client.post(
        f"/runs/{request_id}/confirm", json={"approved": True, "actor": "x"}
    ).json()["detail"]
    assert "actor" in detail
    assert "authenticated credential" in detail


def test_a_rejected_body_executes_nothing(client, platform):
    request_id = suspend(client)
    client.post(f"/runs/{request_id}/confirm", json={"approved": True, "actor": "x"})
    # `search` runs during retrieval, before the action is ever proposed, so
    # the assertion is about the suspended tool specifically rather than about
    # tool calls in general.
    executed = {
        e.tool
        for e in platform.repository.events
        if e.event_type == "tool_call" and e.status == "success"
    }
    assert "send_email" not in executed
    # And the confirmation is still there to be resolved properly.
    assert platform.has_pending_confirmation(request_id)


# ================================================ the record names the caller


def test_the_recorded_approver_is_the_authenticated_principal(client, platform):
    request_id = suspend(client)
    assert client.post(f"/runs/{request_id}/confirm", json={"approved": True}).status_code == 200

    resolved = resolutions(platform)
    assert len(resolved) == 1
    assert resolved[0]["actor"] == "operator"
    assert resolved[0]["source"] == "api"
    assert resolved[0]["approved"] is True


def test_a_different_credential_produces_a_different_approver(
    settings, platform, api_credentials
):
    """The complement. If the field were constant this test would pass anyway.

    ``approver`` and ``operator`` are separate identities, and the record has to
    tell them apart -- otherwise "the approver is taken from the credential" is
    a claim about code rather than about what was written down.
    """
    app = create_app(platform, settings=settings)
    with TestClient(app, headers=bearer(api_credentials.operator)) as c:
        first = suspend(c)
    with TestClient(app, headers=bearer(api_credentials.operator)) as c:
        c.post(f"/runs/{first}/confirm", json={"approved": True})

    with TestClient(app, headers=bearer(api_credentials.operator)) as c:
        second = suspend(c)
    with TestClient(app, headers=bearer(api_credentials.approver)) as c:
        c.post(f"/runs/{second}/confirm", json={"approved": True})

    actors = [r["actor"] for r in resolutions(platform)]
    assert actors == ["operator", "approver"]


def test_a_declined_action_also_records_who_declined(client, platform):
    request_id = suspend(client)
    r = client.post(f"/runs/{request_id}/confirm", json={"approved": False})
    assert r.status_code == 200

    resolved = resolutions(platform)
    assert resolved[0]["actor"] == "operator"
    assert resolved[0]["approved"] is False


def test_an_unauthenticated_deployment_says_so_in_the_record(tmp_path, monkeypatch):
    """Running with authentication off must not imply an operator who never existed.

    "unauthenticated" in the actor field is the honest value. A friendly default
    like "operator" would put a plausible human into an audit record that nobody
    verified, which is the defect this whole file is about, wearing a different
    hat.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "open.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS", raising=False)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.setenv("API_AUTH_MODE", "disabled")
    settings = Settings.from_env(load_dotenv_file=False)

    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(create_app(platform, settings=settings)) as c:
            request_id = suspend(c)
            c.post(f"/runs/{request_id}/confirm", json={"approved": True})
        assert resolutions(platform)[0]["actor"] == "unauthenticated"
    finally:
        platform.close()


# ======================================================== attested vs asserted


def test_source_distinguishes_an_attested_actor_from_an_asserted_one(
    client, platform, settings
):
    """No new field was needed. ``source`` already carried this distinction.

    It only became worth reading once ``actor`` stopped being whatever the
    request said it was: ``api`` is now verified, ``cli`` is still the local
    operator's word, and the record says which.
    """
    over_http = suspend(client)
    client.post(f"/runs/{over_http}/confirm", json={"approved": True})

    direct = platform.run(HIGH_RISK)
    assert direct.status == "awaiting_confirmation"
    platform.confirm(direct.request_id, approved=True, actor="cli-user", source="cli")

    by_source = {r["source"]: r["actor"] for r in resolutions(platform)}
    assert by_source == {"api": "operator", "cli": "cli-user"}


def test_the_confirmation_schema_no_longer_has_an_actor_field():
    """Structural: the field is gone, not merely ignored.

    A field that is parsed and discarded is a field somebody re-wires later.
    """
    from agent_platform.api.schemas import ConfirmRequest

    assert set(ConfirmRequest.model_fields) == {"approved"}
    assert ConfirmRequest.model_config["extra"] == "forbid"
