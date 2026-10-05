r"""Security tests for what the dashboard itself renders.

Finding F9: Streamlit renders unhandled exceptions with a full traceback,
including absolute source paths, directly into the page. That output never
passes through :func:`secure_output`, so the platform's own redaction cannot
reach it -- the control has to be a Streamlit configuration option.

Reproduction (Streamlit 1.62): a page that raises renders
``File "C:\Users\<user>\...\page.py", line 3`` in the browser, alongside
"Ask Google" / "Ask ChatGPT" affordances that would forward that path to a
third party.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

CONFIG = Path(__file__).resolve().parents[2] / ".streamlit" / "config.toml"


@pytest.fixture(scope="module")
def client_config() -> dict[str, object]:
    assert CONFIG.is_file(), f"missing dashboard hardening config: {CONFIG}"
    return tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("client", {})


def test_tracebacks_are_not_rendered_to_the_browser(client_config):
    """F9: no absolute paths in the page, because no traceback in the page."""
    assert client_config.get("showErrorDetails") in {"none", "type"}, (
        "showErrorDetails must not be 'full'/'stacktrace': a rendered traceback "
        "discloses the operating user's name and directory layout"
    )


#: Toolbar modes that do not put the developer controls in front of a viewer.
#:
#: Streamlit 1.62 defines four (`client.toolbarMode`):
#:
#: * ``"developer"`` -- shows the developer options to **all** viewers;
#: * ``"auto"`` (the default) -- shows them on localhost or to a Community
#:   Cloud admin, hides them otherwise;
#: * ``"viewer"`` -- hides them from all viewers;
#: * ``"minimal"`` -- shows only options set externally, through
#:   ``st.set_page_config(menu_items=...)`` or by the host.
#:
#: Only the last two are safe here. ``"auto"`` is unsafe *for this app* rather
#: than in general: the dashboard is run on localhost every time it is
#: demonstrated, which is precisely the case where ``"auto"`` shows the deploy,
#: rerun and clear-cache controls.
_SAFE_TOOLBAR_MODES = frozenset({"viewer", "minimal"})


def test_developer_toolbar_is_hidden(client_config):
    """The deploy / rerun / clear-cache controls are not viewer-facing.

    The property is "no developer options", not one particular spelling of it.
    This pinned the literal ``"viewer"`` and the config later moved to
    ``"minimal"``, which is strictly *less* exposed -- so a tightening read as a
    regression. Asserting the safe set instead means the test fails for an
    actual loosening and only for that.
    """
    mode = client_config.get("toolbarMode")
    assert mode in _SAFE_TOOLBAR_MODES, (
        f"toolbarMode is {mode!r}: 'developer' shows the deploy/rerun/clear-cache "
        "controls to every viewer, and 'auto' shows them on localhost -- which is "
        "how this dashboard is run when it is demonstrated"
    )


def test_a_minimal_toolbar_is_backed_by_an_app_that_declares_no_menu_items():
    """What makes ``"minimal"`` mean *empty* rather than merely *short*.

    ``"minimal"`` hides everything except options the app sets itself. That is
    the strictest mode only while the app sets none, so the claim depends on a
    fact about the code rather than about the config -- and a later
    ``menu_items={...}`` would put entries back into a header this project
    deliberately emptied, with the config still reading as hardened.

    Skipped rather than failed under ``"viewer"``: that mode is safe on its own
    terms and has no such precondition.
    """
    import ast

    config = tomllib.loads(CONFIG.read_text(encoding="utf-8")).get("client", {})
    if config.get("toolbarMode") != "minimal":
        pytest.skip("only 'minimal' depends on the app declaring no menu items")

    dashboard = CONFIG.parent.parent / "dashboard"
    offenders = [
        f"{path.name}:{node.lineno}"
        for path in sorted(dashboard.glob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Call)
        for keyword in node.keywords
        if keyword.arg == "menu_items"
    ]
    assert not offenders, (
        f"menu_items is declared at {', '.join(offenders)}; under toolbarMode "
        "'minimal' those entries are exactly what still renders"
    )


def test_usage_stats_gathering_is_disabled():
    """A demo of controlled execution should not phone home by default."""
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert data.get("browser", {}).get("gatherUsageStats") is False
