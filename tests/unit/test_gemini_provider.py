"""Unit tests for GeminiProvider using a faked SDK client.

No network, no API key, fully deterministic. This is where the bulk of Gemini
coverage lives: the live tests can only smoke-test a happy path, whereas a fake
lets every failure branch be exercised exactly.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from google.genai import errors as genai_errors

from agent_platform.llm.errors import (
    LLMEmptyResponseError,
    LLMSafetyBlockedError,
    LLMTruncatedError,
)
from agent_platform.llm.gemini import GeminiProvider
from agent_platform.llm.provider import (
    LLMTimeoutError,
    LLMUnavailableError,
    Purpose,
    RetryPolicy,
)

FAKE_KEY = "AIzaSyD1234567890123456789012345678901c"


def _usage(prompt=10, candidates=20, thoughts=0):
    return SimpleNamespace(
        prompt_token_count=prompt,
        candidates_token_count=candidates,
        thoughts_token_count=thoughts,
    )


def _response(text="{}", finish="STOP", usage=None):
    return SimpleNamespace(
        text=text,
        candidates=[SimpleNamespace(finish_reason=finish)],
        usage_metadata=usage if usage is not None else _usage(),
    )


def _client_error(message: str, code: int = 400, status: str = "INVALID_ARGUMENT"):
    """Build a ClientError without depending on its constructor signature.

    ``code`` and ``status`` are set explicitly because the provider decides
    whether to run the thinking probe from the status, and a fake that leaves
    them unset makes every error look like a 400.
    """
    exc = genai_errors.ClientError.__new__(genai_errors.ClientError)
    Exception.__init__(exc, message)
    exc.code = code
    exc.status = status
    exc.message = message
    return exc


def _server_error(message: str = "backend unavailable"):
    exc = genai_errors.ServerError.__new__(genai_errors.ServerError)
    Exception.__init__(exc, message)
    return exc


class FakeModels:
    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        outcome = self._outcomes.pop(0) if self._outcomes else _response()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def build(outcomes, monkeypatch, **kwargs):
    """Construct a provider whose SDK client is a fake.

    The fake is injected at construction rather than monkeypatched afterwards,
    because the provider now refuses to build a *real* client without live
    authorisation. Passing the fake in says what these tests were always doing
    -- exercising the provider without an SDK -- and says it to the constructor
    rather than behind its back.
    """
    fake = FakeModels(outcomes)
    provider = GeminiProvider(
        FAKE_KEY,
        "gemini-3.5-flash-lite",
        client=SimpleNamespace(models=fake),
        **kwargs,
    )
    return provider, fake


def generate(provider, **kwargs):
    return provider.generate("hello", purpose=Purpose.ROUTE, **kwargs)


# --------------------------------------------------------------- construction


def test_empty_api_key_is_refused():
    with pytest.raises(LLMUnavailableError):
        GeminiProvider("", "gemini-3.5-flash-lite")


def test_provider_identifies_itself(monkeypatch):
    provider, _ = build([_response()], monkeypatch)
    assert provider.name == "gemini"
    assert provider.model == "gemini-3.5-flash-lite"


# ---------------------------------------------------------------- happy paths


def test_successful_call_reports_provider_usage(monkeypatch):
    provider, _ = build([_response(text='{"route":"researcher"}')], monkeypatch)
    result = generate(provider)
    assert result.text == '{"route":"researcher"}'
    assert result.provider == "gemini"
    assert result.input_tokens == 10
    assert result.output_tokens == 20
    assert result.tokens_estimated is False
    assert result.finish_reason == "STOP"


def test_thinking_tokens_are_billed_as_output(monkeypatch):
    """Thinking tokens are charged as output; omitting them understates cost."""
    provider, _ = build(
        [_response(usage=_usage(prompt=10, candidates=20, thoughts=35))], monkeypatch
    )
    result = generate(provider)
    assert result.output_tokens == 55


def test_missing_usage_falls_back_to_an_estimate(monkeypatch):
    provider, _ = build([_response(usage=SimpleNamespace(prompt_token_count=None))], monkeypatch)
    result = generate(provider)
    assert result.tokens_estimated is True
    assert result.input_tokens > 0


# ------------------------------------------------------------------- config


def test_thinking_config_is_sent_by_default(monkeypatch):
    provider, fake = build([_response()], monkeypatch, thinking_budget=0)
    generate(provider)
    config = fake.calls[0]["config"]
    assert config.thinking_config is not None
    assert config.thinking_config.thinking_budget == 0


def test_timeout_is_expressed_in_milliseconds(monkeypatch):
    """A silent 1000x error if seconds were assumed."""
    provider, fake = build([_response()], monkeypatch, timeout_seconds=30.0)
    generate(provider)
    assert fake.calls[0]["config"].http_options.timeout == 30_000


def test_response_schema_forces_json_mime_type(monkeypatch):
    provider, fake = build([_response()], monkeypatch)
    generate(provider, response_schema={"type": "OBJECT", "properties": {}})
    config = fake.calls[0]["config"]
    assert config.response_mime_type == "application/json"
    assert config.response_schema is not None


def test_unsupported_thinking_config_is_probed_once_then_dropped(monkeypatch):
    """A model that rejects thinking must degrade, not fail every call."""
    provider, fake = build(
        [_client_error("Invalid field thinking_config for this model"), _response()],
        monkeypatch,
    )
    result = generate(provider)
    assert result.text == "{}"
    assert len(fake.calls) == 2
    assert fake.calls[0]["config"].thinking_config is not None
    assert fake.calls[1]["config"].thinking_config is None

    # The capability is remembered, so later calls do not repeat the probe.
    fake._outcomes = [_response()]
    generate(provider)
    assert fake.calls[2]["config"].thinking_config is None


# ------------------------------------------------------------ retry behaviour


def test_client_error_is_not_retried_once_thinking_is_ruled_out(monkeypatch):
    """4xx is permanent; retrying burns quota for nothing.

    Updated for F14. This previously asserted that a *named* error still cost
    two calls, because the probe ran on any 4xx. It now costs one: an error
    that names its own cause was never ambiguous, so there is nothing to
    disambiguate. The property under test -- a 4xx is not retried -- is
    unchanged and now holds more tightly.

    The genuinely ambiguous case still spends its one probe call; that is
    test_bare_400_still_triggers_the_probe_exactly_once.
    """
    provider, fake = build(
        [_client_error("API key not valid", 400), _client_error("API key not valid", 400)],
        monkeypatch,
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 1
    assert provider._thinking_supported is True


def test_client_error_is_not_retried_when_thinking_is_already_off(monkeypatch):
    provider, fake = build([_client_error("API key not valid", 400)], monkeypatch)
    provider._thinking_supported = False
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 1


def test_bare_400_triggers_the_thinking_probe(monkeypatch):
    """Verified against the live API.

    gemini-3.5-flash-lite rejects thinking_budget=0 with a bare
    "400 INVALID_ARGUMENT. Request contains an invalid argument" -- the
    offending field is not named, so the probe cannot match on message text and
    must trigger on the presence of the thinking config itself.
    """
    provider, fake = build(
        [
            _client_error(
                "400 INVALID_ARGUMENT. {'error': {'code': 400, 'message': "
                "'Request contains an invalid argument.'}}"
            ),
            _response(text='{"route":"researcher"}'),
        ],
        monkeypatch,
    )
    assert generate(provider).text == '{"route":"researcher"}'
    assert len(fake.calls) == 2
    assert fake.calls[0]["config"].thinking_config is not None
    assert fake.calls[1]["config"].thinking_config is None


def test_server_error_is_retried_then_succeeds(monkeypatch):
    provider, fake = build([_server_error(), _response(text='{"ok":1}')], monkeypatch,
                           retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.0))
    assert generate(provider).text == '{"ok":1}'
    assert len(fake.calls) == 2


def test_retries_are_bounded(monkeypatch):
    provider, fake = build(
        [_server_error(), _server_error(), _server_error(), _server_error()],
        monkeypatch,
        retry_policy=RetryPolicy(max_attempts=3, initial_backoff_seconds=0.0),
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 3


def test_timeout_raises_a_timeout_error(monkeypatch):
    provider, _ = build(
        [TimeoutError("timed out"), TimeoutError("timed out")],
        monkeypatch,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.0),
    )
    with pytest.raises(LLMTimeoutError):
        generate(provider)


# ------------------------------------------------------- response integrity


def test_safety_block_is_classified(monkeypatch):
    provider, _ = build([_response(text="", finish="SAFETY")], monkeypatch)
    with pytest.raises(LLMSafetyBlockedError) as excinfo:
        generate(provider)
    assert excinfo.value.reason == "SAFETY"


def test_safety_block_is_detected_even_with_partial_text(monkeypatch):
    provider, _ = build([_response(text="partial", finish="PROHIBITED_CONTENT")], monkeypatch)
    with pytest.raises(LLMSafetyBlockedError):
        generate(provider)


def test_thinking_starvation_is_classified_as_truncation(monkeypatch):
    """The most likely live failure: thinking ate the whole output budget."""
    provider, _ = build(
        [_response(text="", finish="MAX_TOKENS", usage=_usage(10, 0, 2048))], monkeypatch
    )
    with pytest.raises(LLMTruncatedError) as excinfo:
        generate(provider)
    assert excinfo.value.thought_tokens == 2048
    assert "GEMINI_THINKING_BUDGET=0" in str(excinfo.value)


def test_empty_response_without_a_known_reason(monkeypatch):
    provider, _ = build([_response(text="", finish="OTHER")], monkeypatch)
    with pytest.raises(LLMEmptyResponseError):
        generate(provider)


def test_whitespace_only_response_is_treated_as_empty(monkeypatch):
    provider, _ = build([_response(text="   \n  ", finish="STOP")], monkeypatch)
    with pytest.raises(LLMEmptyResponseError):
        generate(provider)


def test_none_text_is_handled(monkeypatch):
    provider, _ = build([_response(text=None, finish="STOP")], monkeypatch)
    with pytest.raises(LLMEmptyResponseError):
        generate(provider)


def test_missing_candidates_does_not_crash(monkeypatch):
    provider, _ = build(
        [SimpleNamespace(text='{"ok":1}', candidates=[], usage_metadata=_usage())], monkeypatch
    )
    assert generate(provider).text == '{"ok":1}'


# -------------------------------------------------------- secret containment


def test_api_key_is_scrubbed_from_error_messages(monkeypatch):
    """A provider error must never become the thing that leaks the key."""
    provider, _ = build(
        [_client_error(f"bad request with key={FAKE_KEY}")] * 2, monkeypatch
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        generate(provider)
    assert FAKE_KEY not in str(excinfo.value)


def test_api_key_is_scrubbed_from_retry_exhaustion_messages(monkeypatch):
    provider, _ = build(
        [_server_error(f"upstream said {FAKE_KEY}")] * 2,
        monkeypatch,
        retry_policy=RetryPolicy(max_attempts=2, initial_backoff_seconds=0.0),
    )
    with pytest.raises(LLMUnavailableError) as excinfo:
        generate(provider)
    assert FAKE_KEY not in str(excinfo.value)


# ------------------------------------------------ proposed-argument parsing


@pytest.mark.parametrize(
    "payload,expected",
    [
        # Schema-constrained providers return a JSON string (the live shape).
        ({"tool": "get_order", "arguments_json": '{"order_id": "ORD-1001"}'},
         {"order_id": "ORD-1001"}),
        # The deterministic stub ignores schemas and returns a plain dict.
        ({"tool": "get_order", "arguments": {"order_id": "ORD-1001"}},
         {"order_id": "ORD-1001"}),
        # arguments_json wins when both are present.
        ({"arguments_json": '{"a": 1}', "arguments": {"b": 2}}, {"a": 1}),
        # Degenerate inputs yield {}, which the tool's real schema then rejects.
        ({}, {}),
        ({"arguments_json": ""}, {}),
        ({"arguments_json": "not json"}, {}),
        ({"arguments_json": "[1,2,3]"}, {}),
        ({"arguments_json": "null"}, {}),
        ({"arguments": None}, {}),
        ({"arguments": "a string"}, {}),
    ],
)
def test_parse_proposed_arguments_accepts_both_shapes(payload, expected):
    from agent_platform.agent.base import parse_proposed_arguments

    assert parse_proposed_arguments(payload) == expected


def test_action_schema_declares_arguments_as_a_string():
    """Regression: a bare OBJECT constrains the model to an empty object.

    Verified against the live API -- every proposal came back as
    ``"arguments": {}``, so the tool was chosen correctly and then refused for
    missing required fields.
    """
    from agent_platform.agent.researcher import ACTION_SCHEMA

    assert ACTION_SCHEMA["properties"]["arguments_json"]["type"] == "STRING"
    assert "arguments" not in ACTION_SCHEMA["properties"]


# ================== F14: the thinking probe must not fire on a quota error


def test_quota_error_does_not_trigger_the_thinking_probe(monkeypatch):
    """F14: a 429 says nothing about thinking, and retrying it costs quota.

    The probe exists because a bare 400 does not name the offending field. A
    429 names its problem precisely -- it is not ambiguous, and spending a
    second call on it burns the very quota that is exhausted.
    """
    provider, fake = build(
        [
            _client_error(
                "429 RESOURCE_EXHAUSTED. quota exceeded",
                code=429,
                status="RESOURCE_EXHAUSTED",
            ),
            _response(text='{"route":"researcher"}'),
        ],
        monkeypatch,
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 1, (
        f"a quota error cost {len(fake.calls)} HTTP calls; it must cost exactly one"
    )


def test_quota_error_does_not_disable_thinking_support(monkeypatch):
    """The lasting damage is worse than the extra call.

    Treating a 429 as a thinking rejection makes the provider believe, for the
    rest of its life, that the model has no thinking budget -- degrading every
    later request for a reason unrelated to thinking.
    """
    provider, _ = build(
        [
            _client_error(
                "429 RESOURCE_EXHAUSTED. quota exceeded",
                code=429,
                status="RESOURCE_EXHAUSTED",
            )
        ],
        monkeypatch,
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert provider._thinking_supported is True, (
        "a quota error silently disabled the thinking budget"
    )


def test_permission_error_does_not_trigger_the_thinking_probe(monkeypatch):
    """403 is equally unambiguous."""
    provider, fake = build(
        [
            _client_error(
                "403 PERMISSION_DENIED. caller lacks permission",
                code=403,
                status="PERMISSION_DENIED",
            )
        ],
        monkeypatch,
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 1


def test_invalid_key_does_not_trigger_the_thinking_probe(monkeypatch):
    """A rejected key is not a thinking-budget problem either."""
    provider, fake = build(
        [
            _client_error(
                "400 INVALID_ARGUMENT. API key not valid. Please pass a valid API key.",
                code=400,
                status="INVALID_ARGUMENT",
            )
        ],
        monkeypatch,
    )
    with pytest.raises(LLMUnavailableError):
        generate(provider)
    assert len(fake.calls) == 1, (
        "an explicitly-named key error was retried as if it were ambiguous"
    )


def test_bare_400_still_triggers_the_probe_exactly_once(monkeypatch):
    """The probe must survive the fix -- this is the case it exists for."""
    provider, fake = build(
        [
            _client_error(
                "400 INVALID_ARGUMENT. Request contains an invalid argument.",
                code=400,
                status="INVALID_ARGUMENT",
            ),
            _response(text='{"route":"researcher"}'),
        ],
        monkeypatch,
    )
    assert generate(provider).text == '{"route":"researcher"}'
    assert len(fake.calls) == 2
    assert provider._thinking_supported is False
