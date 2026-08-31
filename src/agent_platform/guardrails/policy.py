"""The policy engine: the single authority on whether an action may execute.

Design notes worth stating explicitly, because both source blueprints were
ambiguous here:

* The engine is the *only* component that decides. The gateway enforces the
  decision but never makes one, so budget and rate-limit logic is not
  duplicated across two layers.
* A confirmation is an object the application constructs and binds to one
  exact action fingerprint. A model cannot manufacture one, because
  ``ProposedAction`` forbids extra fields and confirmations are never parsed
  out of model output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from ..cost.budget import BudgetGuard
from ..models import (
    AgentName,
    Decision,
    PolicyDecision,
    PolicyViolation,
    ProposedAction,
    RiskLevel,
)
from ..security.rate_limit import RateLimiterLike
from ..tools.models import ToolDefinition, ToolNotRegisteredError
from ..tools.registry import ToolRegistry
from .authorization import authorize
from .input import InputAssessment
from .risk import RiskAssessment, assess_risk
from .rules import RULES_BY_ID, action_fingerprint, is_blocked_by_default, needs_confirmation


@dataclass(frozen=True, slots=True)
class Confirmation:
    """Proof that a human approved one specific action.

    ``action_fingerprint`` binds the approval to the exact tool and arguments
    that were shown to the user. ``source`` records which trusted surface
    issued it. Neither field is ever populated from model output.
    """

    approved: bool
    source: str
    actor: str
    action_fingerprint: str


@dataclass(slots=True)
class PolicyContext:
    """Everything the engine needs to decide, gathered by the caller."""

    request_id: str
    agent: AgentName
    action: ProposedAction
    input_assessment: InputAssessment | None = None
    confirmation: Confirmation | None = None
    projected_cost_usd: Decimal = Decimal(0)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def input_suspicious(self) -> bool:
        return bool(self.input_assessment and self.input_assessment.suspicious)


@dataclass(slots=True)
class EvaluationOutcome:
    """The decision plus the derived facts the gateway needs to act on it."""

    decision: PolicyDecision
    tool: ToolDefinition | None = None
    validated_arguments: dict[str, Any] | None = None
    risk: RiskAssessment | None = None
    fingerprint: str = ""


def _violation(rule_id: str, detail: str, risk: RiskLevel) -> PolicyViolation:
    return PolicyViolation(rule_id=rule_id, detail=detail, risk_level=risk)


def _deny(rule_id: str, detail: str, risk: RiskLevel) -> PolicyDecision:
    return PolicyDecision(
        decision=Decision.DENY,
        risk_level=risk,
        reason=f"{RULES_BY_ID[rule_id].title}: {detail}",
        violations=(_violation(rule_id, detail, risk),),
    )


class PolicyEngine:
    """Evaluates proposed actions against the rule catalogue."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        budget_guard: BudgetGuard | None = None,
        # Typed as the protocol rather than the concrete class: the shared
        # limiter is a different implementation of the same two methods, and
        # the policy engine has no reason to know which one it holds.
        rate_limiter: RateLimiterLike | None = None,
    ) -> None:
        self._registry = registry
        self._budget = budget_guard
        self._rate_limiter = rate_limiter

    # ------------------------------------------------------------------ helpers

    def _validate_arguments(
        self, tool: ToolDefinition, arguments: dict[str, Any]
    ) -> tuple[dict[str, Any] | None, str]:
        try:
            model = tool.parameters.model_validate(arguments)
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
                for err in exc.errors()[:5]
            )
            return None, problems
        return model.model_dump(), ""

    # --------------------------------------------------------------- evaluation

    def evaluate(self, context: PolicyContext) -> EvaluationOutcome:
        """Decide whether the proposed action may proceed."""
        action = context.action
        fingerprint = action_fingerprint(action)

        # PL002 -- structural: the router has no tools at all, so this is
        # checked before the tool is even resolved.
        if context.agent is AgentName.ROUTER:
            return EvaluationOutcome(
                decision=_deny(
                    "PL002",
                    "the router may only classify requests, never propose tool calls",
                    RiskLevel.HIGH,
                ),
                fingerprint=fingerprint,
            )

        # PL001 -- unregistered tool.
        try:
            tool = self._registry.get(action.tool)
        except ToolNotRegisteredError as exc:
            return EvaluationOutcome(
                decision=_deny("PL001", str(exc), RiskLevel.CRITICAL),
                fingerprint=fingerprint,
            )

        # PL005 (base risk) -- a CRITICAL tool is refused for every agent, so
        # this is checked before authorisation. Ordering it here means the
        # recorded reason is "CRITICAL actions are blocked" rather than the
        # weaker "this agent lacks the capability", which would also be true but
        # would misdescribe why the action can never run.
        if is_blocked_by_default(tool.risk_level):
            return EvaluationOutcome(
                decision=_deny(
                    "PL005",
                    f"tool {tool.name!r} is classified CRITICAL and is blocked by default "
                    "for all agents; there is no confirmation path to it",
                    RiskLevel.CRITICAL,
                ),
                tool=tool,
                fingerprint=fingerprint,
            )

        # PL003 -- least privilege.
        auth = authorize(context.agent, tool)
        if not auth.authorized:
            return EvaluationOutcome(
                decision=_deny("PL003", auth.reason, RiskLevel.HIGH),
                tool=tool,
                fingerprint=fingerprint,
            )

        # PL004 -- schema validation. Invalid arguments never reach a handler.
        validated, problems = self._validate_arguments(tool, action.arguments)
        if validated is None:
            return EvaluationOutcome(
                decision=_deny(
                    "PL004", f"arguments rejected by schema ({problems})", RiskLevel.MEDIUM
                ),
                tool=tool,
                fingerprint=fingerprint,
            )

        risk = assess_risk(tool, validated, input_suspicious=context.input_suspicious)
        violations: list[PolicyViolation] = []

        # PL006 -- credentials in arguments escalate to CRITICAL in assess_risk;
        # reported separately so the reason is legible.
        credential_reasons = [r for r in risk.reasons if "credential-shaped" in r]
        if credential_reasons:
            return EvaluationOutcome(
                decision=_deny("PL006", credential_reasons[0], RiskLevel.CRITICAL),
                tool=tool,
                validated_arguments=validated,
                risk=risk,
                fingerprint=fingerprint,
            )

        # PL005 -- CRITICAL is refused outright, with no confirmation path.
        if is_blocked_by_default(risk.level):
            detail = (
                f"tool {tool.name!r} carries CRITICAL risk"
                if not risk.escalated
                else f"tool {tool.name!r} escalated to CRITICAL ({'; '.join(risk.reasons)})"
            )
            return EvaluationOutcome(
                decision=_deny("PL005", detail, RiskLevel.CRITICAL),
                tool=tool,
                validated_arguments=validated,
                risk=risk,
                fingerprint=fingerprint,
            )

        # PL008 -- refuse high-risk actions while injection signals are present.
        # Defence in depth: detection is unreliable, so it only ever *adds*
        # restriction and is never the sole thing standing between a request
        # and a dangerous tool.
        if context.input_suspicious and risk.level.at_least(RiskLevel.HIGH):
            signals = ", ".join(context.input_assessment.signal_ids)  # type: ignore[union-attr]
            return EvaluationOutcome(
                decision=_deny(
                    "PL008",
                    f"HIGH-risk action requested while input carried injection signals ({signals})",
                    risk.level,
                ),
                tool=tool,
                validated_arguments=validated,
                risk=risk,
                fingerprint=fingerprint,
            )

        # PL011 -- rate limit. Consulted, not consumed: quota is spent once per
        # request at the orchestrator entry point.
        #
        # F1: this previously passed context.request_id, which is unique per
        # request and therefore always addressed an empty bucket -- the rule
        # could never fire. The entry point consumes on the global bucket, so
        # this must consult the same one. Regression:
        # test_pl011_fires_through_the_real_platform_wiring.
        if self._rate_limiter is not None:
            limit = self._rate_limiter.check()
            if not limit.allowed:
                return EvaluationOutcome(
                    decision=_deny("PL011", limit.reason, risk.level),
                    tool=tool,
                    validated_arguments=validated,
                    risk=risk,
                    fingerprint=fingerprint,
                )

        # PL007 -- budget.
        if self._budget is not None:
            budget = self._budget.check(
                context.projected_cost_usd, request_id=context.request_id
            )
            if not budget.allowed:
                return EvaluationOutcome(
                    decision=_deny("PL007", budget.reason, risk.level),
                    tool=tool,
                    validated_arguments=validated,
                    risk=risk,
                    fingerprint=fingerprint,
                )

        # PL009 / PL010 -- confirmation.
        if needs_confirmation(risk.level, tool.requires_confirmation):
            confirmation = context.confirmation
            if confirmation is None or not confirmation.approved:
                violations.append(
                    _violation(
                        "PL009",
                        f"tool {tool.name!r} at {risk.level.value} risk requires confirmation",
                        risk.level,
                    )
                )
                return EvaluationOutcome(
                    decision=PolicyDecision(
                        decision=Decision.REQUIRE_CONFIRMATION,
                        risk_level=risk.level,
                        reason=(
                            f"{tool.name} is a {risk.level.value}-risk action and needs "
                            "explicit approval before it can run"
                        ),
                        violations=tuple(violations),
                    ),
                    tool=tool,
                    validated_arguments=validated,
                    risk=risk,
                    fingerprint=fingerprint,
                )

            if confirmation.action_fingerprint != fingerprint:
                return EvaluationOutcome(
                    decision=_deny(
                        "PL010",
                        "the supplied confirmation was issued for a different action",
                        RiskLevel.CRITICAL,
                    ),
                    tool=tool,
                    validated_arguments=validated,
                    risk=risk,
                    fingerprint=fingerprint,
                )

        reason = f"{tool.name} permitted for {context.agent.value} at {risk.level.value} risk"
        if risk.escalated:
            reason += f" (escalated from {risk.base_level.value})"

        return EvaluationOutcome(
            decision=PolicyDecision(
                decision=Decision.ALLOW,
                risk_level=risk.level,
                reason=reason,
                violations=tuple(violations),
            ),
            tool=tool,
            validated_arguments=validated,
            risk=risk,
            fingerprint=fingerprint,
        )
