"""Run the tool server: ``python -m agent_platform.mcp_server``."""

from __future__ import annotations

from .server import main

if __name__ == "__main__":  # pragma: no cover - process entrypoint
    raise SystemExit(main())
