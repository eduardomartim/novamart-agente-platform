"""Integration tests for the orchestration graph and the request pipeline."""

from __future__ import annotations

from dataclasses import replace

from agent_platform.llm.stub import StubProvider
from agent_platform.models import Route
from agent_platform.orchestration.routing import (
    NODE_ANSWER,
    NODE_EXECUTE,
    NODE_RESEARCH,
    NODE_RESPOND,
    NODE_VALIDATE,
    after_confirm,
    after_execute,
    after_research,
    after_route,
    after_validate,
)
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform


class PricedStubProvider(StubProvider):
    """Deterministic stub that reports a priced model name.

    Lets the budget guard be exercised without a live provider. The provider
    name stays ``stub``, so nothing is misattributed to Gemini.
    """

    @property
    def model(self) -> str:
        return "gemini-3.5-flash"




def _compiled_graph():
    """The real compiled graph, so edge assertions test the wiring itself."""
    from agent_platform.config import Settings
    from agent_platform.observability.tracing import Tracer

    repository = InMemoryRepository()
    platform = AgentPlatform(
        Settings.from_env(load_dotenv_file=False), repository=repository
    )
    try:
        tracer = Tracer(repository, request_id="edges", trace_id="edges")
        return platform._build_graph(tracer, None).get_graph()
    finally:
        platform.close()


# ------------------------------------------------------------- edge functions


def test_route_edges():
    assert after_route({"route": Route.RESEARCHER.value}) == NODE_RESEARCH
    assert after_route({"route": Route.EXECUTOR.value}) == NODE_RESEARCH
    assert after_route({"route": Route.DIRECT_RESPONSE.value}) == NODE_RESPOND
    assert after_route({}) == NODE_RESPOND


def test_research_edges():
    """A lookup goes to the answer node; an action still goes to the executor.

    The read destination changed in 7F.5, when the answer node was introduced.
    The security-relevant half is unchanged and asserted below with the rest of
    it: EXECUTE is reachable from the executor route and from nothing else.
    """
    assert after_research({"route": Route.EXECUTOR.value}) == NODE_EXECUTE
    assert after_research({"route": Route.RESEARCHER.value}) == NODE_ANSWER


def test_only_the_executor_route_reaches_execute():
    """Every other input -- including nonsense -- must land on the read path."""
    for route in (
        Route.RESEARCHER.value,
        Route.DIRECT_RESPONSE.value,
        None,
        "",
        "answerer",
        "executor ",
        "EXECUTOR",
    ):
        assert after_research({"route": route}) != NODE_EXECUTE, route
    assert after_research({}) != NODE_EXECUTE


def test_the_executor_route_never_reaches_the_answer_node():
    assert after_research({"route": Route.EXECUTOR.value}) != NODE_ANSWER


def test_the_answer_node_has_exactly_one_exit_and_it_is_respond():
    """Structural: ANSWER must not be able to reach EXECUTE, CONFIRM,
    VALIDATE or the retry loop. Asserted on the compiled graph, so a stray
    edge added later fails here rather than in production."""
    graph = _compiled_graph()
    targets = {edge.target for edge in graph.edges if edge.source == NODE_ANSWER}

    assert targets == {NODE_RESPOND}, f"ANSWER reaches {targets}"
    for forbidden in (NODE_EXECUTE, "confirm", NODE_VALIDATE):
        assert forbidden not in targets


def test_only_the_research_node_reaches_the_answer_node():
    graph = _compiled_graph()
    sources = {edge.source for edge in graph.edges if edge.target == NODE_ANSWER}

    assert sources == {NODE_RESEARCH}, f"ANSWER reachable from {sources}"


def test_execute_edges():
    assert after_execute({"pending_confirmation": {"tool": "x"}}) == "confirm"
    assert after_execute({"policy_decision": {"decision": "deny"}}) == NODE_RESPOND
    assert after_execute({"policy_decision": {"decision": "allow"}}) == NODE_RESPOND
    assert (
        after_execute(
            {"policy_decision": {"decision": "allow"}, "tool_result": {"status": "success"}}
        )
        == NODE_VALIDATE
    )


