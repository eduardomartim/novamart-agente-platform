"""Hard, model-independent resource limits for a single request.

**This is not an authorisation mechanism.** It has exactly one verb -- *stop* --
and no way to express *permit*. Every method either returns ``None`` or raises;
none returns a decision, and nothing here is consulted when deciding whether an
action is allowed. The policy engine remains the sole authority on that
question. A request that clears every limit here has been granted nothing; it
has merely not been stopped.

Why it exists alongside the budget guard: the budget caps *cost*, which is the
wrong instrument on its own. The deterministic stub costs exactly ``$0``, so
under a purely monetary limit a runaway loop is free and therefore unbounded.
These limits are denominated in calls, seconds and bytes, so they hold whatever
the provider costs.

Design constraints, all load-bearing:

* **Fails closed.** Any internal error raises rather than permitting.
* **Never derived from model output.** Limits come from :class:`Settings` only.
* **Deterministic.** The clock is injectable, so every limit is testable
  without sleeping.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Final

#: Limit identifiers, used in errors, traces and the dashboard.
LIMIT_LLM_CALLS: Final[str] = "llm_calls_per_request"
LIMIT_TOOL_CALLS: Final[str] = "tool_calls_per_request"
LIMIT_DEADLINE: Final[str] = "request_deadline_seconds"
#: Physical provider attempts. A logical call can retry inside the provider, so
#: this is the ceiling that actually bounds requests to the model's endpoint.
LIMIT_LLM_ATTEMPTS: Final[str] = "llm_attempts_per_request"
#: Embedding calls, counted apart from generation so neither distorts the other.
LIMIT_EMBEDDING_CALLS: Final[str] = "embedding_calls_per_request"


class ResourceLimitExceeded(RuntimeError):
    """A hard resource limit stopped the request.

    Deliberately *not* a policy decision. It carries no notion of permission
    and callers must not treat it as one; it means "stop", never "this was
    disallowed by policy".
    """

    def __init__(self, limit: str, detail: str) -> None:
        super().__init__(detail)
        self.limit = limit
        self.detail = detail


#: The request whose ledger the current context spends from.
#:
#: The provider charges a physical attempt from inside its own retry loop, and
#: it has no request id to charge it against: ``LLMProvider.generate`` takes a
#: prompt, not a caller. Threading one through would change a protocol three
#: implementations share, for the benefit of one accounting concern.
#:
#: A context variable instead -- the same mechanism this codebase already uses
#: to carry the quota bucket into the policy engine and the retrieval provider
#: into the strategy, and for the same reason: the value belongs to the request
#: rather than to any one call signature.
_current_request: ContextVar[tuple[ResourceGuard, str] | None] = ContextVar(
    "agent_platform_resource_request", default=None
)


@contextmanager
def resource_scope(guard: ResourceGuard | None, request_id: str) -> Iterator[None]:
    """Bind *request_id*'s ledger for the dynamic extent of one request."""
    token = _current_request.set(None if guard is None else (guard, request_id))
    try:
        yield
    finally:
        _current_request.reset(token)


def charge_current_llm_attempt() -> None:
    """Charge one physical provider attempt against the request in scope.

    A no-op when nothing is bound. That is not a hole in the ceiling: the
    binding exists for the whole extent of a graph run, and a model call made
    outside one -- the evaluator's judge, a CLI probe, a test constructing a
    provider directly -- has no per-request ledger to charge and never had.
    The logical ceiling still fails closed for anything the graph does, because
    ``ResourceGuard._require`` refuses a request that never called ``begin``.
    """
    bound = _current_request.get()
    if bound is None:
        return
    guard, request_id = bound
    guard.charge_llm_attempt(request_id)


