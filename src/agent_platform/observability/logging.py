"""Structured logs that cannot become an exfiltration path.

Logs are the third place this platform sends data outward, after the response
body and the trace. The first two go through ``sanitize_text``; a log formatter
that did not would be a way to read, in cleartext, exactly the things the other
two were built to mask.

So every rendered value goes through the same sanitiser, and the record carries
a fixed, small set of fields. There is no ``**kwargs`` that lands arbitrary
objects in the output and no ``%r`` of a caller's argument.

Correlation
-----------
``request_id`` and ``trace_id`` already exist -- the ``Tracer`` has minted them
per request since V1. They are bound to a ``ContextVar`` at the HTTP edge and
picked up here, so a line emitted deep in the platform carries the id of the
request that caused it without every function having to pass it down.

With several replicas behind one Service, that id is what makes a request
followable: the client sees it in ``X-Request-ID``, and every line about that
request carries it, whichever pod produced it.
"""

from __future__ import annotations

import json
import logging
import time
from contextvars import ContextVar
from typing import Any

from ..security.sanitization import sanitize_text

#: Bound at the HTTP edge for the extent of one request.
_REQUEST_ID: ContextVar[str | None] = ContextVar("agent_platform_log_request_id", default=None)
_TRACE_ID: ContextVar[str | None] = ContextVar("agent_platform_log_trace_id", default=None)

#: Ceiling on a rendered message. A log line is not a place to dump a document,
#: and an unbounded one is a cheap way to fill a disk.
MAX_MESSAGE_CHARS = 2_000

#: Record attributes that are ours to emit. Anything else on the record --
#: including whatever a library attached -- is dropped rather than serialised,
#: because "serialise whatever is there" is how a credential ends up in a log.
#: An allow-list, not a filter: a field absent from here never reaches a log
#: line, so adding one is a deliberate act. ``principal`` and ``reason``
#: joined it for authentication -- the first names a verified caller (a
#: principal name is configuration, never a credential), the second is the
#: closed vocabulary of refusal causes that operators need and that callers
#: deliberately never see.
_ALLOWED_EXTRA = frozenset(
    {"event", "tool", "decision", "outcome", "latency_ms", "status", "principal", "reason"}
)

#: Exact secret values to redact, on top of the pattern-based redaction.
#:
#: ``sanitize_text`` recognises credential *shapes* -- an ``AIzaSy`` key, a
#: bearer token. It cannot recognise an arbitrary one, and the grant signing
#: secret is arbitrary by construction: a random string with no distinguishing
#: form. Registering the value lets it be redacted by identity rather than by
#: pattern, which is what ``platform._known_secrets`` already does for prompts
#: and responses.
#:
#: This is a redaction list, not a store: the values are already in this
#: process's memory because it uses them.
_KNOWN_SECRETS: tuple[str, ...] = ()


def bind_request(request_id: str | None, trace_id: str | None = None) -> None:
    """Attach ids to this request's context."""
    _REQUEST_ID.set(request_id)
    _TRACE_ID.set(trace_id)


def current_request_id() -> str | None:
    return _REQUEST_ID.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with a fixed shape."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            raw = record.getMessage()
        except Exception:  # pragma: no cover - a broken %-format in a caller
            raw = "<unrenderable log message>"

        message, _ = sanitize_text(
            raw, max_chars=MAX_MESSAGE_CHARS, known_secrets=_KNOWN_SECRETS
        )

        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": message,
        }

        request_id = _REQUEST_ID.get()
        if request_id:
            payload["request_id"] = request_id
        trace_id = _TRACE_ID.get()
        if trace_id:
            payload["trace_id"] = trace_id

        for key in _ALLOWED_EXTRA:
            value = getattr(record, key, None)
            if value is None:
                continue
            payload[key] = (
                sanitize_text(value, max_chars=200, known_secrets=_KNOWN_SECRETS)[0]
                if isinstance(value, str)
                else value
            )

        if record.exc_info:
            # The type only. A traceback carries file paths, local variables and
            # whatever was being processed when it failed.
            exc_type = record.exc_info[0]
            payload["error"] = exc_type.__name__ if exc_type else "Exception"

        return json.dumps(payload, separators=(",", ":"), default=str)


def register_secrets(*values: str | None) -> None:
    """Redact these exact values wherever they appear in a log line.

    Called once at start-up with whatever credentials this process holds.
    Short values are ignored: redacting a two-character string would blank
    out unrelated text and make the logs useless.
    """
    global _KNOWN_SECRETS
    _KNOWN_SECRETS = tuple(v for v in values if v and len(v) >= 8)


def configure(
    level: str = "INFO", *, stream: Any = None, known_secrets: tuple[str | None, ...] = ()
) -> None:
    """Install the JSON formatter on the root handler.

    Replaces existing handlers rather than adding to them: two handlers means
    every line twice, once structured and once not, and the unstructured copy
    would not be going through the sanitiser.
    """
    if known_secrets:
        register_secrets(*known_secrets)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


__all__ = [
    "MAX_MESSAGE_CHARS",
    "JsonFormatter",
    "bind_request",
    "configure",
    "current_request_id",
    "register_secrets",
]
