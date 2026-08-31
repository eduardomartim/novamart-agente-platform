"""The tool gateway: the only component that executes tools.

The gateway does not decide anything. It asks the policy engine, records the
verdict, and either performs the call or does not. Keeping decision and
enforcement in separate objects is what allows the security tests to assert
that no path reaches a handler without a matching ALLOW.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from contextvars import copy_context
from dataclasses import dataclass
from typing import Any

from ..guardrails.policy import EvaluationOutcome, PolicyContext, PolicyEngine
from ..models import AgentName, Decision, PolicyDecision, ProposedAction, ToolResult
from ..observability.events import EventStatus, EventType
from ..observability.tracing import Tracer
from ..security.resources import ResourceGuard, ResourceLimitExceeded
from ..security.sanitization import sanitize_text
from .execution import gateway_execution
from .models import ToolDefinition
from .registry import ToolRegistry

#: Tools that must run in this process even when a tool transport is configured.
#:
#: ``search`` is here for a reason worth stating rather than discovering. Hybrid
#: retrieval is selected by a provider bound to the *request* through a
#: ContextVar, and a separate tool-server process has no such binding: the same
#: search would silently fall back to lexical BM25 and return a worse answer
#: with nothing to say it had. Silently degraded retrieval that looks like
#: normal retrieval is the exact failure 7G.0 exists to prevent.
#:
#: The alternative -- shipping the provider credential to the tool server so it
#: could embed for itself -- is worse, and is forbidden outright: that process
#: runs simulated tools and has no business holding a real key.
#:
#: This is also the honest boundary. ``search`` is not an external system; it is
#: the platform's own knowledge. The tools that belong behind MCP are the ones
#: standing in for systems the platform does not own.
IN_PROCESS_ONLY: frozenset[str] = frozenset({"search"})


@dataclass(slots=True)
class GatewayResult:
    """Outcome of submitting an action to the gateway."""

    decision: PolicyDecision
    result: ToolResult
    fingerprint: str
    tool: ToolDefinition | None = None

    @property
    def executed(self) -> bool:
        return self.result.status not in {"denied", "invalid_arguments"}

    @property
    def awaiting_confirmation(self) -> bool:
        return self.decision.decision is Decision.REQUIRE_CONFIRMATION


class ToolGateway:
    """Mediates every tool invocation in the platform."""

    def __init__(
        self,
        registry: ToolRegistry,
        policy_engine: PolicyEngine,
        *,
        default_timeout: float = 10.0,
        resources: ResourceGuard | None = None,
        transport: Any = None,
        grant_secret: str | None = None,
    ) -> None:
        self._registry = registry
        self._policy = policy_engine
        self._default_timeout = default_timeout
        #: Resource accounting. A precondition on execution, never a decision:
        #: the gateway still asks the policy engine for every action.
        self._resources = resources
        # When set, tools run in a separate process behind a verified execution
        # grant instead of being called directly. None means the in-process
        # path, which is the default and what every earlier version did.
        self._transport = transport
        self._grant_secret = grant_secret
        # A dedicated executor gives tool calls a timeout without blocking the
        # caller indefinitely. See the note in _invoke about what this can and
        # cannot guarantee.
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="tool-gateway")

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        # Without this the tool-server subprocess outlives the process that
        # launched it.
        if self._transport is not None:
            self._transport.close()

    # ----------------------------------------------------------------- execution

    def _invoke(
        self, tool: ToolDefinition, arguments: dict[str, Any], execution_id: str = ""
    ) -> tuple[ToolResult, float]:
        """Run a tool handler under a timeout.

        Honest limitation: Python cannot forcibly cancel a running thread, so a
        handler that ignores its timeout keeps running in the background even
        though the gateway has stopped waiting and reported a timeout. That is
        acceptable here because every registered tool is an in-memory
        simulation. A production gateway would run tools out-of-process, where
        the timeout can actually terminate the work.
        """
        started = time.perf_counter()
        # gateway_execution() is what makes require_gateway() inside each tool
        # pass. It is opened here and nowhere else in the codebase.
        # Carry the caller's context onto the worker thread.
        #
        # A ``ThreadPoolExecutor`` worker starts with a fresh, empty context:
        # ContextVars propagate into asyncio tasks but *not* across threads.
        # Without this, anything the request scoped with a ContextVar simply
        # is not there when the handler runs, and the handler cannot tell the
        # difference between "not bound" and "bound but unreachable".
        #
        # Found by the 7G live gate: the retrieval provider was bound in the
        # calling thread and invisible here, so hybrid retrieval silently ran
        # as lexical. Twenty-six offline tests missed it because they called
        # the retriever directly rather than through this executor.
        #
        # Deliberately generic. The gateway copies the whole context and knows
        # nothing about what is in it; ``gateway_execution()`` still opens
        # inside ``_run_guarded``, and because the copy is a separate context
        # its token cannot leak back to the caller.
        future = self._executor.submit(
            copy_context().run, self._dispatch, tool, arguments, execution_id
        )
        try:
            output = future.result(timeout=tool.timeout_seconds or self._default_timeout)
        except FutureTimeoutError:
            elapsed = (time.perf_counter() - started) * 1000.0
            return (
                ToolResult(
                    tool=tool.name,
                    status="timeout",
                    error=f"tool exceeded its {tool.timeout_seconds}s timeout",
                    latency_ms=elapsed,
                    simulated=tool.simulated,
                ),
                elapsed,
            )
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000.0
            # Sanitise at the source. The response boundary redacts credentials
            # too, but relying on a single downstream gate means any other
            # consumer of ToolResult.error receives raw exception text -- which
            # can carry credentials, local paths, or internal structure. The
            # exception *type* is preserved because it is what a developer
            # actually needs.
            detail, _ = sanitize_text(str(exc), max_chars=200)
            return (
                ToolResult(
                    tool=tool.name,
                    status="error",
                    error=f"{type(exc).__name__}: {detail}",
                    latency_ms=elapsed,
                    simulated=tool.simulated,
                ),
                elapsed,
            )

        elapsed = (time.perf_counter() - started) * 1000.0
        return (
            ToolResult(
                tool=tool.name,
                status="success",
                output=output,
                latency_ms=elapsed,
                simulated=tool.simulated,
            ),
            elapsed,
        )

    def _dispatch(
        self, tool: ToolDefinition, arguments: dict[str, Any], execution_id: str = ""
    ) -> Any:
        """Run the tool here, or ask the tool server to run it.

        The decision is made once, by configuration. Everything above this
        point -- policy, capabilities, resource accounting -- has already
        happened and is identical either way: the transport changes where a
        permitted action executes, never whether it is permitted.
        """
        if self._transport is None or tool.name in IN_PROCESS_ONLY:
            return self._run_guarded(tool, arguments)

        from ..execution import grant as grants

        # Issued only now, from the *validated* arguments, so what the grant
        # binds is what will actually run rather than what an agent asked for.
        token, _ = grants.issue(
            secret=self._grant_secret,
            execution_id=execution_id or "unattributed",
            tool=tool.name,
            arguments=arguments,
        )
        return self._transport.call(
            tool.name, {"grant": token, "arguments": arguments}
        )

    @staticmethod
    def _run_guarded(tool: ToolDefinition, arguments: dict[str, Any]) -> Any:
        with gateway_execution():
            return tool.handler(**arguments)

    def _bound_output(
        self, result: ToolResult, *, tracer: Tracer, agent: AgentName
    ) -> ToolResult:
        """Replace an oversized tool result before it can propagate.

        ``max_context_items`` bounds how many results reach the context, not how
        large each one is, so a single huge result would otherwise inflate every
        subsequent prompt and every persisted trace.

        The payload is replaced rather than truncated: half a JSON document is
        not a smaller document, it is a corrupt one, and feeding a model a
        malformed fragment is worse than telling it plainly that the result was
        too large.
        """
        if self._resources is None or not result.ok:
            return result

        try:
            size = len(json.dumps(result.output, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            size = self._resources.limits.max_tool_output_bytes + 1

        if self._resources.account_tool_output(tracer.request_id, size):
            return result

        limit = self._resources.limits.max_tool_output_bytes
        tracer.event(
            EventType.OUTPUT_TRUNCATED,
            status=EventStatus.BLOCKED,
            agent=agent,
            tool=result.tool,
            payload={"size_bytes": size, "limit_bytes": limit},
        )
        return ToolResult(
            tool=result.tool,
            status="error",
            output=None,
            error=(
                f"tool result was {size} bytes, over the {limit}-byte ceiling; "
                "the payload was discarded rather than truncated"
            ),
            latency_ms=result.latency_ms,
            simulated=result.simulated,
        )

    # -------------------------------------------------------------------- entry

    def submit(
        self,
        action: ProposedAction,
        *,
        agent: AgentName,
        context: PolicyContext,
        tracer: Tracer,
    ) -> GatewayResult:
        """Submit a proposed action for evaluation and, if permitted, execution."""
        tracer.event(
            EventType.ACTION_PROPOSED,
            status=EventStatus.INFO,
            agent=agent,
            tool=action.tool,
            payload={"arguments": action.arguments},
        )

        outcome: EvaluationOutcome = self._policy.evaluate(context)
        decision = outcome.decision
        tracer.policy_event(decision, agent=agent, tool=action.tool)

        if decision.decision is Decision.DENY:
            return GatewayResult(
                decision=decision,
                result=ToolResult(
                    tool=action.tool,
                    status="denied",
                    error=decision.reason,
                    simulated=True,
                ),
                fingerprint=outcome.fingerprint,
                tool=outcome.tool,
            )

        if decision.decision is Decision.REQUIRE_CONFIRMATION:
            tracer.event(
                EventType.CONFIRMATION_REQUESTED,
                status=EventStatus.PENDING,
                agent=agent,
                tool=action.tool,
                policy_decision=decision.decision.value,
                risk_level=decision.risk_level.value,
                payload={
                    "reason": decision.reason,
                    "fingerprint": outcome.fingerprint,
                },
            )
            return GatewayResult(
                decision=decision,
                result=ToolResult(
                    tool=action.tool,
                    status="denied",
                    error="awaiting confirmation",
                    simulated=True,
                ),
                fingerprint=outcome.fingerprint,
                tool=outcome.tool,
            )

        # ALLOW. Both of these are guaranteed non-None by the engine on an
        # ALLOW path; assert rather than silently coercing, so a future change
        # to the engine fails loudly here instead of executing with empty args.
        assert outcome.tool is not None
        assert outcome.validated_arguments is not None

        # Hard tool-call ceiling and request deadline. Charged after the policy
        # has already permitted the action, so this can only ever subtract from
        # what policy allowed -- it can never turn a DENY into an execution.
        if self._resources is not None:
            try:
                self._resources.charge_tool_call(tracer.request_id)
            except ResourceLimitExceeded as exc:
                tracer.event(
                    EventType.RESOURCE_LIMIT,
                    status=EventStatus.BLOCKED,
                    agent=agent,
                    tool=action.tool,
                    error=exc.detail,
                    payload={"limit": exc.limit},
                )
                raise

        result, elapsed = self._invoke(
            outcome.tool, outcome.validated_arguments, tracer.request_id
        )
        result = self._bound_output(result, tracer=tracer, agent=agent)

        tracer.event(
            EventType.TOOL_CALL,
            status=EventStatus.SUCCESS if result.ok else EventStatus.FAILURE,
            agent=agent,
            tool=outcome.tool.name,
            policy_decision=decision.decision.value,
            risk_level=decision.risk_level.value,
            latency_ms=elapsed,
            error=result.error,
            payload={
                "arguments": outcome.validated_arguments,
                "output": result.output,
                "simulated": result.simulated,
            },
        )

        return GatewayResult(
            decision=decision,
            result=result,
            fingerprint=outcome.fingerprint,
            tool=outcome.tool,
        )
