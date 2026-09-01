"""What a read-only request actually *says* back.

Every other test of the read path checks status, route, tools or events. None
checked the sentence. So when the answer node was introduced and
``INSUFFICIENT_EVIDENCE`` began short-circuiting the deterministic summary,
five of the six read-only tools started replying "I could not find enough
information in the available documents" about records they had just read
successfully -- and 1158 tests stayed green.

These tests assert the text. That is the whole point of them: a request can be
routed correctly, call the right tool, touch nothing it shouldn't, report
``success``, and still tell the user something false.
"""

from __future__ import annotations

import pytest

from agent_platform.agent.answer import NO_EVIDENCE_TEXT, AnswerOutcome
from agent_platform.config import Settings
from agent_platform.llm.provider import Purpose
from agent_platform.llm.stub import StubProvider
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform
from agent_platform.tools.dataset import dataset_digest

DIGEST = "db512de8207f751e"

#: The opening of the sentence that must never appear in reply to a lookup that
#: succeeded. Taken from the source rather than retyped, so the day the wording
#: changes this test follows it instead of quietly checking for a string nobody
#: produces. Lower-cased because every comparison below lower-cases the reply.
DENIAL = NO_EVIDENCE_TEXT[:40].lower()


@pytest.fixture
def settings(tmp_path, monkeypatch) -> Settings:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    return Settings.from_env(load_dotenv_file=False)


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


class CountingStub(StubProvider):
    def __init__(self) -> None:
        super().__init__()
        self.by_purpose: dict[str, int] = {}

    def generate(self, prompt, **kwargs):
        purpose = kwargs.get("purpose")
        key = purpose.value if isinstance(purpose, Purpose) else str(purpose)
        self.by_purpose[key] = self.by_purpose.get(key, 0) + 1
        return super().generate(prompt, **kwargs)


# ============================================ the regression, tool by tool


@pytest.mark.parametrize(
    ("question", "tool", "must_contain"),
    [
        ("What is the status of order ORD-1001?", "get_order", ["ORD-1001", "shipped"]),
        ("Tell me about customer CUS-2001", "get_customer", ["CUS-2001", "Ana Ribeiro"]),
        (
            "Show me the orders for customer CUS-2001",
            "list_customer_orders",
            ["CUS-2001", "ORD-1001"],
        ),
        ("What is ticket TKT-4002 about?", "get_ticket", ["TKT-4002", "ORD-1003"]),
        (
            "What is the status of Ana Ribeiro's order?",
            "find_customer",
            ["Ana Ribeiro", "CUS-2001"],
        ),
    ],
)
def test_a_successful_lookup_answers_with_its_data(settings, question, tool, must_contain):
    """The failing case. Before the fix every one of these replied with DENIAL."""
    result, events = run(settings, question)

    executed = [e.tool for e in events if e.event_type == "tool_call" and e.status == "success"]
    assert tool in executed, f"{tool} did not run; this test is not measuring what it claims"

    assert DENIAL not in (result.response or "").lower(), (
        f"{tool} succeeded but the reply denied having the information"
    )
    for fragment in must_contain:
        assert fragment in result.response, (
            f"{tool} succeeded but {fragment!r} is missing from the reply"
        )


def test_a_knowledge_base_question_still_lists_its_articles(settings):
    result, _events = run(settings, "What is the refund policy?")
    assert DENIAL not in (result.response or "").lower()
    assert "Refund policy" in result.response


# ================================================ the other half, preserved


def test_a_knowledge_base_question_with_no_match_still_says_so(settings):
    """The fix must not turn a genuine absence of evidence into a claim."""
    result, events = run(settings, "How do I reset my password?")

    assert DENIAL in (result.response or "").lower()
    assert result.status == "success"
    assert answer_event(events).payload["outcome"] == (
        AnswerOutcome.INSUFFICIENT_EVIDENCE.value
    )


def test_a_lookup_that_finds_nothing_does_not_invent_data(settings):
    """A missing record must be reported as missing, not fabricated."""
    result, events = run(settings, "What is the status of order ORD-9999?")

    executed = [e.tool for e in events if e.event_type == "tool_call" and e.status == "success"]
    if "get_order" not in executed:
        pytest.skip("the stub router did not select get_order for this phrasing")

    lowered = (result.response or "").lower()
    assert "no record was found" in lowered or "not found" in lowered
    assert "shipped" not in lowered
    assert "R$" not in (result.response or "")


def test_a_retrieval_failure_is_still_a_provider_error(settings):
    """7G.0 must not regress: an outage stays an outage."""
    import dataclasses

    from agent_platform.tools import fake_tools, registry
    from agent_platform.tools.execution import require_gateway

    def failing(query: str):
        require_gateway("search")
        raise RuntimeError("embedding provider unavailable")

    patched = tuple(
        dataclasses.replace(t, handler=failing) if t.name == "search" else t
        for t in registry.TOOL_DEFINITIONS
    )
    original_reg, original_ft = registry.TOOL_DEFINITIONS, fake_tools.TOOL_DEFINITIONS
    registry.TOOL_DEFINITIONS = patched
    fake_tools.TOOL_DEFINITIONS = patched
    try:
        result, events = run(settings, "What is the refund policy?")
    finally:
        registry.TOOL_DEFINITIONS = original_reg
        fake_tools.TOOL_DEFINITIONS = original_ft

    assert answer_event(events).payload["outcome"] == AnswerOutcome.PROVIDER_ERROR.value
    assert result.status == "failed"
    assert DENIAL not in (result.response or "").lower()


# ========================================================= cost of the path


def test_a_record_lookup_never_consults_the_answerer(settings):
    """There are no documents to answer from, so there is nothing to spend a
    call on. The deterministic summary handles it, as it always did."""
    provider = CountingStub()
    run(settings, "What is the status of order ORD-1001?", provider=provider)
    assert provider.by_purpose.get(Purpose.RESPOND.value, 0) == 0


def test_a_knowledge_base_question_does_consult_the_answerer(settings):
    """The complement, so the assertion above is not passing because the
    answerer is never called for anything."""
    provider = CountingStub()
    run(settings, "What is the refund policy?", provider=provider)
    assert provider.by_purpose.get(Purpose.RESPOND.value, 0) == 1


# ======================================================== the discriminator


def test_the_discriminator_reads_tool_shape_not_model_output():
    """``_asked_for_documents`` keys on the ``results`` field, which the tool
    layer produces. Nothing a model says can change which branch is taken."""
    from agent_platform.orchestration.graph import _asked_for_documents

    assert _asked_for_documents([]) is True
    assert _asked_for_documents([{"source": "search", "data": {"results": []}}]) is True
    assert (
        _asked_for_documents([{"source": "get_order", "data": {"found": True, "order": {}}}])
        is False
    )
    assert _asked_for_documents([{"source": "get_order", "data": None}]) is False


def test_the_dataset_is_unchanged():
    assert dataset_digest() == DIGEST
