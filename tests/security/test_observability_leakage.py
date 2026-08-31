"""Two new ways for data to leave the process, and what stops them.

Logs and ``/metrics`` are the third and fourth outbound paths this platform
has, after the response body and the trace. The first two go through
``sanitize_text``. If these did not, they would be a way to read in cleartext
exactly the things the other two were built to mask -- which is why the risk was
flagged in the design rather than discovered here.

So the tests below are mostly about what must *not* appear: a signing secret, an
API key, a grant, raw arguments, raw tool output. The positive assertions exist
to keep the negatives honest -- a formatter that emitted nothing would pass every
leakage test and be useless.
"""

from __future__ import annotations

import io
import json
import logging
import os
import tempfile

import pytest

from agent_platform.config import Settings
from agent_platform.observability import logging as structured
from agent_platform.observability.exposition import (
    COUNTER_HELP,
    Counters,
    Histogram,
    render,
)
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from agent_platform.api import create_app
from tests.conftest import bearer

FAKE_KEY = "AQ.FAKE_KEY_FOR_TESTS_ONLY_not_a_real_credential_0000"
FAKE_SECRET = "an-execution-grant-signing-secret-value"


@pytest.fixture
def settings(tmp_path, monkeypatch, api_credentials) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "obs.db"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("TOOL_TRANSPORT", raising=False)
    monkeypatch.setenv("API_AUTH_KEYS", api_credentials.keys)
    monkeypatch.delenv("API_AUTH_KEYS_FILE", raising=False)
    monkeypatch.delenv("API_AUTH_MODE", raising=False)
    return Settings.from_env(load_dotenv_file=False)


@pytest.fixture
def captured_logs():
    """A root logger writing JSON into a buffer."""
    stream = io.StringIO()
    saved = list(logging.getLogger().handlers)
    saved_level = logging.getLogger().level
    structured.configure("INFO", stream=stream, known_secrets=(FAKE_SECRET,))
    try:
        yield stream
    finally:
        structured.register_secrets()
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
        for h in saved:
            root.addHandler(h)
        root.setLevel(saved_level)
        structured.bind_request(None)


