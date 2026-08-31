"""What a first-time visitor must be able to discover, unaided.

This is the Phase 4 acceptance test: someone who has never seen the project
opens the app and must be able to work out, from the screens alone, what they
are looking at and what they can do with it.

Each assertion below corresponds to one thing a recruiter should not have to
ask about. They are deliberately written against *rendered pages* rather than
against the modules behind them, because the question is not "does the data
exist" but "would anyone find it".
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

import demo_budget  # noqa: E402
import demo_content as demo  # noqa: E402

APP = DASHBOARD / "app.py"


@pytest.fixture(scope="module", autouse=True)
def _isolated_environment():
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
def seeded(tmp_path_factory) -> Path:
    from agent_platform.config import Settings
    from agent_platform.platform import AgentPlatform

    path = tmp_path_factory.mktemp("discovery") / "demo.db"
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(path)
    platform = AgentPlatform(Settings.from_env(load_dotenv_file=False))
    try:
        platform.run("What is the status of order ORD-1001?")
        platform.run("Delete order ORD-1001 immediately")
    finally:
        platform.close()
    return path


def render(db: Path, page: str | None = None) -> AppTest:
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(db)
    st.cache_resource.clear()
    st.cache_data.clear()
    app = AppTest.from_file(str(APP), default_timeout=90)
    app.run()
    if page:
        app.radio[0].set_value(page).run()
    return app


def text_of(app: AppTest) -> str:
    parts = [
        e.value for e in app.main if isinstance(getattr(e, "value", None), str)
    ]
    parts += [str(getattr(f, "value", "")) for f in app.dataframe]
    return "\n".join(parts)


# ============================================ the ten discovery questions


@pytest.mark.slow
def test_1_which_company_is_being_simulated(seeded):
    body = text_of(render(seeded))
    assert demo.COMPANY_NAME in body


@pytest.mark.slow
def test_2_that_it_is_a_simulation_not_a_real_business(seeded):
    assert "simulat" in text_of(render(seeded)).lower()


@pytest.mark.slow
def test_3_what_problem_the_product_solves(seeded):
    body = text_of(render(seeded)).lower()
    assert "orchestrat" in body
    assert "policy" in body


@pytest.mark.slow
def test_4_what_data_exists(seeded):
    """Company must cover all four entity families, not just two."""
    body = text_of(render(seeded, "Company")).lower()
    for entity in ("customer", "order", "ticket", "product"):
        assert entity in body, f"Company never mentions {entity}s"


@pytest.mark.slow
def test_5_what_can_be_tested_against_that_data(seeded):
    """Seeing rows is not the same as knowing what to do with them."""
    body = text_of(render(seeded, "Company"))
    assert "What you can test" in body, (
        "Company shows data but never says what a visitor can try"
    )


@pytest.mark.slow
def test_6_real_ids_are_visible_as_context(seeded):
    body = text_of(render(seeded, "Data explorer"))
    for marker in ("CUS-2001", "ORD-1001", "TKT-4001", "SKU-"):
        assert marker in body, f"{marker} is not discoverable"


@pytest.mark.slow
def test_7_which_agents_exist_and_who_the_authority_is(seeded):
    body = text_of(render(seeded, "Agents"))
    for role in demo.AGENT_ROLES:
        assert role["title"] in body
    assert "not an agent" in body.lower()


@pytest.mark.slow
def test_8_what_questions_can_be_asked(seeded):
    body = text_of(render(seeded, "Try a request"))
    assert any(q in body for q, _ in demo.READ_ONLY_EXAMPLES)


@pytest.mark.slow
def test_9_what_security_can_be_tried(seeded):
    body = text_of(render(seeded, "Demo scenarios"))
    assert any(q in body for q, _ in demo.SECURITY_EXAMPLES)
    assert "LEVEL 5" in body


@pytest.mark.slow
@pytest.mark.parametrize("page", ["Try a request", "Demo scenarios"])
def test_10_how_stub_and_live_differ(seeded, page):
    """The honest version: the model decides routing, not the prose."""
    body = text_of(render(seeded, page)).lower()
    assert "stub" in body or "simulation" in body
    assert "live" in body
    assert "route" in body or "tool" in body, (
        f"{page} never says what the model actually decides"
    )


@pytest.mark.slow
@pytest.mark.parametrize("page", ["Start here", "Try a request"])
def test_11_that_gemini_usage_is_capped(seeded, page):
    """The budget is real engineering; a visitor should be able to see it."""
    body = text_of(render(seeded, page)).lower()
    assert "capacity" in body or "budget" in body, (
        f"{page} never reveals that provider usage is capped"
    )


# ------------------------------------------------- and nothing dishonest


@pytest.mark.slow
@pytest.mark.parametrize(
    "page", ["Start here", "Company", "Data explorer", "Agents", "Try a request"]
)
def test_no_page_claims_an_unbuilt_capability(seeded, page):
    """Roadmap words must never appear as though they were implemented."""
    body = text_of(render(seeded, page)).lower()
    for unbuilt in ("vector database", "kubernetes", "retrieval-augmented"):
        assert unbuilt not in body, f"{page} claims {unbuilt!r}, which does not exist"


@pytest.mark.slow
@pytest.mark.parametrize("page", ["Start here", "Company", "Try a request"])
def test_no_page_leaks_internals(seeded, page):
    body = text_of(render(seeded, page)).lower()
    # Named technologies are fine -- the landing page names SQLite precisely to
    # disclaim it as a production store, which is the honesty this project is
    # built on. What must never appear is a path, a query or a credential.
    for leak in (
        "gemini_api_key", "aizasy", "provider_budget.db", ".db\"",
        "select ", "insert ", "c:\\users", "/home/", "traceback",
        "sqlite3.", "sqlite:///",
    ):
        assert leak not in body, f"{page} leaked {leak!r}"


@pytest.mark.slow
def test_the_budget_figures_shown_are_the_real_ones(seeded):
    """The displayed cap must be the enforced cap, not a decorative number."""
    body = text_of(render(seeded, "Try a request"))
    assert str(demo_budget.LIVE_CALL_BUDGET) in body


def test_only_the_four_real_agents_are_ever_named():
    """No customer-agent, order-agent or support-agent may appear."""
    from agent_platform.models import AgentName

    real = {a.value for a in AgentName}
    assert {r["name"] for r in demo.AGENT_ROLES} == real
    for invented in ("customer agent", "order agent", "support agent"):
        for role in demo.AGENT_ROLES:
            assert invented not in role["title"].lower()
