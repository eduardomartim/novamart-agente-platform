"""Core domain models shared across the platform.

These types deliberately live at the package root rather than inside ``tools/``:
risk levels, decisions and agent identities are referenced by the guardrails,
cost, observability and evaluation layers alike, and centralising them keeps the
dependency graph acyclic.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class RiskLevel(StrEnum):
    """Severity of an action's potential impact.

    Ordering is exposed through :attr:`rank` rather than the inherited string
    comparison, because alphabetical ordering of the values would be wrong
    (``"critical" < "low"`` as a string).
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return _RISK_RANK[self]

    def at_least(self, other: RiskLevel) -> bool:
        return self.rank >= other.rank


_RISK_RANK: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


class Decision(StrEnum):
    """The three outcomes the policy engine may return."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_CONFIRMATION = "require_confirmation"


class AgentName(StrEnum):
    ROUTER = "router"
    RESEARCHER = "researcher"
    EXECUTOR = "executor"
    VALIDATOR = "validator"
    #: Writes a grounded answer from documents that were already retrieved. It
    #: proposes nothing and holds no capability, so it appears in the matrix
    #: with an empty set rather than being absent from it -- "holds nothing" is
    #: a decision worth stating, not an oversight worth inferring.
    ANSWERER = "answerer"


class Route(StrEnum):
    RESEARCHER = "researcher"
    EXECUTOR = "executor"
    DIRECT_RESPONSE = "direct_response"


class Capability(StrEnum):
    """Coarse permission buckets used by the least-privilege matrix."""

    SEARCH = "search"
    READ_DATA = "read_data"
    WRITE_DATA = "write_data"
    SEND_MESSAGE = "send_message"
    DELETE = "delete"


class ProposedAction(BaseModel):
    """A tool call an agent would like the gateway to perform.

    ``extra="forbid"`` is a security control, not a style choice: it stops a
    model from smuggling fields such as ``risk_level`` or ``confirmed`` into its
    own proposal in an attempt to influence the authorisation decision. Risk is
    always derived from tool metadata by the platform, never supplied by the
    model.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class PolicyViolation(BaseModel):
    """A single rule that fired while evaluating an action."""

    model_config = ConfigDict(frozen=True)

    rule_id: str
    detail: str
    risk_level: RiskLevel


class PolicyDecision(BaseModel):
    """The structured verdict returned by the policy engine."""

    model_config = ConfigDict(frozen=True)

    decision: Decision
    risk_level: RiskLevel
    reason: str
    violations: tuple[PolicyViolation, ...] = ()

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    @property
    def rule_ids(self) -> tuple[str, ...]:
        return tuple(v.rule_id for v in self.violations)


ToolStatus = Literal["success", "error", "denied", "timeout", "invalid_arguments"]


class ToolResult(BaseModel):
    """Outcome of a gateway-mediated tool invocation."""

    model_config = ConfigDict(frozen=True)

    tool: str
    status: ToolStatus
    output: Any = None
    error: str | None = None
    latency_ms: float = 0.0
    simulated: bool = True

    @property
    def ok(self) -> bool:
        return self.status == "success"
