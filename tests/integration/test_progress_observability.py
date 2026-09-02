"""The time nobody was measuring, and the progress nobody could see.

Two recorded live requests spent thirty-one and thirty-seven seconds between
`agent_started` and `llm_call`, with the model call itself taking under two
seconds. No event covered the difference, so a third of the slowest request was
invisible in a trace whose whole argument is that nothing is invisible.

There were two candidates, and they sit either side of a stopwatch:

* everything `AgentBase._generate` does before it reaches the provider --
  egress scrubbing, cost authorisation, the resource ceiling, the circuit
  breaker -- none of which was timed;
* the daily-budget ledger, charged inside `GeminiProvider.generate` *before*
  the stopwatch that produces `latency_ms`, in a `BEGIN IMMEDIATE` transaction
  that blocks while another process holds it.

Both are now measured and both are always reported, including as zero, because
"we measured it and it was nothing" is a different claim from "we did not
measure it".

The second half is the progress line. `platform.run()` is synchronous, so a
visitor waiting a minute on a retrying provider saw a spinner and nothing else.
The observer turns the events the request already emits into something a person
can read -- and, because it is presentation code reaching into a trace, it is
built so that it cannot possibly break one.

Nothing here calls a provider.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from agent_platform.config import Settings
from agent_platform.llm.provider import LLMResponse, Purpose
from agent_platform.observability.tracing import Tracer
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform


@pytest.fixture
def platform(settings: Settings, tmp_path):
    tuned = replace(
        settings,
        database_path=tmp_path / "progress.db",
        requests_per_minute=500,
        requests_per_hour=5000,
        global_requests_per_minute=5000,
        global_requests_per_hour=50000,
    )
    instance = AgentPlatform(tuned)
    try:
        yield instance
    finally:
        instance.close()


def _llm_calls(platform, request_id) -> list[dict]:
    import json

    out = []
    for event in platform.repository.events_for_request(request_id):
        if event.get("event_type") != "llm_call":
            continue
        payload = event.get("payload")
        out.append(json.loads(payload) if isinstance(payload, str) else payload)
    return out


# ============================================================== the two fields


@pytest.mark.slow
def test_every_model_call_reports_its_preflight(platform):
    """Present on every call, not only when it is large.

    A field that appears only when something went wrong cannot be used to show
    that nothing did.
    """
    result = platform.run("How many customers do we have?")
    calls = _llm_calls(platform, result.request_id)

    assert calls, "the request made no model call"
    for call in calls:
        assert "preflight_ms" in call, call
        assert isinstance(call["preflight_ms"], (int, float))
        assert call["preflight_ms"] >= 0.0


@pytest.mark.slow
def test_every_model_call_reports_its_budget_wait(platform):
    result = platform.run("How many customers do we have?")
    calls = _llm_calls(platform, result.request_id)

    for call in calls:
        assert "budget_wait_ms" in call, call
        assert call["budget_wait_ms"] >= 0.0


@pytest.mark.slow
def test_the_stub_reports_no_budget_wait(platform):
    """The stub is charged against no ledger, so its wait is honestly zero."""
    result = platform.run("How many customers do we have?")
    for call in _llm_calls(platform, result.request_id):
        assert call["budget_wait_ms"] == 0.0


def test_the_response_carries_the_field_with_a_safe_default():
    """Every existing construction of `LLMResponse` keeps working."""
    response = LLMResponse(text="{}", provider="x", model="m", purpose=Purpose.ROUTE)
    assert response.budget_wait_ms == 0.0


def test_a_blocking_ledger_is_attributed_to_the_ledger():
    """The measurement that would have settled the thirty-second gap.

    A ledger that takes measurable time to answer must show up in
    `budget_wait_ms` and *not* in `latency_ms`, which is the mistake the field
    exists to correct: the charge happens before the provider's own stopwatch
    starts.
    """
    import time

    from agent_platform.llm.gemini import GeminiProvider
    from agent_platform.llm.provider import RetryPolicy

    class _SlowLedger:
        def try_consume(self) -> bool:
            time.sleep(0.05)
            return True

        def used_today(self) -> int:  # pragma: no cover - not exercised
            return 0

    class _Models:
        def generate_content(self, **_kwargs):
            class _R:
                text = '{"route": "researcher"}'
                candidates: tuple = ()
                usage_metadata = None

            return _R()

    class _Client:
        def __init__(self) -> None:
            self.models = _Models()

    provider = GeminiProvider(
        api_key="not-a-real-key",
        model="fake-model",
        client=_Client(),
        budget=_SlowLedger(),
        retry_policy=RetryPolicy(max_attempts=1),
    )
    response = provider.generate("hello", purpose=Purpose.ROUTE)

    assert response.budget_wait_ms >= 40.0, response.budget_wait_ms
    assert response.latency_ms < response.budget_wait_ms, (
        "the ledger's wait leaked into the model's latency"
    )


def test_the_retry_payload_is_unchanged():
    """The one payload that must not grow a field.

    `llm_retry` has a pinned key set in the security suite, and these two
    fields belong to the logical call rather than to a lost attempt.
    """
    import ast
    from pathlib import Path

    source = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "agent_platform"
        / "agent"
        / "base.py"
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    retries = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_record_retries"
    )
    body = ast.dump(retries)
    assert "preflight_ms" not in body
    assert "budget_wait_ms" not in body


# ================================================================ the observer


@pytest.mark.slow
def test_the_observer_sees_each_agent_start(platform):
    seen: list[tuple[str, str | None]] = []
    platform.run(
        "How many customers do we have?",
        on_event=lambda kind, agent: seen.append((kind, agent)),
    )

    agents = [agent for kind, agent in seen if kind == "agent_started"]
    assert "router" in agents
    assert "researcher" in agents


@pytest.mark.slow
def test_the_observer_is_handed_identifiers_not_prose(platform):
    """A dashboard translates; the trace does not know one exists."""
    seen: list[tuple[str, str | None]] = []
    platform.run("How many customers do we have?", on_event=lambda k, a: seen.append((k, a)))

    for kind, agent in seen:
        assert kind == kind.lower() and kind.replace("_", "").isalnum(), kind
        if agent is not None:
            assert agent.isascii() and agent.islower(), agent


@pytest.mark.slow
def test_an_observer_that_raises_cannot_fail_the_request(platform):
    """The property that lets presentation code touch a trace at all."""

    def explode(_kind: str, _agent: str | None) -> None:
        raise RuntimeError("the UI fell over")

    result = platform.run("How many customers do we have?", on_event=explode)

    assert result.status == "success"
    assert result.response


@pytest.mark.slow
def test_an_observer_that_raises_still_leaves_a_complete_trace(platform):
    """The events are durable before the observer is ever called."""

    def explode(_kind: str, _agent: str | None) -> None:
        raise RuntimeError("the UI fell over")

    quiet = platform.run("How many customers do we have?")
    noisy = platform.run("How many customers do we have?", on_event=explode)

    def kinds(request_id: str) -> list:
        return [
            e.get("event_type")
            for e in platform.repository.events_for_request(request_id)
        ]

    assert kinds(noisy.request_id) == kinds(quiet.request_id)


@pytest.mark.slow
def test_no_observer_is_the_unchanged_path(platform):
    """Every existing caller passes nothing, and must be byte-for-byte as it was."""
    result = platform.run("How many customers do we have?")
    assert result.status == "success"


def test_the_observer_runs_after_the_event_is_stored():
    """Order matters: an observer must never see an event that was not written.

    Asserted against `Tracer` directly, because the ordering is its promise and
    a platform-level test could not tell the two orders apart.
    """
    repository = InMemoryRepository()
    seen_counts: list[int] = []

    def observe(_kind: str, _agent: str | None) -> None:
        seen_counts.append(len(repository.events_for_request("req-1")))

    from agent_platform.observability.events import EventStatus, EventType

    tracer = Tracer(
        repository, request_id="req-1", trace_id="trace-1", observer=observe
    )
    tracer.event(EventType.REQUEST_STARTED, status=EventStatus.INFO)

    assert seen_counts == [1], (
        "the observer ran before the event reached the repository"
    )


# ==================================================== the line a visitor reads


DASHBOARD = None


def _dashboard_app():
    import sys
    from pathlib import Path

    path = Path(__file__).resolve().parents[2] / "dashboard"
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    return path / "app.py"


@pytest.mark.slow
def test_a_question_renders_a_progress_container(tmp_path):
    """The spinner is gone; a status container took its place and the run works.

    Driven in Portuguese only. `AppTest`'s widget proxies resolve the
    navigation radio's `format_func` outside the script's session context,
    where the chosen locale is invisible, so any widget interaction after
    switching to English fails inside the harness rather than in the app --
    the English render is covered by `test_i18n.py`, which seeds state instead
    of clicking.
    """
    import os

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    app_file = _dashboard_app()
    saved = {n: os.environ.get(n) for n in ("GEMINI_API_KEY", "DATABASE_PATH")}
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(tmp_path / "progress_ui.db")
    st.cache_resource.clear()
    try:
        app = AppTest.from_file(str(app_file), default_timeout=240)
        app.session_state["nav"] = "orchestrator"
        app.run()
        app.text_input(key="question_box").set_value(
            "How many customers do we have?"
        ).run()
        app.button(key="run_question").click().run()

        assert not app.exception
        kinds = {type(element).__name__ for element in app.main}
        assert "Status" in kinds, f"no progress container rendered: {sorted(kinds)}"

        body = " ".join(str(getattr(e, "value", "")) for e in app.main)
        assert "12" in body, "the answer did not render"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        st.cache_resource.clear()


def test_every_progress_label_resolves_in_both_languages():
    _dashboard_app()
    import app as dashboard_app
    import i18n
    import streamlit as st

    keys = set(dashboard_app._PROGRESS_STEPS.values()) | {
        "progress.finishing",
        "progress.done",
    }
    for locale in i18n.LOCALES:
        st.session_state[i18n.LOCALE_KEY] = locale
        for key in sorted(keys):
            value = i18n.t(key)
            assert value and value != key, f"{key!r} unresolved in {locale!r}"
    st.session_state.pop(i18n.LOCALE_KEY, None)


def test_progress_is_keyed_on_agent_identifiers_not_labels():
    """The identifiers stay English and stable; only the value is translated.

    A map keyed on a translated label would silently stop matching the moment
    the reader switched language.
    """
    _dashboard_app()
    import app as dashboard_app

    from agent_platform.models import AgentName

    known = {agent.value for agent in AgentName}
    for identifier in dashboard_app._PROGRESS_STEPS:
        assert identifier in known, f"{identifier!r} is not an agent the platform has"
        assert identifier.isascii() and identifier.islower()


def test_the_progress_container_introduces_no_markup():
    """No HTML, no JavaScript, no DOM handling -- the rule this app runs on."""
    import ast

    source = _dashboard_app().read_text(encoding="utf-8")
    tree = ast.parse(source)
    fn = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_run_question"
    )
    for node in ast.walk(fn):
        if isinstance(node, ast.keyword) and node.arg == "unsafe_allow_html":
            raise AssertionError("the progress container renders raw HTML")
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "<script" not in node.value.lower()
            assert "<div" not in node.value.lower()


def test_the_spinner_it_replaced_is_gone():
    """Both would have shown at once, which is worse than either."""
    source = _dashboard_app().read_text(encoding="utf-8")
    assert "st.spinner" not in source
