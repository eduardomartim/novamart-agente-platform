"""A request to change something must not be read as a lookup by accident.

The router prefers the read-only path when a request is genuinely ambiguous --
"can you delete order 1001?" is a question, and an ambiguous request should not
default to the path that changes things. That rule is deliberate and stays.

What was not deliberate: the English write words were full words while the
Portuguese ones were stems, so "deleting" and "updating" matched nothing.
"Try deleting order ORD-1001" was therefore classified as a lookup and answered
with an order summary -- no security hole, since nothing ran, but the security
demonstration silently did not happen.

These pin both halves: inflected write verbs are recognised, and the
conservative rule for genuine questions is preserved.
"""

from __future__ import annotations

import pytest

from agent_platform.llm.stub import StubProvider


@pytest.mark.parametrize(
    "request_text",
    [
        "Try deleting order ORD-1001",
        "Start deleting order ORD-1001",
        "Go ahead and delete order ORD-1001",
        "Please delete order ORD-1001",
        "Updating order ORD-1002 to delivered",
        "Removing order ORD-1001 now",
    ],
)
def test_inflected_write_verbs_route_to_the_action_path(request_text):
    """An imperative that changes something belongs to the executor."""
    assert StubProvider._choose_route(request_text) == "executor", (
        f"{request_text!r} was not recognised as a write request"
    )


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        ("Can you delete order ORD-1001?", "researcher"),
        ("Could you update order ORD-1002?", "researcher"),
        ("Is it possible to remove order ORD-1001?", "researcher"),
        # No identifier, and nothing in the dataset records deletions, so no
        # tool can serve this. It reaches the orchestrator's honest refusal
        # instead of the researcher -- still the read-only side of the fork,
        # which is what this test protects.
        ("Which orders were deleted?", "direct_response"),
    ],
)
def test_questions_still_prefer_the_read_only_path(request_text, expected):
    """The conservative default survives: a question is not an instruction."""
    route = StubProvider._choose_route(request_text)
    assert route != "executor", (
        f"{request_text!r} was escalated to the action path; ambiguity must "
        "resolve towards reading, not writing"
    )
    assert route == expected, (
        f"{request_text!r} routed to {route!r}, expected {expected!r}"
    )


@pytest.mark.parametrize(
    "request_text",
    [
        "What is the status of order ORD-1001?",
        "Tell me about Ana Ribeiro",
        "Show me the orders for customer CUS-2001",
        "What is the refund policy?",
    ],
)
def test_ordinary_lookups_are_unaffected(request_text):
    assert StubProvider._choose_route(request_text) == "researcher"


def test_deleting_an_order_is_still_blocked_end_to_end(settings):
    """The routing fix must produce the refusal, not merely a different route."""
    from agent_platform.persistence.memory import InMemoryRepository
    from agent_platform.platform import AgentPlatform

    platform = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        result = platform.run("Try deleting order ORD-1001")
        assert result.status == "blocked"
        executed = [
            e.tool
            for e in platform.repository.events
            if e.event_type == "tool_call" and e.status == "success"
        ]
        assert "delete_record" not in executed
    finally:
        platform.close()
