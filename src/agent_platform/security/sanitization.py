"""The single sanitisation choke point for everything the platform persists.

Blueprint rule: no event reaches storage without passing through this module.
Traces, metrics, evaluation records and cost rows all call :func:`sanitize`
before they are written, so redaction cannot be forgotten at an individual call
site.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Final

from .pii import redact_pii
from .secrets import redact_known_values, redact_secrets

#: User-home path prefixes, Windows and POSIX. Only the *prefix* is replaced:
#: the tail is genuinely useful when reading a trace and carries no personal
#: information, whereas the prefix carries a username and the machine's layout.
#: System paths (/usr, /opt, C:\Windows) are deliberately not matched -- they
#: disclose nothing and redacting them would only make errors harder to read.
_HOME_PATH = re.compile(
    r"(?i)"
    r"(?:"
    r"[A-Z]:[\\/]Users[\\/][^\\/:*?<>|\s\"]+"
    r"|/(?:home|Users)/[^/\s:]+"
    r")"
)

DEFAULT_MAX_CHARS: Final[int] = 500
MAX_DEPTH: Final[int] = 6
MAX_ITEMS: Final[int] = 50

#: Dictionary keys whose values are dropped wholesale, regardless of the value's
#: shape. Cheaper and far more reliable than pattern-matching the value.
SENSITIVE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "api_key",
        "apikey",
        "gemini_api_key",
        "authorization",
        "auth",
        "password",
        "passwd",
        "pwd",
        "secret",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "private_key",
        "credentials",
        "cookie",
        "session_id",
        "set-cookie",
    }
)

#: Keys that must never be persisted because they would capture model
#: reasoning. Enforces the "no chain-of-thought in traces" rule structurally.
REASONING_KEYS: Final[frozenset[str]] = frozenset(
    {"reasoning", "thought", "thoughts", "chain_of_thought", "scratchpad", "deliberation"}
)


@dataclass(slots=True)
class SanitizationReport:
    """What the sanitiser removed, for surfacing in the security dashboard."""

    secret_kinds: list[str] = field(default_factory=list)
    pii_kinds: list[str] = field(default_factory=list)
    dropped_keys: list[str] = field(default_factory=list)
    truncated: bool = False

    @property
    def clean(self) -> bool:
        return not (self.secret_kinds or self.pii_kinds or self.dropped_keys)

    def merge(self, other: SanitizationReport) -> None:
        for kind in other.secret_kinds:
            if kind not in self.secret_kinds:
                self.secret_kinds.append(kind)
        for kind in other.pii_kinds:
            if kind not in self.pii_kinds:
                self.pii_kinds.append(kind)
        for key in other.dropped_keys:
            if key not in self.dropped_keys:
                self.dropped_keys.append(key)
        self.truncated = self.truncated or other.truncated


def redact_home_paths(text: str) -> str:
    """Replace user-home path prefixes with ``<HOME>``.

    A local path in an error message discloses the operating user's name and
    the machine's directory layout. That is information disclosure in any
    user-facing output, and it is gratuitous in a trace.
    """
    if not text:
        return text
    return _HOME_PATH.sub("<HOME>", text)


def content_digest(value: str) -> str:
    """Stable short digest, so content can be correlated without being stored."""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def sanitize_text(
    text: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    known_secrets: object = None,
) -> tuple[str, SanitizationReport]:
    """Redact secrets and PII from *text*, then truncate it."""
    report = SanitizationReport()
    if not text:
        return text, report

    result = redact_known_values(text, known_secrets) if known_secrets else text
    result, secret_kinds = redact_secrets(result)
    result, pii_kinds = redact_pii(result)
    result = redact_home_paths(result)
    report.secret_kinds.extend(secret_kinds)
    report.pii_kinds.extend(pii_kinds)

    if max_chars >= 0 and len(result) > max_chars:
        result = result[:max_chars] + f"...[truncated {len(result) - max_chars} chars]"
        report.truncated = True
    return result, report


def sanitize(
    value: Any,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    known_secrets: object = None,
    _depth: int = 0,
) -> tuple[Any, SanitizationReport]:
    """Recursively sanitise an arbitrary payload before persistence.

    Handles the container types that actually appear in traces. Anything else is
    rendered via ``repr`` and then treated as text, so an unexpected object can
    never smuggle raw content past the redactor.
    """
    report = SanitizationReport()

    if _depth > MAX_DEPTH:
        return "[truncated: max depth]", report

    if value is None or isinstance(value, (bool, int, float)):
        return value, report

    if isinstance(value, str):
        return sanitize_text(value, max_chars=max_chars, known_secrets=known_secrets)

    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:MAX_ITEMS]:
            key = str(raw_key)
            lowered = key.lower()
            if lowered in SENSITIVE_KEYS:
                out[key] = "[REDACTED:sensitive_key]"
                report.dropped_keys.append(key)
                continue
            if lowered in REASONING_KEYS:
                report.dropped_keys.append(key)
                continue
            child, child_report = sanitize(
                raw_value, max_chars=max_chars, known_secrets=known_secrets, _depth=_depth + 1
            )
            report.merge(child_report)
            out[key] = child
        if len(value) > MAX_ITEMS:
            out["__truncated__"] = f"{len(value) - MAX_ITEMS} more keys"
            report.truncated = True
        return out, report

    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out_list: list[Any] = []
        for item in items[:MAX_ITEMS]:
            child, child_report = sanitize(
                item, max_chars=max_chars, known_secrets=known_secrets, _depth=_depth + 1
            )
            report.merge(child_report)
            out_list.append(child)
        if len(items) > MAX_ITEMS:
            out_list.append(f"[truncated {len(items) - MAX_ITEMS} items]")
            report.truncated = True
        return out_list, report

    return sanitize_text(repr(value), max_chars=max_chars, known_secrets=known_secrets)
