"""The shared state contract passed between graph nodes.

What this state deliberately does not carry:

* API keys or any credential;
* chain-of-thought or model reasoning;
* raw prompts or completions;
* unbounded context growth.

Those exclusions are the reason the state is a narrow, explicit ``TypedDict``
rather than a free-form dictionary that accumulates whatever a node felt like
attaching.
"""

from __future__ import annotations

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    """State threaded through the orchestration graph."""

    request_id: str
    trace_id: str
    user_input: str

    route: str | None
    current_agent: str | None

    #: Read-only findings gathered by the researcher. Bounded by
    #: ``Settings.max_context_items``.
    context: list[dict[str, Any]]

    #: The action the executor wants to take, as a plain dict.
    proposed_action: dict[str, Any] | None
    #: The policy engine's verdict, recorded for the trace and the UI.
    policy_decision: dict[str, Any] | None
    #: Set when execution is suspended pending human approval.
    pending_confirmation: dict[str, Any] | None

    tool_result: dict[str, Any] | None
    validation: dict[str, Any] | None

    #: The answer node's result, flattened to a dict like every other record
    #: here. Carries ``outcome``, ``text``, ``citations`` and ``reason`` so the
    #: response node can tell a grounded answer from an absence of evidence
    #: from an outage. Model text, never authority: nothing reads this to
    #: decide what the platform may do.
    answer: dict[str, Any] | None

    retry_count: int
    errors: list[str]

    status: str
    final_response: str


def initial_state(*, request_id: str, trace_id: str, user_input: str) -> AgentState:
    return AgentState(
        request_id=request_id,
        trace_id=trace_id,
        user_input=user_input,
        route=None,
        current_agent=None,
        context=[],
        proposed_action=None,
        policy_decision=None,
        pending_confirmation=None,
        tool_result=None,
        validation=None,
        answer=None,
        retry_count=0,
        errors=[],
        status="running",
        final_response="",
    )


def append_error(state: AgentState, message: str) -> list[str]:
    """Return the error list with *message* appended, bounded in length."""
    errors = list(state.get("errors") or [])
    errors.append(message)
    return errors[-10:]
