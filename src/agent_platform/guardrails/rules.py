"""The policy rule catalogue.

Rules are declared as data so that the set of enforced controls can be listed
in the dashboard, in the docs and in tests without reading the engine's code.
The engine in ``policy.py`` applies them; the predicates here stay pure and
independently testable.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Final

from ..models import Decision, ProposedAction, RiskLevel


@dataclass(frozen=True, slots=True)
class Rule:
    rule_id: str
    title: str
    description: str
    outcome: Decision


RULES: Final[tuple[Rule, ...]] = (
    Rule(
        "PL001",
        "Unregistered tool",
        "Only tools present in the static registry may be executed.",
        Decision.DENY,
    ),
    Rule(
        "PL002",
        "Router holds no tools",
        "The router classifies requests and may never propose a tool call.",
        Decision.DENY,
    ),
    Rule(
        "PL003",
        "Agent not authorised",
        "The agent must pass both the tool allow-list and the capability matrix.",
        Decision.DENY,
    ),
    Rule(
        "PL004",
        "Invalid arguments",
        "Arguments must validate against the tool's declared schema.",
        Decision.DENY,
    ),
    Rule(
        "PL005",
        "Critical risk blocked",
        "CRITICAL actions are refused by default and have no confirmation path.",
        Decision.DENY,
    ),
    Rule(
        "PL006",
        "Credentials in arguments",
        "Tool arguments must not carry credential-shaped content.",
        Decision.DENY,
    ),
    Rule(
        "PL007",
        "Budget exceeded",
        "The projected cost must fit the per-request and daily budgets.",
        Decision.DENY,
    ),
    Rule(
        "PL008",
        "High-risk action from suspicious input",
        "HIGH-risk actions are refused when the originating input carried "
        "prompt-injection signals.",
        Decision.DENY,
    ),
    Rule(
        "PL009",
        "Confirmation required",
        "HIGH-risk or explicitly flagged tools require an application-issued "
        "confirmation before execution.",
        Decision.REQUIRE_CONFIRMATION,
    ),
    Rule(
        "PL010",
        "Confirmation does not match action",
        "A confirmation is bound to one exact action; it cannot be replayed "
        "against a different tool or different arguments.",
        Decision.DENY,
    ),
    Rule(
        "PL011",
        "Rate limit exceeded",
        "Request volume must stay within the configured per-minute and "
        "per-hour quotas.",
        Decision.DENY,
    ),
)

RULES_BY_ID: Final[dict[str, Rule]] = {rule.rule_id: rule for rule in RULES}


def action_fingerprint(action: ProposedAction) -> str:
    """A stable digest of exactly which action is being authorised.

    Confirmations are bound to this value. Without it, a confirmation obtained
    for a harmless action could be replayed to authorise a different one -- so
    the fingerprint covers the tool name *and* every argument.
    """
    canonical = json.dumps(
        {"tool": action.tool, "arguments": action.arguments},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def needs_confirmation(risk: RiskLevel, requires_confirmation_flag: bool) -> bool:
    """Confirmation is required by tool metadata or by reaching HIGH risk."""
    return requires_confirmation_flag or risk.at_least(RiskLevel.HIGH)


def is_blocked_by_default(risk: RiskLevel) -> bool:
    """CRITICAL is refused outright; there is no confirmation path to it."""
    return risk is RiskLevel.CRITICAL


def describe_rules() -> list[dict[str, Any]]:
    """Rule catalogue for the dashboard and documentation."""
    return [
        {
            "rule_id": rule.rule_id,
            "title": rule.title,
            "description": rule.description,
            "outcome": rule.outcome.value,
        }
        for rule in RULES
    ]
