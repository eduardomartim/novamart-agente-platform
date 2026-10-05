"""A confirmed request is one request, and its audit trail must read as one.

Resuming after a confirmation builds a fresh Tracer. It used to start its
sequence at zero, so a confirmed request carried two events numbered 1, two
numbered 2 and so on -- and every reader orders by sequence, so the dashboard
and the API showed the two halves of the timeline shuffled together. The same
Tracer was also built without the platform's known secrets, so the events
written after a human approved an action were redacted less thoroughly than the
ones written before it.

SQLite is exercised as well as memory because its ORDER BY is what made the
interleaving visible.
"""

from __future__ import annotations

import dataclasses

import pytest

from agent_platform import platform as platform_module
from agent_platform.observability.events import EventType
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.persistence.sqlite import SQLiteRepository
from agent_platform.platform import AgentPlatform

WRITE_REQUEST = "Update order 1002 status to delivered"


@pytest.fixture(params=["memory", "sqlite"])
def repository(request, tmp_path):
    if request.param == "memory":
        return InMemoryRepository()
    return SQLiteRepository(tmp_path / "trail.db")


def _sequences(platform: AgentPlatform, request_id: str) -> list[int]:
    return [int(e["sequence"]) for e in platform.repository.events_for_request(request_id)]


@pytest.mark.parametrize("approved", [True, False])
def test_events_after_a_confirmation_continue_the_sequence(settings, repository, approved):
    platform = AgentPlatform(settings, repository=repository)
    try:
        suspended = platform.run(WRITE_REQUEST)
        assert suspended.status == "awaiting_confirmation"
        before = _sequences(platform, suspended.request_id)

        platform.confirm(suspended.request_id, approved=approved, actor="auditor")
        after = _sequences(platform, suspended.request_id)
    finally:
        platform.close()

    assert len(after) > len(before), "the resumed half wrote no events"
    assert len(after) == len(set(after)), f"duplicated sequence numbers: {after}"
    assert after == list(range(1, len(after) + 1)), after
    assert after[: len(before)] == before


def test_the_timeline_reads_in_the_order_it_happened(settings, repository):
    platform = AgentPlatform(settings, repository=repository)
    try:
        suspended = platform.run(WRITE_REQUEST)
        platform.confirm(suspended.request_id, approved=True, actor="auditor")
        events = platform.repository.events_for_request(suspended.request_id)
    finally:
        platform.close()

    types = [e["event_type"] for e in events]
    assert types[0] == EventType.REQUEST_STARTED.value
    resolved = types.index(EventType.CONFIRMATION_RESOLVED.value)
    # Everything from the suspension sits before the human's decision.
    assert EventType.REQUEST_STARTED.value not in types[1:]
    assert resolved > types.index(EventType.REQUEST_STARTED.value)


def test_the_resumed_tracer_redacts_the_same_secrets(settings, monkeypatch):
    """The second half of a request must know every secret the first half did."""
    grant_secret = "synthetic-grant-secret-" + "x" * 16
    configured = dataclasses.replace(settings, execution_grant_secret=grant_secret)

    created: list[tuple[str, ...]] = []
    real_tracer = platform_module.Tracer

    def recording_tracer(*args, **kwargs):
        tracer = real_tracer(*args, **kwargs)
        created.append(tracer._known_secrets)
        return tracer

    monkeypatch.setattr(platform_module, "Tracer", recording_tracer)

    platform = AgentPlatform(configured, repository=InMemoryRepository())
    try:
        suspended = platform.run(WRITE_REQUEST)
        platform.confirm(suspended.request_id, approved=True, actor="auditor")
    finally:
        platform.close()

    assert len(created) == 2, "expected one tracer per half of the request"
    first, resumed = created
    assert grant_secret in first
    assert resumed == first


def test_a_secret_in_a_resumed_event_is_redacted(settings, monkeypatch):
    """End to end: a known value reaching the trace after approval is scrubbed."""
    grant_secret = "synthetic-grant-secret-" + "y" * 16
    configured = dataclasses.replace(settings, execution_grant_secret=grant_secret)
    platform = AgentPlatform(configured, repository=InMemoryRepository())
    try:
        suspended = platform.run(WRITE_REQUEST)
        # The actor is caller-supplied text that lands in the trace verbatim.
        platform.confirm(
            suspended.request_id, approved=True, actor=f"ops {grant_secret}"
        )
        events = platform.repository.events_for_request(suspended.request_id)
    finally:
        platform.close()

    rendered = repr(events)
    assert grant_secret not in rendered
    assert "[REDACTED:known_secret]" in rendered
