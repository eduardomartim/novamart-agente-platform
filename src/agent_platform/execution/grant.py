"""Authorisation that survives leaving the process.

``require_gateway()`` proves a tool is being called from inside the gateway --
within *this* process. That is exactly the right guarantee while the tool is a
function in the same interpreter, and exactly no guarantee once the tool lives
somewhere else: a ``ContextVar`` in the orchestrator says nothing to a separate
tool server, and anything that can reach that server would otherwise execute
without the policy engine ever being consulted.

A grant is the same idea expressed so it can cross that boundary. The gateway
issues one *after* the policy engine has allowed a specific action, and it binds
that authorisation to the thing that was allowed:

* **which tool** -- a grant for ``get_order`` cannot run ``delete_record``;
* **which arguments** -- via a digest, so a grant for ``ORD-1001`` cannot be
  redirected at ``ORD-9999``;
* **for how long** -- a short window, not an indefinite capability;
* **once** -- a nonce, consumed atomically, so it cannot be replayed.

Why this is not JWT
-------------------
JWT carries the algorithm in the token, which is how ``alg=none`` became a
recurring vulnerability. There is one algorithm here, it is not negotiable, and
it is not named in the token. The version is, and a token whose version is not
understood is refused rather than interpreted.

What is deliberately absent
---------------------------
The arguments themselves, any credential, any tool output, any request context.
The grant carries a digest and identifiers. Something that reads a grant learns
that *an* action was authorised, not what it was about.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any, Final

from ..guardrails.rules import action_fingerprint
from ..models import ProposedAction

#: Bumped when the payload's meaning changes. Present in the prefix *and* inside
#: the signed payload: the prefix allows a cheap reject, and the signed copy
#: stops the prefix from being rewritten to something a future verifier treats
#: differently.
GRANT_VERSION: Final[int] = 1

#: How long a grant is good for. Long enough to make one call, short enough that
#: a captured token is worth little. This is a ceiling on exposure, not a
#: latency budget.
DEFAULT_TTL_SECONDS: Final[float] = 30.0

#: Tolerance for clocks that disagree between the issuing and verifying process.
#: Small on purpose: a generous skew window silently extends every TTL.
CLOCK_SKEW_SECONDS: Final[float] = 2.0

#: The reserved argument key a grant travels in. No tool declares a parameter by
#: this name, and one appearing in business arguments is treated as an attempt
#: to confuse the two.
GRANT_ARGUMENT: Final[str] = "__grant__"

_MIN_SECRET_BYTES: Final[int] = 16


class GrantError(Exception):
    """A grant was refused.

    The message is deliberately coarse. A verifier that explains precisely
    which check failed is a verifier that helps someone iterate towards a valid
    forgery, and the caller can do nothing differently for one reason versus
    another.
    """


class GrantConfigurationError(Exception):
    """The signing secret is missing or unusable.

    Separate from :class:`GrantError` because it is an operator problem, not a
    caller problem, and because it must never be reported to a caller.
    """


def arguments_digest(tool: str, arguments: dict[str, Any]) -> str:
    """Bind a grant to exactly one tool call.

    Delegates to :func:`~agent_platform.guardrails.rules.action_fingerprint`,
    which the platform already uses to bind *human confirmations* to a specific
    action. Reusing it is the point: a second canonicalisation would mean two
    definitions of "the same action", and the gap between them would be
    somewhere an approval for one thing authorises another.
    """
    return action_fingerprint(ProposedAction(tool=tool, arguments=arguments))


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """One authorised execution, as data."""

    version: int
    execution_id: str
    tool: str
    arguments_digest: str
    issued_at: float
    expires_at: float
    nonce: str

    def payload(self) -> dict[str, Any]:
        return {
            "v": self.version,
            "eid": self.execution_id,
            "tool": self.tool,
            "adg": self.arguments_digest,
            "iat": self.issued_at,
            "exp": self.expires_at,
            "nonce": self.nonce,
        }

    @classmethod
    def _from_payload(cls, data: dict[str, Any]) -> ExecutionGrant:
        try:
            return cls(
                version=int(data["v"]),
                execution_id=str(data["eid"]),
                tool=str(data["tool"]),
                arguments_digest=str(data["adg"]),
                issued_at=float(data["iat"]),
                expires_at=float(data["exp"]),
                nonce=str(data["nonce"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GrantError("grant refused") from exc


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _secret_bytes(secret: str | None) -> bytes:
    if not secret or len(secret.encode()) < _MIN_SECRET_BYTES:
        # The secret's value is never named here, and neither is its length.
        raise GrantConfigurationError(
            "EXECUTION_GRANT_SECRET is not configured, or is too short to be "
            f"used (minimum {_MIN_SECRET_BYTES} bytes)"
        )
    return secret.encode()


def _sign(secret: str | None, message: bytes) -> bytes:
    return hmac.new(_secret_bytes(secret), message, hashlib.sha256).digest()


def issue(
    *,
    secret: str | None,
    execution_id: str,
    tool: str,
    arguments: dict[str, Any],
    ttl_seconds: float = DEFAULT_TTL_SECONDS,
    now: float | None = None,
) -> tuple[str, ExecutionGrant]:
    """Mint a grant for one specific call. Returns the token and the grant.

    Called by the gateway only after the policy engine has allowed the action,
    with the *validated* arguments -- so what is bound is what will run, not
    what an agent asked for.
    """
    if ttl_seconds <= 0:
        raise GrantConfigurationError("grant ttl must be positive")

    issued = time.time() if now is None else now
    grant = ExecutionGrant(
        version=GRANT_VERSION,
        execution_id=execution_id,
        tool=tool,
        arguments_digest=arguments_digest(tool, arguments),
        issued_at=issued,
        expires_at=issued + ttl_seconds,
        nonce=secrets.token_hex(16),
    )
    body = _b64(json.dumps(grant.payload(), sort_keys=True, separators=(",", ":")).encode())
    signature = _b64(_sign(secret, body.encode("ascii")))
    return f"v{GRANT_VERSION}.{body}.{signature}", grant


def verify(
    token: str,
    *,
    secret: str | None,
    expected_tool: str,
    arguments: dict[str, Any],
    now: float | None = None,
) -> ExecutionGrant:
    """Check a token against the call it is supposed to authorise.

    Every check is a refusal, never a repair. The order matters only in that
    the signature is verified before anything inside the payload is trusted --
    the fields are attacker-supplied until then.
    """
    moment = time.time() if now is None else now

    parts = token.split(".") if isinstance(token, str) else []
    if len(parts) != 3:
        raise GrantError("grant refused")
    prefix, body, signature = parts

    # Cheap version reject before doing any crypto.
    if prefix != f"v{GRANT_VERSION}":
        raise GrantError("grant refused")

    try:
        expected_sig = _sign(secret, body.encode("ascii"))
        provided_sig = _unb64(signature)
    except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
        raise GrantError("grant refused") from exc

    # Constant-time. A comparison that returns early leaks how much of a forged
    # signature was correct, which is enough to reconstruct one byte at a time.
    if not hmac.compare_digest(expected_sig, provided_sig):
        raise GrantError("grant refused")

    try:
        data = json.loads(_unb64(body))
    except (binascii.Error, json.JSONDecodeError, ValueError) as exc:
        raise GrantError("grant refused") from exc
    if not isinstance(data, dict):
        raise GrantError("grant refused")

    grant = ExecutionGrant._from_payload(data)

    # The signed version must agree with the prefix, or a token could be
    # presented to two verifiers as two different versions.
    if grant.version != GRANT_VERSION:
        raise GrantError("grant refused")

    # Timestamps must be sane before they are trusted as a window. An expiry at
    # or before issue is not a short grant, it is a malformed one.
    if not (grant.expires_at > grant.issued_at):
        raise GrantError("grant refused")
    if grant.issued_at > moment + CLOCK_SKEW_SECONDS:
        raise GrantError("grant refused")
    if moment > grant.expires_at + CLOCK_SKEW_SECONDS:
        raise GrantError("grant refused")

    # Tool binding: the caller says which tool it wants; the grant says which
    # tool was authorised. Only their agreement authorises anything.
    if grant.tool != expected_tool:
        raise GrantError("grant refused")

    # Argument binding, recomputed from what actually arrived rather than
    # compared against anything the caller sent alongside it.
    if not hmac.compare_digest(
        grant.arguments_digest, arguments_digest(expected_tool, arguments)
    ):
        raise GrantError("grant refused")

    return grant


__all__ = [
    "CLOCK_SKEW_SECONDS",
    "DEFAULT_TTL_SECONDS",
    "GRANT_ARGUMENT",
    "GRANT_VERSION",
    "ExecutionGrant",
    "GrantConfigurationError",
    "GrantError",
    "arguments_digest",
    "issue",
    "verify",
]
