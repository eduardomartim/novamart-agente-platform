"""Time spent retrying must appear in the trace.

A live smoke test asked three questions. One took 39 seconds; the event stream
accounted for 2.7 of them. Another spent roughly six physical calls against the
provider — the ledger charged every one — and recorded `llm_calls: 0`.

Both had the same cause, in two halves:

* `GeminiProvider._generate` restarts its stopwatch on every attempt, so
  `latency_ms` measures the try that worked and says nothing about the ones
  before it or the backoff between them;
* when every attempt failed, `AgentBase` recorded the circuit failure and
  re-raised without emitting anything, so the accounting and the trace
  disagreed about the same event.

Nothing here calls a provider. A fake raises the same exception types the SDK
raises, which is enough to drive the retry loop and is the only way to test
this without spending quota on deliberate failures.
"""

from __future__ import annotations

import time

import pytest
from google.genai import errors as genai_errors

from agent_platform.llm.provider import LLMResponse, Purpose, RetryPolicy


class _FakeResponse:
    """The shape `GeminiProvider` reads out of a successful SDK response."""

    def __init__(self, text: str = '{"route": "researcher"}') -> None:
        self.text = text
        self.candidates = []
        self.usage_metadata = None


class _FlakyModels:
    """Fails `failures` times with a server error, then answers."""

    def __init__(self, failures: int) -> None:
        self.remaining = failures
        self.calls = 0

    def generate_content(self, **_kwargs):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise genai_errors.ServerError(
                503, {"error": {"code": 503, "message": "high demand"}}
            )
        return _FakeResponse()


class _FakeClient:
    def __init__(self, failures: int) -> None:
        self.models = _FlakyModels(failures)


@pytest.fixture
def provider_factory():
    """A GeminiProvider over a fake client. No network, no key, no gate to lift.

    Uses the constructor's own `client=` seam, which exists for exactly this.
    A *real* `genai.Client` handed in that way still needs authorisation --
    the seam is a way to test without a provider, not a way around the gate --
    and a fake is not one, so nothing here weakens the live barrier.
    """

    def build(failures: int, *, attempts: int = 3, backoff: float = 0.01):
        from agent_platform.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key="not-a-real-key",
            model="fake-model",
            client=_FakeClient(failures),
            retry_policy=RetryPolicy(
                max_attempts=attempts, initial_backoff_seconds=backoff
            ),
        )

    return build


# ==================================================== what the provider reports


def test_a_call_that_succeeds_first_time_reports_one_attempt(provider_factory):
    """The common case stays silent, so `retried` means something."""
    provider = provider_factory(failures=0)
    response = provider.generate("hello", purpose=Purpose.ROUTE)

    assert response.attempts == 1
    assert response.retry_reasons == ()
    assert response.retried is False


def test_the_total_includes_every_attempt_and_the_backoff(provider_factory):
    """The headline: the number the caller waited, not the try that worked."""
    backoff = 0.05
    provider = provider_factory(failures=2, backoff=backoff)

    started = time.perf_counter()
    response = provider.generate("hello", purpose=Purpose.ROUTE)
    wall_clock_ms = (time.perf_counter() - started) * 1000.0

    assert response.attempts == 3
    assert response.total_elapsed_ms >= response.latency_ms, (
        "the total is smaller than one of its parts"
    )
    # Two backoffs happened between three attempts. The total has to have seen
    # them; `latency_ms` by construction cannot.
    assert response.total_elapsed_ms >= backoff * 2 * 1000.0
    assert response.total_elapsed_ms <= wall_clock_ms + 5.0


def test_every_lost_attempt_reports_why(provider_factory):
    provider = provider_factory(failures=2)
    response = provider.generate("hello", purpose=Purpose.ROUTE)

    assert len(response.retry_reasons) == 2
    assert all("503" in reason or "high demand" in reason for reason in response.retry_reasons)


def test_the_reasons_carry_no_credential(provider_factory):
    """`_scrub` redacts the configured key by value before anything is stored."""
    provider = provider_factory(failures=2)
    response = provider.generate("hello", purpose=Purpose.ROUTE)

    blob = " ".join(response.retry_reasons)
    assert "not-a-real-key" not in blob


# ======================================================= what the agent records


