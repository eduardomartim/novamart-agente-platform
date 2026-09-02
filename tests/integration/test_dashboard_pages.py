"""Every dashboard page must render, on an empty database and a seeded one.

These replace a manual click-through. Browser automation could not drive
Streamlit's radio reliably, and a rendering regression is exactly the kind of
thing that only shows up when someone opens the app -- which, for a portfolio
project, is the worst moment to find it.

Two states matter and they fail differently:

* **empty** -- a fresh clone with no database. Every page must explain what to
  run, not render a bare zero or raise on an absent table.
* **seeded** -- after ``agent-platform demo``. Every page must render its real
  content.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("streamlit")

import streamlit as st
from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[2] / "dashboard" / "app.py"

PAGES = [
    "overview",
    "company",
    "orchestrator",
    "security",
    "architecture",
    "observability",
]

#: Streamlit reruns the whole script per interaction, and this app seeds nothing
#: itself, so a generous timeout avoids flaking on a cold import of pandas.
TIMEOUT = 60


def _run(db_path: Path, page: str) -> AppTest:
    """Render one page against *db_path*, in demo (stub) mode."""
    # An empty key forces the deterministic stub: a dashboard test must never
    # depend on a provider being reachable, or on a key being valid.
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(db_path)

    # The app caches its platform -- and therefore its database connection --
    # with @st.cache_resource. That cache is process-global and outlives a
    # single AppTest run, so without clearing it the next test silently reuses
    # the previous test's database and ignores DATABASE_PATH entirely. That is
    # correct behaviour for the app and a pollution source for these tests:
    # it made them pass only in the order they happened to be written in.
    st.cache_resource.clear()
    st.cache_data.clear()

    app = AppTest.from_file(str(APP), default_timeout=TIMEOUT)
    app.run()
    if page != PAGES[0]:
        app.radio(key="nav_pt").set_value(page).run()
    return app


def _assert_clean(app: AppTest, page: str) -> None:
    assert not app.exception, (
        f"{page} raised: {[e.value for e in app.exception]}"
    )


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
    """Restore the environment this module mutates.

    ``GEMINI_API_KEY`` and ``DATABASE_PATH`` are process-global, and the suite
    runs in randomised order, so leaking them would silently point another
    test at this module's database -- or at a provider.
    """
    saved = {k: os.environ.get(k) for k in ("GEMINI_API_KEY", "DATABASE_PATH")}
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@pytest.fixture(scope="module")
def empty_db(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("empty") / "none.db"


@pytest.fixture(scope="module")
def seeded_db(tmp_path_factory) -> Path:
    """A database seeded through the real CLI path, not a fixture shortcut."""
    from agent_platform.cli import DEMO_REQUESTS
    from agent_platform.config import Settings
    from agent_platform.evaluation.evaluator import build_evaluation_settings
    from agent_platform.platform import AgentPlatform

    path = tmp_path_factory.mktemp("seeded") / "seeded.db"
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(path)
    settings = build_evaluation_settings(Settings.from_env())
    platform = AgentPlatform(settings)
    try:
        for text in DEMO_REQUESTS:
            result = platform.run(text)
            if result.awaiting_confirmation:
                platform.confirm(
                    result.request_id, approved=True, actor="test", source="test"
                )
    finally:
        platform.close()
    return path


@pytest.mark.slow
@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_on_an_empty_database(page, empty_db):
    """A fresh clone must not show a stack trace to a first-time reader."""
    app = _run(empty_db, page)
    _assert_clean(app, page)


@pytest.mark.slow
@pytest.mark.parametrize("page", PAGES)
def test_every_page_renders_with_seeded_data(page, seeded_db):
    app = _run(seeded_db, page)
    _assert_clean(app, page)


@pytest.mark.slow
@pytest.mark.parametrize("page", PAGES)
def test_data_pages_explain_themselves_when_empty(page, empty_db):
    """No page may render an unexplained blank.

    "orchestrator" is exempt: it is a form, so an empty database is its
    normal resting state and the form itself is the content.
    """
    if page == "orchestrator":
        pytest.skip("a form is not an empty state")
    app = _run(empty_db, page)
    body = " ".join(
        [el.value for el in app.info] + [el.value for el in app.markdown]
    )
    assert body.strip(), f"{page} rendered nothing at all on an empty database"


@pytest.mark.slow
def test_an_empty_page_names_the_command_that_actually_fills_it(empty_db):
    """Regression: a generic hint was once appended to every page, telling a
    reader to run something that could not fix what they were looking at.

    The assertion is the same property as before, against the page that now
    exists: Observabilidade is populated by traffic, and ``agent-platform demo``
    is what produces traffic -- so here that hint is the correct one.
    """
    app = _run(empty_db, "observability")
    joined = " ".join(el.value for el in app.info)
    assert "agent-platform demo" in joined, (
        "the empty state must name the command that populates this page"
    )


@pytest.mark.slow
def test_demo_mode_is_stated_on_every_page(seeded_db):
    """The stub must never be silently passed off as a live model."""
    for page in PAGES:
        app = _run(seeded_db, page)
        blob = " ".join(
            [el.value for el in app.info]
            + [el.value for el in app.warning]
            + [el.value for el in app.markdown]
        )
        assert "stub" in blob.lower(), f"{page} does not disclose demo mode"


@pytest.mark.slow
def test_pages_are_isolated_from_a_previously_loaded_database(seeded_db, empty_db):
    """Regression: rendering a seeded database must not leak into the next run.

    The app caches its platform process-globally. Without clearing that cache
    between runs, this test sees the seeded database through an "empty" path
    and the empty-state assertions pass or fail depending purely on the order
    tests happened to run in.
    """
    seeded = _run(seeded_db, "observability")
    seeded_body = " ".join(el.value for el in seeded.metric)
    assert seeded_body.strip(), "the seeded database rendered no metrics"

    empty = _run(empty_db, "observability")
    empty_body = " ".join(
        [el.value for el in empty.info] + [el.value for el in empty.markdown]
    )
    assert "Nenhuma requisição registrada" in empty_body, (
        "the empty database did not render its empty state; the previous "
        "run's platform is still cached"
    )