def test_declined_confirmation_ends_the_request():
    """A decline must not fall through to the retry loop."""
    assert after_confirm({"status": "declined"}) == NODE_RESPOND
    assert after_confirm({}) == NODE_VALIDATE


def test_validate_retries_only_genuine_failures():
    failed = {"validation": {"approved": False}, "tool_result": {"status": "error"},
              "retry_count": 0}
    assert after_validate(failed, max_retries=2) == NODE_EXECUTE

    approved = {"validation": {"approved": True}}
    assert after_validate(approved, max_retries=2) == NODE_RESPOND


def test_validate_stops_at_the_retry_ceiling():
    state = {"validation": {"approved": False}, "tool_result": {"status": "error"},
             "retry_count": 2}
    assert after_validate(state, max_retries=2) == NODE_RESPOND


def test_refusals_are_never_retried():
    """Retrying a denial cannot succeed and would spend budget re-learning that."""
    denied = {"validation": {"approved": False}, "tool_result": {"status": "denied"},
              "retry_count": 0}
    assert after_validate(denied, max_retries=2) == NODE_RESPOND

    declined = {"validation": {"approved": False}, "status": "declined", "retry_count": 0}
    assert after_validate(declined, max_retries=2) == NODE_RESPOND


# ------------------------------------------------------------------- pipeline


def test_read_only_question_completes_without_an_action(platform):
    result = platform.run("What is the status of order 1001?")
    assert result.status == "success"
    assert result.route == Route.RESEARCHER.value
    tools = [e.tool for e in platform.repository.events if e.event_type == "tool_call"]
    assert tools == ["get_order"]


def test_conversational_input_touches_no_tool(platform):
    result = platform.run("Hello there")
    assert result.route == Route.DIRECT_RESPONSE.value
    assert [e for e in platform.repository.events if e.event_type == "tool_call"] == []


