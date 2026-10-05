"""F-04: the API refuses an oversized body while reading it, not after.

Both write routes used ``await request.body()``, which buffers whatever the
client sends before a single check runs. The schema's character limit was
applied after parsing -- after the memory had already been spent -- so it
bounded what the platform was asked, not what the server would hold.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("starlette")
pytest.importorskip("httpx")

from starlette.testclient import TestClient

from agent_platform.api import create_app
from agent_platform.api.app import _BodyTooLarge, _read_body
from agent_platform.api.schemas import MAX_BODY_BYTES, MAX_INPUT_CHARS
from agent_platform.config import Settings
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from tests.conftest import bearer


@pytest.fixture
def client(tmp_path, monkeypatch, api_credentials):
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "api.db"))
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    platform = AgentPlatform(
        Settings.from_env(load_dotenv_file=False), repository=InMemoryRepository()
    )
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as c:
            yield c
    finally:
        platform.close()


def test_the_byte_limit_admits_every_body_the_schema_accepts():
    """Never the binding constraint: the longest valid input, fully escaped."""
    worst = json.dumps({"input": "é" * MAX_INPUT_CHARS}, ensure_ascii=True)
    assert len(worst.encode()) <= MAX_BODY_BYTES


def test_an_ordinary_run_is_accepted(client):
    r = client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
    assert r.status_code == 200


def test_a_declared_oversized_body_is_refused_with_413(client):
    body = b'{"input": "' + b"a" * (MAX_BODY_BYTES + 1) + b'"}'
    r = client.post("/runs", content=body, headers={"content-type": "application/json"})
    assert r.status_code == 413
    assert r.json()["error"] == "payload_too_large"


def test_an_oversized_chunked_body_is_refused_with_413(client):
    """No Content-Length to trust: the stream itself is counted."""

    def chunks():
        yield b'{"input": "'
        for _ in range(MAX_BODY_BYTES // 65_536 + 2):
            yield b"a" * 65_536
        yield b'"}'

    r = client.post("/runs", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413


def test_the_confirm_route_is_bounded_too(client):
    body = b'{"approved": true, "pad": "' + b"a" * (MAX_BODY_BYTES + 1) + b'"}'
    r = client.post(
        "/runs/req-000000000000/confirm",
        content=body,
        headers={"content-type": "application/json"},
    )
    assert r.status_code == 413


def test_a_non_integer_content_length_is_a_bad_request(client):
    r = client.post(
        "/runs",
        content=b'{"input": "hi"}',
        headers={"content-type": "application/json", "content-length": "lots"},
    )
    assert r.status_code == 400


class _FakeRequest:
    """Just enough of a Starlette request to drive ``_read_body`` directly."""

    def __init__(self, chunks, content_length=None):
        self._chunks = chunks
        self.headers = {} if content_length is None else {"content-length": content_length}
        self.consumed = 0

    async def stream(self):
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


@pytest.mark.anyio
async def test_reading_stops_at_the_first_chunk_over_the_limit():
    request = _FakeRequest([b"a" * 10] * 100)
    with pytest.raises(_BodyTooLarge):
        await _read_body(request, limit=25)
    assert request.consumed == 3, "the rest of the stream must not be pulled"


@pytest.mark.anyio
async def test_a_declared_length_over_the_limit_reads_nothing():
    request = _FakeRequest([b"a" * 10], content_length="1000")
    with pytest.raises(_BodyTooLarge):
        await _read_body(request, limit=25)
    assert request.consumed == 0


@pytest.fixture
def anyio_backend():
    return "asyncio"
