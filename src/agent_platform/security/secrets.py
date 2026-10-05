"""Detection and redaction of credential-shaped strings.

This module is intentionally conservative about what it considers a secret:
a false positive costs a redacted trace field, while a false negative writes a
credential to disk. When the two trade off, we redact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final
from urllib.parse import unquote, urlsplit

REDACTED: Final[str] = "[REDACTED:{kind}]"


@dataclass(frozen=True, slots=True)
class SecretPattern:
    kind: str
    pattern: re.Pattern[str]


#: Ordered most-specific-first so that a Google key is labelled as such rather
#: than being swallowed by the generic assignment rule.
SECRET_PATTERNS: Final[tuple[SecretPattern, ...]] = (
    # Two live formats, because Google is mid-migration.
    #
    # "AIza" is the legacy *standard* key. "AQ." is the *authorization*
    # key that every key created in AI Studio now defaults to, and which
    # the API will require outright by September 2026
    # (https://ai.google.dev/gemini-api/docs/api-key). Detecting only the
    # legacy shape meant the format all new keys use went unredacted.
    #
    # The length floor keeps "AQ." from matching ordinary prose or a short
    # identifier; real authorization keys are far longer.
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    SecretPattern("google_api_key", re.compile(r"\bAQ\.[0-9A-Za-z_\-]{30,}")),
    SecretPattern("anthropic_api_key", re.compile(r"\bsk-ant-[0-9A-Za-z_\-]{20,}")),
    SecretPattern("openai_api_key", re.compile(r"\bsk-(?!ant-)[0-9A-Za-z_\-]{20,}")),
    SecretPattern("aws_access_key_id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    SecretPattern("github_token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    SecretPattern("slack_token", re.compile(r"\bxox[abprs]-[0-9A-Za-z\-]{10,}\b")),
    # Credentials embedded in a connection string: the userinfo of
    # `scheme://user:password@host`, with the user part allowed to be empty as
    # in `redis://:password@cache:6379/0`. Only the userinfo is replaced, so the
    # scheme and host -- which is what a person debugging needs -- stay
    # readable. Before this, a DSN was redacted only by accident, when
    # `user:pass@host.tld` happened to look like an email address, and a
    # password in `redis://:pw@cache` or `postgres://u:pw@10.0.0.5` went
    # through untouched.
    SecretPattern(
        "uri_credentials",
        re.compile(r"(?i)(?<=://)[^\s:/@'\"<>]*:[^\s/@'\"<>]+(?=@)"),
    ),
    SecretPattern(
        "private_key",
        re.compile(r"-----BEGIN[ A-Z]*PRIVATE KEY-----.*?-----END[ A-Z]*PRIVATE KEY-----", re.S),
    ),
    SecretPattern(
        "jwt",
        re.compile(
            r"\beyJ[0-9A-Za-z_\-]{10,}\.eyJ[0-9A-Za-z_\-]{10,}\.[0-9A-Za-z_\-]+"
        ),
    ),
    SecretPattern(
        "authorization_header",
        re.compile(r"(?i)\bauthorization\s*[:=]\s*['\"]?(?:bearer|basic|token)\s+\S+"),
    ),
    SecretPattern("bearer_token", re.compile(r"(?i)\bbearer\s+[0-9A-Za-z._\-]{16,}")),
    #: Generic ``key = value`` shapes. Requires a quote or 8+ non-space chars so
    #: that prose such as "the password policy" is not flagged.
    SecretPattern(
        "credential_assignment",
        re.compile(
            r"(?i)\b(?:api[_\-]?key|apikey|secret|password|passwd|pwd|token|access[_\-]?key|"
            r"client[_\-]?secret|private[_\-]?key)\b\s*[:=]\s*(?:['\"][^'\"]{4,}['\"]|[^\s,;'\"]{8,})"
        ),
    ),
)


def find_secrets(text: str) -> list[str]:
    """Return the kinds of secret detected in *text*, without echoing values."""
    if not text:
        return []
    found: list[str] = []
    for sp in SECRET_PATTERNS:
        if sp.pattern.search(text):
            found.append(sp.kind)
    return found


def redact_secrets(text: str) -> tuple[str, list[str]]:
    """Replace credential-shaped substrings with typed placeholders.

    Returns the redacted text and the list of secret kinds that were removed.
    """
    if not text:
        return text, []
    kinds: list[str] = []
    result = text
    for sp in SECRET_PATTERNS:
        if not sp.pattern.search(result):
            continue
        kinds.append(sp.kind)
        result = sp.pattern.sub(REDACTED.format(kind=sp.kind), result)
    return result, kinds


def redact_known_values(text: str, values: object) -> str:
    """Redact literal occurrences of specific known-sensitive values.

    Pattern matching cannot recognise every credential format, so the platform
    additionally scrubs values it knows to be secret (for example the configured
    API key). Values shorter than 8 characters are ignored to avoid mangling
    unrelated text.
    """
    if not text:
        return text
    if isinstance(values, str):
        candidates: list[str] = [values]
    elif isinstance(values, (list, tuple, set, frozenset)):
        candidates = [v for v in values if isinstance(v, str)]
    else:
        return text
    result = text
    for value in candidates:
        if value and len(value) >= 8:
            result = result.replace(value, REDACTED.format(kind="known_secret"))
    return result


def known_secret_values(*values: str | None) -> tuple[str, ...]:
    """The literal values to redact by identity, from configured credentials.

    Each configured value is included as-is, and a connection string also
    contributes the password inside it -- the password is what leaks when an
    error message quotes a driver's parsed settings rather than the URL. Empty
    values are dropped; nothing here is ever logged or returned to a caller.
    """
    found: list[str] = []
    for value in values:
        if not value:
            continue
        found.append(value)
        if "://" in value:
            try:
                password = urlsplit(value).password
            except ValueError:
                password = None
            if password:
                found.append(unquote(password))
                found.append(password)
    return tuple(dict.fromkeys(found))


def contains_secret(text: str) -> bool:
    return bool(find_secrets(text))
