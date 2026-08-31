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


def test_developer_toolbar_is_hidden(client_config):
    """The deploy/rerun/clear-cache controls are not viewer-facing."""
    assert client_config.get("toolbarMode") == "viewer"


def test_usage_stats_gathering_is_disabled():
    """A demo of controlled execution should not phone home by default."""
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    assert data.get("browser", {}).get("gatherUsageStats") is False
