"""Unit tests for redaction, PII handling, sanitisation and rate limiting."""

from __future__ import annotations

import pytest

from agent_platform.security.pii import _luhn_valid, detect_pii, redact_pii
from agent_platform.security.rate_limit import RateLimiter
from agent_platform.security.sanitization import (
    REASONING_KEYS,
    SENSITIVE_KEYS,
    content_digest,
    sanitize,
    sanitize_text,
)
from agent_platform.security.secrets import (
    contains_secret,
    find_secrets,
    redact_known_values,
    redact_secrets,
)

GOOGLE_KEY = "AIzaSyD1234567890123456789012345678901c"


@pytest.mark.parametrize(
    "text,kind",
    [
        (GOOGLE_KEY, "google_api_key"),
        ("sk-ant-abcdefghijklmnopqrstuvwxyz01", "anthropic_api_key"),
        ("AKIAIOSFODNN7EXAMPLE", "aws_access_key_id"),
        ("ghp_abcdefghijklmnopqrstuvwxyz0123456789", "github_token"),
        ("password = 'hunter2hunter2'", "credential_assignment"),
        ("api_key=abcdefgh12345678", "credential_assignment"),
        ("Authorization: Bearer abcdef1234567890", "authorization_header"),
    ],
)
def test_secret_shapes_are_detected(text, kind):
    assert kind in find_secrets(text)
    redacted, kinds = redact_secrets(text)
    assert kind in kinds
    assert not contains_secret(redacted)


@pytest.mark.parametrize(
    "text",
    [
        "the password policy requires rotation",
        "please reset your password",
        "our token economy article",
        "",
    ],
)
def test_ordinary_prose_is_not_flagged(text):
    assert find_secrets(text) == []


def test_known_values_are_redacted_even_without_a_pattern():
    text = "the value is zzzz-not-a-known-shape-9999"
    assert "zzzz-not-a-known-shape-9999" not in redact_known_values(
        text, ["zzzz-not-a-known-shape-9999"]
    )


def test_short_known_values_are_ignored():
    """Redacting a 3-character value would mangle unrelated text."""
    assert redact_known_values("the cat sat on the mat", ["cat"]) == "the cat sat on the mat"


# ------------------------------------------------------------------------- PII


def test_luhn_rejects_non_card_numbers():
    assert _luhn_valid("4111111111111111") is True
    assert _luhn_valid("1234567890123456") is False


def test_long_order_numbers_are_not_reported_as_cards():
    assert detect_pii("order 1234567890123456") == []


def test_card_numbers_are_masked_keeping_the_last_four():
    masked, kinds = redact_pii("card 4111 1111 1111 1111 here")
    assert "card" in kinds
    assert "1111 1111" not in masked
    assert "1111]" in masked


def test_email_masking_keeps_the_domain():
    masked, kinds = redact_pii("write to ana.ribeiro@example.com")
    assert "email" in kinds
    assert "a***@example.com" in masked
    assert "ana.ribeiro" not in masked


@pytest.mark.parametrize(
    "text,kind",
    [
        ("CPF 123.456.789-09", "cpf"),
        ("call (11) 91234-5678", "phone"),
        ("host 192.168.1.44", "ipv4"),
    ],
)
def test_pii_categories(text, kind):
    assert kind in [m.kind for m in detect_pii(text)]
    _, kinds = redact_pii(text)
    assert kind in kinds


# ---------------------------------------------------------------- sanitisation


def test_sensitive_keys_are_dropped_by_name():
    for key in list(SENSITIVE_KEYS)[:6]:
        result, report = sanitize({key: "whatever-the-value-is"})
        assert result[key] == "[REDACTED:sensitive_key]"
        assert key in report.dropped_keys


def test_reasoning_keys_are_removed_not_masked():
    for key in REASONING_KEYS:
        result, report = sanitize({key: "internal deliberation", "keep": "yes"})
        assert key not in result
        assert result["keep"] == "yes"
        assert key in report.dropped_keys


def test_sanitisation_recurses_into_nested_structures():
    payload = {"a": [{"b": {"api_key": GOOGLE_KEY}}]}
    result, _ = sanitize(payload)
    assert GOOGLE_KEY not in str(result)


def test_deeply_nested_payloads_terminate():
    payload: dict = {}
    node = payload
    for _ in range(50):
        node["next"] = {}
        node = node["next"]
    result, _ = sanitize(payload)
    assert "max depth" in str(result)


def test_long_text_is_truncated():
    _, report = sanitize_text("x" * 2000, max_chars=100)
    assert report.truncated is True


def test_large_collections_are_capped():
    result, report = sanitize(list(range(500)))
    assert len(result) <= 51
    assert report.truncated is True


def test_unknown_objects_are_stringified_and_scanned():
    class Custom:
        def __repr__(self) -> str:
            return f"Custom(key={GOOGLE_KEY})"

    result, _ = sanitize(Custom())
    assert GOOGLE_KEY not in str(result)


def test_content_digest_is_stable_and_short():
    assert content_digest("hello") == content_digest("hello")
    assert content_digest("hello") != content_digest("world")
    assert len(content_digest("hello")) == 16


# ------------------------------------------------------------------ rate limit


def test_minute_limit_is_enforced():
    limiter = RateLimiter(3, 100)
    assert [limiter.acquire().allowed for _ in range(4)] == [True, True, True, False]


def test_hour_limit_is_enforced():
    limiter = RateLimiter(100, 2)
    results = [limiter.acquire().allowed for _ in range(3)]
    assert results == [True, True, False]
    assert "per-hour" in limiter.acquire().reason


def test_window_slides():
    now = [1000.0]
    limiter = RateLimiter(2, 100, clock=lambda: now[0])
    assert limiter.acquire().allowed and limiter.acquire().allowed
    assert limiter.acquire().allowed is False
    now[0] += 61.0
    assert limiter.acquire().allowed is True


def test_check_does_not_consume_quota():
    limiter = RateLimiter(1, 10)
    assert limiter.check().allowed is True
    assert limiter.check().allowed is True
    assert limiter.acquire().allowed is True
    assert limiter.check().allowed is False


def test_keys_are_independent():
    limiter = RateLimiter(1, 10)
    assert limiter.acquire("a").allowed is True
    assert limiter.acquire("b").allowed is True
    assert limiter.acquire("a").allowed is False


def test_reset_clears_quota():
    limiter = RateLimiter(1, 10)
    limiter.acquire()
    limiter.reset()
    assert limiter.acquire().allowed is True


def test_invalid_limits_are_rejected():
    with pytest.raises(ValueError):
        RateLimiter(0, 10)
