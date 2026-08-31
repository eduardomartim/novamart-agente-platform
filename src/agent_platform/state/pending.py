"""Suspended runs, as data rather than as live objects.

The V1 registry held a ``_PendingRun`` containing a compiled LangGraph and a
``Tracer``. That is correct for one process and impossible for two: neither
object can cross a process boundary, and pickling them would serialise a
provider, a tool registry and an open repository handle along with them.

What is stored instead is the smallest set of *facts* from which the run can be
rebuilt on any replica. Everything else -- the graph, the agents, the tracer,
the gateway -- is reconstructed from configuration, because it is derived state
and was never worth persisting.

Deliberately absent from the payload
------------------------------------
``tool_result``, ``validation`` and the gathered ``context`` are not here. They
are raw tool output, captured before the output-security layer masks PII, and
nothing about resuming needs them: they live in the graph checkpoint, which is
the component that legitimately owns execution state.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any

from .backend import Backend

#: Keeps suspended runs from colliding with counters or windows in one Redis.
NAMESPACE = "pending"

#: How much longer than its logical lifetime an entry is physically retained.
#:
#: This is what preserves an existing, user-visible distinction: an expired
#: confirmation is reported as expired (409), a nonexistent one as not found
#: (404). Once the backend has actually evicted a key those two are the same
#: observation, so the record outlives its own expiry to act as a tombstone.
GRACE_MULTIPLIER = 3.0


@dataclass(frozen=True, slots=True)
class SuspendedRun:
    """Everything needed to resume, and nothing that cannot be serialised."""

    request_id: str
    trace_id: str
    user_input: str
    #: Wall clock, not ``perf_counter``. A monotonic reading is meaningful only
    #: inside the process that took it, so it cannot be the basis of an expiry
    #: another replica has to honour.
    created_at: float
    #: Resource counters as they stood when the run suspended.
    #:
    #: Carried because a per-request ceiling has to mean the same thing however
    #: many processes the request passes through. Without them the resuming
    #: replica starts from zero and the run quietly gets a second allowance --
    #: which is precisely what ``ResourceGuard.begin`` refuses to do within one
    #: process. Counters and timings only: no prompt text, no output, no
    #: credentials.
    llm_calls: int = 0
    tool_calls: int = 0
    tool_output_bytes: int = 0
    elapsed_seconds: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, raw: str) -> SuspendedRun:
        data: dict[str, Any] = json.loads(raw)
        return cls(
            request_id=str(data["request_id"]),
            trace_id=str(data["trace_id"]),
            user_input=str(data["user_input"]),
            created_at=float(data["created_at"]),
            llm_calls=int(data.get("llm_calls", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            tool_output_bytes=int(data.get("tool_output_bytes", 0)),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
        )


class SharedPendingRegistry:
    """Bounded, expiring, single-use store of suspended runs.

    Interface-compatible with the V1 ``PendingRegistry`` so the platform's call
    sites are unchanged, with two differences that are the point of the class:
    the entries are serialisable, and ``pop`` is atomic across replicas.
    """

    def __init__(
        self,
        backend: Backend,
        *,
        max_entries: int,
        ttl_seconds: float,
        namespace: str = NAMESPACE,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._max = max_entries
        self._ttl = ttl_seconds
        self._namespace = namespace
        #: Public and reassignable so a test can drive expiry without sleeping.
        #: Both the stamp and the check read it, so they can never disagree
        #: about what time it is -- which is the bug that arises when one uses
        #: the injected clock and the other reaches for ``time.time`` directly.
        self.clock = clock

    @property
    def backend_kind(self) -> str:
        return self._backend.kind

    def put(self, key: str, value: SuspendedRun) -> bool:
        """Store a suspended run. ``False`` when the store is full.

        Refusing beats evicting: dropping someone else's pending action to make
        room would let a flood of suspensions push a legitimate one out, and a
        refused request simply does not execute.
        """
        # Stamped here rather than by the caller, so the write and every
        # later expiry check share one clock.
        now = self.clock()
        stamped = replace(value, created_at=now)
        return self._backend.entry_put(
            self._namespace,
            key,
            stamped.to_json(),
            ttl_seconds=self._ttl,
            max_entries=self._max,
            grace_seconds=self._ttl * GRACE_MULTIPLIER,
            now=now,
        )

    def get(self, key: str) -> SuspendedRun | None:
        """The run, if present and not past its logical expiry."""
        raw = self._backend.entry_peek(self._namespace, key, now=self.clock())
        if raw is None:
            return None
        run = SuspendedRun.from_json(raw)
        return None if self._expired(run) else run

    def take(self, key: str) -> SuspendedRun | None:
        """Consume the run. At most one caller anywhere ever succeeds.

        The atomicity is the backend's: a single scripted get-and-delete, so two
        replicas approving the same confirmation cannot both be handed it.
        Expiry is enforced *after* the take -- an aged-out entry is still
        removed, because leaving it would let a second attempt observe it.
        """
        raw = self._backend.entry_take(self._namespace, key, now=self.clock())
        if raw is None:
            return None
        run = SuspendedRun.from_json(raw)
        return None if self._expired(run) else run

    def is_expired(self, key: str) -> bool:
        """True when the key exists but has aged out.

        Answerable only because of the grace window; without it an expired
        entry and an absent one look identical.
        """
        raw = self._backend.entry_peek(self._namespace, key, now=self.clock())
        if raw is None:
            return False
        return self._expired(SuspendedRun.from_json(raw))

    def discard(self, key: str) -> None:
        """Drop an entry without caring whether it was there."""
        self._backend.entry_take(self._namespace, key, now=self.clock())

    def _expired(self, run: SuspendedRun) -> bool:
        return (self.clock() - run.created_at) >= self._ttl

    def __len__(self) -> int:
        return self._backend.entry_count(self._namespace, now=self.clock())