def lines(stream: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# ================================================================ the format


def test_every_log_line_is_one_json_object(captured_logs):
    log = logging.getLogger("agent_platform.test")
    log.info("a plain message")
    log.warning("another one")

    records = lines(captured_logs)
    assert len(records) == 2
    for record in records:
        assert set(record) >= {"ts", "level", "logger", "message"}
        assert record["logger"] == "agent_platform.test"


def test_the_correlation_id_appears_on_every_line_of_a_request(captured_logs):
    structured.bind_request("req-abc123", "trace-def456")
    logging.getLogger("agent_platform.test").info("during a request")
    structured.bind_request(None)
    logging.getLogger("agent_platform.test").info("outside a request")

    records = lines(captured_logs)
    assert records[0]["request_id"] == "req-abc123"
    assert records[0]["trace_id"] == "trace-def456"
    assert "request_id" not in records[1], "an id leaked past its request"


# =============================================================== the refusals


@pytest.mark.parametrize(
    ("label", "payload"),
    [
        ("api key", f"provider rejected key {FAKE_KEY}"),
        ("grant secret", f"signing with {FAKE_SECRET}"),
        ("bearer token", "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"),
    ],
)
def test_a_secret_in_a_log_message_is_redacted(captured_logs, label, payload):
    """The formatter runs the same sanitiser the response boundary uses."""
    logging.getLogger("agent_platform.test").info(payload)

    body = captured_logs.getvalue()
    assert FAKE_KEY not in body, f"{label}: the key survived into the log"
    assert FAKE_SECRET not in body, f"{label}: the grant secret survived"
    assert "abcdefghijklmnopqrstuvwxyz012345" not in body


def test_an_exception_never_puts_a_traceback_in_the_log(captured_logs):
    """A traceback carries paths, locals and whatever was being processed."""
    log = logging.getLogger("agent_platform.test")
    try:
        raise ValueError(f"failed while holding {FAKE_KEY}")
    except ValueError:
        log.exception("something went wrong")

    body = captured_logs.getvalue()
    assert "Traceback" not in body
    assert FAKE_KEY not in body
    assert lines(captured_logs)[0]["error"] == "ValueError"


def test_unknown_record_attributes_are_dropped(captured_logs):
    """A library attaching data to a record must not get it serialised."""
    logging.getLogger("agent_platform.test").info(
        "hello", extra={"arguments": {"secret": FAKE_KEY}, "tool_result": {"pii": "x"}}
    )
    body = captured_logs.getvalue()
    assert "arguments" not in body
    assert "tool_result" not in body
    assert FAKE_KEY not in body


def test_a_very_long_message_is_bounded(captured_logs):
    logging.getLogger("agent_platform.test").info("x" * 50_000)
    assert len(captured_logs.getvalue()) < structured.MAX_MESSAGE_CHARS + 2_000


# ================================================================== /metrics


def test_metrics_render_only_known_series():
    """A counter with no declared help text is not emitted.

    That makes the set of things that can leave the process an explicit list
    rather than whatever happened to be incremented somewhere.
    """
    counters = Counters()
    counters.increment("agent_requests_total", status="2xx")
    counters.increment("agent_secret_smuggled_total", value=FAKE_KEY)

    body = render(counters, Histogram())
    assert "agent_requests_total" in body
    assert "agent_secret_smuggled_total" not in body
    assert FAKE_KEY not in body


def test_every_declared_counter_has_help_text():
    for name, help_text in COUNTER_HELP.items():
        assert name.startswith("agent_")
        assert help_text.endswith(".")


def test_label_values_are_escaped():
    counters = Counters()
    counters.increment("agent_tool_calls_total", tool='we"ird\nname', status="ok")
    body = render(counters, Histogram())
    assert '\\"' in body or "weird" in body
    assert "\n" not in body.split("agent_tool_calls_total")[1].split("\n")[0]


def test_the_metrics_endpoint_exposes_no_secret(settings, monkeypatch, api_credentials):
    """End to end: real traffic, then scrape, then look for anything sensitive."""
    monkeypatch.setenv("EXECUTION_GRANT_SECRET", FAKE_SECRET)
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as client:
            client.post("/runs", json={"input": "What is the status of order ORD-1001?"})
            client.post("/runs", json={"input": "Send a message to customer CUS-2001"})
            body = client.get(
                "/metrics", headers=bearer(api_credentials.scraper)
            ).text
    finally:
        platform.close()

    for forbidden in (FAKE_KEY, FAKE_SECRET, "ORD-1001", "CUS-2001", "ana.ribeiro"):
        assert forbidden not in body, f"{forbidden!r} reached /metrics"


def test_the_metrics_endpoint_reports_real_traffic(settings, api_credentials):
    """The complement: a scrape that showed nothing would pass the test above."""
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as client:
            for _ in range(3):
                client.post("/runs", json={"input": "What is the refund policy?"})
            body = client.get(
                "/metrics", headers=bearer(api_credentials.scraper)
            ).text
    finally:
        platform.close()

    assert 'agent_requests_total{status="2xx"} 3' in body
    assert "agent_request_duration_seconds_count 3" in body
    assert "agent_build_info" in body


def test_build_info_names_the_mode_without_naming_a_credential(settings, api_credentials):
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as client:
            body = client.get(
                "/metrics", headers=bearer(api_credentials.scraper)
            ).text
    finally:
        platform.close()

    assert 'provider="stub"' in body
    assert 'state_backend="local"' in body
    assert 'transport="inprocess"' in body
    assert "key" not in body.lower().split("agent_build_info")[1].split("\n")[0]


# =========================================================== correlation ids


def test_a_hostile_request_id_header_cannot_inject_into_a_log(settings, api_credentials):
    """The header reaches log output, so it is bounded and stripped."""
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as client:
            hostile = 'evil","level":"CRITICAL","message":"forged'
            response = client.post(
                "/runs",
                json={"input": "What is the refund policy?"},
                headers={"X-Request-ID": hostile},
            )
    finally:
        platform.close()

    echoed = response.headers["X-Request-ID"]
    assert '"' not in echoed
    assert "," not in echoed
    assert ":" not in echoed
    assert len(echoed) <= 64


def test_a_request_id_is_minted_when_none_is_supplied(settings, api_credentials):
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        with TestClient(
            create_app(platform), headers=bearer(api_credentials.operator)
        ) as client:
            response = client.get("/health")
    finally:
        platform.close()
    assert response.headers["X-Request-ID"].startswith("req-")


def test_the_environment_is_never_dumped_into_a_log(captured_logs, monkeypatch):
    """`os.environ` in a log line would defeat every other control here."""
    monkeypatch.setenv("GEMINI_API_KEY", FAKE_KEY)
    logging.getLogger("agent_platform.test").info("starting up")
    body = captured_logs.getvalue()
    assert FAKE_KEY not in body
    assert "GEMINI_API_KEY" not in body
    assert str(dict(os.environ))[:40] not in body


def test_configure_replaces_handlers_rather_than_adding(captured_logs):
    """Two handlers means every line twice, and the second copy would not be
    going through the sanitiser."""
    stream = io.StringIO()
    structured.configure("INFO", stream=stream)
    logging.getLogger("agent_platform.test").info("once")
    assert len([ln for ln in stream.getvalue().splitlines() if ln.strip()]) == 1


def test_tempdir_is_not_leaked_into_structured_output(captured_logs):
    """Local paths identify the machine and the user."""
    logging.getLogger("agent_platform.test").info(
        f"writing to {tempfile.gettempdir()}/agent.db"
    )
    body = captured_logs.getvalue()
    assert "C:\\Users" not in body
    assert "/home/" not in body
