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

#: Namespace suffix for one caller's reservations. See :meth:`put`.
#:
#: A separate namespace rather than a separate kind of storage, because
#: ``entry_put`` already enforces a ceiling atomically -- one Lua script on
#: Redis, one lock locally. Counting a caller's entries in Python and then
#: storing would be the classic read-modify-write race: two requests from the
#: same visitor both read "one slot left" and both proceed. Reusing the
#: primitive that already solves that means the per-caller cap is exactly as
#: race-proof as the global one, with no second implementation to keep honest.
OWNER_NAMESPACE_SUFFIX = "by-key"

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
    #: Physical provider attempts, carried for the same reason as the logical
    #: count above: a ceiling means the same thing however many processes the
    #: request passes through.
    llm_attempts: int = 0
    embedding_calls: int = 0
    tool_calls: int = 0
    tool_output_bytes: int = 0
    elapsed_seconds: float = 0.0
    #: Which quota bucket this suspension is charged to.
    #:
    #: Internal accounting only. It is the same opaque value the rate limiter
    #: uses -- a salted digest for a dashboard visitor, a principal name for an
    #: API caller -- and it is never a raw address. It travels with the record
    #: because the slot has to be released to *whoever took it*, and the person
    #: who approves an action is deliberately not required to be the person who
    #: requested it. It is not traced, not logged and not returned to a caller.
    #:
    #: Empty means unattributed, which disables the per-caller cap for that
    #: record rather than grouping every unattributed record into one shared
    #: bucket. Nothing that serves a request can produce it: ``current_quota_key``
    #: falls back to the global bucket, never to nothing.
    quota_key: str = ""

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
            llm_attempts=int(data.get("llm_attempts", 0)),
            embedding_calls=int(data.get("embedding_calls", 0)),
            tool_calls=int(data.get("tool_calls", 0)),
            tool_output_bytes=int(data.get("tool_output_bytes", 0)),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
            # ``.get`` with a default, like every field above it: a record
            # written by an older replica mid-rollout deserialises as
            # unattributed rather than raising, and is simply not counted
            # against anyone's share.
            quota_key=str(data.get("quota_key", "")),
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
        max_per_key: int = 0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._backend = backend
        self._max = max_entries
        self._ttl = ttl_seconds
        self._namespace = namespace
        #: How many entries one quota key may hold. ``0`` disables the cap,
        #: which is the behaviour every caller had before it existed.
        self._max_per_key = max_per_key
        #: Public and reassignable so a test can drive expiry without sleeping.
        #: Both the stamp and the check read it, so they can never disagree
        #: about what time it is -- which is the bug that arises when one uses
        #: the injected clock and the other reaches for ``time.time`` directly.
        self.clock = clock

    @property
    def backend_kind(self) -> str:
        return self._backend.kind

    def _owner_namespace(self, owner: str) -> str:
        return f"{self._namespace}:{OWNER_NAMESPACE_SUFFIX}:{owner}"

    def _reserve(self, owner: str, key: str, now: float) -> bool:
        """Take one of ``owner``'s slots, or report that they have none left.

        The reservation is a marker keyed by the same request id as the record
        it accompanies, in a namespace of the caller's own. That makes the
        per-caller ceiling one ``entry_put`` -- atomic by construction -- and it
        makes a repeated ``put`` of the same request id consume nothing extra,
        because ``entry_put`` already treats a key it is holding as occupying
        the slot it is holding.

        No grace period: the marker has no tombstone duty. Nothing reads it to
        tell "expired" from "never existed", so outliving its own slot would
        only hold capacity that the record it tracks has already released.
        """
        return self._backend.entry_put(
            self._owner_namespace(owner),
            key,
            "1",
            ttl_seconds=self._ttl,
            max_entries=self._max_per_key,
            grace_seconds=0.0,
            now=now,
        )

    def _release(self, owner: str, key: str) -> None:
        """Give a slot back. Safe to call for a key that holds none."""
        if not owner or self._max_per_key <= 0:
            return
        self._backend.entry_take(self._owner_namespace(owner), key, now=self.clock())

    def put(self, key: str, value: SuspendedRun) -> bool:
        """Store a suspended run. ``False`` when there is no room for it.

        Refusing beats evicting: dropping someone else's pending action to make
        room would let a flood of suspensions push a legitimate one out, and a
        refused request simply does not execute. That was always true of the
        global ceiling and is equally true of the per-caller one -- a caller at
        their limit is refused, and every entry already in the store, theirs and
        everyone else's, is left exactly where it was.

        Two ceilings, both binding, checked caller-first. Caller-first because
        the common refusal is a caller who has had their share, and refusing
        there writes nothing at all; only the rarer "the deployment is full"
        path has anything to undo.

        The invariant the undo maintains is worth naming: **a reservation exists
        only for a key with a live record.** If the store refuses, the record is
        not there, so the reservation must not be either -- releasing it is
        correct rather than merely tidy. Both sides carry the same TTL and are
        stamped from the same clock, so they also expire together.
        """
        # Stamped here rather than by the caller, so the write and every
        # later expiry check share one clock.
        now = self.clock()
        stamped = replace(value, created_at=now)
        owner = value.quota_key
        capped = bool(owner) and self._max_per_key > 0

        if capped and not self._reserve(owner, key, now):
            return False

        stored = self._backend.entry_put(
            self._namespace,
            key,
            stamped.to_json(),
            ttl_seconds=self._ttl,
            max_entries=self._max,
            grace_seconds=self._ttl * GRACE_MULTIPLIER,
            now=now,
        )
        if capped and not stored:
            self._release(owner, key)
        return stored

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
        # The slot goes back to whoever took it, on every path out of the store.
        # Approval and decline both arrive here -- the platform consumes the
        # record either way and only then acts on the decision -- so one release
        # covers both, and an entry taken after it aged out releases too rather
        # than holding capacity for a record nobody can use.
        self._release(run.quota_key, key)
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
        raw = self._backend.entry_take(self._namespace, key, now=self.clock())
        if raw is not None:
            # Same reason as ``take``: the record is gone, so the slot it held
            # is free. A cleanup path that forgot this would leak one slot of
            # the owner's share on every cancellation.
            self._release(SuspendedRun.from_json(raw).quota_key, key)

    def _expired(self, run: SuspendedRun) -> bool:
        return (self.clock() - run.created_at) >= self._ttl

    def __len__(self) -> int:
        return self._backend.entry_count(self._namespace, now=self.clock())