def charge_current_embedding_call() -> None:
    """Charge one embedding against the request in scope. No-op when unbound."""
    bound = _current_request.get()
    if bound is None:
        return
    guard, request_id = bound
    guard.charge_embedding_call(request_id)


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """The configured ceilings. Constructed from :class:`Settings`."""

    max_llm_calls_per_request: int
    max_tool_calls_per_request: int
    request_deadline_seconds: float
    max_tool_output_bytes: int
    max_pending_confirmations: int

    #: Ceiling on *physical* attempts against the provider.
    #:
    #: ``max_llm_calls_per_request`` counts logical calls, and one of those can
    #: retry inside the provider -- so twelve logical calls were up to
    #: thirty-six requests to the endpoint and the ceiling was three times
    #: optimistic. Both counters are kept: the logical one is what traces and
    #: agents talk about, this one is what the network sees.
    #:
    #: ``0`` means "derive", and it derives to the *fail-closed* value: as many
    #: attempts as there are logical calls, i.e. no retries at all. A caller
    #: that forgets to pass it therefore gets a tighter limit, never a looser
    #: one. :class:`~agent_platform.platform.AgentPlatform` always passes the
    #: real figure, computed from the provider's retry policy.
    max_llm_attempts_per_request: int = 0

    #: Ceiling on embedding calls, counted apart from generation.
    #:
    #: Retrieval embeds through ``provider.embed``, which does not pass through
    #: ``BaseAgent._generate`` and so was charged against nothing per request.
    #: ``0`` derives to ``max_tool_calls_per_request``: every tool call could in
    #: principle be a search, and a search is what embeds.
    max_embedding_calls_per_request: int = 0

    #: What one model call can cost in wall-clock, worst case.
    #:
    #: The deadline is checked when work is *charged*, not while it runs, so a
    #: call admitted at 179.9s used to run its full timeout and every internal
    #: retry on top of the budget -- measured at up to 91.5s past a 180s
    #: deadline. Reserving this before admitting a call is what makes the
    #: deadline mean what it says. ``0.0`` reserves nothing, which is the
    #: behaviour every earlier version had.
    llm_call_worst_case_seconds: float = 0.0

    #: The same reservation for a tool invocation.
    tool_call_worst_case_seconds: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "max_llm_calls_per_request",
            "max_tool_calls_per_request",
            "max_tool_output_bytes",
            "max_pending_confirmations",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be >= 1")
        if self.request_deadline_seconds <= 0:
            raise ValueError("request_deadline_seconds must be > 0")
        for name in ("llm_call_worst_case_seconds", "tool_call_worst_case_seconds"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0")
        # Resolve the two derived ceilings. `object.__setattr__` because the
        # dataclass is frozen; the value is settled here, once, and never again.
        if self.max_llm_attempts_per_request < 1:
            object.__setattr__(
                self, "max_llm_attempts_per_request", self.max_llm_calls_per_request
            )
        if self.max_embedding_calls_per_request < 1:
            object.__setattr__(
                self, "max_embedding_calls_per_request", self.max_tool_calls_per_request
            )


@dataclass(slots=True)
class ResourceUsage:
    """Live consumption for one in-flight request.

    Surfaced to the dashboard. Contains counters and timings only -- no prompt
    text, no model output, no credentials.
    """

    request_id: str
    started_at: float
    llm_calls: int = 0
    #: Attempts actually made against the provider. Never below ``llm_calls``:
    #: every logical call makes at least one.
    llm_attempts: int = 0
    embedding_calls: int = 0
    tool_calls: int = 0
    tool_output_bytes: int = 0

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.started_at)

    def as_dict(self, now: float, limits: ResourceLimits) -> dict[str, float | int | str]:
        return {
            "request_id": self.request_id,
            "llm_calls": self.llm_calls,
            "llm_calls_limit": limits.max_llm_calls_per_request,
            "llm_attempts": self.llm_attempts,
            "llm_attempts_limit": limits.max_llm_attempts_per_request,
            "embedding_calls": self.embedding_calls,
            "embedding_calls_limit": limits.max_embedding_calls_per_request,
            "tool_calls": self.tool_calls,
            "tool_calls_limit": limits.max_tool_calls_per_request,
            "tool_output_bytes": self.tool_output_bytes,
            "elapsed_seconds": round(self.elapsed(now), 3),
            "deadline_seconds": limits.request_deadline_seconds,
        }


