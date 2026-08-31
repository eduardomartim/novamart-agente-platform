"""The first sixty seconds, asserted.

A portfolio demo is only as good as what a stranger understands without being
told. These pin the things a first-time visitor must be able to work out from
the screen alone: what they are looking at, that it is a simulation, what they
can ask, who does the work, and how to tell an allowed action from a refused
one.

They are ordinary regression tests, not decoration: each one failed at least
once during this pass, on real friction found by walking the app as a stranger.
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
def seeded_db(tmp_path_factory) -> Path:
    from agent_platform.config import Settings
    from agent_platform.platform import AgentPlatform

    path = tmp_path_factory.mktemp("recruiter") / "demo.db"
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(path)
    platform = AgentPlatform(Settings.from_env(load_dotenv_file=False))
    try:
        platform.run("What is the status of order ORD-1001?")
        platform.run("Delete order ORD-1001 immediately")
    finally:
        platform.close()
    return path


def _render(db_path: Path, page: str | None = None) -> AppTest:
    os.environ["GEMINI_API_KEY"] = ""
    os.environ["DATABASE_PATH"] = str(db_path)
    st.cache_resource.clear()
    st.cache_data.clear()
    app = AppTest.from_file(str(APP), default_timeout=90)
    app.run()
    if page:
        app.radio[0].set_value(page).run()
    return app


def _main_text(app: AppTest) -> str:
    parts = []
    for element in app.main:
        value = getattr(element, "value", None)
        if isinstance(value, str):
            parts.append(value)
    for frame in app.dataframe:
        parts.append(str(getattr(frame, "value", "")))
    return "\n".join(parts)


# ------------------------------------------------------- what am I looking at


@pytest.mark.slow
def test_landing_leads_with_identity_not_a_technical_warning(seeded_db):
    """The first thing on screen must be the product, not a config warning.

    The provider banner used to render above the page header, so a stranger's
    first impression was a yellow box about a missing environment variable.
    """
    app = _render(seeded_db)
    headers = [e.value for e in app.header]
    assert headers, "the landing page has no header"
    first = headers[0]
    assert demo.COMPANY_NAME in first or "Orchestrator" in first, (
        f"the landing page leads with {first!r} instead of the product identity"
    )


@pytest.mark.slow
def test_landing_names_the_company_and_the_product(seeded_db):
    text = _main_text(_render(seeded_db))
    assert demo.COMPANY_NAME in text
    assert "orchestrat" in text.lower(), "the page never says what the product is"


@pytest.mark.slow
def test_landing_says_it_is_a_simulation(seeded_db):
    text = _main_text(_render(seeded_db)).lower()
    assert "simulat" in text, "a visitor could mistake this for a real company"


@pytest.mark.slow
def test_simulation_is_stated_once_not_four_times(seeded_db):
    """Repeating the disclaimer four times buries it instead of landing it."""
    app = _render(seeded_db)
    banners = [
        e.value
        for e in list(app.warning) + list(app.info)
        if isinstance(getattr(e, "value", None), str)
        and ("stub" in e.value.lower() or "simulat" in e.value.lower()
             or "demo mode" in e.value.lower())
    ]
    assert len(banners) <= 2, (
        f"{len(banners)} overlapping demo/simulation banners on the landing page"
    )


@pytest.mark.slow
def test_landing_does_not_lead_with_environment_variable_jargon(seeded_db):
    """`GEMINI_API_KEY` means nothing to a non-engineer reading a demo."""
    app = _render(seeded_db)
    leading = " ".join(
        e.value for e in app.warning if isinstance(getattr(e, "value", None), str)
    )
    assert "GEMINI_API_KEY" not in leading, (
        "the landing page's status banner exposes an env-var name"
    )


# -------------------------------------------------------------- what to ask


@pytest.mark.slow
def test_landing_shows_what_can_be_asked(seeded_db):
    """A visitor must not have to guess a question or an ID."""
    text = _main_text(_render(seeded_db))
    assert any(question in text for question, _ in demo.READ_ONLY_EXAMPLES), (
        "the landing page shows no example question"
    )


@pytest.mark.slow
def test_runner_offers_every_category_of_example(seeded_db):
    text = _main_text(_render(seeded_db, "Try a request"))
    for question, _ in demo.READ_ONLY_EXAMPLES[:2]:
        assert question in text
    for question, _ in demo.ACTION_EXAMPLES[:1]:
        assert question in text
    for question, _ in demo.SECURITY_EXAMPLES[:1]:
        assert question in text


# ------------------------------------------------------------ who does work


@pytest.mark.slow
def test_agents_page_names_every_real_agent(seeded_db):
    text = _main_text(_render(seeded_db, "Agents"))
    for role in demo.AGENT_ROLES:
        assert role["title"] in text, f"{role['title']} is missing from the agents page"


@pytest.mark.slow
def test_agents_page_says_policy_engine_is_not_an_agent(seeded_db):
    """Architectural literacy: the authority is not one of the agents."""
    text = _main_text(_render(seeded_db, "Agents")).lower()
    assert "policy engine" in text
    assert "not an agent" in text, (
        "the page does not distinguish the policy engine from the agents"
    )


# --------------------------------------------------------- scenarios & data


@pytest.mark.slow
def test_scenarios_are_ordered_by_difficulty(seeded_db):
    """A visitor should be able to start easy and escalate."""
    text = _main_text(_render(seeded_db, "Demo scenarios"))
    for scenario in demo.SCENARIOS:
        assert scenario["level"] in text, f"level {scenario['level']} missing"


@pytest.mark.slow
def test_data_explorer_shows_ids_a_visitor_can_use(seeded_db):
    text = _main_text(_render(seeded_db, "Data explorer"))
    for marker in ("CUS-2001", "ORD-1001", "TKT-4001"):
        assert marker in text, f"{marker} is not visible in the data explorer"


# ----------------------------------------------------------- mode is obvious


@pytest.mark.slow
def test_stub_mode_is_stated_in_plain_language(seeded_db):
    app = _render(seeded_db)
    blob = " ".join(
        e.value
        for e in list(app.warning) + list(app.info) + list(app.sidebar.warning)
        if isinstance(getattr(e, "value", None), str)
    ).lower()
    assert "stub" in blob or "simulation" in blob
    assert "no ai provider" in blob or "no language model" in blob or "deterministic" in blob


# --------------------------------------------------------------- navigation


@pytest.mark.slow
def test_navigation_puts_the_demo_before_the_technical_pages(seeded_db):
    """The guided journey comes first; the instruments are clearly secondary.

    ``options`` reports the *rendered* labels, which is what a visitor reads --
    so this asserts on exactly what they see in the sidebar.
    """
    import app as dashboard_app

    app = _render(seeded_db)
    labels = list(app.radio[0].options)

    assert labels[0] == "Start here", "the guided entry point is not first"

    demo_count = len(dashboard_app.DEMO_PAGES)
    demo_labels, platform_labels = labels[:demo_count], labels[demo_count:]

    assert len(platform_labels) == len(dashboard_app.PLATFORM_PAGES)
    # The technical group is visually marked, so the two are distinguishable
    # without reading a caption.
    assert all(label.startswith("⚙") for label in platform_labels), (
        f"platform pages are not visually grouped: {platform_labels}"
    )
    assert not any(label.startswith("⚙") for label in demo_labels), (
        f"a demo page is marked as a technical page: {demo_labels}"
    )


@pytest.mark.slow
def test_the_runner_is_labelled_for_a_visitor_not_a_developer(seeded_db):
    """"Try a request" is a page key; "Try the orchestrator" is an invitation."""
    app = _render(seeded_db)
    assert "Try the orchestrator" in list(app.radio[0].options)


# ------------------------------------------------- what engineering is shown


@pytest.mark.slow
def test_landing_states_what_is_demonstrated(seeded_db):
    """A technical reader wants the engineering, not adjectives."""
    text = _main_text(_render(seeded_db)).lower()
    for capability in ("orchestrat", "policy", "confirmation", "injection"):
        assert capability in text, f"the landing page never mentions {capability}"


@pytest.mark.slow
def test_landing_separates_demonstrated_from_not_built(seeded_db):
    """Claiming production integrations that do not exist would be dishonest."""
    text = _main_text(_render(seeded_db))
    assert any(
        marker in text for marker in ("Not built", "not built", "Limitations")
    ), "the landing page does not distinguish what is built from what is not"


@pytest.mark.slow
def test_landing_shows_a_test_matrix_without_digging(seeded_db):
    """The five things worth trying should be visible on the first screen."""
    text = _main_text(_render(seeded_db))
    levels = [s["level"].split(" - ")[-1] for s in demo.SCENARIOS]
    found = sum(1 for level in levels if level in text)
    assert found >= 4, (
        f"only {found} of {len(levels)} test levels are visible on the landing page"
    )


def test_capability_claims_are_declared_not_improvised():
    """The claims live in one reviewable list, not scattered through the UI."""
    assert demo.DEMONSTRATED, "no demonstrated-capability list"
    assert demo.NOT_BUILT, "no limitations list"
    overlap = {c.lower() for c, _ in demo.DEMONSTRATED} & {
        c.lower() for c, _ in demo.NOT_BUILT
    }
    assert not overlap, f"a capability is claimed and disclaimed at once: {overlap}"
