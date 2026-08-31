"""Talking to the tool server without making the platform asynchronous.

The MCP SDK is async throughout: the client is an async context manager and
``call_tool`` is a coroutine. The platform is synchronous throughout, and stays
that way -- rewriting twelve thousand lines of working code so a transport can
be reached would be the tail wagging the dog, and it is not what this phase is
for.

So async is confined here, the same way it is confined to the ASGI edge in the
HTTP layer. One background thread owns an event loop; the loop owns one
long-lived MCP session over one stdio subprocess; synchronous callers hand work
to it with ``run_coroutine_threadsafe`` and wait for the result.

The session is long-lived on purpose. Opening a client per call would spawn and
tear down a subprocess for every tool invocation, which turns a tool call into a
process launch and makes the boundary cost far more than it needs to.
"""

from __future__ import annotations

import asyncio
import os
import threading
from typing import Any

#: How long a synchronous caller waits for the loop thread. Beyond this the tool
#: call is abandoned rather than blocking the request forever.
DEFAULT_CALL_TIMEOUT = 30.0

#: Environment handed to the subprocess. Deliberately built from nothing rather
#: than inherited: an inherited environment carries GEMINI_API_KEY, and the tool
#: server has no use for a provider credential. What it cannot see, it cannot
#: leak.
_FORWARDED = ("EXECUTION_GRANT_SECRET", "REDIS_URL", "PATH", "PYTHONPATH", "SYSTEMROOT")


def subprocess_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """The minimal environment the tool server needs.

    An allow-list, not a deny-list. A deny-list silently starts forwarding every
    new variable someone adds, and the first time that matters is the first time
    it forwards a credential.
    """
    env = dict(os.environ if source is None else source)
    return {name: env[name] for name in _FORWARDED if env.get(name)}


class McpToolTransport:
    """A synchronous façade over an MCP stdio session."""

    def __init__(
        self,
        *,
        command: str,
        args: list[str],
        env: dict[str, str] | None = None,
        call_timeout: float = DEFAULT_CALL_TIMEOUT,
    ) -> None:
        self._command = command
        self._args = args
        self._env = env if env is not None else subprocess_environment()
        self._timeout = call_timeout

        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._client: Any = None
        self._session: Any = None
        self._lock = threading.Lock()
        self._started = threading.Event()
        self._error: BaseException | None = None

    # ------------------------------------------------------------- lifecycle

    def start(self) -> None:
        """Launch the loop thread and the tool-server subprocess."""
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run_loop, name="mcp-transport", daemon=True
            )
            self._thread.start()
        if not self._started.wait(timeout=60):
            raise RuntimeError("the tool server did not start in time")
        if self._error is not None:
            raise RuntimeError("the tool server failed to start") from self._error

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._serve())
        except BaseException as exc:
            self._error = exc
            self._started.set()
        finally:
            loop.close()

    async def _serve(self) -> None:
        from mcp import Client, StdioServerParameters

        params = StdioServerParameters(
            command=self._command, args=self._args, env=self._env
        )
        self._client = Client(params)
        try:
            async with self._client as session:
                self._session = session
                self._started.set()
                # Park until shutdown. The session stays open, and with it the
                # subprocess, so tool calls do not pay for a process launch.
                self._stop = asyncio.Event()
                await self._stop.wait()
        except BaseException as exc:  # surfaced to the caller via start()
            self._error = exc
            self._started.set()
            raise

    def close(self) -> None:
        """Shut the session and the subprocess down.

        Called from the gateway's own ``shutdown``; without it the subprocess
        outlives the process that launched it.
        """
        loop, thread = self._loop, self._thread
        if loop is None or thread is None:
            return
        stop = getattr(self, "_stop", None)
        if stop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(stop.set)
        thread.join(timeout=15)
        self._thread = None
        self._loop = None
        self._session = None

    # ------------------------------------------------------------------ call

    def call(self, tool: str, arguments: dict[str, Any]) -> Any:
        """Invoke a tool over MCP and return its structured result."""
        self.start()
        loop, session = self._loop, self._session
        if loop is None or session is None:
            raise RuntimeError("the tool transport is not running")

        future = asyncio.run_coroutine_threadsafe(
            session.call_tool(tool, arguments), loop
        )
        result = future.result(timeout=self._timeout)
        return _unwrap(result)


def _unwrap(result: Any) -> Any:
    """Turn an MCP tool result into the value the platform expects.

    The tools return dictionaries, so the structured content is what matters;
    the text blocks are a rendering of it. An error result is raised rather
    than returned, because a tool that refused is not a tool that answered.
    """
    if getattr(result, "isError", False) or getattr(result, "is_error", False):
        raise PermissionError("execution refused")

    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        # The SDK wraps a non-dict return under "result"; unwrap that one level.
        if set(structured) == {"result"}:
            return structured["result"]
        return structured

    content = getattr(result, "content", None) or []
    for block in content:
        text = getattr(block, "text", None)
        if text is not None:
            import json

            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


__all__ = [
    "DEFAULT_CALL_TIMEOUT",
    "McpToolTransport",
    "subprocess_environment",
]
