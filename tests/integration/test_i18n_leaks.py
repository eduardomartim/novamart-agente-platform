"""Text that reached the page in the wrong language, and what keeps it out.

`test_i18n.py` proves the two catalogues agree and the chrome switches. What it
could not see were strings that never went through a catalogue at all:

* run states shown as their enum values -- "SUCCESS" on the Portuguese page and
  "SEM RESPOSTA" on the English one;
* the Empresa tables, whose headings and values were the dataset's English keys
  ("Customer", "delivered") on the Portuguese page;
* headings typed in Portuguese inside the technical details, and the empty-state
  hint "Popule o banco", on the English page;
* the platform's own sentences for a refusal, a decline or a failure, which
  were English only.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

from agent_platform.i18n import use_locale

DASHBOARD = Path(__file__).resolve().parents[2] / "dashboard"
if str(DASHBOARD) not in sys.path:
    sys.path.insert(0, str(DASHBOARD))
APP = DASHBOARD / "app.py"

import execution_view  # noqa: E402
import i18n  # noqa: E402


@pytest.fixture
def locale():
    def choose(code: str) -> None:
        st.session_state[i18n.LOCALE_KEY] = code

    yield choose
    st.session_state.pop(i18n.LOCALE_KEY, None)


@pytest.mark.parametrize("state", list(execution_view.State))
def test_every_run_state_has_a_label_in_both_languages(state, locale):
    labels = {}
    for code in i18n.LOCALES:
        locale(code)
        labels[code] = i18n.t(f"state.{state.name.lower()}")
        assert labels[code] != f"state.{state.name.lower()}"
    if state is execution_view.State.OUT_OF_SCOPE:
        assert labels["en"] == "NO ANSWER"
        assert labels["pt"] == "SEM RESPOSTA"
    if state is execution_view.State.SUCCESS:
        assert labels["pt"] == "SUCESSO"


def test_step_details_follow_the_language(locale):
    events = [
        {"sequence": 1, "event_type": "policy_decision", "status": "success",
         "tool": "get_order", "risk_level": "high", "rule_ids": ["PL009"]},
    ]
    locale("pt")
    pt = execution_view._steps(events)[0].detail
    locale("en")
    en = execution_view._steps(events)[0].detail
    assert "ferramenta get_order" in pt and "risco alto" in pt and "regra PL009" in pt
    assert "tool get_order" in en and "high risk" in en and "rule PL009" in en


def _company(tmp_path, code: str) -> AppTest:
    saved = {n: os.environ.get(n) for n in ("GEMINI_API_KEY", "DATABASE_PATH")}
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(tmp_path / "leaks.db")
    st.cache_resource.clear()
    try:
        app = AppTest.from_file(str(APP), default_timeout=180)
        app.session_state["locale"] = code
        app.session_state["nav"] = "company"
        app.run()
        return app
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_the_company_tables_speak_portuguese_on_the_portuguese_page(tmp_path):
    app = _company(tmp_path, "pt")
    assert not app.exception
    columns = {c for frame in app.dataframe for c in frame.value.columns}
    assert {"Nome", "Cliente", "Status"} <= columns, columns
    assert not {"Name", "Customer", "Placed on", "Subject"} & columns, columns
    values = {str(v) for frame in app.dataframe for v in frame.value.to_numpy().ravel()}
    assert "entregue" in values or "em processamento" in values
    assert "delivered" not in values


def test_the_company_tables_stay_english_on_the_english_page(tmp_path):
    app = _company(tmp_path, "en")
    assert not app.exception
    columns = {c for frame in app.dataframe for c in frame.value.columns}
    assert {"Name", "Customer"} <= columns, columns
    assert not {"Nome", "Cliente"} & columns, columns


@pytest.mark.parametrize(
    ("code", "expected", "absent"),
    [
        ("pt", "recusado pelo motor de políticas", "refused by the platform"),
        ("en", "refused by the platform's policy engine", "recusado pelo motor"),
    ],
)
def test_a_refusal_is_written_in_the_readers_language(platform, code, expected, absent):
    with use_locale(code):
        result = platform.run("Delete order 1001 immediately")
    assert result.status == "blocked"
    assert expected in result.response
    assert absent not in result.response


@pytest.mark.parametrize(
    ("code", "expected"),
    [("pt", "foi recusada na revisão"), ("en", "declined during review")],
)
def test_a_declined_action_is_written_in_the_readers_language(platform, code, expected):
    with use_locale(code):
        suspended = platform.run("Update order 1002 status to delivered")
        declined = platform.confirm(suspended.request_id, approved=False, actor="t")
    assert declined.status == "declined"
    assert expected in declined.response


@pytest.mark.parametrize(
    ("code", "expected"),
    [("pt", "esta ferramenta é simulada"), ("en", "this tool is simulated")],
)
def test_the_simulation_note_is_in_the_readers_language(platform, code, expected):
    with use_locale(code):
        suspended = platform.run("Update order 1002 status to delivered")
        done = platform.confirm(suspended.request_id, approved=True, actor="t")
    assert done.status == "success"
    assert expected in done.response
