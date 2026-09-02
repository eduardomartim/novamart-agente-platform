"""The properties that killed the `insertBefore` error, pinned.

The dashboard used to throw React's `insertBefore` DOM error. Three things
caused it, and all three are code properties rather than visual ones:

* a widget key built from ``id(examples)`` -- a memory address, so the same
  widget in the same position got a new identity whenever the module was
  reloaded, and React was asked to move a node that no longer existed;
* ``st.rerun()`` called from inside a tab, which tears down a subtree React is
  still holding a reference into;
* the navigation radio taking ``index=`` instead of ``key=``, which made the
  widget's own identity a function of the current page: every navigation
  destroyed and rebuilt it.

So this file asserts identity stability, not appearance. Nothing here looks at
a colour, a label or a layout -- those are meant to change. What must not
change is that widget identity is deterministic and survives navigation.

Two halves, deliberately:

* the static half reads the source and rules out the *patterns*, which catches
  a regression at the moment it is written even on a page nobody exercises;
* the runtime half drives the real app and compares real widget ids, which is
  the property itself rather than a proxy for it.
"""

from __future__ import annotations

import ast
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

APP = DASHBOARD / "app.py"

#: Anything whose value differs between two runs of the same code. A key built
#: from one of these is a new widget every rerun, which is the defect.
NON_DETERMINISTIC = frozenset(
    {
        "id", "hash", "uuid1", "uuid4", "uuid", "random", "randint", "choice",
        "time", "time_ns", "monotonic", "now", "object", "next", "token_hex",
    }
)


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(APP.read_text(encoding="utf-8"), filename=str(APP))


def _keyword(call: ast.Call, name: str) -> ast.expr | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _calls(tree: ast.Module) -> list[ast.Call]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Call)]


# ============================================================ the static half


def test_the_app_never_calls_rerun(tree):
    """A rerun raised mid-render tears down a subtree React still references.

    Streamlit reruns on its own after a widget interaction, so every
    navigation in this app is a callback that writes state and returns. No
    case in this file needs to force one.
    """
    offenders = [
        node.func.attr
        for node in _calls(tree)
        if isinstance(node.func, ast.Attribute)
        and node.func.attr in {"rerun", "experimental_rerun"}
    ]
    assert not offenders, (
        f"st.{offenders[0]}() is back in the dashboard; use an on_click "
        "callback that writes session state instead"
    )


def test_every_widget_key_is_deterministic(tree):
    """A key must depend on position and content, never on an address."""
    checked = 0
    for call in _calls(tree):
        key = _keyword(call, "key")
        if key is None:
            continue
        checked += 1
        for node in ast.walk(key):
            if not isinstance(node, ast.Call):
                continue
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else getattr(node.func, "id", "")
            )
            assert name not in NON_DETERMINISTIC, (
                f"line {call.lineno}: the widget key calls {name}(), whose "
                "value changes between runs -- that is the `insertBefore` bug"
            )
    assert checked, "no keyed widgets found; this test stopped checking anything"


def test_literal_widget_keys_are_unique(tree):
    """Two widgets sharing a literal key is a Streamlit error, not a style."""
    seen: dict[str, int] = {}
    for call in _calls(tree):
        key = _keyword(call, "key")
        if isinstance(key, ast.Constant) and isinstance(key.value, str):
            assert key.value not in seen, (
                f"key {key.value!r} is used on line {call.lineno} and again on "
                f"line {seen[key.value]}"
            )
            seen[key.value] = call.lineno
    assert seen, "no literal widget keys found; this test stopped checking anything"


def test_interpolated_keys_interpolate_only_plain_values(tree):
    """An f-string key is the normal way to key a loop.

    What it may not contain is a call, because a call is where a fresh value
    comes from: `f"ex_{prefix}_{index}"` is fine, `f"ex_{id(x)}"` was the bug.
    """
    for call in _calls(tree):
        key = _keyword(call, "key")
        if not isinstance(key, ast.JoinedStr):
            continue
        for part in key.values:
            if not isinstance(part, ast.FormattedValue):
                continue
            assert isinstance(
                part.value, ast.Name | ast.Attribute | ast.Subscript
            ), (
                f"line {call.lineno}: the key interpolates an expression that "
                "is not a plain variable, so its value can differ between runs"
            )


