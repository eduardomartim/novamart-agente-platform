"""Request-scoped tracing.

One :class:`Tracer` is created per user request. It owns the sequence counter
that makes the event stream reconstructable, and it routes every payload
through the central sanitiser before handing it to the repository.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from typing import Any

from ..models import AgentName, PolicyDecision
from ..persistence.repository import EventRecord, Repository
from ..security.sanitization import SanitizationReport, sanitize
from .events import EventStatus, EventType


def new_request_id() -> str:
    return f"req-{uuid.uuid4().hex[:12]}"


def new_trace_id() -> str:
    return f"trace-{uuid.uuid4().hex[:16]}"


@dataclass(slots=True)
class TraceContext:
    request_id: str
    trace_id: str


class Tracer:
    """Writes ordered, sanitised events for a single request."""

    def __init__(
        self,
        repository: Repository,
        *,
        request_id: str,
        trace_id: str,
        max_payload_chars: int = 500,
        known_secrets: tuple[str, ...] = (),
    ) -> None:
        self._repository = repository
        self.request_id = request_id
        self.trace_id = trace_id
        self._max_payload_chars = max_payload_chars
        self._known_secrets = tuple(s for s in known_secrets if s)
        self._sequence = 0
        self._lock = threading.Lock()
        self._sanitization_findings: list[str] = []

    @property
    def context(self) -> TraceContext:
        return TraceContext(request_id=self.request_id, trace_id=self.trace_id)

    @property
    def sanitization_findings(self) -> tuple[str, ...]:
        """Anything the sanitiser stripped while writing this request's trace."""
        return tuple(self._sanitization_findings)

    def _next_sequence(self) -> int:
        with self._lock:
            self._sequence += 1
            return self._sequence

    def _record_findings(self, report: SanitizationReport) -> None:
        for kind in report.secret_kinds:
            self._sanitization_findings.append(f"secret:{kind}")
        for kind in report.pii_kinds:
            self._sanitization_findings.append(f"pii:{kind}")
        for key in report.dropped_keys:
            self._sanitization_findings.append(f"dropped_key:{key}")

    def event(
        self,
        event_type: EventType,
        *,
        status: EventStatus = EventStatus.INFO,
        agent: AgentName | str | None = None,
        tool: str | None = None,
        policy_decision: str | None = None,
        risk_level: str | None = None,
        rule_ids: tuple[str, ...] = (),
        latency_ms: float = 0.0,
        error: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        """Record one event. Payloads are sanitised before they leave this call."""
        safe_payload, report = sanitize(
            payload or {},
            max_chars=self._max_payload_chars,
            known_secrets=self._known_secrets,
        )
        self._record_findings(report)

        safe_error: str | None = None
        if error:
            safe_error_value, error_report = sanitize(
                error, max_chars=self._max_payload_chars, known_secrets=self._known_secrets
            )
            safe_error = str(safe_error_value)
            self._record_findings(error_report)

        agent_name = agent.value if isinstance(agent, AgentName) else agent

        self._repository.save_event(
            EventRecord(
                request_id=self.request_id,
                trace_id=self.trace_id,
                sequence=self._next_sequence(),
                event_type=event_type.value,
                status=status.value,
                agent=agent_name,
                tool=tool,
                policy_decision=policy_decision,
                risk_level=risk_level,
                rule_ids=rule_ids,
                latency_ms=latency_ms,
                error=safe_error,
                payload=safe_payload if isinstance(safe_payload, dict) else {"value": safe_payload},
            )
        )

    def policy_event(
        self,
        decision: PolicyDecision,
        *,
        agent: AgentName,
        tool: str,
        latency_ms: float = 0.0,
    ) -> None:
        """Convenience wrapper for the most security-relevant event type."""
        status = {
            "allow": EventStatus.SUCCESS,
            "deny": EventStatus.BLOCKED,
            "require_confirmation": EventStatus.PENDING,
        }[decision.decision.value]

        self.event(
            EventType.POLICY_DECISION,
            status=status,
            agent=agent,
            tool=tool,
            policy_decision=decision.decision.value,
            risk_level=decision.risk_level.value,
            rule_ids=decision.rule_ids,
            latency_ms=latency_ms,
            payload={"reason": decision.reason},
        )
