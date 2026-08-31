"""Single use, enforced where more than one process can be asking.

A signature proves a grant was issued. It cannot prove the grant has not been
used, because a copy of a valid token is also a valid token. That is what the
nonce is for, and why checking it has to be atomic: two replicas presented with
the same captured grant must not both conclude they are the first.

No new storage primitive was needed. The shared backend built in V2.2 already
has a capped counter whose read and increment are one operation, and a cap of
one *is* single use: the first caller charges it, every later caller finds it
spent. Local mode gets the same semantics from the in-process backend, which is
correct there because in local mode there is only one process to disagree.
"""

from __future__ import annotations

from ..state.backend import Backend

#: Keeps consumed nonces from colliding with pending confirmations, budgets or
#: rate-limit windows sharing one Redis.
PREFIX = "grant-nonce"

#: How long a spent nonce is remembered. It only has to outlive the grant that
#: carried it -- after expiry the grant is refused on its own terms, so
#: remembering it further protects nothing and costs storage.
RETENTION_MARGIN_SECONDS = 60.0


class NonceReplayed(Exception):
    """This grant has already been used.

    Distinct from a signature failure so the *platform* can tell replay from
    forgery in its own events. Callers are told neither.
    """


def consume(
    backend: Backend, nonce: str, *, expires_at: float, now: float
) -> None:
    """Claim a nonce, or refuse because someone already did.

    Raises :class:`NonceReplayed` on the second and every subsequent attempt,
    including when those attempts are simultaneous and on different replicas.
    """
    ttl = max(1.0, (expires_at - now) + RETENTION_MARGIN_SECONDS)
    if not backend.counter_charge(f"{PREFIX}:{nonce}", limit=1, ttl_seconds=ttl):
        raise NonceReplayed("grant refused")


__all__ = ["PREFIX", "RETENTION_MARGIN_SECONDS", "NonceReplayed", "consume"]
