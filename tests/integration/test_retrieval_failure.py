"""An empty context has two causes, and they must never be confused.

Retrieval ran and found nothing is a *result*: the knowledge base does not
cover the question, and saying so is correct.

Retrieval did not run is a *fault*: the tool raised, the model failed to
propose a lookup, or the search was refused. Reporting that as "I could not
find enough information in the available documents" is a confident claim about
the corpus manufactured out of an outage -- the user cannot tell the two apart,
and the second one is a lie.

Before the 7G.0 fix the platform made exactly that substitution. This module
pins both halves so it cannot come back.
"""

from __future__ import annotations

import copy
import dataclasses

import pytest

from agent_platform.agent.answer import AnswerOutcome
from agent_platform.config import Settings
from agent_platform.llm.provider import Purpose
from agent_platform.llm.stub import StubProvider
from agent_platform.models import Route
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.tools import fake_tools, registry
from agent_platform.tools.dataset import dataset_digest
from agent_platform.tools.execution import require_gateway

DIGEST = "db512de8207f751e"
READ = "What is the refund policy?"


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    return Settings.from_env(load_dotenv_file=False)


@pytest.fixture
def broken_search(monkeypatch):
    """Replace the search handler with one that raises.

    This is what an embedding outage will look like once hybrid retrieval is
    wired in: the tool raises, the gateway records ``status="failure"``, and
    the context arrives empty.
    """

    def failing(query: str):
        require_gateway("search")
        raise RuntimeError("embedding provider unavailable")

    patched = tuple(
        dataclasses.replace(tool, handler=failing) if tool.name == "search" else tool
        for tool in registry.TOOL_DEFINITIONS
    )
    monkeypatch.setattr(registry, "TOOL_DEFINITIONS", patched)
    monkeypatch.setattr(fake_tools, "TOOL_DEFINITIONS", patched)
    return failing


class CountingStub(StubProvider):
    """Counts model calls, per purpose."""

    def __init__(self) -> None:
        super().__init__()
        self.by_purpose: dict[str, int] = {}

    def generate(self, prompt, **kwargs):
        purpose = kwargs.get("purpose")
        key = purpose.value if isinstance(purpose, Purpose) else str(purpose)
        self.by_purpose[key] = self.by_purpose.get(key, 0) + 1
        return super().generate(prompt, **kwargs)


def run(settings, question, *, provider=None):
    platform = AgentPlatform(settings, repository=InMemoryRepository())
    if provider is not None:
        platform.provider = provider
    try:
        return platform.run(question), list(platform.repository.events)
    finally:
        platform.close()


def answer_event(events):
    return next((e for e in events if e.event_type == "answer_composed"), None)


# ======================================================= RETRIEVAL FAILURE


def test_a_failing_search_is_reported_as_a_provider_error(settings, broken_search):
    """The behaviour 7G.0 fixed. Before it, this was insufficient_evidence."""
    _result, events = run(settings, READ)
    event = answer_event(events)

    assert event is not None, "the answer node did not run"
    assert event.payload["outcome"] == AnswerOutcome.PROVIDER_ERROR.value


def test_a_failing_search_is_not_reported_as_missing_information(
    settings, broken_search
):
    """The specific false claim this module exists to prevent."""
    result, events = run(settings, READ)
    event = answer_event(events)

    assert event.payload["outcome"] != AnswerOutcome.INSUFFICIENT_EVIDENCE.value
    lowered = (result.response or "").lower()
    assert "could not find enough information" not in lowered
    assert "available documents" not in lowered


def test_a_failing_search_does_not_report_success(settings, broken_search):
    result, _events = run(settings, READ)
    assert result.status != "success"


def test_the_tool_failure_is_still_visible_in_the_trace(settings, broken_search):
    """The gateway's record must survive; the fix must not hide the cause."""
    _result, events = run(settings, READ)
    calls = [e for e in events if e.event_type == "tool_call"]

    assert calls, "the search tool never ran"
    assert calls[0].status == "failure"
    assert "RuntimeError" in (calls[0].error or "")


def test_the_answerer_is_never_consulted_when_retrieval_failed(
    settings, broken_search
):
    """Spending a model call to answer from documents that were never
    retrieved would waste quota on a question nothing can answer."""
    provider = CountingStub()
    _result, _events = run(settings, READ, provider=provider)

    assert provider.by_purpose.get(Purpose.RESPOND.value, 0) == 0


def test_the_failure_reason_reaches_the_trace(settings, broken_search):
    _result, events = run(settings, READ)
    reason = answer_event(events).payload["reason"]

    assert reason, "the provider error carried no reason"
    assert "retrieval did not complete" in reason