class ResourceGuard:
    """Per-request resource accounting.

    Thread-safe. One instance is shared across requests; state is keyed by
    ``request_id`` and released when the request finishes.
    """

    def __init__(
        self,
        limits: ResourceLimits,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._limits = limits
        self._clock = clock
        self._lock = threading.Lock()
        self._usage: dict[str, ResourceUsage] = {}

    @property
    def limits(self) -> ResourceLimits:
        return self._limits

    # ------------------------------------------------------------- lifecycle

    def begin(self, request_id: str) -> None:
        """Start accounting for a request. Idempotent.

        Re-entry does not restart the deadline clock: a resumed request must
        not win itself a fresh time budget by being suspended.
        """
        with self._lock:
            if request_id not in self._usage:
                self._usage[request_id] = ResourceUsage(
                    request_id=request_id, started_at=self._clock()
                )

    def restore(
        self,
        request_id: str,
        *,
        llm_calls: int,
        tool_calls: int,
        tool_output_bytes: int,
        elapsed_seconds: float,
        llm_attempts: int = 0,
        embedding_calls: int = 0,
    ) -> None:
        """Re-establish accounting for a request that began in another process.

        Exists so that resuming a suspended run on a different replica does not
        hand it a fresh allowance. ``begin()`` already refuses to restart the
        deadline for a re-entered request; this extends the same rule across a
        process boundary, where the original counters are not in memory to be
        preserved and have to be carried explicitly.

        The start time is back-dated by the elapsed time already spent, so the
        request deadline continues to run from when the request actually
        started rather than from when it was picked up.
        """
        with self._lock:
            if request_id in self._usage:
                return
            self._usage[request_id] = ResourceUsage(
                request_id=request_id,
                started_at=self._clock() - max(0.0, elapsed_seconds),
                llm_calls=max(0, llm_calls),
                # A physical count lower than the logical one means a record
                # written before this counter existed. One attempt per logical
                # call is the floor, so a resumed run cannot start with an
                # emptier ledger than it earned.
                llm_attempts=max(0, llm_attempts, llm_calls),
                embedding_calls=max(0, embedding_calls),
                tool_calls=max(0, tool_calls),
                tool_output_bytes=max(0, tool_output_bytes),
            )

    def release(self, request_id: str) -> ResourceUsage | None:
        with self._lock:
            return self._usage.pop(request_id, None)

    def usage(self, request_id: str) -> ResourceUsage | None:
        with self._lock:
            return self._usage.get(request_id)

    def active_requests(self) -> int:
        with self._lock:
            return len(self._usage)

    def _require(self, request_id: str) -> ResourceUsage:
        """Fetch accounting state, failing closed if it is missing.

        A missing entry means a caller skipped ``begin()``. Creating one here
        would silently grant an unmetered request, so it raises instead.
        """
        usage = self._usage.get(request_id)
        if usage is None:
            raise ResourceLimitExceeded(
                LIMIT_DEADLINE,
                f"no resource accounting is active for request {request_id!r}",
            )
        return usage

    # ---------------------------------------------------------------- checks

    def _enforce_deadline(self, usage: ResourceUsage, reserve_seconds: float) -> None:
        """The one deadline rule. Callers hold the lock.

        ``reserve_seconds`` is what the work about to start could cost in the
        worst case. Admitting a call only because the clock has not run out
        *yet* is how a 180s deadline used to finish at 271s: the check passed at
        179.9s and the call then spent its full timeout and every internal retry
        outside anybody's budget. Requiring the worst case to fit turns the
        deadline from a description into a bound.

        ``0`` reserves nothing, which is exactly the older behaviour and what a
        caller that does not know its own worst case should get.
        """
        elapsed = usage.elapsed(self._clock())
        deadline = self._limits.request_deadline_seconds
        if elapsed >= deadline:
            raise ResourceLimitExceeded(
                LIMIT_DEADLINE,
                f"request exceeded its {deadline:.0f}s deadline "
                f"(elapsed {elapsed:.1f}s)",
            )
        if reserve_seconds > 0 and elapsed + reserve_seconds > deadline:
            raise ResourceLimitExceeded(
                LIMIT_DEADLINE,
                f"request has {deadline - elapsed:.1f}s left of its {deadline:.0f}s "
                f"deadline, which cannot cover the {reserve_seconds:.1f}s this "
                f"call could take; it was not started",
            )

    def check_deadline(self, request_id: str) -> None:
        """Raise if the request has run past its wall-clock ceiling."""
        with self._lock:
            self._enforce_deadline(self._require(request_id), 0.0)

    def charge_llm_call(self, request_id: str) -> int:
        """Account for one model call. Raises before the call that would exceed.

        A *logical* call: what an agent asked for, and what traces name. The
        provider may make several attempts to satisfy it, which
        :meth:`charge_llm_attempt` counts separately. Both ceilings are checked
        here, so a request already out of physical attempts is refused before
        an agent builds a prompt it can never send.
        """
        with self._lock:
            usage = self._require(request_id)
            self._enforce_deadline(usage, self._limits.llm_call_worst_case_seconds)
            if usage.llm_calls >= self._limits.max_llm_calls_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_LLM_CALLS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_llm_calls_per_request} model calls",
                )
            if usage.llm_attempts >= self._limits.max_llm_attempts_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_LLM_ATTEMPTS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_llm_attempts_per_request} provider attempts",
                )
            usage.llm_calls += 1
            return usage.llm_calls

    def charge_llm_attempt(self, request_id: str) -> int:
        """Account for one *physical* attempt against the provider.

        Charged inside the provider's retry loop, immediately before the
        request leaves the process, so a retry costs exactly what a first try
        costs. This is the counter that bounds traffic to the endpoint; the
        logical one bounds how much work the graph asked for.

        Deliberately no deadline reservation: the logical call that owns this
        attempt already reserved its whole worst case, retries included.
        Reserving again would refuse the second attempt of a call the request
        had already been given the time for.
        """
        with self._lock:
            usage = self._require(request_id)
            self._enforce_deadline(usage, 0.0)
            if usage.llm_attempts >= self._limits.max_llm_attempts_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_LLM_ATTEMPTS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_llm_attempts_per_request} provider attempts",
                )
            usage.llm_attempts += 1
            return usage.llm_attempts

    def charge_embedding_call(self, request_id: str) -> int:
        """Account for one embedding call.

        Its own counter rather than a share of the model-call one: retrieval
        embeds through a different provider method, on a different quota, for a
        different purpose, and folding the two together would make both numbers
        harder to read for no gain. It carries the same deadline reservation as
        a generation, because it is the same kind of network round trip.
        """
        with self._lock:
            usage = self._require(request_id)
            self._enforce_deadline(usage, self._limits.llm_call_worst_case_seconds)
            if usage.embedding_calls >= self._limits.max_embedding_calls_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_EMBEDDING_CALLS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_embedding_calls_per_request} embedding calls",
                )
            usage.embedding_calls += 1
            return usage.embedding_calls

    def charge_tool_call(self, request_id: str) -> int:
        """Account for one tool invocation."""
        with self._lock:
            usage = self._require(request_id)
            self._enforce_deadline(usage, self._limits.tool_call_worst_case_seconds)
            if usage.tool_calls >= self._limits.max_tool_calls_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_TOOL_CALLS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_tool_calls_per_request} tool calls",
                )
            usage.tool_calls += 1
            return usage.tool_calls

    def account_tool_output(self, request_id: str, size_bytes: int) -> bool:
        """Record a tool result's size.

        Returns ``False`` when the result is over the ceiling. Deliberately a
        return value rather than an exception: an oversized result is a
        *truncation* event, not a reason to abort the request. The caller
        replaces the payload; nothing partial is passed on.
        """
        with self._lock:
            usage = self._usage.get(request_id)
            if usage is not None:
                usage.tool_output_bytes += max(0, size_bytes)
        return size_bytes <= self._limits.max_tool_output_bytes

    def snapshot(self, request_id: str) -> dict[str, float | int | str] | None:
        with self._lock:
            usage = self._usage.get(request_id)
            if usage is None:
                return None
            return usage.as_dict(self._clock(), self._limits)


