"""Vercel entrypoint for the NovaMart API.

Vercel's Python builder looks for ``app``/``index``/``server``/``main``/
``wsgi``/``asgi`` in the repository root or in ``src/``, ``app/`` or ``api/``,
and serves the module-level ASGI object named ``app``. This file is that
object.

**It builds nothing of its own.** The application, its authentication, its rate
limiting and its platform all come from ``create_app()`` -- the same factory
``python -m agent_platform.api`` runs under uvicorn and the same one the API
tests drive. A second construction path would be a second place for the
security posture to differ, and the one that only runs in production is the one
nobody exercises.

Configuration comes from the environment, as it does everywhere else:
``create_app()`` calls ``Settings.from_env()`` and ``build_keyring()``, so a
deployment missing ``API_AUTH_KEYS`` under ``API_AUTH_MODE=enforced`` fails at
import rather than serving an unauthenticated API. That is the intended
behaviour on a cold start, not a startup bug.

Two things the uvicorn entrypoint does for its process are done here for the
function's, and only when Vercel is the one running it (``VERCEL=1``, which the
platform sets at run time):

* **Redacting logs.** ``python -m agent_platform.api`` installs the JSON
  formatter that strips credential shapes and the exact values of every
  configured secret. Without it an unhandled error's traceback reached Vercel's
  logs unredacted.
* **No local state.** A function's filesystem is read-only and its instances
  share nothing, so the SQLite and process-local fallbacks are wrong here rather
  than merely slower: without ``DATABASE_URL`` the platform tried to create a
  SQLite file and the cold start died with "unable to open database file", and
  without ``REDIS_URL`` every instance would keep its own rate limits, budget
  and pending approvals. Both are refused up front, by name, with no value
  printed.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# The project is a src-layout and Vercel installs dependencies without
# installing the project itself, so `agent_platform` is not importable from a
# bare checkout. `dashboard/app.py` resolves it exactly this way; the entrypoint
# follows the pattern the repository already uses rather than inventing one.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

#: Shared backends a Vercel deployment cannot run without. See the docstring.
REQUIRED_ON_VERCEL = ("DATABASE_URL", "REDIS_URL")

if os.environ.get("VERCEL"):
    from agent_platform.observability import logging as structured
    from agent_platform.security.secrets import known_secret_values

    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        structured.configure(
            os.getenv("LOG_LEVEL", "info"),
            known_secrets=known_secret_values(
                os.getenv("GEMINI_API_KEY"),
                os.getenv("EXECUTION_GRANT_SECRET"),
                os.getenv("REDIS_URL"),
                os.getenv("DATABASE_URL"),
            ),
        )

    missing = [name for name in REQUIRED_ON_VERCEL if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError(
            "NovaMart API on Vercel needs shared storage; set "
            + ", ".join(missing)
            + " in the project's environment variables (see docs/deploy.md)."
        )

from agent_platform.api.app import create_app  # noqa: E402

#: The ASGI application Vercel serves. The name is required by the runtime.
app = create_app()
