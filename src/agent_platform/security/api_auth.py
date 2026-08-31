"""Who is calling, and what they are allowed to ask for.

Until this module existed the HTTP boundary had a hole with a specific shape.
It was not merely that anything reaching the port could submit a request --
that much was documented. It was that ``POST /runs/{id}/confirm`` accepted an
``actor`` string in the request body, wrote it into the ``CONFIRMATION_RESOLVED``
audit event beside the action fingerprint, and never checked it. The record of
*which human approved a high-risk action* was a value the caller chose.

Everything else about that path is careful: the fingerprint stops an approval
being redirected onto another action, the nonce stops it being replayed, the TTL
stops it being applied to stale state. Identity was the one link nobody
verified, which made the audit trail worse than absent -- an absent trail is
known to be absent.

Two design notes, because they look wrong at a glance.

**SHA-256, not argon2 or bcrypt.** Slow hashes exist to make brute force
expensive against low-entropy secrets that humans chose. The secrets here are
256 bits from :mod:`secrets`; no hash speed brings them into reach. A KDF would
add a dependency and put ~100ms of CPU behind every *unauthenticated* request,
which is a denial-of-service vector installed in the name of security. The
argument holds only because the entropy is ours, so :func:`parse_credentials`
refuses anything shorter than ``MIN_SECRET_CHARS`` -- an operator cannot
register a weak secret and inherit reasoning that does not apply to it.

**A public key id inside the token.** The audit trail needs a stable handle for
the caller. Without a non-secret component, recording *who* would mean recording
a hash of the secret. The key id is public by construction: it is what appears
in logs and metrics, and it says which credential of a principal was used.

The token's alphabet is deliberately ``[0-9A-Za-z._-]`` and its length well over
16, so ``Authorization: Bearer ap_...`` matches the ``bearer_token`` pattern
already in :mod:`agent_platform.security.secrets`. A token that reaches a log
line is redacted by shape, with nothing here needing to know it happened.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Final

#: Marks a credential as this platform's. Not a security control -- it makes a
#: leaked token greppable during incident response and recognisable to secret
#: scanners, which is worth two characters.
TOKEN_PREFIX: Final[str] = "ap"  # noqa: S105 - a public prefix, not a credential

#: Bytes of entropy in a generated secret. 32 bytes -> 43 urlsafe characters.
SECRET_BYTES: Final[int] = 32

#: Floor on a *configured* secret's length, in characters. This is what keeps
#: the SHA-256 argument honest: it holds for high-entropy secrets and for
#: nothing else, so a weak one is refused rather than accepted and reasoned
#: about as though it were strong. 32 urlsafe characters is ~190 bits.
MIN_SECRET_CHARS: Final[int] = 32

SCOPE_RUNS_WRITE: Final[str] = "runs:write"
SCOPE_CONFIRM_WRITE: Final[str] = "confirm:write"
SCOPE_METRICS_READ: Final[str] = "metrics:read"

#: The complete set. There is deliberately no ``admin`` scope subsuming the
#: others: the moment one exists every deployment uses it, and the separation
#: between requesting an action and approving it becomes decorative. An identity
#: that genuinely needs both carries both, and the configuration line says so
#: where a reviewer can see it.
KNOWN_SCOPES: Final[frozenset[str]] = frozenset(
    {SCOPE_RUNS_WRITE, SCOPE_CONFIRM_WRITE, SCOPE_METRICS_READ}
)

_KEY_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{8}$")
_PRINCIPAL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_HASH_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")

#: Compared against when the key id is unknown, so that "no such credential"
#: costs what "wrong secret" costs.
_DUMMY_HASH: Final[str] = hashlib.sha256(b"agent-platform/no-such-credential").hexdigest()


class AuthError(Exception):
    """A credential was refused.

    ``reason`` is for the operator's log and for a metric label. It is a closed
    vocabulary and it is **never** returned to the caller: a verifier that
    explains which check failed helps someone iterate towards a valid forgery,
    and the caller can do nothing differently for one reason versus another.
    The same rule the execution grant follows.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class AuthConfigError(Exception):
    """The credential configuration is unusable.

    Separate from :class:`AuthError` because it is an operator's problem rather
    than a caller's, and it is surfaced in full: nobody iterates towards a
    forgery by reading their own configuration error.
    """


@dataclass(frozen=True, slots=True)
class Principal:
    """A verified caller."""

    key_id: str
    name: str
    scopes: frozenset[str]

    def may(self, scope: str) -> bool:
        return scope in self.scopes


@dataclass(frozen=True, slots=True)
class Credential:
    """One configured credential. Holds a hash; never a secret."""

    key_id: str
    principal: str
    scopes: frozenset[str]
    secret_hash: str

    def as_principal(self) -> Principal:
        return Principal(key_id=self.key_id, name=self.principal, scopes=self.scopes)


