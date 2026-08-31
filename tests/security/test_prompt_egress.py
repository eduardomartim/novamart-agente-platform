"""Security tests: what may leave the process inside a prompt.

Sending a prompt to a hosted model hands text to a third party. These tests
pin the rule: credentials never leave, PII leaves only where a tool genuinely
needs it, and the trace records categories rather than values.
"""

from __future__ import annotations

import json

import pytest

from agent_platform.guardrails.egress import (
    MASKED_PII_KINDS,
    PRESERVED_PII_KINDS,
    prepare_for_egress,
)
from agent_platform.security.secrets import find_secrets

GOOGLE_KEY = "AIzaSyD1234567890123456789012345678901c"


# ------------------------------------------------------------- credentials


@pytest.mark.parametrize(
    "text",
    [
        f"use {GOOGLE_KEY} please",
        "api_key=supersecretvalue12345",
        "password = 'hunter2hunter2'",
        "Authorization: Bearer abcdef1234567890abcdef",
        "sk-ant-abcdefghijklmnopqrstuvwxyz012345",
    ],
)
def test_credentials_never_leave_the_process(text):
    result = prepare_for_egress(text)
    assert find_secrets(result.text) == []
    assert result.secret_kinds


def test_configured_key_is_stripped_even_without_a_matching_pattern():
    odd = "zzzz-unrecognised-secret-format-9999"
    result = prepare_for_egress(f"token {odd}", known_secrets=(odd,))
    assert odd not in result.text


# --------------------------------------------------------------------- PII


def test_email_is_preserved_because_a_tool_consumes_it():
    """send_email needs a real recipient; masking it would break the workflow."""
    text = "Send an email to ana.ribeiro@example.com about her order"
    result = prepare_for_egress(text)
    assert "ana.ribeiro@example.com" in result.text
    assert "email" in PRESERVED_PII_KINDS


@pytest.mark.parametrize(
    "text,kind",
    [
        ("my CPF is 123.456.789-09", "cpf"),
        ("card 4111 1111 1111 1111", "card"),
        ("call me on (11) 91234-5678", "phone"),
        ("host 192.168.1.44", "ipv4"),
    ],
)
def test_pii_no_tool_consumes_is_masked(text, kind):
    result = prepare_for_egress(text)
    assert kind in result.pii_kinds
    assert kind in MASKED_PII_KINDS


def test_masked_and_preserved_categories_do_not_overlap():
    assert not (MASKED_PII_KINDS & PRESERVED_PII_KINDS)


# ---------------------------------------------------------------- metadata


def test_metadata_carries_categories_not_values():
    result = prepare_for_egress(
        f"key {GOOGLE_KEY} and CPF 123.456.789-09", known_secrets=(GOOGLE_KEY,)
    )
    blob = json.dumps(result.metadata)
    assert GOOGLE_KEY not in blob
    assert "123.456.789-09" not in blob
    assert "cpf" in blob


def test_clean_text_is_unchanged_and_not_flagged():
    text = "What is the status of order 1001?"
    result = prepare_for_egress(text)
    assert result.text == text
    assert result.redacted is False


def test_empty_input_is_handled():
    assert prepare_for_egress("").text == ""


# ------------------------------------------------------------- integration


def test_secret_in_a_request_never_reaches_the_provider(platform, monkeypatch):
    """End to end: a credential in user input must not reach provider.generate."""
    seen: list[str] = []
    original = platform.provider.generate

    def capture(prompt, **kwargs):
        seen.append(prompt)
        return original(prompt, **kwargs)

    monkeypatch.setattr(platform.provider, "generate", capture)
    platform.run("Use api_key=supersecretvalue12345 to look up order 1001")

    assert seen, "the provider was never called"
    for prompt in seen:
        assert "supersecretvalue12345" not in prompt
        assert find_secrets(prompt) == []


def test_redaction_is_recorded_as_a_trace_event(platform):
    platform.run("Use api_key=supersecretvalue12345 to look up order 1001")
    events = [e for e in platform.repository.events if e.event_type == "prompt_redacted"]
    assert events, "no prompt_redacted event was recorded"
    blob = json.dumps([e.payload for e in events])
    assert "supersecretvalue12345" not in blob


def test_egress_control_applies_to_the_stub_too(platform, monkeypatch):
    """A control that only engages for one provider gets found broken in the other."""
    seen: list[str] = []
    original = platform.provider.generate
    monkeypatch.setattr(
        platform.provider,
        "generate",
        lambda prompt, **kw: (seen.append(prompt), original(prompt, **kw))[1],
    )
    platform.run("My CPF is 123.456.789-09, where is order 1001?")
    assert seen
    assert all("123.456.789-09" not in prompt for prompt in seen)
