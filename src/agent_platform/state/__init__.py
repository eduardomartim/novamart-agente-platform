"""Shared state: what has to be the same on every replica, and what does not.

Two modes, and the platform always says which one it is in:

**local** (default, ``REDIS_URL`` unset) -- state lives in this process. Correct
for one process, and the mode the offline suite and the demo run in.

**shared** (``REDIS_URL`` set) -- pending confirmations, the provider-call
ledger, the rate-limiter window and the graph checkpointer live in Redis, so a
confirmation suspended on one replica can be approved on another and a limit
configured once means the same thing however many replicas exist.

Not everything moves, and the ones that stay are not oversights:

* ``require_gateway()``'s ``ContextVar`` is deliberately process-local -- it
  marks the dynamic extent of an authorised call, which is a property of *this*
  call stack and would be meaningless, and dangerous, if shared.
* The per-request cost accumulator is scoped to a request, and a request never
  spans processes.
* The retriever and vector index are derived from the corpus; rebuilding them
  is cheaper than coordinating them.
* Daily money spend already comes from the repository, which is durable.
"""

from __future__ import annotations

from dataclasses import dataclass

from .backend import Backend, RedisBackend, SharedStateUnavailable, redis_backend_from_url
from .budget import SharedProviderBudget
from .limiter import SharedRateLimiter
from .local import LocalBackend
from .pending import NAMESPACE, SharedPendingRegistry, SuspendedRun

__all__ = [
    "NAMESPACE",
    "Backend",
    "LocalBackend",
    "RedisBackend",
    "SharedPendingRegistry",
    "SharedProviderBudget",
    "SharedRateLimiter",
    "SharedState",
    "SharedStateUnavailable",
    "SuspendedRun",
    "build_shared_state",
    "redis_backend_from_url",
]


@dataclass(frozen=True, slots=True)
class SharedState:
    """The chosen backend plus a name for it.

    The name is carried rather than inferred so that ``/ready``, the logs and
    the tests can all report the mode. "Is this actually sharing anything?" is
    the question this phase exists to answer, and it should never require
    reading the configuration to work out.
    """

    backend: Backend
    #: ``"local"`` or ``"redis"``.
    kind: str

    @property
    def is_shared(self) -> bool:
        return self.kind != "local"


def build_shared_state(redis_url: str | None) -> SharedState:
    """Choose a backend from configuration.

    A configured URL that cannot be reached raises rather than falling back:
    silently degrading to process-local state would mean the operator asked for
    a shared rate limit and got N independent ones, with nothing to indicate it.
    """
    if not redis_url:
        return SharedState(backend=LocalBackend(), kind="local")
    backend = redis_backend_from_url(redis_url)
    return SharedState(backend=backend, kind=backend.kind)