def test_a_failed_retrieval_carries_no_citations(settings, broken_search):
    _result, events = run(settings, READ)
    assert answer_event(events).payload["citations"] == []


# =================================================== SUCCESSFUL EMPTY RETRIEVAL


def test_a_search_that_finds_nothing_is_still_insufficient_evidence(settings):
    """The other half. Retrieval succeeded; the corpus simply has no answer."""
    _result, events = run(settings, "How do I reset my password?")
    event = answer_event(events)

    assert event is not None
    assert event.payload["outcome"] == AnswerOutcome.INSUFFICIENT_EVIDENCE.value
    assert event.payload["document_count"] == 0
    assert event.payload["citations"] == []


def test_an_empty_result_still_reports_success(settings):
    """Finding nothing is a correct outcome, not a failed request."""
    result, _events = run(settings, "How do I reset my password?")
    assert result.status == "success"
    assert "could not find enough information" in (result.response or "").lower()


def test_the_answerer_is_not_consulted_for_an_empty_result_either(settings):
    provider = CountingStub()
    _result, _events = run(settings, "How do I reset my password?", provider=provider)
    assert provider.by_purpose.get(Purpose.RESPOND.value, 0) == 0


def test_the_two_cases_produce_different_outcomes(settings, monkeypatch):
    """The distinction, asserted directly rather than inferred from two files."""
    empty_result, empty_events = run(settings, "How do I reset my password?")

    def failing(query: str):
        require_gateway("search")
        raise RuntimeError("embedding provider unavailable")

    patched = tuple(
        dataclasses.replace(tool, handler=failing) if tool.name == "search" else tool
        for tool in registry.TOOL_DEFINITIONS
    )
    monkeypatch.setattr(registry, "TOOL_DEFINITIONS", patched)
    monkeypatch.setattr(fake_tools, "TOOL_DEFINITIONS", patched)
    failed_result, failed_events = run(settings, READ)

    empty_outcome = answer_event(empty_events).payload["outcome"]
    failed_outcome = answer_event(failed_events).payload["outcome"]

    assert empty_outcome == AnswerOutcome.INSUFFICIENT_EVIDENCE.value
    assert failed_outcome == AnswerOutcome.PROVIDER_ERROR.value
    assert empty_outcome != failed_outcome
    assert empty_result.status != failed_result.status


# ==================================================== THE PINNED ASSUMPTION


def test_only_the_research_node_writes_errors_before_the_answer_node():
    """``answer_node`` distinguishes the two cases by reading ``state['errors']``.

    That is only sound while ``research_node`` is the sole writer of errors on
    the read path. ``execute_node`` also appends errors, but it lives on the
    action branch and never reaches the answer node. If a third writer appears
    upstream, this test fails and the heuristic must be revisited.
    """
    import ast
    import inspect

    from agent_platform.orchestration import graph

    tree = ast.parse(inspect.getsource(graph))

    # Attribute each call to its *nearest* enclosing function. The nodes are
    # nested inside build_graph, so a plain ast.walk would credit every call to
    # the outer function and prove nothing.
    nearest: dict[int, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            for child in ast.walk(node):
                if child is not node:
                    nearest[id(child)] = node.name

    writers = {
        nearest[id(call)]
        for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "append_error"
        and id(call) in nearest
    }

    assert writers == {"research_node", "execute_node"}, (
        f"a new writer of state['errors'] appeared: {writers}. answer_node "
        "reads that field to tell a retrieval failure from an empty result."
    )


# ==================================================== SECURITY / INTEGRITY


def test_a_failed_retrieval_touches_no_tool_and_no_action(settings, broken_search):
    orders = copy.deepcopy(fake_tools._orders)
    customers = copy.deepcopy(fake_tools._customers)

    result, events = run(settings, READ)
    kinds = {e.event_type for e in events}
    executed = [e.tool for e in events if e.event_type == "tool_call" and e.status == "success"]

    assert executed == [], "a tool executed during a failed retrieval"
    assert "confirmation_requested" not in kinds
    assert result.route == Route.RESEARCHER.value
    assert fake_tools._orders == orders
    assert fake_tools._customers == customers


def test_a_failed_retrieval_never_reaches_the_action_flow(settings, broken_search):
    _result, events = run(settings, READ)
    agents = {e.agent for e in events if e.agent}

    assert "executor" not in agents
    assert "validation" not in {e.event_type for e in events}


def test_the_dataset_is_unchanged():
    assert dataset_digest() == DIGEST
