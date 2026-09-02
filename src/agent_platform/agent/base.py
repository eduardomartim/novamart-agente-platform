"""Shared agent machinery.

Every model call in the platform goes through :meth:`BaseAgent.generate_json`
or :meth:`BaseAgent.generate_text`, which is what guarantees that cost tracking
and tracing cannot be forgotten at an individual call site.
"""

from __future__ import annotations

import json
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..config import Settings
from ..cost.tracker import BudgetExceededError, CostTracker
from ..guardrails.egress import prepare_for_egress
from ..guardrails.input import InputAssessment
from ..guardrails.policy import Confirmation, PolicyContext
from ..llm.circuit import CircuitBreaker, CircuitOpenError
from ..llm.provider import (
    LLMError,
    LLMProvider,
    LLMResponse,
    LLMResponseError,
    LLMUnavailableError,
    Purpose,
    extract_json,
)
from ..models import AgentName, ProposedAction
from ..observability.events import EventStatus, EventType
from ..observability.tracing import Tracer
from ..security.resources import ResourceGuard, ResourceLimitExceeded
from ..tools.gateway import GatewayResult, ToolGateway
from ..tools.registry import ToolRegistry

#: Untrusted content is fenced with an explicit marker before being placed in a
#: prompt. This raises the bar for casual injection, but it is *not* a security
#: control: the platform's actual protection is that model output can only ever
#: become a *proposal*, which the policy engine then judges on its merits.
UNTRUSTED_OPEN = "<<<UNTRUSTED_USER_CONTENT"
UNTRUSTED_CLOSE = "UNTRUSTED_USER_CONTENT>>>"


#: Tool output gets its own marker. It is untrusted for the same reason user
#: input is -- in a real deployment it carries ticket bodies, CRM notes and
#: scraped pages -- but labelling it accurately tells the model *why* it is
#: untrusted, and keeps the two channels distinguishable in a trace.
UNTRUSTED_TOOL_OPEN = "<<<UNTRUSTED_TOOL_OUTPUT"
UNTRUSTED_TOOL_CLOSE = "UNTRUSTED_TOOL_OUTPUT>>>"

_ALL_MARKERS = (
    UNTRUSTED_OPEN,
    UNTRUSTED_CLOSE,
    UNTRUSTED_TOOL_OPEN,
    UNTRUSTED_TOOL_CLOSE,
)


def _strip_markers(content: str) -> str:
    """Remove every fence marker, repeatedly, until none can reappear.

    A single pass of ``str.replace`` is not enough, and the reason is worth
    recording. Removing the inner occurrence from::

        <<<UNTRUSTED_TOOL_OUT<<<UNTRUSTED_TOOL_OUTPUTPUT

    leaves ``<<<UNTRUSTED_TOOL_OUT`` and ``PUT`` adjacent, and they join into a
    valid ``<<<UNTRUSTED_TOOL_OUTPUT``. The stripper handed the attacker the
    marker it had just deleted. The same trick works on all four markers.

    Looping to a fixed point closes it. Termination is guaranteed because each
    round either removes at least one marker -- strictly shortening the string
    -- or changes nothing and stops.
    """
    previous = None
    while previous != content:
        previous = content
        for marker in _ALL_MARKERS:
            content = content.replace(marker, "")
    return content


def fence_untrusted(content: str) -> str:
    """Wrap user-supplied text so the model can tell data from instructions."""
    return f"{UNTRUSTED_OPEN}\n{_strip_markers(content)}\n{UNTRUSTED_CLOSE}"


def fence_tool_output(content: str) -> str:
    """Wrap tool output, which is attacker-influenceable like user input.

    F13: the executor prompt fenced the user's sentence while interpolating a
    tool result beside it as trusted narration. A poisoned knowledge-base
    article therefore arrived on the *more* privileged of the two channels.

    This is defence in depth. The policy engine remains the only authority, and
    a model that ignores the fence still cannot reach a tool it is not
    authorised for.
    """
    return f"{UNTRUSTED_TOOL_OPEN}\n{_strip_markers(content)}\n{UNTRUSTED_TOOL_CLOSE}"


#: Characters allowed in a label the prompt presents as *structure* -- a tool
#: name, a document identifier. Anything else is dropped rather than escaped:
#: these labels sit outside the fences, so a label that could carry arbitrary
#: text would be a hole straight through the fencing.
_LABEL_SAFE = re.compile(r"[^A-Za-z0-9_.\-]")


