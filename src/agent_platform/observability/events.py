"""Structured event vocabulary.

Traces record *what the platform did*, never what the model reasoned. There is
deliberately no event type for deliberation or intermediate thinking: the
sanitiser drops reasoning-shaped keys, and no event here invites them.
"""

from __future__ import annotations

from enum import StrEnum


class EventType(StrEnum):
    REQUEST_STARTED = "request_started"
    #: Raised only for injection-shaped signals, so the security dashboard's
    #: "prompt injection" counter means exactly that.
    INPUT_FLAGGED = "input_flagged"
    #: Raised for credential- or PII-shaped input. Kept separate from
    #: INPUT_FLAGGED because an email address in a support request is not an
    #: attack, and counting it as one would inflate the attack metric.
    INPUT_SENSITIVE = "input_sensitive"
    INPUT_REJECTED = "input_rejected"
    RATE_LIMITED = "rate_limited"
    #: A hard resource ceiling stopped the request (calls, seconds, bytes).
    #: Distinct from a policy denial: nothing was disallowed, the request
    #: simply ran out of budget in a non-monetary unit.
    RESOURCE_LIMIT = "resource_limit"
    #: The provider circuit was open, so no call was attempted.
    CIRCUIT_OPEN = "circuit_open"
    #: A tool result exceeded the byte ceiling and was replaced.
    OUTPUT_TRUNCATED = "output_truncated"
    #: A suspended confirmation aged out and can no longer execute.
    CONFIRMATION_EXPIRED = "confirmation_expired"
    ROUTE_SELECTED = "route_selected"
    AGENT_STARTED = "agent_started"
    AGENT_COMPLETED = "agent_completed"
    #: Recorded when material was stripped from a prompt before it was sent
    #: to a provider. Carries category names only, never values.
    PROMPT_REDACTED = "prompt_redacted"
    LLM_CALL = "llm_call"
    #: One physical attempt that failed and was retried inside the provider.
    #: Additive: a provider that never retries emits none of these, and LLM_CALL
    #: keeps meaning exactly what it meant -- the call that eventually returned.
    #: Without it a call that lost thirty seconds to 503s and then succeeded was
    #: recorded as the successful attempt alone, and a call that failed outright
    #: was recorded as nothing at all.
    LLM_RETRY = "llm_retry"
    #: Every attempt failed and the provider gave up. Distinct from LLM_RETRY,
    #: which is one attempt among several, and from CIRCUIT_OPEN, where no call
    #: was made at all.
    LLM_FAILED = "llm_failed"
    ACTION_PROPOSED = "action_proposed"
    POLICY_DECISION = "policy_decision"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    CONFIRMATION_RESOLVED = "confirmation_resolved"
    TOOL_CALL = "tool_call"
    VALIDATION = "validation"
    #: The answer node finished. One event for all three outcomes, with the
    #: outcome in the payload: a grounded answer, an absence of evidence and a
    #: fall back to the deterministic summary are the same step ending
    #: differently, and splitting them into three types would scatter one fact
    #: across three places.
    ANSWER_COMPOSED = "answer_composed"
    RETRY = "retry"
    OUTPUT_REDACTED = "output_redacted"
    REQUEST_COMPLETED = "request_completed"
    REQUEST_FAILED = "request_failed"
    ERROR = "error"


class EventStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    BLOCKED = "blocked"
    PENDING = "pending"
    INFO = "info"