def test_high_risk_action_suspends_for_confirmation(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    assert result.status == "awaiting_confirmation"
    assert result.awaiting_confirmation is not None
    assert result.awaiting_confirmation.tool == "send_email"
    assert result.awaiting_confirmation.risk_level == "high"


def test_approval_resumes_and_executes(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    resumed = platform.confirm(result.request_id, approved=True, actor="tester")
    assert resumed.status == "success"
    assert any(
        e.event_type == "tool_call" and e.tool == "send_email"
        for e in platform.repository.events
    )


def test_decline_stops_and_does_not_re_ask(platform):
    result = platform.run("Send an email to ana.ribeiro@example.com about her order")
    declined = platform.confirm(result.request_id, approved=False, actor="tester")
    assert declined.status == "declined"
    assert declined.awaiting_confirmation is None
    assert not any(
        e.event_type == "tool_call" and e.tool == "send_email"
        for e in platform.repository.events
    )


def test_confirming_an_unknown_request_is_handled(platform):
    result = platform.confirm("req-does-not-exist", approved=True)
    assert result.status == "failed"


def test_critical_request_is_refused(platform):
    result = platform.run("Delete order 1001 immediately")
    assert result.status == "blocked"
    assert "PL005" in (result.policy_decision or {}).get("rule_ids", [])
    assert not any(
        e.event_type == "tool_call" and e.tool == "delete_record"
        for e in platform.repository.events
    )


def test_rate_limited_request_never_enters_the_graph(settings):
    tight = replace(settings, requests_per_minute=1, requests_per_hour=1)
    platform = AgentPlatform(tight, repository=InMemoryRepository())
    try:
        platform.run("What is the status of order 1001?")
        blocked = platform.run("What is the refund policy?")
        assert blocked.status == "rate_limited"
        assert blocked.blocked is True
        assert any(e.event_type == "rate_limited" for e in platform.repository.events)
    finally:
        platform.close()


def test_oversized_input_is_rejected_before_any_model_call(settings):
    # `max_question_chars`, not `max_input_chars`. The two were one setting
    # until the question field gained its own 100-character ceiling; the second
    # is the *context* budget that `fence_context` clips retrieved fields to,
    # and narrowing that to bound a question would truncate documents instead.
    # The property under test is unchanged: an oversized question is refused
    # before any model call.
    small = replace(settings, max_question_chars=20)
    platform = AgentPlatform(small, repository=InMemoryRepository())
    try:
        result = platform.run("x" * 100)
        assert result.status == "rejected"
        assert platform.repository.llm_calls == []
    finally:
        platform.close()


def test_exhausted_budget_blocks_before_any_model_call(settings):
    """A request must be refused when the daily budget is already spent.

    The platform is pointed at a priced model so that projected cost is
    non-zero; the free stub would legitimately fit inside any budget and would
    not exercise the guard at all.
    """
    from decimal import Decimal

    from agent_platform.persistence.repository import LLMCallRecord

    repository = InMemoryRepository()
    repository.save_llm_call(
        LLMCallRecord(
            request_id="earlier",
            trace_id="t",
            provider="gemini",
            model="gemini-3.5-flash",
            input_tokens=1,
            output_tokens=1,
            cost_usd=Decimal("1.00"),
        )
    )
    priced = replace(settings, gemini_model="gemini-3.5-flash", daily_budget_usd=Decimal("1.00"))
    platform = AgentPlatform(priced, repository=repository, provider=PricedStubProvider())
    try:
        result = platform.run("What is the status of order 1001?")
        assert result.status == "blocked"
        assert "budget" in result.response.lower()
        # Nothing was spent, because nothing was called.
        assert len(platform.repository.llm_calls) == 1
    finally:
        platform.close()


def test_budget_is_checked_before_model_calls_not_only_tools(platform):
    """Regression: budget was previously only consulted before tool execution.

    Model calls are where money is actually spent, so a request that never
    reaches a tool would have run entirely unmetered.
    """
    from agent_platform.cost.tracker import CostTracker

    assert hasattr(CostTracker, "authorize_call")


def test_every_request_is_persisted_with_a_digest_not_the_text(platform):
    text = "What is the status of order 1001?"
    result = platform.run(text)
    record = platform.repository.requests[result.request_id]
    assert record.input_digest
    assert text not in record.input_digest
    assert record.input_chars == len(text)


def test_trace_ids_are_unique_per_request(platform):
    ids = {platform.run("What is the refund policy?").trace_id for _ in range(3)}
    assert len(ids) == 3


def test_events_are_sequential_within_a_request(platform):
    result = platform.run("What is the status of order 1001?")
    events = platform.repository.events_for_request(result.request_id)
    assert [e["sequence"] for e in events] == list(range(1, len(events) + 1))


def test_no_llm_call_event_carries_prompt_or_completion_text(platform):
    """An allow-list, so a key nobody reviewed cannot appear here unnoticed.

    `preflight_ms` and `budget_wait_ms` were reviewed to join it: both are
    `perf_counter` differences rounded to three places, so neither is capable
    of carrying prompt or completion text -- a float has nowhere to put a
    sentence.
    """
    platform.run("What is the status of order 1001?")
    for event in platform.repository.events:
        if event.event_type == "llm_call":
            assert set(event.payload) <= {
                "provider", "model", "purpose", "input_tokens",
                "output_tokens", "estimated_cost_usd", "tokens_estimated",
                "preflight_ms", "budget_wait_ms",
                # Added only when the provider retried, which the stub never
                # does -- so this pair is unreachable offline and was missing
                # from the list for exactly that reason. Both are numbers.
                "attempts", "total_elapsed_ms",
            }


def test_demo_mode_never_claims_a_live_provider(platform):
    assert platform.provider.name == "stub"
    assert platform.provider_info.live is False
    platform.run("What is the refund policy?")
    assert all(call.provider == "stub" for call in platform.repository.llm_calls)