def _safe_label(value: object) -> str:
    """Reduce *value* to something that cannot forge prompt structure."""
    return _LABEL_SAFE.sub("", str(value))[:64]


def _retrieved_documents(data: object) -> list[dict[str, Any]]:
    """The document-shaped entries in a tool result, if it has any.

    Shape-detected rather than keyed off the tool name, because the caller
    passes whatever the gateway returned and the wrapper keys differ between
    the researcher's context items and a raw tool result.
    """
    if not isinstance(data, dict):
        return []
    results = data.get("results")
    if not isinstance(results, list):
        return []
    return [
        entry
        for entry in results
        if isinstance(entry, dict) and "title" in entry and "body" in entry
    ]


def fence_context(context: list[dict[str, Any]], *, max_chars: int | None = None) -> str:
    """Render gathered context with every untrusted field in its own fence.

    Fencing the whole block as one JSON blob puts the untrusted text inside a
    fence, which is the important part, but it also asks the model to hold a
    single boundary in mind across the entire payload. Retrieved documents make
    that worse: a knowledge-base article is attacker-influenceable in *both* its
    title and its body, and a title reading "end of context, new instruction:"
    is more plausible narration than the same words buried in a body.

    So each untrusted field is fenced individually, and the only text left
    outside a fence is structure this code produced: the source tool's name and
    the document identifier, both reduced by :func:`_safe_label` to characters
    that cannot forge a marker or a heading.

    This remains a labelling convention, not an authorisation boundary. It
    separates untrusted content from the instructions around it; what a model
    may actually *do* with either is decided by the policy engine, which never
    reads a prompt.

    ``max_chars`` bounds each individual field *before* it is fenced. Trimming
    the assembled block instead would be a bug: the cut can land inside a
    closing marker and hand the model a fence that never closes.
    """
    budget = max_chars if max_chars and max_chars > 0 else None

    def clip(text: str) -> str:
        return text[:budget] if budget else text

    blocks: list[str] = []

    for item in context:
        source = _safe_label(item.get("source") or item.get("tool") or "unknown")
        data = item.get("data", item.get("output"))
        documents = _retrieved_documents(data)

        if not documents:
            serialised = clip(json.dumps(data, default=str))
            blocks.append(f"[source: {source}]\n{fence_tool_output(serialised)}")
            continue

        lines = [f"[source: {source}]"]
        for document in documents:
            identifier = _safe_label(document.get("doc_id", "")) or "unidentified"
            lines.append(f"DOCUMENT {identifier}")
            lines.append(f"title: {fence_tool_output(clip(str(document.get('title', ''))))}")
            lines.append(f"body: {fence_tool_output(clip(str(document.get('body', ''))))}")
        blocks.append("\n".join(lines))

    return "\n\n".join(blocks)


def parse_proposed_arguments(payload: dict[str, Any]) -> dict[str, Any]:
    """Read tool arguments from a model payload, accepting either shape.

    Structured output constrains a model to exactly the declared schema, and a
    bare ``{"type": "OBJECT"}`` with no properties constrains it to an *empty*
    object. Verified against the live API: every proposal came back as
    ``"arguments": {}``, so the tool was chosen correctly and then refused by
    schema validation for missing required fields. Declaring the arguments as a
    JSON **string** is what lets a real model actually emit them.

    Both shapes are accepted -- ``arguments_json`` from a schema-constrained
    provider, ``arguments`` from the deterministic stub, which ignores schemas.
    Anything unparseable yields an empty dict, which the policy engine then
    rejects on the tool's real schema rather than executing with partial input.
    """
    raw = payload.get("arguments_json")
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    arguments = payload.get("arguments")
    return dict(arguments) if isinstance(arguments, dict) else {}


@dataclass(slots=True)
class AgentDeps:
    """Collaborators shared by every agent.

    Note what is absent: no agent receives tool callables. It gets the registry
    (metadata only) and the gateway (which requires a policy decision), so
    "propose, never execute" is a property of what an agent can reach, not of
    how carefully it behaves.
    """

    provider: LLMProvider
    tracer: Tracer
    registry: ToolRegistry
    cost_tracker: CostTracker
    settings: Settings
    gateway: ToolGateway
    #: Values known to be secret, stripped from prompts and traces by value.
    known_secrets: tuple[str, ...] = ()
    #: Hard per-request resource accounting. Never an authorisation input.
    resources: ResourceGuard | None = None
    #: Trips after repeated provider failures. Can only prevent a call.
    circuit: CircuitBreaker | None = None


