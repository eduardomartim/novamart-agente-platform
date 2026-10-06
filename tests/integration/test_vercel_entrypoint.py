"""The Vercel entrypoint's two duties when Vercel is the one running it.

A function's filesystem is read-only and its instances share nothing, so on
Vercel the SQLite and process-local fallbacks are wrong rather than slow: the
first cold start without ``DATABASE_URL`` died with "unable to open database
file". And the uvicorn entrypoint's redacting log formatter was never installed
on this path, so an unhandled error's traceback reached Vercel's logs
unredacted.

Outside Vercel (``VERCEL`` unset) the entrypoint behaves exactly as before; the
cold-start tests in ``test_visitor_quota.py`` cover that path.

Every value here is synthetic. ``create_app`` is replaced, so nothing connects
to a database, a Redis or a provider.
"""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("starlette")

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "synthetic-" + "db-password-0000"
DATABASE_URL = f"postgresql://app:{PASSWORD}@db.example.invalid:5432/app"
REDIS_URL = "redis://:" + "synthetic-redis-pw-0000" + "@cache.example.invalid:6379/0"


@pytest.fixture
def load_entrypoint(monkeypatch):
    """Import `api/index.py` fresh under a given environment, then undo it all."""
    saved = dict(os.environ)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import agent_platform.api.app as api_app
    from agent_platform.observability import logging as structured

    configured: list[dict] = []
    monkeypatch.setattr(api_app, "create_app", lambda *a, **k: "app-sentinel")
    monkeypatch.setattr(
        structured, "configure", lambda *a, **k: configured.append(k)
    )

    def load(**env: str | None):
        for name in ("VERCEL", "DATABASE_URL", "REDIS_URL", "GEMINI_API_KEY",
                     "EXECUTION_GRANT_SECRET", "LOG_FORMAT"):
            os.environ.pop(name, None)
        for name, value in env.items():
            if value is not None:
                os.environ[name] = value
        sys.modules.pop("api.index", None)
        return importlib.import_module("api.index")

    try:
        yield load, configured
    finally:
        sys.modules.pop("api.index", None)
        os.environ.clear()
        os.environ.update(saved)


def test_on_vercel_a_missing_database_url_is_refused_by_name(load_entrypoint):
    load, _configured = load_entrypoint
    with pytest.raises(RuntimeError) as excinfo:
        load(VERCEL="1", REDIS_URL=REDIS_URL)
    assert "DATABASE_URL" in str(excinfo.value)
    assert "REDIS_URL" not in str(excinfo.value)


def test_on_vercel_a_missing_redis_url_is_refused_by_name(load_entrypoint):
    load, _configured = load_entrypoint
    with pytest.raises(RuntimeError) as excinfo:
        load(VERCEL="1", DATABASE_URL=DATABASE_URL)
    assert "REDIS_URL" in str(excinfo.value)


def test_the_refusal_prints_no_value(load_entrypoint):
    load, _configured = load_entrypoint
    with pytest.raises(RuntimeError) as excinfo:
        load(VERCEL="1", DATABASE_URL=DATABASE_URL)
    assert PASSWORD not in str(excinfo.value)
    assert "db.example.invalid" not in str(excinfo.value)


def test_on_vercel_logs_are_redacted_with_every_configured_secret(load_entrypoint):
    load, configured = load_entrypoint
    module = load(VERCEL="1", DATABASE_URL=DATABASE_URL, REDIS_URL=REDIS_URL)
    assert module.app == "app-sentinel"
    assert len(configured) == 1
    secrets = configured[0]["known_secrets"]
    assert DATABASE_URL in secrets and PASSWORD in secrets
    assert REDIS_URL in secrets


def test_outside_vercel_nothing_changes(load_entrypoint):
    """No VERCEL: no requirement, no logging takeover -- the previous behaviour."""
    load, configured = load_entrypoint
    module = load()
    assert module.app == "app-sentinel"
    assert configured == []
