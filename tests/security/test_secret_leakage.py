"""Security tests: credentials must not reach output, traces, state or storage."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from agent_platform.guardrails.output import secure_output
from agent_platform.guardrails.policy import PolicyContext
from agent_platform.models import AgentName, Decision
from agent_platform.observability.events import EventType
from agent_platform.observability.tracing import Tracer
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.sanitization import sanitize
from agent_platform.security.secrets import find_secrets, redact_secrets
from tests.conftest import action

FAKE_GOOGLE_KEY = "AIzaSyD1234567890123456789012345678901c"
FAKE_BEARER = "Bearer abcdefghijklmnop1234567890"
FAKE_ASSIGNMENT = "api_key=supersecretvalue12345"


# 10. Secrets in output are redacted or blocked.
@pytest.mark.parametrize(
    "text",
    [
        f"Your key is {FAKE_GOOGLE_KEY}",
        f"Authorization: {FAKE_BEARER}",
        f"Use {FAKE_ASSIGNMENT} to authenticate",
        "sk-ant-abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_secrets_are_removed_from_output(text):
    assessment = secure_output(text)
    assert assessment.redacted or assessment.blocked
    assert not find_secrets(assessment.text)


def test_configured_credential_blocks_the_whole_response():
    """A confirmed leak of our own key discards the response entirely."""
    assessment = secure_output(
        f"here it is: {FAKE_GOOGLE_KEY}", known_secrets=(FAKE_GOOGLE_KEY,)
    )
    assert assessment.blocked is True
    assert FAKE_GOOGLE_KEY not in assessment.text


# 11. Secrets never appear in traces.
def test_tracer_redacts_secrets_in_payloads(repository):
    tracer = Tracer(
        repository,
        request_id="req-test",
        trace_id="trace-test",
        known_secrets=(FAKE_GOOGLE_KEY,),
    )
    tracer.event(
        EventType.TOOL_CALL,
        payload={
            "api_key": FAKE_GOOGLE_KEY,
            "note": f"token {FAKE_GOOGLE_KEY}",
            "nested": {"authorization": FAKE_BEARER},
        },
        error=f"failed using {FAKE_GOOGLE_KEY}",
    )
    serialised = json.dumps([e.payload for e in repository.events]) + str(
        repository.events[0].error
    )
    assert FAKE_GOOGLE_KEY not in serialised
    assert "abcdefghijklmnop1234567890" not in serialised


def test_reasoning_keys_are_dropped_entirely(repository):
    """Chain-of-thought must never be persisted, even if a caller passes it."""
    tracer = Tracer(repository, request_id="req-test", trace_id="trace-test")
    tracer.event(
        EventType.LLM_CALL,
        payload={
            "reasoning": "first I will consider...",
            "chain_of_thought": "step 1, step 2",
            "scratchpad": "notes",
            "model": "deterministic-stub-v1",
        },
    )
    payload = repository.events[0].payload
    assert "reasoning" not in payload
    assert "chain_of_thought" not in payload
    assert "scratchpad" not in payload
    assert payload["model"] == "deterministic-stub-v1"


def test_repository_sanitises_even_on_a_direct_write(repository):
    """The persistence layer is a second, independent gate."""
    from agent_platform.persistence.repository import EventRecord

    repository.save_event(
        EventRecord(
            request_id="r",
            trace_id="t",
            sequence=1,
            event_type="tool_call",
            status="success",
            payload={"api_key": FAKE_GOOGLE_KEY},
        )
    )
    assert FAKE_GOOGLE_KEY not in json.dumps(repository.events[0].payload)


# 15. Platform state carries no credentials.
def test_settings_never_expose_the_key(settings):
    described = replace(settings, gemini_api_key=FAKE_GOOGLE_KEY).describe()
    assert FAKE_GOOGLE_KEY not in json.dumps(described)
    assert described["gemini_api_key"] == "configured"


def test_credentials_in_tool_arguments_are_denied(policy_engine):
    outcome = policy_engine.evaluate(
        PolicyContext(
            request_id="req-test",
            agent=AgentName.EXECUTOR,
            action=action(
                "update_record",
                record_id="ORD-1001",
                field="status",
                value=FAKE_ASSIGNMENT,
            ),
        )
    )
    assert outcome.decision.decision is Decision.DENY
    assert "PL006" in outcome.decision.rule_ids


def test_end_to_end_request_with_a_secret_leaks_nothing(settings):
    """A credential supplied by the user must not survive anywhere."""
    platform = AgentPlatform(
        replace(settings, gemini_api_key=None), repository=InMemoryRepository()
    )
    try:
        result = platform.run(f"Use {FAKE_ASSIGNMENT} to look up order 1001")
        assert "supersecretvalue12345" not in result.response

        stored = json.dumps(
            [e.payload for e in platform.repository.events]  # type: ignore[attr-defined]
            + [r.input_digest for r in platform.repository.requests.values()]  # type: ignore[attr-defined]
        )
        assert "supersecretvalue12345" not in stored
    finally:
        platform.close()


def test_sanitize_is_idempotent():
    payload = {"api_key": FAKE_GOOGLE_KEY, "text": f"key {FAKE_GOOGLE_KEY}"}
    once, _ = sanitize(payload)
    twice, _ = sanitize(once)
    assert once == twice


# ============================================ F6: credential in an object repr


def test_settings_repr_never_contains_the_key(settings):
    """F6 regression: a dataclass repr prints every field, including secrets.

    Found in practice, not in theory: pytest assertion introspection rendered a
    Settings fixture into test output and leaked a live API key in cleartext.
    Any traceback, log line or debugger frame would have done the same.
    """
    keyed = replace(settings, gemini_api_key=FAKE_GOOGLE_KEY)
    for rendering in (repr(keyed), str(keyed), f"{keyed}", f"{keyed}"):
        assert FAKE_GOOGLE_KEY not in rendering
    assert "<configured>" in repr(keyed)


def test_settings_repr_distinguishes_configured_from_absent(settings):
    assert "gemini_api_key=None" in repr(replace(settings, gemini_api_key=None))
    assert "<configured>" in repr(replace(settings, gemini_api_key=FAKE_GOOGLE_KEY))


def test_no_credential_bearing_object_leaks_through_repr(settings, monkeypatch, tmp_path):
    """Sweep the objects a traceback is most likely to render.

    The platform is built with a key so the *live* provider is the one under
    inspection -- a stub holds no credential and would make this vacuous. That
    now needs live authorisation, so the SDK client is replaced with an inert
    double first: the point is to construct the objects and read their reprs,
    and nothing here goes near a network.
    """
    from google import genai

    from agent_platform.llm.authorization import LIVE_AUTHORISED_VALUE, LIVE_ENV_VAR
    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    class InertClient:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(genai, "Client", InertClient)
    monkeypatch.setenv(LIVE_ENV_VAR, LIVE_AUTHORISED_VALUE)

    keyed = replace(
        settings, gemini_api_key=FAKE_GOOGLE_KEY, database_path=tmp_path / "leak.db"
    )
    platform = AgentPlatform(keyed, repository=InMemoryRepository())
    try:
        candidates = [
            platform.settings,
            platform.provider_info,
            platform.resources.limits,
            platform.circuit.snapshot(),
        ]
        for obj in candidates:
            assert FAKE_GOOGLE_KEY not in repr(obj), f"{type(obj).__name__} leaks the key"
    finally:
        platform.close()


def test_gemini_provider_does_not_repr_its_key():
    """The provider holds the key as an attribute; its repr must not show it."""
    from agent_platform.llm.gemini import GeminiProvider

    provider = GeminiProvider(
        FAKE_GOOGLE_KEY, "gemini-3.5-flash-lite", client=object()
    )
    assert FAKE_GOOGLE_KEY not in repr(provider)
    assert FAKE_GOOGLE_KEY not in str(provider)


# =============== F11: Gemini authorization keys ("AQ." prefix) are detected
#
# Google is retiring the standard "AIza" key format. Every key created in AI
# Studio now defaults to an *authorization* key with an "AQ." prefix, and the
# API will reject standard keys outright by September 2026
# (https://ai.google.dev/gemini-api/docs/api-key). The detector only knew the
# legacy shape, so the format that all new keys use went unredacted.
#
# These are synthetic values with the right shape. No real key appears here.

AUTH_KEY = "AQ." + "b7Kd2Xq9mZr4Tn8Lw5Yc1Vh6Jf3Pg0Ds" + "Ae9Bu2Cx7Rk4Nt1"
LEGACY_KEY = "AIzaSyD1234567890123456789012345678901c"


def test_authorization_key_is_detected():
    """F11: the current Gemini key format must be recognised as a secret."""
    kinds = list(find_secrets(f"my key is {AUTH_KEY} please use it"))
    assert "google_api_key" in kinds, (
        "the AQ. authorization key format is not detected; every key issued by "
        "AI Studio now uses it"
    )


def test_authorization_key_is_redacted():
    redacted, kinds = redact_secrets(f"key={AUTH_KEY} end")
    assert AUTH_KEY not in redacted
    assert "google_api_key" in kinds


def test_legacy_key_is_still_detected():
    """Extending the pattern must not drop the format still in circulation."""
    assert "google_api_key" in list(find_secrets(f"key {LEGACY_KEY}"))


def test_authorization_key_is_redacted_from_a_response():
    result = secure_output(f"here it is: {AUTH_KEY}")
    assert AUTH_KEY not in result.text


def test_authorization_key_is_redacted_from_a_trace_payload():
    payload, report = sanitize({"note": f"leaked {AUTH_KEY}"})
    assert AUTH_KEY not in str(payload)
    assert "google_api_key" in report.secret_kinds


def test_ordinary_text_beginning_with_aq_is_not_a_false_positive():
    """"AQ." must not swallow ordinary prose or short identifiers."""
    for benign in (
        "AQ. This sentence starts with an abbreviation.",
        "the ticket AQ.1234 was closed",
        "AQ.short",
    ):
        assert "google_api_key" not in list(find_secrets(benign)), benign
