"""Edge functions for the orchestration graph.

Kept separate from graph construction so that the branching rules -- including
the retry ceiling that prevents an infinite validator/executor loop -- can be
unit tested without building a graph.
"""

from __future__ import annotations

from ..models import Route
from .state import AgentState

NODE_RESEARCH = "research"
NODE_EXECUTE = "execute"
NODE_CONFIRM = "confirm"
NODE_VALIDATE = "validate"
#: Writes a grounded answer from what the researcher gathered. Reachable only
#: from the read path, and its only exit is RESPOND -- see :func:`after_research`.
NODE_ANSWER = "answer"
NODE_RESPOND = "respond"


def after_route(state: AgentState) -> str:
    """Pick the first working node based on the router's classification.

    Both the researcher and executor routes begin with research: an action
    request benefits from context, which is the researcher-feeds-executor flow
    the blueprint describes. Only a conversational request skips straight to a
    reply.
    """
    route = state.get("route")
    if route in {Route.RESEARCHER.value, Route.EXECUTOR.value}:
        return NODE_RESEARCH
    return NODE_RESPOND


def after_research(state: AgentState) -> str:
    """Proceed to an action only when the request actually asked for one.

    A lookup request ends after research. Sending it through the executor
    anyway would force the agent to invent an action for a question that only
    wanted an answer -- which, for a platform whose whole point is not taking
    unnecessary actions, would be precisely the wrong default.

    A lookup now goes to :data:`NODE_ANSWER` rather than straight to RESPOND,
    so that the retrieved documents get turned into an answer instead of a list
    of titles. The branch is the *only* thing separating the two paths, and it
    reads the route the router chose -- never anything a document said. A
    poisoned article cannot move a request from the read path to the action
    path, because nothing downstream of retrieval is consulted here.
    """
    if state.get("route") == Route.EXECUTOR.value:
        return NODE_EXECUTE
    return NODE_ANSWER


def after_execute(state: AgentState) -> str:
    """Suspend for confirmation, otherwise proceed to validation.

    A denied action skips validation entirely: there is no result to validate,
    and the response node reports the refusal.
    """
    if state.get("pending_confirmation"):
        return NODE_CONFIRM

    decision = state.get("policy_decision") or {}
    if decision.get("decision") == "deny":
        return NODE_RESPOND

    if state.get("tool_result") is None:
        return NODE_RESPOND

    return NODE_VALIDATE


def after_confirm(state: AgentState) -> str:
    """A declined action ends the request; it is never retried.

    Without this the decline would fall through to the validator, fail
    validation, and be re-proposed by the retry loop -- asking the reviewer the
    same question again after they already said no.
    """
    if state.get("status") == "declined":
        return NODE_RESPOND
    return NODE_VALIDATE


def after_validate(state: AgentState, *, max_retries: int) -> str:
    """Retry a rejected action, up to the configured ceiling.

    The ceiling is what makes this terminate. Once it is reached the request
    ends with whatever it has, and the response says so.
    """
    validation = state.get("validation") or {}
    if validation.get("approved"):
        return NODE_RESPOND

    # A refusal is not a transient failure. Retrying a denied or declined action
    # cannot succeed -- the policy engine would reach the same verdict -- and
    # would spend budget to re-learn that. Only genuine execution failures are
    # worth another attempt.
    result_status = (state.get("tool_result") or {}).get("status")
    if result_status == "denied" or state.get("status") == "declined":
        return NODE_RESPOND

    if state.get("retry_count", 0) >= max_retries:
        return NODE_RESPOND

    return NODE_EXECUTE
