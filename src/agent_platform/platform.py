"""Composition root.

Everything is wired together here so that the individual modules stay free of
construction logic and remain independently testable. This is also the single
place where the order of the request pipeline is visible:

    rate limit -> input security -> graph -> output security -> persistence
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.types import Command

from .agent.answerer import AnswererAgent
from .agent.base import AgentDeps
from .agent.executor import ExecutorAgent
from .agent.researcher import ResearcherAgent
from .agent.router import RouterAgent
from .agent.validator import ValidatorAgent
from .config import Settings
from .cost.budget import BudgetGuard
from .cost.tracker import BudgetExceededError, CostTracker
from .guardrails.input import InputAssessment, assess_input
from .guardrails.policy import PolicyEngine
from .llm import LLMProvider, build_provider, describe_provider
from .llm.circuit import CircuitBreaker
from .llm.provider import LLMError, ProviderInfo
from .observability.events import EventStatus, EventType
from .observability.tracing import Tracer, new_request_id, new_trace_id
from .orchestration.graph import GraphDeps, build_graph
from .orchestration.state import initial_state
from .persistence.repository import Repository, RequestRecord
from .persistence.sqlite import SQLiteRepository
from .retrieval.strategy import retrieval_provider
from .security.rate_limit import (
    GLOBAL_KEY,
    RateLimiter,
    current_quota_key,
    quota_scope,
)
from .security.resources import (
    ResourceGuard,
    ResourceLimitExceeded,
    ResourceLimits,
)
from .security.sanitization import content_digest, sanitize_text
from .state import (
    SharedPendingRegistry,
    SharedRateLimiter,
    SharedState,
    SharedStateUnavailable,
    SuspendedRun,
    build_shared_state,
)
from .tools.gateway import ToolGateway
from .tools.registry import ToolRegistry, default_registry


def _build_repository(settings: Settings) -> Repository:
    """Durable storage, chosen once from configuration.

    SQLite by default, which is what every earlier version used and what the
    offline suite runs on. ``DATABASE_URL`` switches to PostgreSQL so that
    replicas share one ledger.

    A configured URL that cannot be reached raises rather than falling back.
    Silently returning to a pod-local SQLite would mean an operator who asked
    for one shared budget got one per replica -- the exact defect this is here
    to remove -- with nothing to indicate it.
    """
    if not settings.database_url:
        return SQLiteRepository(settings.database_path)

    from .persistence.postgres import PostgresRepository

    return PostgresRepository(settings.database_url)


def _build_tool_transport(settings: Settings) -> Any:
    """The tool transport, chosen once from configuration.

    ``None`` means tools run in this process, which is the default and the only
    mode the offline suite needs. ``mcp`` puts a real MCP server in its own
    process between the gateway and the tools, so the execution boundary is a
    process boundary and has to be proven with a signed grant rather than
    assumed from a ContextVar.

    A missing secret in ``mcp`` mode stops construction. Starting a platform
    that would refuse every tool call is worse than refusing to start: the
    failure would look like a policy problem rather than a configuration one.
    """
    if settings.tool_transport != "mcp":
        return None

    from .execution.grant import GrantConfigurationError
    from .execution.transport import McpToolTransport, subprocess_environment

    if not settings.execution_grant_secret:
        raise GrantConfigurationError(
            "TOOL_TRANSPORT=mcp requires EXECUTION_GRANT_SECRET to be set"
        )

    import sys

    return McpToolTransport(
        command=sys.executable,
        args=["-m", "agent_platform.mcp_server"],
        env=subprocess_environment(),
        call_timeout=settings.tool_timeout + 20.0,
    )


def _suspended_record(
    *,
    request_id: str,
    trace_id: str,
    user_input: str,
    usage: Any,
    now: float,
) -> SuspendedRun:
    """Snapshot a suspending run into something serialisable.

    Only identifiers, the original request text and counters. Explicitly not
    the graph, the tracer, the provider, the gathered context or any tool
    output -- the first three cannot cross a process and the last two are raw,
    pre-redaction data that resuming does not need.
    """
    return SuspendedRun(
        request_id=request_id,
        trace_id=trace_id,
        user_input=user_input,
        created_at=time.time(),
        llm_calls=getattr(usage, "llm_calls", 0) or 0,
        tool_calls=getattr(usage, "tool_calls", 0) or 0,
        tool_output_bytes=getattr(usage, "tool_output_bytes", 0) or 0,
        elapsed_seconds=usage.elapsed(now) if usage is not None else 0.0,
    )


def _build_checkpointer(
    shared_state: SharedState, settings: Settings
) -> BaseCheckpointSaver:
    """The graph checkpoint, local or shared.

    In local mode this is the V1 ``InMemorySaver``, unchanged.

    In shared mode it is Redis, and that is what carries a suspended run across
    replicas: the pending record identifies *which* run is waiting, while the
    checkpoint holds where that run had reached. Neither alone is enough.

    The checkpoint is given the same lifetime as the confirmation it belongs
    to, with headroom. A checkpoint that outlived its confirmation would be
    execution state for something nobody may resume; one that expired first
    would strand an approval the platform still considers valid.
    """
    if not shared_state.is_shared:
        return InMemorySaver()

    from langgraph.checkpoint.redis import RedisSaver

    backend = shared_state.backend
    client = getattr(backend, "_r", None)
    if client is None:  # pragma: no cover - only a non-Redis shared backend
        raise SharedStateUnavailable(
            "shared mode requires a Redis backend to checkpoint the graph"
        )

    ttl_minutes = max(1, int(settings.confirmation_ttl_seconds * 4 / 60) or 1)
    saver = RedisSaver(redis_client=client, ttl={"default_ttl": ttl_minutes})
    saver.setup()
    return saver


@dataclass(slots=True)
class ConfirmationRequest:
    """A suspended action awaiting human approval."""

    request_id: str
    tool: str
    arguments: dict[str, Any]
    risk_level: str
    reason: str


@dataclass(slots=True)
class RunResult:
    """The outcome of one request through the platform."""

    request_id: str
    trace_id: str
    status: str
    response: str
    route: str | None = None
    awaiting_confirmation: ConfirmationRequest | None = None
    policy_decision: dict[str, Any] | None = None
    tool_result: dict[str, Any] | None = None
    validation: dict[str, Any] | None = None
    retry_count: int = 0
    errors: list[str] = field(default_factory=list)
    latency_ms: float = 0.0
    provider: str = ""

    @property
    def blocked(self) -> bool:
        return self.status in {"blocked", "rate_limited", "rejected"}


@dataclass(slots=True)
class _ResumeContext:
    """A suspended run, rehydrated.

    Built on demand from the serialisable :class:`SuspendedRun` record rather
    than kept alive between calls. The graph, the agents and the tracer are all
    derived from configuration, so reconstructing them costs a few objects and
    buys the ability to resume on a replica that never saw the original
    request.
    """

    tracer: Tracer
    input_assessment: InputAssessment
    graph: Any
    started_at: float
    user_input: str
    trace_id: str


class AgentPlatform:
    """The assembled platform. Construct once, call :meth:`run` per request."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository: Repository | None = None,
        provider: LLMProvider | None = None,
        registry: ToolRegistry | None = None,
        use_judge: bool = True,
        shared_state: SharedState | None = None,
        checkpointer: BaseCheckpointSaver | None = None,
    ) -> None:
        self.settings = settings or Settings.from_env()
        self.repository = repository or _build_repository(self.settings)
        self.repository.initialize()

        self.provider = provider or build_provider(self.settings)
        self.registry = registry or default_registry()

        # Shared state, chosen once. `kind` is carried on the platform so the
        # mode can be reported rather than inferred -- "is this actually
        # sharing anything?" must never require reading the configuration.
        self.shared_state = shared_state or build_shared_state(self.settings.redis_url)

        self.rate_limiter: Any = (
            SharedRateLimiter(
                self.shared_state.backend,
                self.settings.requests_per_minute,
                self.settings.requests_per_hour,
            )
            if self.shared_state.is_shared
            else RateLimiter(
                self.settings.requests_per_minute, self.settings.requests_per_hour
            )
        )
        # The deployment-wide backstop, on its own bucket and its own numbers.
        #
        # Two ceilings with one number would have been theatre: an attacker
        # filling the shared bucket denies every other caller exactly as before,
        # so per-principal quota would have bought nothing. They are different
        # controls -- one is fairness between callers, the other is capacity for
        # the deployment -- and they only looked like one number while there was
        # one caller.
        self.global_rate_limiter: Any = (
            SharedRateLimiter(
                self.shared_state.backend,
                self.settings.effective_global_requests_per_minute,
                self.settings.effective_global_requests_per_hour,
                prefix="ratelimit:deployment",
            )
            if self.shared_state.is_shared
            else RateLimiter(
                self.settings.effective_global_requests_per_minute,
                self.settings.effective_global_requests_per_hour,
            )
        )
        self.budget_guard = BudgetGuard(
            self.repository,
            daily_budget_usd=self.settings.daily_budget_usd,
            max_request_cost_usd=self.settings.max_request_cost_usd,
        )
        self.cost_tracker = CostTracker(self.repository, self.budget_guard)
        self.policy_engine = PolicyEngine(
            self.registry, budget_guard=self.budget_guard, rate_limiter=self.rate_limiter
        )
        # Hard resource ceilings, denominated in calls/seconds/bytes rather
        # than money, so they hold even when the provider is free.
        self.resources = ResourceGuard(
            ResourceLimits(
                max_llm_calls_per_request=self.settings.max_llm_calls_per_request,
                max_tool_calls_per_request=self.settings.max_tool_calls_per_request,
                request_deadline_seconds=self.settings.request_deadline_seconds,
                max_tool_output_bytes=self.settings.max_tool_output_bytes,
                max_pending_confirmations=self.settings.max_pending_confirmations,
            )
        )
        self.circuit = CircuitBreaker(
            failure_threshold=self.settings.circuit_failure_threshold,
            cooldown_seconds=self.settings.circuit_cooldown_seconds,
        )
        self.gateway = ToolGateway(
            self.registry,
            self.policy_engine,
            default_timeout=self.settings.tool_timeout,
            resources=self.resources,
            transport=_build_tool_transport(self.settings),
            grant_secret=self.settings.execution_grant_secret,
        )

        # The graph checkpoint is what actually lets another replica resume a
        # suspended run: the pending record says *which* run, the checkpoint
        # holds where it got to. In local mode this is unchanged from V1.
        self._checkpointer: BaseCheckpointSaver = checkpointer or _build_checkpointer(
            self.shared_state, self.settings
        )
        # Bounded and expiring. Previously an unbounded dict cleared only on the
        # success path, so an interrupted request that was never confirmed
        # stayed resident for the process lifetime and remained resumable
        # forever.
        # Serialisable records, not live objects, and a consumption that is
        # atomic across replicas. See state/pending.py for why the entry
        # outlives its own expiry.
        self._pending = SharedPendingRegistry(
            self.shared_state.backend,
            max_entries=self.settings.max_pending_confirmations,
            ttl_seconds=self.settings.confirmation_ttl_seconds,
        )
        self._use_judge = use_judge

    # ------------------------------------------------------------------ helpers

    @property
    def provider_info(self) -> ProviderInfo:
        return describe_provider(self.provider, self.settings)

    @property
    def _known_secrets(self) -> tuple[str, ...]:
        key = self.settings.gemini_api_key
        return (key,) if key else ()

    def has_pending_confirmation(self, request_id: str) -> bool:
        """Whether a suspended action with this id is still resumable.

        Read-only, and deliberately not a way to reach the action itself. It
        exists so a caller that is not the CLI -- the HTTP boundary -- can tell
        "never existed" from "aged out" *before* asking to resume, and answer
        accordingly. It grants nothing: resuming still goes through
        :meth:`confirm`, which rebuilds the action from platform-held state.
        """
        return self._pending.get(request_id) is not None

    def confirmation_expired(self, request_id: str) -> bool:
        """Whether a suspended action with this id existed and aged out."""
        return self._pending.is_expired(request_id)

    def close(self) -> None:
        self.gateway.shutdown()
        self.repository.close()

    def _build_graph(self, tracer: Tracer, assessment: InputAssessment) -> Any:
        deps = AgentDeps(
            provider=self.provider,
            tracer=tracer,
            registry=self.registry,
            cost_tracker=self.cost_tracker,
            settings=self.settings,
            gateway=self.gateway,
            known_secrets=self._known_secrets,
            resources=self.resources,
            circuit=self.circuit,
        )
        return build_graph(
            GraphDeps(
                router=RouterAgent(deps),
                researcher=ResearcherAgent(deps),
                executor=ExecutorAgent(deps),
                validator=ValidatorAgent(deps),
                answerer=AnswererAgent(deps),
                tracer=tracer,
                settings=self.settings,
                input_assessment=assessment,
                known_secrets=self._known_secrets,
                use_judge=self._use_judge,
            ),
            self._checkpointer,
        )

    def _persist_request(
        self,
        *,
        request_id: str,
        trace_id: str,
        user_input: str,
        status: str,
        blocked: bool,
        block_reason: str | None,
        route: str | None,
        retry_count: int,
        latency_ms: float,
    ) -> None:
        # Only a digest of the input is stored. The full text is never needed to
        # operate the platform, and storing it would put arbitrary user content
        # in the database for no operational benefit.
        self.repository.save_request(
            RequestRecord(
                request_id=request_id,
                trace_id=trace_id,
                input_digest=content_digest(user_input),
                input_chars=len(user_input),
                status=status,
                blocked=blocked,
                block_reason=block_reason,
                route=route,
                retry_count=retry_count,
                latency_ms=latency_ms,
                provider=self.provider.name,
            )
        )

    # ---------------------------------------------------------------------- run

    def run(self, user_input: str, *, quota_key: str | None = None) -> RunResult:
        """Process one user request.

        ``quota_key`` names the rate-limit bucket this request spends from.
        ``None`` -- what the CLI, the dashboard and the evaluator pass, because
        they do not pass it at all -- is the global bucket, so their behaviour
        is byte-for-byte what it was. The HTTP boundary passes the authenticated
        principal, which is what stops one caller from denying every other by
        looping.

        The key is bound for the dynamic extent of the request rather than
        handed down as a parameter, because the policy engine consults the
        limiter from inside the graph, through a call site with no access to
        the caller. See ``security.rate_limit.quota_scope``.
        """
        with quota_scope(quota_key):
            return self._run(user_input)

    def _run(self, user_input: str) -> RunResult:
        request_id = new_request_id()
        trace_id = new_trace_id()
        started = time.perf_counter()

        tracer = Tracer(
            self.repository,
            request_id=request_id,
            trace_id=trace_id,
            max_payload_chars=self.settings.max_trace_payload_chars,
            known_secrets=self._known_secrets,
        )
        tracer.event(
            EventType.REQUEST_STARTED,
            status=EventStatus.INFO,
            payload={"input_chars": len(user_input), "provider": self.provider.name},
        )

        # 1. Rate limiting. Quota is consumed once per inbound request, from
        # the bucket this request was scoped to.
        limit = self.rate_limiter.acquire()
        # Then from the global bucket, when they are not the same one.
        #
        # Per-principal quota on its own would *remove* a control rather than
        # add one: with N principals the platform would admit N times the
        # configured per-minute limit, which is the shape of defect V2.5 just
        # closed for the budget. Both ceilings therefore bind.
        #
        # The cost, stated rather than hidden: when the global bucket refuses,
        # the principal's unit has already been spent. The error is one request
        # per rejection and it is in the conservative direction -- the platform
        # denies marginally early, never late.
        if limit.allowed and current_quota_key() != GLOBAL_KEY:
            limit = self.global_rate_limiter.acquire(GLOBAL_KEY)
        if not limit.allowed:
            tracer.event(
                EventType.RATE_LIMITED, status=EventStatus.BLOCKED, error=limit.reason
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._persist_request(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="rate_limited",
                blocked=True,
                block_reason=limit.reason,
                route=None,
                retry_count=0,
                latency_ms=elapsed,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="rate_limited",
                response=f"Request refused: {limit.reason}.",
                latency_ms=elapsed,
                provider=self.provider.name,
            )

        # 2. Input security.
        assessment = assess_input(user_input, max_chars=self.settings.max_input_chars)
        if not assessment.accepted:
            tracer.event(
                EventType.INPUT_REJECTED,
                status=EventStatus.BLOCKED,
                error=assessment.rejection_reason,
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._persist_request(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="rejected",
                blocked=True,
                block_reason=assessment.rejection_reason,
                route=None,
                retry_count=0,
                latency_ms=elapsed,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="rejected",
                response=f"Request refused: {assessment.rejection_reason}.",
                latency_ms=elapsed,
                provider=self.provider.name,
            )

        # Flagged, not blocked. Detection informs risk; it does not decide.
        # Injection signals and sensitive-content signals are reported as
        # separate events so neither metric inflates the other.
        injection_signals = [s for s in assessment.signal_ids if s.startswith("injection.")]
        sensitive_signals = [
            s for s in assessment.signal_ids if s.startswith(("input.pii.", "input.secret."))
        ]
        if injection_signals:
            tracer.event(
                EventType.INPUT_FLAGGED,
                status=EventStatus.INFO,
                payload={"signals": injection_signals},
            )
        if sensitive_signals:
            tracer.event(
                EventType.INPUT_SENSITIVE,
                status=EventStatus.INFO,
                payload={"signals": sensitive_signals},
            )

        # Start the wall-clock and per-request counters. Done after the cheap
        # entry-point checks so a rejected request costs no accounting state.
        self.resources.begin(request_id)

        graph = self._build_graph(tracer, assessment)
        state = initial_state(
            request_id=request_id, trace_id=trace_id, user_input=assessment.normalized_input
        )
        return self._invoke(
            graph,
            state,
            request_id=request_id,
            trace_id=trace_id,
            tracer=tracer,
            assessment=assessment,
            started=started,
            user_input=user_input,
        )

    def confirm(
        self, request_id: str, *, approved: bool, actor: str = "operator", source: str = "ui"
    ) -> RunResult:
        """Resume a suspended request with a human decision.

        ``approved`` is the entire content of the decision. Which action it
        applies to is determined by the platform from persisted state, not by
        anything the caller passes here.
        """
        # is_expired() distinguishes "aged out" from "never existed" so the
        # caller learns which happened. An expired confirmation is refused, not
        # executed: the state it was approved against may have moved on.
        expired = self._pending.is_expired(request_id)
        # Atomic consume. Two replicas racing to approve the same action both
        # call this; Redis serialises the get-and-delete, so exactly one is
        # handed the record and the other sees nothing. That is what makes a
        # confirmation single-use across the deployment rather than merely
        # within a process.
        record = self._pending.take(request_id)
        if record is None:
            if expired:
                return RunResult(
                    request_id=request_id,
                    trace_id="",
                    status="expired",
                    response=(
                        "That action expired before it was confirmed and was not "
                        f"carried out. Confirmations are valid for "
                        f"{self.settings.confirmation_ttl_seconds:.0f}s."
                    ),
                    provider=self.provider.name,
                )
            return RunResult(
                request_id=request_id,
                trace_id="",
                status="failed",
                response="There is no suspended action with that request id.",
                provider=self.provider.name,
            )

        pending = self._rehydrate(record)
        # Re-establish the request's accounting before anything can spend. On
        # the instance that suspended it this is a no-op; on any other it is
        # what stops a resumed run from starting its allowance again.
        self.resources.restore(
            request_id,
            llm_calls=record.llm_calls,
            tool_calls=record.tool_calls,
            tool_output_bytes=record.tool_output_bytes,
            elapsed_seconds=record.elapsed_seconds,
        )
        command = Command(resume={"approved": approved, "actor": actor, "source": source})
        return self._invoke(
            pending.graph,
            command,
            request_id=request_id,
            trace_id=pending.trace_id,
            tracer=pending.tracer,
            assessment=pending.input_assessment,
            started=pending.started_at,
            user_input=pending.user_input,
        )

    def _rehydrate(self, record: SuspendedRun) -> _ResumeContext:
        """Rebuild what is needed to resume, from the stored facts alone.

        Nothing here was persisted: the tracer is reconstructed from the ids,
        the assessment is recomputed from the input, and the graph is rebuilt
        from configuration. That is the point -- none of them *could* have been
        persisted without serialising a provider, a repository handle and a set
        of tool handlers along with them.

        ``started_at`` is translated back into this process's monotonic clock so
        the reported latency stays a duration rather than a comparison between
        two unrelated clocks.
        """
        tracer = Tracer(
            self.repository,
            request_id=record.request_id,
            trace_id=record.trace_id,
            max_payload_chars=self.settings.max_trace_payload_chars,
        )
        assessment = assess_input(
            record.user_input, max_chars=self.settings.max_input_chars
        )
        graph = self._build_graph(tracer, assessment)
        started = time.perf_counter() - max(0.0, time.time() - record.created_at)
        return _ResumeContext(
            tracer=tracer,
            input_assessment=assessment,
            graph=graph,
            started_at=started,
            user_input=record.user_input,
            trace_id=record.trace_id,
        )

    # ----------------------------------------------------------------- internals

    def _invoke(
        self,
        graph: Any,
        payload: Any,
        *,
        request_id: str,
        trace_id: str,
        tracer: Tracer,
        assessment: InputAssessment,
        started: float,
        user_input: str,
    ) -> RunResult:
        config = {
            "configurable": {"thread_id": request_id},
            "recursion_limit": self.settings.recursion_limit,
        }

        # Bind the provider for the retrieval layer, for this request only.
        #
        # In demo mode nothing is bound, and that is how lexical retrieval gets
        # selected: the strategy has no provider to embed with, so it does not
        # try. The alternative -- asking the stub to embed and catching its
        # refusal -- would turn a capability question into exception handling,
        # and would let a real outage look like demo mode.
        #
        # The ContextVar resets in retrieval_provider's finally, so an
        # exception cannot leave a provider bound to a context that has moved
        # on, and concurrent requests cannot see each other's.
        provider = None if self.settings.demo_mode else self.provider

        try:
            with retrieval_provider(provider):
                final = graph.invoke(payload, config=config)
        except BudgetExceededError as exc:
            # Raised before a model call that would breach a budget. The
            # request stops here; there is nothing to retry.
            tracer.event(
                EventType.REQUEST_FAILED, status=EventStatus.BLOCKED, error=str(exc)
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._finish(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="blocked",
                route=None,
                retry_count=0,
                latency_ms=elapsed,
                blocked=True,
                block_reason=str(exc),
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="blocked",
                response=f"Request refused: {exc}.",
                policy_decision={
                    "decision": "deny",
                    "risk_level": "low",
                    "reason": str(exc),
                    "rule_ids": ["PL007"],
                },
                latency_ms=elapsed,
                provider=self.provider.name,
            )
        except ResourceLimitExceeded as exc:
            # A hard ceiling in calls, seconds or bytes. Not a policy denial --
            # nothing was disallowed; the request ran out of a non-monetary
            # budget. No tool has executed past this point.
            tracer.event(
                EventType.REQUEST_FAILED,
                status=EventStatus.BLOCKED,
                error=exc.detail,
                payload={"control": "resource_limit", "limit": exc.limit},
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._finish(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="blocked",
                route=None,
                retry_count=0,
                latency_ms=elapsed,
                blocked=True,
                block_reason=exc.detail,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="blocked",
                response=f"Request stopped by a resource limit: {exc.detail}.",
                policy_decision={
                    "decision": "deny",
                    "risk_level": "low",
                    "reason": exc.detail,
                    "rule_ids": [],
                },
                errors=[exc.detail],
                latency_ms=elapsed,
                provider=self.provider.name,
            )
        except LLMError as exc:
            # Any provider failure -- unreachable, timed out, safety-blocked,
            # truncated, empty. These escape the graph, so without this handler
            # a live provider hiccup would surface as a raw traceback with an
            # unsanitised message. The request ends as a safe, recorded failure
            # and, critically, no tool has run: the provider fails before an
            # action is ever proposed.
            safe_detail, _ = sanitize_text(
                str(exc), max_chars=300, known_secrets=self._known_secrets
            )
            tracer.event(
                EventType.REQUEST_FAILED,
                status=EventStatus.FAILURE,
                error=safe_detail,
                payload={"control": "provider", "error_type": type(exc).__name__},
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._finish(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="failed",
                route=None,
                retry_count=0,
                latency_ms=elapsed,
                blocked=False,
                block_reason=None,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="failed",
                response=(
                    "The request could not be completed because the language model "
                    f"provider failed: {safe_detail}"
                ),
                errors=[safe_detail],
                latency_ms=elapsed,
                provider=self.provider.name,
            )
        except GraphRecursionError as exc:
            # The graph's own loop ceiling. Reaching it is a bug or an attack,
            # so it terminates the request rather than being retried.
            tracer.event(
                EventType.REQUEST_FAILED, status=EventStatus.FAILURE, error=str(exc)
            )
            elapsed = (time.perf_counter() - started) * 1000.0
            self._finish(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="failed",
                route=None,
                retry_count=self.settings.max_retries,
                latency_ms=elapsed,
                blocked=False,
                block_reason=None,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="failed",
                response="The request was stopped after exceeding the orchestration step limit.",
                latency_ms=elapsed,
                provider=self.provider.name,
            )

        interrupts = final.get("__interrupt__") or ()
        if interrupts:
            stored = self._pending.put(
                request_id,
                _suspended_record(
                    request_id=request_id,
                    trace_id=trace_id,
                    user_input=user_input,
                    usage=self.resources.usage(request_id),
                    now=time.perf_counter(),
                ),
            )
            if not stored:
                # The store is full of live suspensions. Refusing the new one
                # fails closed: nothing executes. Evicting an existing entry
                # would let an attacker push a legitimate pending action out.
                tracer.event(
                    EventType.RESOURCE_LIMIT,
                    status=EventStatus.BLOCKED,
                    error="pending confirmation store is full",
                    payload={"limit": "pending_confirmations"},
                )
                elapsed = (time.perf_counter() - started) * 1000.0
                self._finish(
                    request_id=request_id,
                    trace_id=trace_id,
                    user_input=user_input,
                    status="failed",
                    route=final.get("route"),
                    retry_count=0,
                    latency_ms=elapsed,
                    blocked=True,
                    block_reason="pending confirmation store is full",
                )
                return RunResult(
                    request_id=request_id,
                    trace_id=trace_id,
                    status="failed",
                    response=(
                        "Too many actions are already awaiting confirmation. "
                        "This request was not carried out."
                    ),
                    latency_ms=elapsed,
                    provider=self.provider.name,
                )
            value = getattr(interrupts[0], "value", {}) or {}
            elapsed = (time.perf_counter() - started) * 1000.0
            self._persist_request(
                request_id=request_id,
                trace_id=trace_id,
                user_input=user_input,
                status="awaiting_confirmation",
                blocked=False,
                block_reason=None,
                route=final.get("route"),
                retry_count=final.get("retry_count", 0),
                latency_ms=elapsed,
            )
            return RunResult(
                request_id=request_id,
                trace_id=trace_id,
                status="awaiting_confirmation",
                response=(
                    f"This action needs approval before it can run: "
                    f"{value.get('reason', 'confirmation required')}"
                ),
                route=final.get("route"),
                awaiting_confirmation=ConfirmationRequest(
                    request_id=request_id,
                    tool=str(value.get("tool", "")),
                    arguments=dict(value.get("arguments") or {}),
                    risk_level=str(value.get("risk_level", "")),
                    reason=str(value.get("reason", "")),
                ),
                policy_decision=final.get("policy_decision"),
                latency_ms=elapsed,
                provider=self.provider.name,
            )

        self._pending.discard(request_id)
        elapsed = (time.perf_counter() - started) * 1000.0
        status = final.get("status", "success")

        tracer.event(
            EventType.REQUEST_COMPLETED
            if status == "success"
            else EventType.REQUEST_FAILED,
            status=EventStatus.SUCCESS if status == "success" else EventStatus.FAILURE,
            latency_ms=elapsed,
            payload={"status": status, "route": final.get("route")},
        )

        self._finish(
            request_id=request_id,
            trace_id=trace_id,
            user_input=user_input,
            status=status,
            route=final.get("route"),
            retry_count=final.get("retry_count", 0),
            latency_ms=elapsed,
            blocked=status == "blocked",
            block_reason=(final.get("policy_decision") or {}).get("reason")
            if status == "blocked"
            else None,
        )

        return RunResult(
            request_id=request_id,
            trace_id=trace_id,
            status=status,
            response=final.get("final_response", ""),
            route=final.get("route"),
            policy_decision=final.get("policy_decision"),
            tool_result=final.get("tool_result"),
            validation=final.get("validation"),
            retry_count=final.get("retry_count", 0),
            errors=list(final.get("errors") or []),
            latency_ms=elapsed,
            provider=self.provider.name,
        )

    def _finish(
        self,
        *,
        request_id: str,
        trace_id: str,
        user_input: str,
        status: str,
        route: str | None,
        retry_count: int,
        latency_ms: float,
        blocked: bool,
        block_reason: str | None,
    ) -> None:
        self.budget_guard.release(request_id)
        self.resources.release(request_id)
        self._persist_request(
            request_id=request_id,
            trace_id=trace_id,
            user_input=user_input,
            status=status,
            blocked=blocked,
            block_reason=block_reason,
            route=route,
            retry_count=retry_count,
            latency_ms=latency_ms,
        )
