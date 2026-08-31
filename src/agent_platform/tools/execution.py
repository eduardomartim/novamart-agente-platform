"""Runtime enforcement of the gateway-only execution rule.

The blueprint's central invariant is that no agent may call a tool directly.
Documenting that is not enough, so it is enforced mechanically: every tool
implementation opens with :func:`require_gateway`, which fails unless the call
stack is inside :func:`gateway_execution`. Only the tool gateway opens that
context.

This turns "agents cannot bypass the gateway" from a convention into a testable
property.
"""

from __future__ import annotations

import secrets
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_GATEWAY_TOKEN: ContextVar[str | None] = ContextVar("agent_platform_gateway_token", default=None)


class DirectToolInvocationError(RuntimeError):
    """Raised when a tool is invoked outside the gateway."""


@contextmanager
def gateway_execution() -> Iterator[str]:
    """Mark the dynamic extent in which tool execution is authorised.

    A fresh token per invocation means a leaked token from an earlier call is
    useless, and ``ContextVar`` scoping means the authorisation cannot escape
    into another thread or task.
    """
    token_value = secrets.token_hex(8)
    reset = _GATEWAY_TOKEN.set(token_value)
    try:
        yield token_value
    finally:
        _GATEWAY_TOKEN.reset(reset)


def require_gateway(tool_name: str) -> None:
    """Abort unless execution is currently authorised by the gateway."""
    if _GATEWAY_TOKEN.get() is None:
        raise DirectToolInvocationError(
            f"tool {tool_name!r} was invoked outside the tool gateway; "
            "agents must submit a proposed action for policy evaluation instead"
        )


def is_inside_gateway() -> bool:
    """Introspection helper for tests and diagnostics."""
    return _GATEWAY_TOKEN.get() is not None