def secret_hash(secret: str) -> str:
    """The stored form of a secret."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_token() -> tuple[str, str, str]:
    """Mint a credential: ``(key_id, token, secret_hash)``.

    The token is returned once, to be handed to its holder. Nothing in this
    process keeps it; only the hash is ever configured.
    """
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(SECRET_BYTES)
    return key_id, f"{TOKEN_PREFIX}_{key_id}_{secret}", secret_hash(secret)


def _split_token(token: str) -> tuple[str, str]:
    """``ap_<keyid>_<secret>`` -> ``(keyid, secret)``.

    Split with ``maxsplit=2`` on purpose. A urlsafe secret contains ``_``, so an
    unbounded split would tear the secret into pieces and reject every genuine
    credential unlucky enough to contain one.
    """
    parts = token.split("_", 2)
    if len(parts) != 3:
        raise AuthError("bad_shape")
    prefix, key_id, secret = parts
    if prefix != TOKEN_PREFIX or not _KEY_ID_RE.match(key_id):
        raise AuthError("bad_shape")
    if len(secret) < MIN_SECRET_CHARS:
        raise AuthError("bad_shape")
    return key_id, secret


class Keyring:
    """The configured credentials, indexed by key id."""

    def __init__(self, credentials: tuple[Credential, ...]) -> None:
        self._by_id = {c.key_id: c for c in credentials}

    def __len__(self) -> int:
        return len(self._by_id)

    @property
    def principals(self) -> tuple[str, ...]:
        return tuple(sorted({c.principal for c in self._by_id.values()}))

    def verify(self, token: str) -> Principal:
        """Return the principal a token proves, or raise :class:`AuthError`.

        The hash is computed whether or not the key id is known, and compared
        against a fixed dummy when it is not, so an unknown key id costs what a
        wrong secret costs. That removes the trivially measurable difference. It
        is not a claim of constant time across a Python interpreter, a dict
        lookup and a garbage collector -- the defence carrying the weight is
        that the secret has 256 bits in it.
        """
        key_id, secret = _split_token(token)
        credential = self._by_id.get(key_id)
        expected = credential.secret_hash if credential is not None else _DUMMY_HASH
        matched = hmac.compare_digest(expected, secret_hash(secret))
        if credential is None:
            raise AuthError("unknown_key")
        if not matched:
            raise AuthError("bad_secret")
        return credential.as_principal()


def principal_from_header(header: str, keyring: Keyring) -> Principal:
    """Verify an ``Authorization`` header.

    Only this header is read. There is no query-parameter form and there will
    not be one: URLs are written to access logs and forwarded by proxies, so a
    credential in a query string is a credential in somebody else's log file.
    """
    if not header:
        raise AuthError("no_header")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer":
        raise AuthError("bad_shape")
    token = value.strip()
    if not token:
        raise AuthError("bad_shape")
    return keyring.verify(token)


def parse_credentials(text: str) -> tuple[Credential, ...]:
    """Parse the credential table.

    One credential per line; ``#`` starts a comment::

        # keyid    principal    scopes                     sha256(secret)
        3f9a1c2d   ops-oncall   confirm:write              9b74c9...
        7e2b4a10   ingest-svc   runs:write,metrics:read    a1d3f0...

    Line-oriented rather than JSON so it survives review: a reviewer sees at a
    glance which identity holds which authority, which is the entire point of
    keeping the scopes separate.
    """
    credentials: list[Credential] = []
    seen: set[str] = set()

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        fields = line.split()
        if len(fields) != 4:
            raise AuthConfigError(
                f"line {lineno}: expected 4 fields "
                f"(key id, principal, scopes, sha256), got {len(fields)}"
            )
        key_id, principal, scopes_raw, digest = fields

        if not _KEY_ID_RE.match(key_id):
            raise AuthConfigError(
                f"line {lineno}: key id must be 8 lowercase hex characters"
            )
        if key_id in seen:
            raise AuthConfigError(f"line {lineno}: duplicate key id {key_id}")
        if not _PRINCIPAL_RE.match(principal):
            raise AuthConfigError(
                f"line {lineno}: principal must match [A-Za-z0-9._-] and be 1-64 characters"
            )

        scopes = frozenset(s for s in scopes_raw.split(",") if s)
        if not scopes:
            raise AuthConfigError(f"line {lineno}: at least one scope is required")
        unknown = sorted(scopes - KNOWN_SCOPES)
        if unknown:
            raise AuthConfigError(
                f"line {lineno}: unknown scope(s) {', '.join(unknown)}; "
                f"known scopes are {', '.join(sorted(KNOWN_SCOPES))}"
            )
        if not _HASH_RE.match(digest):
            raise AuthConfigError(
                f"line {lineno}: the fourth field must be a sha256 hex digest "
                "(64 lowercase hex characters), not a secret"
            )

        seen.add(key_id)
        credentials.append(
            Credential(
                key_id=key_id, principal=principal, scopes=scopes, secret_hash=digest
            )
        )

    if not credentials:
        raise AuthConfigError("no credentials configured")
    return tuple(credentials)


# -------------------------------------------------------- the current caller

_principal: ContextVar[Principal | None] = ContextVar(
    "agent_platform_principal", default=None
)


def current_principal() -> Principal | None:
    """The caller of the request being served, if it was authenticated."""
    return _principal.get()


@contextmanager
def principal_scope(principal: Principal | None) -> Iterator[None]:
    """Bind a principal for the dynamic extent of one request.

    This carries the *cross-cutting* uses -- the quota bucket and the log field
    -- which cannot be threaded through every call site without changing all of
    them. The audit identity is deliberately **not** taken from here: it is
    passed to ``platform.confirm`` as an argument, because authority that
    decides what gets written into an audit record belongs in the signature that
    writes it rather than in the ambient environment.
    """
    token = _principal.set(principal)
    try:
        yield
    finally:
        _principal.reset(token)


__all__ = [
    "KNOWN_SCOPES",
    "MIN_SECRET_CHARS",
    "SCOPE_CONFIRM_WRITE",
    "SCOPE_METRICS_READ",
    "SCOPE_RUNS_WRITE",
    "TOKEN_PREFIX",
    "AuthConfigError",
    "AuthError",
    "Credential",
    "Keyring",
    "Principal",
    "current_principal",
    "issue_token",
    "parse_credentials",
    "principal_from_header",
    "principal_scope",
    "secret_hash",
]