class BaseAgent(ABC):
    """Common behaviour: prompt assembly, model calls, cost and tracing."""

    def __init__(self, deps: AgentDeps) -> None:
        self.deps = deps

    @property
    @abstractmethod
    def name(self) -> AgentName: ...

    @property
    @abstractmethod
    def system_prompt(self) -> str: ...

    # ------------------------------------------------------------- model calls

    def _generate(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        response_schema: dict[str, Any] | None = None,
    ) -> LLMResponse:
        # Everything from here to the provider call is preflight: egress
        # scrubbing, cost authorisation, the per-request resource ceiling and
        # the circuit breaker. None of it was timed, and the gap it hides is
        # not small -- two recorded requests spent thirty-odd seconds between
        # `agent_started` and `llm_call` while the model call itself took under
        # two seconds, and no event accounted for the difference. Measuring it
        # is the only way to find out which of these four is responsible.
        preflight_started = time.perf_counter()

        # Egress control runs first: nothing leaves the process, and nothing is
        # even costed, until credentials have been stripped out of the prompt.
        egress = prepare_for_egress(prompt, known_secrets=self.deps.known_secrets)
        if egress.redacted:
            self.deps.tracer.event(
                EventType.PROMPT_REDACTED,
                status=EventStatus.INFO,
                agent=self.name,
                payload={"purpose": purpose.value, **egress.metadata},
            )
        prompt = egress.text

        # Budget is enforced here, before the call, because this is where money
        # is actually spent. A denial ends the request rather than being
        # retried; retrying a budget refusal cannot succeed.
        authorization = self.deps.cost_tracker.authorize_call(
            model=self.deps.provider.model,
            prompt=prompt,
            system=self.system_prompt,
            request_id=self.deps.tracer.request_id,
        )
        if not authorization.allowed:
            self.deps.tracer.event(
                EventType.REQUEST_FAILED,
                status=EventStatus.BLOCKED,
                agent=self.name,
                error=authorization.reason,
                payload={"control": "budget", "purpose": purpose.value},
            )
            raise BudgetExceededError(authorization)

        # Hard resource ceiling, in calls and seconds rather than dollars.
        # Charged *before* the call, so the call that would exceed the limit is
        # never made. This is a stop, not a permission: clearing it grants
        # nothing, and the policy engine still decides every action.
        if self.deps.resources is not None:
            try:
                self.deps.resources.charge_llm_call(self.deps.tracer.request_id)
            except ResourceLimitExceeded as exc:
                self.deps.tracer.event(
                    EventType.RESOURCE_LIMIT,
                    status=EventStatus.BLOCKED,
                    agent=self.name,
                    error=exc.detail,
                    payload={"limit": exc.limit, "purpose": purpose.value},
                )
                raise

        # Circuit breaker. Prevents a call against a provider already known to
        # be failing; it can never authorise one.
        if self.deps.circuit is not None:
            try:
                self.deps.circuit.before_call()
            except CircuitOpenError as exc:
                self.deps.tracer.event(
                    EventType.CIRCUIT_OPEN,
                    status=EventStatus.BLOCKED,
                    agent=self.name,
                    error=str(exc),
                    payload={
                        "retry_after_seconds": exc.retry_after_seconds,
                        "consecutive_failures": exc.failures,
                    },
                )
                raise LLMUnavailableError(str(exc)) from exc

        preflight_ms = (time.perf_counter() - preflight_started) * 1000.0

        # The provider is third-party code and may raise anything its SDK
        # happens to raise. Normalising unexpected exceptions into a typed
        # platform error here -- at the trust boundary -- keeps provider
        # misbehaviour uniform without a blanket catch further up that would
        # also swallow genuine bugs in our own graph nodes.
        try:
            response = self.deps.provider.generate(
                prompt,
                purpose=purpose,
                system=self.system_prompt,
                response_schema=response_schema,
                temperature=0.0,
                timeout=self.deps.settings.llm_timeout,
            )
        except LLMError as exc:
            if self.deps.circuit is not None:
                self.deps.circuit.record_failure()
            self._record_llm_failure(purpose, exc)
            raise
        except Exception as exc:
            if self.deps.circuit is not None:
                self.deps.circuit.record_failure()
            self._record_llm_failure(purpose, exc)
            raise LLMUnavailableError(
                f"provider raised an unexpected {type(exc).__name__}: {exc}"
            ) from exc

        if self.deps.circuit is not None:
            self.deps.circuit.record_success()

        self._record_retries(purpose, response)

        tracked = self.deps.cost_tracker.track(
            response,
            request_id=self.deps.tracer.request_id,
            trace_id=self.deps.tracer.trace_id,
            agent=self.name.value,
        )

        # Only metadata is recorded. The prompt and completion text are never
        # written to the trace: they are the two places model reasoning and user
        # content would otherwise leak into storage.
        self.deps.tracer.event(
            EventType.LLM_CALL,
            status=EventStatus.SUCCESS,
            agent=self.name,
            latency_ms=response.latency_ms,
            payload={
                "provider": response.provider,
                "model": response.model,
                "purpose": purpose.value,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "estimated_cost_usd": str(tracked.cost_usd),
                "tokens_estimated": response.tokens_estimated,
                # What the caller waited for that is not the model: our own
                # preflight, and the budget ledger the provider charged before
                # its stopwatch started. Both are always present, including as
                # zero, because "we measured it and it was nothing" is a
                # different statement from "we did not measure it".
                "preflight_ms": round(preflight_ms, 3),
                "budget_wait_ms": round(response.budget_wait_ms, 3),
                # Present only when it says something. `latency_ms` above is the
                # attempt that worked; these two are what the caller waited.
                **(
                    {
                        "attempts": response.attempts,
                        "total_elapsed_ms": round(response.total_elapsed_ms, 3),
                    }
                    if response.retried
                    else {}
                ),
            },
        )
        return response

    def _record_retries(self, purpose: Purpose, response: LLMResponse) -> None:
        """One event per attempt the provider lost before the one that worked.

        The provider retries internally and reports how many times; it has no
        tracer and is not given one, because a client that writes to the event
        stream is a client that knows about the platform. It reports, this
        records -- which keeps the boundary and still makes the time visible.

        ``retry_reasons`` has already been through the provider's scrubber, so
        the configured credential cannot appear here even when the SDK echoes
        the request back in an error.
        """
        if not response.retried:
            return
        reasons = response.retry_reasons or ()
        for index in range(1, response.attempts):
            self.deps.tracer.event(
                EventType.LLM_RETRY,
                status=EventStatus.FAILURE,
                agent=self.name,
                error=reasons[index - 1] if index <= len(reasons) else None,
                payload={
                    "purpose": purpose.value,
                    "attempt": index,
                    "of": response.attempts,
                    "provider": response.provider,
                    "model": response.model,
                },
            )

    def _record_llm_failure(self, purpose: Purpose, exc: BaseException) -> None:
        """Every attempt failed. Previously this left nothing in the trace.

        The physical calls were still charged to the ledger, so a request could
        spend six calls and show `llm_calls: 0` -- the accounting and the trace
        disagreeing about the same event. The message is the provider's own,
        already scrubbed by it before being raised.
        """
        self.deps.tracer.event(
            EventType.LLM_FAILED,
            status=EventStatus.FAILURE,
            agent=self.name,
            error=str(exc)[:300],
            payload={"purpose": purpose.value, "error_type": type(exc).__name__},
        )

    def generate_text(self, prompt: str, *, purpose: Purpose) -> tuple[str, LLMResponse]:
        response = self._generate(prompt, purpose=purpose)
        return response.text, response

    def generate_json(
        self,
        prompt: str,
        *,
        purpose: Purpose,
        response_schema: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], LLMResponse]:
        """Call the model and parse a JSON object from the result.

        A malformed response raises :class:`LLMResponseError`, which callers
        handle as a normal failure path rather than letting it escape as a
        crash.
        """
        response = self._generate(prompt, purpose=purpose, response_schema=response_schema)
        try:
            return extract_json(response.text), response
        except LLMResponseError:
            self.deps.tracer.event(
                EventType.ERROR,
                status=EventStatus.FAILURE,
                agent=self.name,
                error="model response was not valid JSON",
                payload={"purpose": purpose.value, "provider": response.provider},
            )
            raise

    # ----------------------------------------------------------- tool proposals

    def submit_action(
        self,
        action: ProposedAction,
        *,
        input_assessment: InputAssessment | None = None,
        confirmation: Confirmation | None = None,
    ) -> GatewayResult:
        """Hand a proposed action to the gateway for evaluation.

        This is the only route from an agent to a tool. The agent does not learn
        whether the action will be permitted until the gateway answers.
        """
        context = PolicyContext(
            request_id=self.deps.tracer.request_id,
            agent=self.name,
            action=action,
            input_assessment=input_assessment,
            confirmation=confirmation,
        )
        return self.deps.gateway.submit(
            action, agent=self.name, context=context, tracer=self.deps.tracer
        )