def test_the_navigation_radio_is_keyed_not_indexed(tree):
    """`index=` makes the widget's identity a function of the current page."""
    radios = [
        call
        for call in _calls(tree)
        if isinstance(call.func, ast.Attribute) and call.func.attr == "radio"
    ]
    assert radios, "the navigation radio is gone; this test checks nothing"
    for radio in radios:
        assert _keyword(radio, "key") is not None, (
            f"line {radio.lineno}: the navigation radio has no key, so its "
            "identity is derived from its arguments and changes with them"
        )
        assert _keyword(radio, "index") is None, (
            f"line {radio.lineno}: `index=` rebuilds the radio on every "
            "navigation; drive it through session state instead"
        )


def test_the_stylesheet_is_one_invariant_constant(tree):
    """Markup that differs between reruns is markup React has to reconcile."""
    assignments = [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_STYLE" for t in node.targets)
    ]
    assert len(assignments) == 1, "_STYLE is not a single module-level constant"
    assert isinstance(assignments[0].value, ast.Constant), (
        "_STYLE is computed rather than literal, so the injected CSS can vary "
        "between reruns"
    )


def test_no_markdown_syntax_is_passed_to_a_raw_html_helper(tree):
    """`lede()` and `eyebrow()` write into a raw `<p>`, where markdown is dead.

    `**Policy Engine**` inside one of them reaches the reader as four literal
    asterisks -- it shipped that way once. Emphasis there has to be a tag.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if getattr(node.func, "id", "") not in {"lede", "eyebrow"}:
            continue
        for arg in node.args:
            try:
                text = str(ast.literal_eval(arg))
            except (ValueError, TypeError):
                continue
            assert "**" not in text, (
                f"line {node.lineno}: markdown bold inside raw HTML renders as "
                "asterisks; use <strong>"
            )
            assert "`" not in text, (
                f"line {node.lineno}: a markdown code span inside raw HTML "
                "renders as backticks; use <code>"
            )


# =========================================================== the runtime half


@pytest.fixture
def app(tmp_path):
    """The real dashboard on an empty database, driven the way a visitor does."""
    saved = {name: os.environ.get(name) for name in ("GEMINI_API_KEY", "DATABASE_PATH")}
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(tmp_path / "stability.db")
    st.cache_resource.clear()
    st.cache_data.clear()
    try:
        harness = AppTest.from_file(str(APP), default_timeout=120)
        harness.run()
        yield harness
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        st.cache_resource.clear()


@pytest.mark.slow
def test_the_navigation_keeps_one_identity_across_every_page(app):
    """The headline property, measured rather than inferred.

    Streamlit derives a widget's id from its arguments unless it is given a
    key. If the id below changed while walking the pages, React would be told
    the sidebar control was replaced on every navigation -- which is what the
    old `index=` version did.
    """
    import app as dashboard

    first = app.radio(key="nav_pt").id
    for page in dashboard.PAGES:
        app.radio(key="nav_pt").set_value(page).run()
        assert app.radio(key="nav_pt").value == page, f"navigation to {page} did not take"
        assert app.radio(key="nav_pt").id == first, (
            f"the navigation radio changed identity on {page}: "
            f"{first} -> {app.radio(key='nav_pt').id}"
        )


@pytest.mark.slow
def test_example_buttons_keep_their_identity_across_a_round_trip(app):
    """Leave the page, come back, and the same buttons must be the same nodes."""
    app.radio(key="nav_pt").set_value("orchestrator").run()
    before = sorted(button.id for button in app.button)
    assert before, "the orchestrator page rendered no buttons"

    app.radio(key="nav_pt").set_value("architecture").run()
    app.radio(key="nav_pt").set_value("orchestrator").run()

    assert sorted(button.id for button in app.button) == before, (
        "a button on the orchestrator page came back with a different id, so "
        "React is being asked to replace nodes that did not change"
    )


@pytest.mark.slow
def test_no_page_raises(app):
    """An exception on a page is a traceback on screen for a visitor."""
    import app as dashboard

    for page in dashboard.PAGES:
        app.radio(key="nav_pt").set_value(page).run()
        assert not app.exception, (
            f"{page} raised: {[str(e.value)[:200] for e in app.exception]}"
        )
