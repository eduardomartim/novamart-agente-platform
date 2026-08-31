"""Deterministic risk assessment.

Risk is derived from platform-owned tool metadata and fixed escalation rules.
No part of it is supplied by, or negotiable with, the model. Risk can only be
escalated here, never reduced -- which is the mechanism that prevents an agent
from talking its way into a lower risk band to obtain authorisation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..models import RiskLevel
from ..security.pii import detect_pii
from ..security.secrets import find_secrets
from ..tools.models import ToolDefinition

_ESCALATION_ORDER: tuple[RiskLevel, ...] = (
    RiskLevel.LOW,
    RiskLevel.MEDIUM,
    RiskLevel.HIGH,
    RiskLevel.CRITICAL,
)


def escalate(level: RiskLevel, steps: int = 1) -> RiskLevel:
    """Raise *level* by *steps*, saturating at CRITICAL."""
    index = min(len(_ESCALATION_ORDER) - 1, level.rank + max(0, steps))
    return _ESCALATION_ORDER[index]


@dataclass(slots=True)
class RiskAssessment:
    level: RiskLevel
    base_level: RiskLevel
    reasons: list[str] = field(default_factory=list)

    @property
    def escalated(self) -> bool:
        return self.level.rank > self.base_level.rank


def _argument_text(arguments: dict[str, Any]) -> str:
    return " ".join(f"{key}={value}" for key, value in arguments.items())


def assess_risk(
    tool: ToolDefinition,
    arguments: dict[str, Any],
    *,
    input_suspicious: bool = False,
) -> RiskAssessment:
    """Compute the effective risk of performing *tool* with *arguments*."""
    assessment = RiskAssessment(level=tool.risk_level, base_level=tool.risk_level)
    argument_text = _argument_text(arguments)

    if input_suspicious and tool.risk_level.at_least(RiskLevel.MEDIUM):
        assessment.level = escalate(assessment.level)
        assessment.reasons.append(
            "originating input carried prompt-injection signals; "
            "non-trivial actions are escalated one level"
        )

    secret_kinds = find_secrets(argument_text)
    if secret_kinds:
        assessment.level = RiskLevel.CRITICAL
        assessment.reasons.append(
            f"tool arguments contain credential-shaped content ({', '.join(secret_kinds)})"
        )

    if tool.capability.value == "send_message":
        pii_kinds = [m.kind for m in detect_pii(argument_text) if m.kind != "email"]
        if pii_kinds:
            assessment.level = escalate(assessment.level)
            assessment.reasons.append(
                f"outbound message payload contains PII ({', '.join(pii_kinds)})"
            )

    if not tool.idempotent and tool.risk_level.at_least(RiskLevel.HIGH):
        assessment.reasons.append(
            "action is non-idempotent; automatic retry is disabled for this tool"
        )

    return assessment
