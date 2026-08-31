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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

#: Limit identifiers, used in errors, traces and the dashboard.
LIMIT_LLM_CALLS: Final[str] = "llm_calls_per_request"
LIMIT_TOOL_CALLS: Final[str] = "tool_calls_per_request"
LIMIT_DEADLINE: Final[str] = "request_deadline_seconds"
LIMIT_TOOL_OUTPUT: Final[str] = "tool_output_bytes"
LIMIT_PENDING: Final[str] = "pending_confirmations"


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


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    """The configured ceilings. Constructed from :class:`Settings`."""

    max_llm_calls_per_request: int
    max_tool_calls_per_request: int
    request_deadline_seconds: float
    max_tool_output_bytes: int
    max_pending_confirmations: int

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


@dataclass(slots=True)
class ResourceUsage:
    """Live consumption for one in-flight request.

    Surfaced to the dashboard. Contains counters and timings only -- no prompt
    text, no model output, no credentials.
    """

    request_id: str
    started_at: float
    llm_calls: int = 0
    tool_calls: int = 0
    tool_output_bytes: int = 0

    def elapsed(self, now: float) -> float:
        return max(0.0, now - self.started_at)

    def as_dict(self, now: float, limits: ResourceLimits) -> dict[str, float | int | str]:
        return {
            "request_id": self.request_id,
            "llm_calls": self.llm_calls,
            "llm_calls_limit": limits.max_llm_calls_per_request,
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

    def check_deadline(self, request_id: str) -> None:
        """Raise if the request has run past its wall-clock ceiling."""
        with self._lock:
            usage = self._require(request_id)
            elapsed = usage.elapsed(self._clock())
            if elapsed >= self._limits.request_deadline_seconds:
                raise ResourceLimitExceeded(
                    LIMIT_DEADLINE,
                    f"request exceeded its {self._limits.request_deadline_seconds:.0f}s "
                    f"deadline (elapsed {elapsed:.1f}s)",
                )

    def charge_llm_call(self, request_id: str) -> int:
        """Account for one model call. Raises before the call that would exceed."""
        with self._lock:
            usage = self._require(request_id)
            if usage.elapsed(self._clock()) >= self._limits.request_deadline_seconds:
                raise ResourceLimitExceeded(
                    LIMIT_DEADLINE,
                    f"request exceeded its {self._limits.request_deadline_seconds:.0f}s deadline",
                )
            if usage.llm_calls >= self._limits.max_llm_calls_per_request:
                raise ResourceLimitExceeded(
                    LIMIT_LLM_CALLS,
                    f"request reached its ceiling of "
                    f"{self._limits.max_llm_calls_per_request} model calls",
                )
            usage.llm_calls += 1
            return usage.llm_calls

    def charge_tool_call(self, request_id: str) -> int:
        """Account for one tool invocation."""
        with self._lock:
            usage = self._require(request_id)
            if usage.elapsed(self._clock()) >= self._limits.request_deadline_seconds:
                raise ResourceLimitExceeded(
                    LIMIT_DEADLINE,
                    f"request exceeded its {self._limits.request_deadline_seconds:.0f}s deadline",
                )
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
