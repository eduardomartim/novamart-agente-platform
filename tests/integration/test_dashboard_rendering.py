"""Two things the dashboard got wrong on screen while getting them right inside.

Both were found by walking the interface rather than reading it, and neither
would have been caught by asserting on `RunResult.response`: the platform's
text was correct and the rendering changed it.

**Currency.** Streamlit reads `$...$` as inline LaTeX. Brazilian money is
written `R$`, so a sentence carrying two amounts -- which every order summary
and every revenue total does -- pairs the dollar signs, swallows them, and
renders what is between as maths. Measured on the page:

    "Os 40 pedidos somam R 33.002,50. Descontando (R 6.374,90),
     o valor efetivo é R$ 26.627,60."

Three amounts, two spellings, one sentence.

**Out of scope.** `declined` arrives for two different events: a human refusing
a suspended action, and the platform having no tool that could answer at all.
Both carry no `blocked_by`, because in neither case did a control refuse
anything -- so the view used to call both BLOCKED, and the honest refusal
appeared wearing the colour of a security denial while the message beside it
said the opposite.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))

import execution_view  # noqa: E402


@pytest.fixture(scope="module")
def dashboard(tmp_path_factory):
    """The dashboard module, imported the only way it can be.

    `dashboard/app.py` calls `main()` at import: it is a Streamlit script, not
    a library. Importing it bare renders outside a session (`Cursor is not
    set`) and builds a platform against whatever `.env` holds, which the live
    gate correctly refuses. Running it once through `AppTest` with the demo
    environment set puts the module in `sys.modules`, after which its helpers
    can be called directly -- the same approach `test_recruiter_experience.py`
    uses.
    """
    saved = {name: os.environ.get(name) for name in ("GEMINI_API_KEY", "DATABASE_PATH")}
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(tmp_path_factory.mktemp("render") / "r.db")
    st.cache_resource.clear()
    st.cache_data.clear()
    try:
        AppTest.from_file(str(DASHBOARD / "app.py"), default_timeout=120).run()
        import app

        yield app
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        st.cache_resource.clear()


def _started(sequence: int = 1) -> dict:
    return {"sequence": sequence, "event_type": "request_started", "status": "info"}


# =============================================================== currency


def test_a_dollar_sign_is_escaped_before_markdown_sees_it(dashboard):
    """The fix itself: `$` reaches the renderer escaped, so it stays visible."""
    text = "Os 40 pedidos somam R$ 33.002,50 e o líquido é R$ 26.627,60."
    rendered = dashboard.as_prose(text)

    assert rendered.count("\\$") == 2, rendered
    assert "$" in rendered
    # Nothing else about the sentence may change.
    assert rendered.replace("\\$", "$") == text


def test_every_amount_survives_a_sentence_with_several(dashboard):
    """One amount always worked. Two is where the pairing began."""
    amounts = ["R$ 1,00", "R$ 2,00", "R$ 3,00", "R$ 4,00"]
    rendered = dashboard.as_prose(" ".join(amounts))
    assert rendered.count("\\$") == len(amounts)


def test_text_with_no_currency_is_returned_unchanged(dashboard):
    text = "Temos 12 clientes cadastrados: 6 Standard, 4 Ouro, 2 Platinum."
    assert dashboard.as_prose(text) == text


def test_the_response_is_rendered_through_the_escape():
    """A guard on the wiring, not just the helper.

    The helper is only worth having if the response goes through it, and the
    call is one edit away from being dropped.
    """
    import ast

    source = (DASHBOARD / "app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wrapped = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "as_prose"
    ]
    assert wrapped, "the response is no longer rendered through as_prose()"


# ============================================================= out of scope


def test_a_question_nothing_can_answer_is_not_a_block():
    """No tool ran, no rule fired, nothing refused anything."""
    view = execution_view.build(
        [_started(), {"sequence": 2, "event_type": "agent_started",
                      "status": "info", "agent": "router"}],
        status="declined",
    )
    assert view.outcome is execution_view.State.OUT_OF_SCOPE
    assert view.blocked_by is None


def test_a_human_declining_an_action_is_still_a_block():
    """The other `declined`. Something was proposed and something was stopped.

    This is the case that makes `blocked_by is None` insufficient on its own:
    a human decision names no control either, so the discriminator has to be
    whether an action ever existed.
    """
    view = execution_view.build(
        [
            _started(),
            {"sequence": 2, "event_type": "action_proposed",
             "status": "info", "tool": "update_record"},
            {"sequence": 3, "event_type": "policy_decision", "status": "info",
             "tool": "update_record", "policy_decision": "require_confirmation"},
            {"sequence": 4, "event_type": "confirmation_resolved",
             "status": "info", "tool": "update_record"},
        ],
        status="declined",
    )
    assert view.outcome is execution_view.State.BLOCKED


def test_a_policy_denial_is_still_a_block():
    """The state that must not have changed."""
    view = execution_view.build(
        [
            _started(),
            {"sequence": 2, "event_type": "action_proposed",
             "status": "info", "tool": "delete_record"},
            {"sequence": 3, "event_type": "policy_decision", "status": "failure",
             "tool": "delete_record", "policy_decision": "deny",
             "rule_ids": ["PL005"]},
        ],
        status="blocked",
    )
    assert view.outcome is execution_view.State.BLOCKED
    assert view.blocked_by == "policy engine"


def test_a_rate_limit_is_still_a_block_and_names_itself():
    view = execution_view.build(
        [
            _started(),
            {"sequence": 2, "event_type": "rate_limited", "status": "failure"},
            {"sequence": 3, "event_type": "request_failed", "status": "failure"},
        ],
        status="failed",
    )
    assert view.outcome is execution_view.State.BLOCKED
    assert view.blocked_by == "rate limit"


@pytest.mark.parametrize(
    ("state", "expected_class"),
    [
        (execution_view.State.SUCCESS, "ap-allow"),
        (execution_view.State.BLOCKED, "ap-deny"),
        (execution_view.State.OUT_OF_SCOPE, "ap-neutral"),
    ],
)
def test_out_of_scope_is_not_painted_like_a_denial(dashboard, state, expected_class):
    """The visible half. A neutral outcome must not borrow the denial colour."""
    markup = dashboard._outcome_pill(state)
    assert expected_class in markup, markup
    if state is execution_view.State.OUT_OF_SCOPE:
        assert "ap-deny" not in markup
