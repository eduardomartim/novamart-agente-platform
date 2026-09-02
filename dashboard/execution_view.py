"""Turn a recorded event stream into an honest picture of what happened.

The governing rule: **never render a step as executed unless the stream says it
executed.** A visualisation that shows an agent succeeding when it never ran is
worse than none at all -- it is a confident claim about the one thing this
project exists to demonstrate.

That is not a hypothetical failure mode. The panel this replaces marked an
agent SUCCESS on an ``agent_completed`` event which, although defined in the
enum, is *never emitted anywhere in the platform*. Every agent therefore showed
as permanently RUNNING, including long after the request had finished. It
looked reasonable, which is why it lasted.

So every state below is keyed on evidence that genuinely appears in the stream,
established by running the platform and reading what it recorded:

    router      agent_started -> llm_call -> route_selected
    researcher  agent_started -> llm_call -> action_proposed -> policy_decision
                              -> tool_call
    executor    same shape, reached only when an action is required
    validator   agent_started -> llm_call -> validation
                (skipped entirely on a read-only route)

Absence is information. An agent with no ``agent_started`` did not run, and is
reported as NOT REACHED rather than quietly omitted or shown as idle-success.

This module is deliberately free of Streamlit so it can be tested against real
event streams rather than against a renderer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from i18n import t

AGENT_ORDER = ("router", "researcher", "executor", "validator")


class State(Enum):
    """What actually happened -- with 'did not happen' as a first-class answer."""

    NOT_REACHED = "NOT REACHED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    SUCCESS = "SUCCESS"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    #: The platform declined to answer because nothing it has could. Not a
    #: block: no control refused anything and no rule fired. It used to render
    #: as BLOCKED, so the honest refusal arrived wearing the colour of a
    #: security denial and contradicted the message beside it.
    OUT_OF_SCOPE = "SEM RESPOSTA"

    def __str__(self) -> str:  # pragma: no cover - display helper
        return self.value


@dataclass(frozen=True, slots=True)
class Step:
    sequence: int
    label: str
    state: State
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class AgentActivity:
    name: str
    state: State
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ToolActivity:
    name: str
    risk: str | None
    proposed_by: str | None
    decision: str | None
    execution: State
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionView:
    steps: tuple[Step, ...]
    agents: tuple[AgentActivity, ...]
    tools: tuple[ToolActivity, ...]
    policy_decision: str | None
    #: The tool ``policy_decision`` was about. They are read from the same
    #: event, because reading them separately is how the summary came to show
    #: `search` beside REQUIRE_CONFIRMATION while the panel below it asked
    #: about `update_record`: the decision was taken from the last policy event
    #: and the tool from the first tool call, which are opposite ends of a
    #: multi-step trace.
    decided_tool: str | None
    policy_rules: str | None
    outcome: State
    blocked_by: str | None


#: Events worth showing a visitor, in plain language. Anything absent from this
#: map is omitted rather than guessed at -- a step nobody can explain is noise.
#: Event -> catalogue key. The words themselves live in the catalogues, so
#: the timeline follows the reader's language and this stays a map of which
#: events are worth showing at all.
_STEP_KEYS: dict[str, str] = {
    "request_started": "step.request_started",
    "input_flagged": "step.input_flagged",
    "input_sensitive": "step.input_sensitive",
    "input_rejected": "step.input_rejected",
    "rate_limited": "step.rate_limited",
    "route_selected": "step.route_selected",
    "agent_started": "step.agent_started",
    "action_proposed": "step.action_proposed",
    "llm_retry": "step.llm_retry",
    "llm_failed": "step.llm_failed",
    "policy_decision": "step.policy_decision",
    "confirmation_requested": "step.confirmation_requested",
    "confirmation_resolved": "step.confirmation_resolved",
    "tool_call": "step.tool_call",
    "validation": "step.validation",
    "resource_limit": "step.resource_limit",
    "circuit_open": "step.circuit_open",
    "prompt_redacted": "step.prompt_redacted",
    "output_redacted": "step.output_redacted",
    "request_completed": "step.request_completed",
    "request_failed": "step.request_failed",
}

#: Deliberately omitted from the timeline: a model call is an implementation
#: detail of an agent's turn, and showing one line per call buries the shape of
#: the run. The count is reported separately instead.
_HIDDEN = {"llm_call"}


def _status_state(status: str | None) -> State:
    return {
        "success": State.SUCCESS,
        "failure": State.FAILED,
        "blocked": State.BLOCKED,
        "pending": State.WAITING,
    }.get(status or "", State.RUNNING)


def _blocked_by(events: list[dict[str, Any]]) -> str | None:
    """Name the control that stopped the request, when one did.

    The four are genuinely different and must never be blurred: a policy denial
    is a decision, a budget block is a choice not to spend, a circuit trip is a
    reaction to failure, and a resource ceiling is arithmetic.
    """
    types = {e.get("event_type") for e in events}
    if "circuit_open" in types:
        return "circuit breaker"
    if "resource_limit" in types:
        return "resource limit"
    if "rate_limited" in types:
        return "rate limit"

    for event in events:
        error = (event.get("error") or "").lower()
        if "provider-call budget" in error or "provider call budget" in error:
            return "provider budget"

    for event in events:
        if (
            event.get("event_type") == "policy_decision"
            and event.get("policy_decision") == "deny"
        ):
            return "policy engine"
    return None


def _agent_states(events: list[dict[str, Any]]) -> tuple[AgentActivity, ...]:
    started = {e.get("agent") for e in events if e.get("event_type") == "agent_started"}
    activities: list[AgentActivity] = []

    for name in AGENT_ORDER:
        if name not in started:
            activities.append(
                AgentActivity(name, State.NOT_REACHED, "did not run for this request")
            )
            continue

        mine = [e for e in events if e.get("agent") == name]
        state = State.RUNNING
        detail: str | None = None

        for event in mine:
            kind = event.get("event_type")
            if kind == "route_selected":
                state, detail = State.SUCCESS, "classified the request"
            elif kind == "validation":
                state = _status_state(event.get("status"))
                detail = "checked the result"
            elif kind == "tool_call" and event.get("status") == "success":
                state, detail = State.SUCCESS, f"ran {event.get('tool')}"
            elif kind == "policy_decision":
                decision = event.get("policy_decision")
                if decision == "deny":
                    state, detail = State.BLOCKED, "its proposal was refused"
                elif decision == "require_confirmation":
                    state, detail = State.WAITING, "its proposal needs approval"
            elif kind in {"resource_limit", "circuit_open"}:
                state, detail = State.BLOCKED, "stopped before calling the model"

        activities.append(AgentActivity(name, state, detail))

    return tuple(activities)


def _tool_activity(events: list[dict[str, Any]]) -> tuple[ToolActivity, ...]:
    """Track each proposal separately: proposed is not executed.

    One entry per *proposal*, not per tool name, because the same tool can be
    proposed more than once in a run -- the researcher and the executor both
    proposed `delete_record` in the observed denial trace, and collapsing those
    would hide one of the two refusals.

    An entry stays open until a `tool_call` resolves it or the stream ends, so
    a policy decision refines it rather than closing it. Closing on the
    decision was the first version of this, and it lost every execution.
    """
    entries: list[dict[str, Any]] = []

    def open_entry(name: str) -> dict[str, Any] | None:
        for entry in reversed(entries):
            if entry["name"] == name and not entry["closed"]:
                return entry
        return None

    for event in events:
        name = event.get("tool")
        if not name:
            continue
        kind = event.get("event_type")

        if kind == "action_proposed":
            entries.append(
                {
                    "name": name,
                    "proposed_by": event.get("agent"),
                    "risk": None,
                    "decision": None,
                    "execution": State.WAITING,
                    "detail": "proposed, not yet evaluated",
                    "closed": False,
                }
            )
            continue

        entry = open_entry(name)
        if entry is None:
            continue

        if kind == "policy_decision":
            decision = event.get("policy_decision")
            entry["risk"] = event.get("risk_level")
            entry["decision"] = decision
            if decision == "deny":
                entry["execution"] = State.BLOCKED
                entry["detail"] = "refused by the policy engine; never executed"
                entry["closed"] = True  # nothing will run after a denial
            elif decision == "require_confirmation":
                entry["execution"] = State.WAITING
                entry["detail"] = "awaiting human approval; not executed"
            else:
                entry["execution"] = State.WAITING
                entry["detail"] = "allowed, awaiting execution"
        elif kind == "tool_call":
            if event.get("status") == "success":
                entry["execution"] = State.SUCCESS
                entry["detail"] = "executed"
            else:
                entry["execution"] = State.FAILED
                entry["detail"] = "execution failed"
            entry["closed"] = True

    return tuple(
        ToolActivity(
            name=e["name"],
            risk=e["risk"],
            proposed_by=e["proposed_by"],
            decision=e["decision"],
            execution=e["execution"],
            detail=e["detail"],
        )
        for e in entries
    )


def _steps(events: list[dict[str, Any]]) -> tuple[Step, ...]:
    steps: list[Step] = []
    for event in events:
        kind = event.get("event_type") or ""
        if kind in _HIDDEN:
            continue
        label_key = _STEP_KEYS.get(kind)
        if label_key is None:
            continue
        label = t(label_key)

        agent = event.get("agent")
        tool = event.get("tool")
        if kind == "agent_started" and agent:
            label = t("step.agent_named_started", agent=agent.capitalize())

        bits = []
        if tool:
            bits.append(f"tool {tool}")
        if event.get("policy_decision"):
            bits.append(str(event["policy_decision"]).replace("_", " "))
        if event.get("risk_level"):
            bits.append(f"{event['risk_level']} risk")
        if event.get("rule_ids"):
            rules = event["rule_ids"]
            bits.append(f"rule {rules if isinstance(rules, str) else ', '.join(rules)}")

        steps.append(
            Step(
                sequence=int(event.get("sequence") or 0),
                label=label,
                state=_status_state(event.get("status")),
                detail=", ".join(bits) or None,
            )
        )
    return tuple(sorted(steps, key=lambda s: s.sequence))


def _outcome(status: str, blocked_by: str | None, *, proposed: bool = True) -> State:
    # `declined` covers two different events: a human refused a suspended
    # action, and the platform had no tool that could answer at all. Both
    # arrive with no `blocked_by`, because in neither case did a control refuse
    # anything -- so the discriminator is whether an action ever existed.
    #
    # A human who declines is deciding about a proposal, and something was
    # stopped: that stays BLOCKED. A question nothing could serve never reached
    # a proposal, and calling it blocked told the reader a control had refused
    # them while the message beside it said the opposite.
    if status == "declined" and blocked_by is None and not proposed:
        return State.OUT_OF_SCOPE
    if status in {"blocked", "rejected", "rate_limited", "declined"}:
        return State.BLOCKED
    if status == "awaiting_confirmation":
        return State.WAITING
    if status == "success":
        return State.SUCCESS
    if status == "failed":
        # A request stopped by a control did not "fail"; it was prevented.
        return State.BLOCKED if blocked_by in {
            "provider budget", "circuit breaker", "resource limit", "rate limit"
        } else State.FAILED
    return State.RUNNING


def build(events: list[dict[str, Any]], *, status: str) -> ExecutionView:
    """Derive the whole view from one recorded stream. No inference beyond it."""
    ordered = sorted(events, key=lambda e: int(e.get("sequence") or 0))

    decision = None
    decided_tool = None
    rules = None
    for event in ordered:
        if event.get("event_type") == "policy_decision" and event.get("policy_decision"):
            decision = event["policy_decision"]
            # Same event, same loop, same iteration. Whatever rule decides
            # which decision is *the* decision, the tool follows it.
            decided_tool = event.get("tool") or None
            raw = event.get("rule_ids")
            if raw:
                rules = raw if isinstance(raw, str) else ", ".join(raw)
            if decision == "deny":
                break  # a denial is the decision that mattered

    blocked_by = _blocked_by(ordered)
    return ExecutionView(
        steps=_steps(ordered),
        agents=_agent_states(ordered),
        tools=_tool_activity(ordered),
        policy_decision=decision,
        decided_tool=decided_tool,
        policy_rules=rules,
        outcome=_outcome(
            status,
            blocked_by,
            # An action was proposed if the trace holds one. Read from the
            # recorded events, never inferred from the status.
            proposed=any(
                event.get("event_type")
                in ("action_proposed", "confirmation_requested", "confirmation_resolved")
                for event in ordered
            ),
        ),
        blocked_by=blocked_by,
    )


def model_calls(events: list[dict[str, Any]]) -> int:
    """Successful model calls recorded for this request."""
    return sum(1 for e in events if e.get("event_type") == "llm_call")
