"""F-03: every credential this process holds is redacted, by shape and by value.

Two gaps were open. A password inside a connection string was redacted only by
accident -- when `user:pass@host.tld` happened to look like an email address --
so `redis://:pw@cache:6379/0` and `postgres://u:pw@10.0.0.5/db` reached traces
and logs intact. And the platform registered only the provider key as a known
value, so the grant secret, which has no recognisable shape at all, could only
ever be caught by luck.

Every value here is synthetic and built at run time; none is a real credential.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.security.sanitization import sanitize_text
from agent_platform.security.secrets import known_secret_values
from agent_platform.state import build_shared_state

PASSWORD = "Synth" + "-not-a-real-pw-" + "42"


def _offline_platform(configured):
    """A platform that holds a Redis URL without ever dialling it."""
    return AgentPlatform(
        configured,
        repository=InMemoryRepository(),
        shared_state=build_shared_state(None),
    )


@pytest.mark.parametrize(
    ("template", "kept"),
    [
        ("redis://:{pw}@cache:6379/0", "@cache:6379/0"),
        ("rediss://default:{pw}@cache.internal:6380", "@cache.internal:6380"),
        ("postgres://svc:{pw}@db.internal:5432/app", "@db.internal:5432/app"),
        ("postgresql://u:{pw}@10.0.0.5/db", "/db"),
        ("amqp://guest:{pw}@mq/vhost", "@mq/vhost"),
        ("error connecting to 'postgresql://app:{pw}@db/app': timeout", "timeout"),
    ],
)
def test_a_password_inside_a_connection_string_is_redacted(template, kept):
    text = template.format(pw=PASSWORD)
    redacted, report = sanitize_text(text)
    assert PASSWORD not in redacted
    assert "uri_credentials" in report.secret_kinds
    assert kept in redacted, "the host part is what a person debugging needs"


@pytest.mark.parametrize(
    "text",
    [
        "see https://example.com/docs/refund-policy",
        "the ratio is 3:2 at https://example.com",
        "ping me at user@example.com",
    ],
)
def test_text_without_userinfo_is_not_mistaken_for_a_credential(text):
    _redacted, report = sanitize_text(text)
    assert "uri_credentials" not in report.secret_kinds


@pytest.mark.parametrize(
    "text",
    [
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345",
        "authorization=Basic dXNlcjpzeW50aGV0aWMtcGFzc3dvcmQ=",
        "X-Api-Key: synthetic0123456789",
        "token=synthetic0123456789",
        "client_secret: 'synthetic-client-secret'",
    ],
)
def test_headers_and_assignments_are_redacted(text):
    redacted, report = sanitize_text(text)
    assert report.secret_kinds, f"nothing detected in {text!r}"
    assert "synthetic" not in redacted.lower() or "[REDACTED" in redacted


def test_known_values_include_the_password_inside_a_url():
    url = f"redis://:{PASSWORD}@cache:6379/0"
    values = known_secret_values(url, None, "")
    assert url in values
    assert PASSWORD in values


def test_known_values_decode_a_percent_encoded_password():
    url = "postgresql://u:" + "p%40ss-synthetic-123" + "@db/app"
    values = known_secret_values(url)
    assert "p@ss-synthetic-123" in values


def test_known_values_survive_a_malformed_url():
    assert known_secret_values("redis://[::1:bad") == ("redis://[::1:bad",)


def test_the_platform_registers_every_credential_it_holds(settings):
    grant = "synthetic-grant-" + "z" * 20
    configured = dataclasses.replace(
        settings,
        execution_grant_secret=grant,
        redis_url=f"redis://:{PASSWORD}@cache:6379/0",
    )
    platform = _offline_platform(configured)
    try:
        secrets = platform._known_secrets
    finally:
        platform.close()
    assert grant in secrets
    assert PASSWORD in secrets


def test_a_bare_password_is_redacted_by_identity(settings):
    """Out of its URL a password has no shape; only identity can catch it."""
    configured = dataclasses.replace(
        settings, redis_url=f"redis://:{PASSWORD}@cache:6379/0"
    )
    platform = _offline_platform(configured)
    try:
        redacted, _report = sanitize_text(
            f"driver said: auth failed for {PASSWORD}",
            known_secrets=platform._known_secrets,
        )
    finally:
        platform.close()
    assert PASSWORD not in redacted
