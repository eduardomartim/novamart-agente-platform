"""HTTP boundary for the agent platform.

Importing this package requires the ``api`` extra (``pip install -e ".[api]"``).
The core platform, the CLI and the test suite do not depend on it.
"""

from __future__ import annotations

from .app import create_app
from .schemas import (
    ConfirmRequest,
    ErrorResponse,
    HealthResponse,
    ReadyResponse,
    RunRequest,
    RunResponse,
    http_status_for,
    run_response_from,
)

__all__ = [
    "ConfirmRequest",
    "ErrorResponse",
    "HealthResponse",
    "ReadyResponse",
    "RunRequest",
    "RunResponse",
    "create_app",
    "http_status_for",
    "run_response_from",
]
