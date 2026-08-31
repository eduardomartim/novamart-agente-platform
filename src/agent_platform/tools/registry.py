"""The tool registry: the sole source of executable tools.

Registration is explicit and static. There is no path by which a name produced
at runtime -- by a model, by user input, or by a tool result -- becomes an
executable tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from ..models import AgentName
from .fake_tools import TOOL_DEFINITIONS
from .models import ToolDefinition, ToolNotRegisteredError


class ToolRegistry:
    """An immutable-by-convention collection of registered tools."""

    def __init__(self, definitions: Iterable[ToolDefinition] = ()) -> None:
        self._tools: dict[str, ToolDefinition] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"tool {definition.name!r} is already registered")
        self._tools[definition.name] = definition

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._tools

    def __iter__(self) -> Iterator[ToolDefinition]:
        return iter(self._tools.values())

    def __len__(self) -> int:
        return len(self._tools)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def get(self, name: str) -> ToolDefinition:
        """Resolve a tool definition, or raise if it is not registered."""
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotRegisteredError(
                f"tool {name!r} is not registered; available tools: {', '.join(self.names())}"
            ) from exc

    def try_get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def specs_for(self, agent: AgentName) -> list[dict[str, Any]]:
        """Public metadata for the tools *agent* is permitted to propose.

        Returns descriptions only. An agent never receives a callable, so the
        prompt-visible surface and the executable surface are separate by
        construction.
        """
        return [
            definition.public_spec()
            for definition in sorted(self._tools.values(), key=lambda d: d.name)
            if definition.permits(agent)
        ]

    def permitted_names(self, agent: AgentName) -> tuple[str, ...]:
        return tuple(
            definition.name
            for definition in sorted(self._tools.values(), key=lambda d: d.name)
            if definition.permits(agent)
        )


def default_registry() -> ToolRegistry:
    """The registry used by the demo platform."""
    return ToolRegistry(TOOL_DEFINITIONS)
