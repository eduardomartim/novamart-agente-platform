"""Run the API: ``python -m agent_platform.api``.

Configuration comes from the environment, which is what ``Settings.from_env()``
already does -- the API adds only where to listen.
"""

from __future__ import annotations

import os
import sys


def main() -> int:
    try:
        import uvicorn
    except ImportError:  # pragma: no cover - depends on the api extra
        print(
            'uvicorn is not installed. Install the api extra: pip install -e ".[api]"',
            file=sys.stderr,
        )
        return 1

    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))

    # Structured logs, and the exact secret values this process holds so they
    # can be redacted by identity as well as by shape. Done before the server
    # starts so no line escapes the plain formatter first.
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        from ..observability import logging as structured
        from ..security.secrets import known_secret_values

        structured.configure(
            os.getenv("LOG_LEVEL", "info"),
            known_secrets=known_secret_values(
                os.getenv("GEMINI_API_KEY"),
                os.getenv("EXECUTION_GRANT_SECRET"),
                os.getenv("REDIS_URL"),
                os.getenv("DATABASE_URL"),
            ),
        )

    # The factory defers building the platform until the server starts, so an
    # import of this module never opens a database.
    structured_logging = os.getenv("LOG_FORMAT", "json").lower() == "json"

    uvicorn.run(
        "agent_platform.api.app:create_app",
        factory=True,
        host=host,
        port=port,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        # `None` stops uvicorn installing its own handlers over the formatter
        # configured above. Without it uvicorn wins -- it reconfigures logging
        # at startup -- and every line comes out as plain text, including the
        # access log, which then never passes the sanitiser. Found by looking at
        # the logs of a running pod rather than by reading the code.
        log_config=None if structured_logging else "auto",
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
