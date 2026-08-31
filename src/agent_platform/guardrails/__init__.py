"""Guardrails: policy engine, authorization, risk, input and output security."""

from .authorization import AGENT_CAPABILITIES, AuthorizationResult, authorize, describe_matrix
from .input import InputAssessment, InputSignal, assess_input
from .output import OutputAssessment, secure_output, validate_against_schema
from .policy import Confirmation, EvaluationOutcome, PolicyContext, PolicyEngine
from .risk import RiskAssessment, assess_risk, escalate
from .rules import RULES, action_fingerprint, describe_rules

__all__ = [
    "AGENT_CAPABILITIES",
    "RULES",
    "AuthorizationResult",
    "Confirmation",
    "EvaluationOutcome",
    "InputAssessment",
    "InputSignal",
    "OutputAssessment",
    "PolicyContext",
    "PolicyEngine",
    "RiskAssessment",
    "action_fingerprint",
    "assess_input",
    "assess_risk",
    "authorize",
    "describe_matrix",
    "describe_rules",
    "escalate",
    "secure_output",
    "validate_against_schema",
]