class _RetryingProvider:
    """A provider that reports retries without any SDK underneath it."""

    name = "fake"
    model = "fake-model"

    def __init__(self, attempts: int = 3) -> None:
        self.attempts = attempts

    def generate(self, _prompt, *, purpose, **_kwargs):
        return LLMResponse(
            text='{"route": "researcher"}',
            provider=self.name,
            model=self.model,
            purpose=purpose,
            latency_ms=12.0,
            attempts=self.attempts,
            total_elapsed_ms=31_000.0,
            retry_reasons=tuple(
                f"503 UNAVAILABLE attempt {i}" for i in range(1, self.attempts)
            ),
        )

    def embed(self, *_args, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError


class _FailingProvider:
    """Every attempt failed; the provider gave up."""

    name = "fake"
    model = "fake-model"

    def generate(self, _prompt, *, purpose, **_kwargs):
        from agent_platform.llm.provider import LLMUnavailableError

        raise LLMUnavailableError("Gemini call failed after 3 attempts: 503 UNAVAILABLE")

    def embed(self, *_args, **_kwargs):  # pragma: no cover - not exercised
        raise NotImplementedError


def _events_of(platform, request_id):
    return platform.repository.events_for_request(request_id)


@pytest.mark.slow
def test_retries_reach_the_event_stream(settings, tmp_path):
    """One event per lost attempt, on a request that still succeeded."""
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    tuned = replace(settings, database_path=tmp_path / "retry.db")
    platform = AgentPlatform(tuned)
    platform.provider = _RetryingProvider(attempts=3)
    try:
        result = platform.run("What is the status of order ORD-1001?")
        events = _events_of(platform, result.request_id)
    finally:
        platform.close()

    retries = [e for e in events if e.get("event_type") == "llm_retry"]
    assert retries, "a call that retried twice left no retry in the trace"

    # Two lost attempts per model call, and the request makes more than one.
    assert len(retries) % 2 == 0
    first = retries[0]
    assert first.get("status") == "failure"
    assert "503" in str(first.get("error"))


@pytest.mark.slow
def test_the_initial_attempt_is_distinguishable_from_a_retry(settings, tmp_path):
    """`attempt` and `of` say which try this was, without counting events."""
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    tuned = replace(settings, database_path=tmp_path / "attempts.db")
    platform = AgentPlatform(tuned)
    platform.provider = _RetryingProvider(attempts=3)
    try:
        result = platform.run("What is the status of order ORD-1001?")
        events = _events_of(platform, result.request_id)
    finally:
        platform.close()

    import json

    payloads = [
        json.loads(e["payload"]) if isinstance(e.get("payload"), str) else e.get("payload")
        for e in events
        if e.get("event_type") == "llm_retry"
    ]
    assert payloads
    assert {p["attempt"] for p in payloads} == {1, 2}
    assert {p["of"] for p in payloads} == {3}

    calls = [
        json.loads(e["payload"]) if isinstance(e.get("payload"), str) else e.get("payload")
        for e in events
        if e.get("event_type") == "llm_call"
    ]
    assert calls, "the successful call disappeared"
    assert calls[0]["attempts"] == 3
    assert calls[0]["total_elapsed_ms"] >= 31_000.0, (
        "the successful call still reports only the attempt that worked"
    )


@pytest.mark.slow
def test_a_call_that_never_succeeds_still_produces_an_event(settings, tmp_path):
    """The trace used to be empty for the case that cost the most quota."""
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    tuned = replace(settings, database_path=tmp_path / "failed.db")
    platform = AgentPlatform(tuned)
    platform.provider = _FailingProvider()
    try:
        result = platform.run("What is the status of order ORD-1001?")
        events = _events_of(platform, result.request_id)
    finally:
        platform.close()

    failed = [e for e in events if e.get("event_type") == "llm_failed"]
    assert failed, "every attempt failed and the trace recorded nothing"
    assert failed[0].get("status") == "failure"
    assert "503" in str(failed[0].get("error"))
    assert result.status == "failed"


@pytest.mark.slow
def test_a_provider_that_never_retries_adds_no_events(settings, tmp_path):
    """Additive means additive: the stub's traces are unchanged."""
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    tuned = replace(settings, database_path=tmp_path / "clean.db")
    platform = AgentPlatform(tuned)
    try:
        result = platform.run("What is the status of order ORD-1001?")
        events = _events_of(platform, result.request_id)
    finally:
        platform.close()

    assert not [e for e in events if e.get("event_type") in ("llm_retry", "llm_failed")]
    assert [e for e in events if e.get("event_type") == "llm_call"], "no call recorded"


def test_no_retry_payload_can_carry_a_secret(settings, tmp_path):
    """Whatever the provider reports, the payload keys are a fixed set."""
    from dataclasses import replace

    from agent_platform.platform import AgentPlatform

    tuned = replace(settings, database_path=tmp_path / "secret.db")
    platform = AgentPlatform(tuned)
    platform.provider = _RetryingProvider(attempts=2)
    try:
        result = platform.run("What is the status of order ORD-1001?")
        events = _events_of(platform, result.request_id)
    finally:
        platform.close()

    import json

    for event in events:
        if event.get("event_type") != "llm_retry":
            continue
        payload = event.get("payload")
        payload = json.loads(payload) if isinstance(payload, str) else payload
        assert set(payload) == {"purpose", "attempt", "of", "provider", "model"}, (
            "the retry payload grew a field nobody reviewed"
        )
