"""The tool server: a process that runs tools, and trusts nothing that asks it to.

This is the other half of the execution boundary. The orchestrator decides what
may run; this decides whether the thing asking has proof. It deliberately shares
no memory with the caller, so every assumption that held while tools were
function calls has to be re-established explicitly:

* the caller names a tool -- that name is *compared* against the grant, never
  used as authority;
* the caller sends arguments -- their digest is recomputed and compared, so
  arguments cannot be swapped under a valid grant;
* the caller may present the same grant twice -- the nonce is consumed
  atomically, so only the first attempt anywhere executes;
* the tool is looked up in *this* process's registry, so a name that is not a
  registered tool cannot become one;
* arguments are validated against the tool's own model before the handler sees
  them.

``require_gateway()`` still applies, and still matters. It is opened here, after
the grant verifies, which makes it the second of two layers rather than the only
one: the grant proves authorisation crossed the boundary, the ContextVar proves
nothing inside this process called a handler around the side.

A note on the tool schema
-------------------------
Each tool is exposed with a uniform ``(grant, arguments)`` signature rather than
its own generated parameter schema, because the SDK derives schemas from Python
signatures and synthesising one per tool would be fragile. The tool's real JSON
Schema is published in the description, and -- what actually matters -- the
arguments are validated against the tool's own pydantic model here, before
execution. Introspection is slightly poorer; the security properties are not.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from ..execution import grant as grants
from ..execution.nonce import NonceReplayed, consume
from ..state import build_shared_state
from ..tools.execution import gateway_execution
from ..tools.models import ToolDefinition
from ..tools.registry import ToolRegistry, default_registry

#: What a refusal looks like on the wire. Uniform on purpose: a server that
#: explains which check failed helps a caller iterate towards a forgery.
REFUSED = "execution refused"


class ToolServer:
    """Verifies grants and runs tools. Holds no opinion about policy."""

    def __init__(
        self,
        *,
        secret: str | None,
        registry: ToolRegistry | None = None,
        redis_url: str | None = None,
    ) -> None:
        if not secret:
            # Fail closed at construction. A server that starts without a key
            # and refuses everything looks identical to a misconfigured
            # deployment; one that refuses to start says what is wrong once.
            raise grants.GrantConfigurationError(
                "EXECUTION_GRANT_SECRET must be set to run the tool server"
            )
        self._secret = secret
        self._registry = registry or default_registry()
        # Nonces must be shared wherever grants are, or two replicas would each
        # consider themselves the first to see a replayed grant.
        self._backend = build_shared_state(redis_url).backend

    @property
    def tool_names(self) -> tuple[str, ...]:
        return self._registry.names()

    def describe(self, tool: ToolDefinition) -> str:
        spec = tool.public_spec()
        return (
            f"{spec['description']}\n\n"
            f"Requires an execution grant. Arguments schema:\n"
            f"{json.dumps(spec['parameters'], separators=(',', ':'))}"
        )

    def execute(self, tool_name: str, grant_token: str, arguments: dict[str, Any]) -> Any:
        """Run a tool, or refuse.

        Every refusal raises :class:`PermissionError` with the same message.
        The reason is recorded by the caller's tracer, never returned.
        """
        if not isinstance(arguments, dict):
            raise PermissionError(REFUSED)
        if grants.GRANT_ARGUMENT in arguments:
            # A grant hiding among the business arguments would be covered by
            # the digest and could confuse the two layers. There is no tool
            # with a parameter by this name.
            raise PermissionError(REFUSED)

        now = time.time()
        try:
            grant = grants.verify(
                grant_token,
                secret=self._secret,
                expected_tool=tool_name,
                arguments=arguments,
                now=now,
            )
        except grants.GrantError as exc:
            raise PermissionError(REFUSED) from exc

        # Single use, and atomic, so two replicas cannot both be first.
        try:
            consume(self._backend, grant.nonce, expires_at=grant.expires_at, now=now)
        except NonceReplayed as exc:
            raise PermissionError(REFUSED) from exc

        # The tool comes from this process's registry. A name the caller
        # invented cannot resolve to anything.
        tool = self._registry.try_get(tool_name)
        if tool is None:
            raise PermissionError(REFUSED)

        # Validate against the tool's own model before the handler runs.
        try:
            validated = tool.parameters(**arguments).model_dump()
        except Exception as exc:
            raise PermissionError(REFUSED) from exc

        # Second layer. Opened only now, and only around the handler.
        with gateway_execution():
            return tool.handler(**validated)


def build_mcp_server(server: ToolServer, name: str = "agent-platform-tools") -> Any:
    """Wrap a :class:`ToolServer` in an MCP server exposing its tools."""
    from mcp.server.mcpserver import MCPServer

    mcp = MCPServer(name=name)

    for tool_name in server.tool_names:
        definition = server._registry.get(tool_name)

        def make(bound_name: str) -> Any:
            def run(grant: str, arguments: dict[str, Any]) -> Any:
                """Execute a registered tool, given a valid execution grant."""
                return server.execute(bound_name, grant, arguments)

            return run

        mcp.add_tool(
            make(tool_name), name=tool_name, description=server.describe(definition)
        )

    return mcp


def main() -> int:  # pragma: no cover - process entrypoint
    """Run the tool server on stdio.

    Configuration comes from the environment. ``GEMINI_API_KEY`` is neither read
    nor needed: this process runs simulated tools and never calls a provider.
    """
    import anyio

    server = ToolServer(
        secret=os.getenv("EXECUTION_GRANT_SECRET"),
        redis_url=os.getenv("REDIS_URL", "").strip() or None,
    )
    anyio.run(build_mcp_server(server).run_stdio_async)
    return 0


__all__ = ["REFUSED", "ToolServer", "build_mcp_server", "main"]