@dataclass(slots=True)
class PendingRegistry:
    """Bounded, expiring store of suspended requests awaiting confirmation.

    Two failure modes are closed here:

    * **Unbounded growth** -- a request that interrupts and is never confirmed
      previously stayed in memory for the process lifetime.
    * **Stale approval** -- there was no upper bound on how long a suspended
      action remained resumable, so an approval could execute against state
      that had long moved on.

    When the store is full of *live* entries a new suspension is refused rather
    than evicting an existing one. Evicting would let an attacker push a
    legitimate pending action out of the store; refusing fails closed, because
    the refused request simply does not execute.
    """

    max_entries: int
    ttl_seconds: float
    clock: Callable[[], float] = time.monotonic
    _entries: dict[str, tuple[float, object]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _purge_expired(self, now: float) -> list[str]:
        expired = [
            key for key, (created, _) in self._entries.items() if now - created >= self.ttl_seconds
        ]
        for key in expired:
            del self._entries[key]
        return expired

    def put(self, key: str, value: object) -> bool:
        """Store a suspended request. Returns False when the store is full."""
        with self._lock:
            now = self.clock()
            self._purge_expired(now)
            if key not in self._entries and len(self._entries) >= self.max_entries:
                return False
            self._entries[key] = (now, value)
            return True

    def get(self, key: str) -> object | None:
        """Fetch a suspended request, or ``None`` if absent or expired."""
        with self._lock:
            now = self.clock()
            self._purge_expired(now)
            entry = self._entries.get(key)
            return entry[1] if entry else None

    def pop(self, key: str) -> object | None:
        with self._lock:
            entry = self._entries.pop(key, None)
            return entry[1] if entry else None

    def is_expired(self, key: str) -> bool:
        """True when the key existed but has aged out.

        Distinguishes "expired" from "never existed" so the caller can explain
        which happened.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return False
            return self.clock() - entry[0] >= self.ttl_seconds

    def purge(self) -> int:
        with self._lock:
            return len(self._purge_expired(self.clock()))

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(self.clock())
            return len(self._entries)
