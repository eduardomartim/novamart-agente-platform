"""Vercel entrypoint for the NovaMart API.

Vercel's Python builder looks for ``app``/``index``/``server``/``main``/
``wsgi``/``asgi`` in the repository root or in ``src/``, ``app/`` or ``api/``,
and serves the module-level ASGI object named ``app``. This file is that
object and nothing else.

**It adds no behaviour.** The application, its authentication, its rate
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
"""

from __future__ import annotations

import sys
from pathlib import Path

# The project is a src-layout and Vercel installs dependencies without
# installing the project itself, so `agent_platform` is not importable from a
# bare checkout. `dashboard/app.py` resolves it exactly this way; the entrypoint
# follows the pattern the repository already uses rather than inventing one.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from agent_platform.api.app import create_app  # noqa: E402

#: The ASGI application Vercel serves. The name is required by the runtime.
app = create_app()
