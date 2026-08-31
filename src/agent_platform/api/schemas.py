"""The public HTTP contract.

These models are the *only* thing the API promises. They deliberately expose
less than :class:`~agent_platform.platform.RunResult` carries:

* ``tool_result`` is raw tool output, captured **before** the output-security
  layer masks PII and blocks secrets. ``response`` is the field that has been
  through that gate, so it is the field that crosses the network.
* ``validation`` is an internal second-opinion record with no meaning to a
  caller.

Everything here is derived from ``RunResult``. Nothing is read out of the event
stream, so the API has exactly one source of truth and cannot drift from what
the platform actually returned.
"""

from __future__ import annotations

from typing import Any, Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..platform import RunResult

#: Structural ceiling on an inbound body, in characters.
#:
#: This is a memory guard, not a validation rule. The semantic limit is
#: ``Settings.max_input_chars`` (8000 by default), enforced by the platform's
#: own input-security layer, which is the single authority on what input is
#: acceptable. This bound sits deliberately far above it so it can never become
#: the binding constraint -- if the API rejected at a threshold below the
#: platform's, the two would disagree about the same input and the API would
#: have become a second validation path.
MAX_INPUT_CHARS: Final[int] = 65_536


class RunRequest(BaseModel):
    """A request for the platform to handle."""

    model_config = ConfigDict(extra="forbid", strict=True)

    input: str = Field(min_length=1, max_length=MAX_INPUT_CHARS)


class ConfirmRequest(BaseModel):
    """A human decision on a suspended action.

    ``approved`` is the entire decision. Which action it applies to is
    determined by the platform from its own state, never from this body -- the
    same rule the CLI path follows, and the reason a confirmation cannot be
    replayed against a different action.

    **``actor`` used to live here and deliberately does not any more.** It was
    written into the ``CONFIRMATION_RESOLVED`` audit event as the human who
    approved a high-risk action, and it was whatever string the caller typed.
    An audit trail that records a self-asserted identity beside a verified
    fingerprint is worse than no trail, because it reads as though somebody
    checked. The approver is now taken from the authenticated credential and
    from nowhere else; ``extra="forbid"`` turns a body that still sends one into
    a 400 rather than silently ignoring it.
    """

    model_config = ConfigDict(extra="forbid", strict=True)

    approved: bool


class PolicyDecisionModel(BaseModel):
    """Why the platform allowed, questioned or refused an action."""

    decision: str = ""
    risk_level: str = ""
    reason: str = ""
    rule_ids: list[str] = Field(default_factory=list)


class PendingConfirmationModel(BaseModel):
    """An action suspended awaiting human approval."""

    request_id: str
    tool: str
    arguments: dict[str, Any]
    risk_level: str
    reason: str


class RunResponse(BaseModel):
    """The outcome of one request.

    ``status`` is the platform's own vocabulary, passed through unchanged:
    ``success``, ``blocked``, ``failed``, ``awaiting_confirmation``,
    ``rejected``, ``rate_limited``, ``expired``.
    """

    request_id: str
    trace_id: str
    status: str
    response: str
    route: str | None = None
    provider: str = ""
    latency_ms: float = 0.0
    retry_count: int = 0
    errors: list[str] = Field(default_factory=list)
    policy_decision: PolicyDecisionModel | None = None
    awaiting_confirmation: PendingConfirmationModel | None = None


class HealthResponse(BaseModel):
    """Liveness. Deliberately carries nothing that could fail to be computed."""

    status: Literal["ok"] = "ok"


class ReadyResponse(BaseModel):
    """Readiness, plus the operational facts an authorised caller may see.

    ``provider``, ``circuit`` and ``budget`` are **reported, not gated on**.
    See the note in ``app.readiness`` for why.

    The detail is only populated for a caller holding ``metrics:read``. The
    route stays public because a kubelet probe sends no credential and needs
    nothing but the status code -- but the provider name and the circuit state
    are free reconnaissance for anyone who can reach the port, so an anonymous
    caller gets the verdict and nothing else.

    ``auth_mode`` is reported to everyone on purpose. "This API is
    unauthenticated" is not a secret from somebody who just reached it without
    a credential, and it is exactly what an operator needs to see.
    """

    ready: bool
    auth_mode: str = "enforced"
    checks: dict[str, bool] = Field(default_factory=dict)
    provider: str = ""
    circuit: str = ""
    detail: str | None = None


class ErrorResponse(BaseModel):
    """A failure that stopped the request before the platform produced a result."""

    error: str
    detail: str
    request_id: str | None = None


def run_response_from(result: RunResult) -> RunResponse:
    """Project a ``RunResult`` onto the public contract."""
    policy = None
    if result.policy_decision:
        raw = result.policy_decision
        policy = PolicyDecisionModel(
            decision=str(raw.get("decision", "")),
            risk_level=str(raw.get("risk_level", "")),
            reason=str(raw.get("reason", "")),
            rule_ids=[str(r) for r in (raw.get("rule_ids") or [])],
        )

    pending = None
    if result.awaiting_confirmation is not None:
        p = result.awaiting_confirmation
        pending = PendingConfirmationModel(
            request_id=p.request_id,
            tool=p.tool,
            arguments=p.arguments,
            risk_level=p.risk_level,
            reason=p.reason,
        )

    return RunResponse(
        request_id=result.request_id,
        trace_id=result.trace_id,
        status=result.status,
        response=result.response,
        route=result.route,
        provider=result.provider,
        latency_ms=result.latency_ms,
        retry_count=result.retry_count,
        errors=list(result.errors),
        policy_decision=policy,
        awaiting_confirmation=pending,
    )


#: How a platform status becomes an HTTP status.
#:
#: The rule, stated once so it can be checked: **the HTTP status describes the
#: fate of the HTTP request; the body's ``status`` describes the fate of the
#: agent run.** If the platform did agent work and returned a result, that is a
#: ``200`` however the run turned out -- including a policy denial, which is the
#: platform working exactly as designed, and including a provider failure, which
#: was recorded, traced, and executed no tool.
#:
#: Two statuses are exceptions, because the platform refused *at the door*
#: before doing any agent work, and HTTP has an exact equivalent for each:
#:
#: * ``rejected``     -- input-security refusal      -> 422 Unprocessable Content
#: * ``rate_limited`` -- quota refusal               -> 429 Too Many Requests
#:
#: ``expired`` is reachable only from the confirmation endpoint and means the
#: suspended action aged out, so the state it was approved against may have
#: moved on -> 409 Conflict.
#: ``declined``, ``denied`` and ``blocked`` are all governed refusals: the
#: platform ran, decided, recorded the decision, and executed nothing. Presenting
#: those as HTTP errors would say the API call failed, which is false, and would
#: invert the point of the system -- refusing correctly is the product, not a
#: fault.
STATUS_TO_HTTP: Final[dict[str, int]] = {
    "success": 200,
    "awaiting_confirmation": 200,
    "blocked": 200,
    "declined": 200,
    "denied": 200,
    "failed": 200,
    "rejected": 422,
    "rate_limited": 429,
    "expired": 409,
}

#: Used when the platform returns a status this map has never seen. Returning
#: 200 for an unknown outcome would silently present a novel failure as normal.
UNKNOWN_STATUS_HTTP: Final[int] = 500


def http_status_for(status: str) -> int:
    """Map a platform status onto an HTTP status code."""
    return STATUS_TO_HTTP.get(status, UNKNOWN_STATUS_HTTP)
