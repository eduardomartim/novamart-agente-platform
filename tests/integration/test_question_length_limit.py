"""The question field accepts 100 characters and refuses the 101st.

Two limits are being asserted here and they are deliberately different numbers.
``max_question_chars`` bounds what a person may ask; ``max_input_chars`` is the
*context* budget that ``fence_context`` clips retrieved fields to. Collapsing
them would truncate knowledge-base documents to the length of a question, so
the separation is a property worth pinning rather than an accident.

The unit throughout is the character -- a Python code point. An accented letter
costs one and so does an emoji. The browser's ``maxlength`` counts UTF-16 units
and is therefore stricter for anything outside the BMP; it can refuse early,
never late, which is why the backend is the authority and this file tests it as
one.

No network, no provider, no live anything: the stub answers and the oversized
inputs never get far enough to need a provider at all.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from agent_platform.config import DEFAULT_MAX_QUESTION_CHARS, Settings
from agent_platform.guardrails.input import assess_input
from agent_platform.persistence.memory import InMemoryRepository
from agent_platform.platform import AgentPlatform

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"

#: One code point each, and one of them is two UTF-16 units.
ACCENT = "ç"
EMOJI = "😀"


@pytest.fixture
def platform(settings: Settings):
    instance = AgentPlatform(settings, repository=InMemoryRepository())
    try:
        yield instance
    finally:
        instance.close()


# ============================================================ the limit itself


def test_the_configured_ceiling_is_one_hundred():
    assert DEFAULT_MAX_QUESTION_CHARS == 100
    assert Settings.from_env(load_dotenv_file=False).max_question_chars == 100


def test_the_question_limit_is_not_the_context_budget(settings):
    """Separate numbers, because they bound separate things.

    If these ever become one setting, a question-sized limit would clip every
    retrieved document to the same length and the answerer would cite
    fragments.
    """
    assert settings.max_question_chars < settings.max_input_chars
    assert settings.max_input_chars == 8000


@pytest.mark.parametrize(
    "length,accepted",
    [(1, True), (99, True), (100, True), (101, False), (500, False)],
)
def test_the_boundary_is_at_one_hundred(settings, length, accepted):
    assessment = assess_input("a" * length, max_chars=settings.max_question_chars)
    assert assessment.accepted is accepted
    if not accepted:
        assert "exceeds 100 characters" in (assessment.rejection_reason or "")
        assert f"got {length}" in (assessment.rejection_reason or "")


# =================================================================== unicode


@pytest.mark.parametrize("filler", [ACCENT, EMOJI, "日", "a"])
def test_the_unit_is_the_character_not_the_byte(settings, filler):
    """100 of anything is accepted; 101 of anything is not.

    ``ç`` is two bytes and ``😀`` is four, so a byte-denominated limit would
    refuse 50 emoji and this test would fail on the emoji case.
    """
    assert assess_input(filler * 100, max_chars=settings.max_question_chars).accepted
    assert not assess_input(filler * 101, max_chars=settings.max_question_chars).accepted


def test_an_emoji_costs_one_character_here_and_two_in_the_browser():
    """Records the asymmetry the front end relies on.

    ``maxlength`` counts UTF-16 units, so the browser stops a run of emoji at
    fifty where Python would allow a hundred. Stricter, never looser -- which
    is what makes it safe to let the browser refuse first and the backend
    decide last.
    """
    hundred_emoji = EMOJI * 100
    assert len(hundred_emoji) == 100, "Python counts code points"
    assert len(hundred_emoji.encode("utf-16-le")) // 2 == 200, "the browser counts units"


def test_a_full_length_question_survives_normalisation(settings):
    """Exactly at the ceiling, with accents, and nothing is trimmed."""
    question = "Qual o status do pedido de Ana Ribeiro e a política de reembolso?"
    question = question.ljust(100, "?")
    assert len(question) == 100
    assessment = assess_input(question, max_chars=settings.max_question_chars)
    assert assessment.accepted
    assert len(assessment.normalized_input) == 100, "an accepted question was clipped"


# ================================ nothing downstream sees an oversized question


def test_an_oversized_question_is_refused_by_the_platform(platform):
    result = platform.run("a" * 101)
    assert result.status == "rejected"
    assert result.blocked is True
    assert "exceeds 100 characters" in result.response


def test_an_oversized_question_reaches_no_agent_no_model_and_no_tool(platform):
    """The refusal is placed before `resources.begin` and before the graph, so
    there is nothing to unwind: no router ran, no tool executed, no model was
    asked."""
    platform.run("b" * 250)

    kinds = {event.event_type for event in platform.repository.events}
    assert "input_rejected" in kinds
    for forbidden in ("agent_started", "llm_call", "tool_call", "policy_decision"):
        assert forbidden not in kinds, f"{forbidden} happened for a refused question"

    assert platform.resources.active_requests() == 0, (
        "resource accounting was started for a refused question"
    )


def test_a_question_at_the_ceiling_does_run(platform):
    """The control: 100 characters is a real question, not a refusal."""
    result = platform.run("What is the status of order ORD-1001?".ljust(100, "?")[:100])
    assert result.status != "rejected"
    kinds = {event.event_type for event in platform.repository.events}
    assert "agent_started" in kinds


def test_an_oversized_question_is_never_silently_trimmed(platform):
    """Refused outright. A question cut at 100 is a different question, and
    answering it would answer something nobody asked."""
    result = platform.run("c" * 300)
    assert result.status == "rejected"
    answered = [
        event for event in platform.repository.events if event.event_type == "tool_call"
    ]
    assert answered == []


def test_the_ceiling_travels_with_the_settings(settings):
    """A deployment that narrows it is honoured; nothing hard-codes 100 twice."""
    narrow = replace(settings, max_question_chars=10)
    platform = AgentPlatform(narrow, repository=InMemoryRepository())
    try:
        assert platform.run("a" * 11).status == "rejected"
    finally:
        platform.close()


# ====================================================== the widget in the page


def _orchestrator_app():
    """Drive the real dashboard script to the Orchestrator page.

    Navigated the way the existing dashboard tests navigate -- through the
    navigation radio, keyed `nav_pt` -- and with the caches cleared, because
    `@st.cache_resource` is process-global and outlives one AppTest run.
    """
    import os

    import streamlit as st
    from streamlit.testing.v1 import AppTest

    os.environ["GEMINI_API_KEY"] = ""
    st.cache_resource.clear()
    st.cache_data.clear()

    app = AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=60)
    app.run()
    app.radio(key="nav_pt").set_value("orchestrator").run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def test_the_question_field_declares_maxlength_one_hundred():
    """Asserted on the widget Streamlit actually rendered, not on source text.

    ``max_chars`` is what becomes ``maxlength`` on the input element, which is
    the browser's own refusal of the 101st character.
    """
    app = _orchestrator_app()
    fields = [widget for widget in app.text_input if widget.key == "question_box"]
    assert fields, "the Orchestrator question field was not rendered"
    assert fields[0].proto.max_chars == 100


def test_the_field_carries_no_hand_written_counter():
    """The count belongs to the widget, not to us.

    Setting ``max_chars`` makes Streamlit draw and update ``n/100`` beside the
    field itself. A second counter was written here and removed: Streamlit
    re-renders the input from its own state, so one reading the DOM went stale
    and displayed ``156/100`` while the field was empty. This pins the removal,
    because a duplicate that can disagree with the field is worse than none.
    """
    app = _orchestrator_app()
    markdown = " ".join(block.value for block in app.markdown)
    assert "ap-charcount" not in markdown
