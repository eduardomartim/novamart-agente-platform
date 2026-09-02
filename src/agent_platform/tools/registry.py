"""The tool registry: the sole source of executable tools.

Registration is explicit and static. There is no path by which a name produced
at runtime -- by a model, by user input, or by a tool result -- becomes an
executable tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import Any

from ..models import AgentName
from .analytics import ANALYTICS_TOOL_DEFINITIONS
from .fake_tools import TOOL_DEFINITIONS
from .models import ToolDefinition, ToolNotRegisteredError


def _for_the_model(spec: dict[str, Any]) -> dict[str, Any]:
    """The same tool, described without the metadata a model cannot use.

    Pydantic's JSON Schema carries two things that mean something to Pydantic
    and nothing to a reader of the prompt:

    * ``title`` -- the class name at the root, the field name inside each
      property. ``"title": "Limit"`` sits beside the key ``limit``, and
      ``"title": "NoArgs"`` names a class the model will never see. Forty-one
      of them across the registry, and not one is read by anything: argument
      validation goes through ``parameters.model_validate``, the Pydantic
      model itself, never through this schema.
    * the schema's root ``description`` -- the args model's docstring, which
      is written for whoever maintains the class. One of them currently tells
      the model *"a tool that takes nothing still declares a schema, so PL004
      can check it"*: our own policy reasoning, sent to a language model that
      has no business knowing it.

    Everything a model needs to choose and call a tool stays: the tool's own
    description, the property names, their types, ``enum``, ``default``,
    ``required``, and the per-property ``description``, which is the one
    written for a reader rather than for Pydantic.

    This is deliberately *not* done in ``ToolDefinition.public_spec``. That
    method also feeds the MCP server, which publishes the arguments schema to
    an external client -- a different audience with a different contract. Only
    the prompt is trimmed here, and only for the agents that receive it.

    The spec is copied rather than edited: ``model_json_schema()`` is cached by
    Pydantic, and mutating what it hands back would corrupt that cache.
    """
    schema = {
        key: value
        for key, value in spec["parameters"].items()
        if key not in ("title", "description")
    }
    properties = schema.get("properties")
    if properties:
        schema["properties"] = {
            name: {k: v for k, v in field.items() if k != "title"}
            for name, field in properties.items()
        }
    return {**spec, "parameters": schema}


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

        Trimmed by :func:`_for_the_model`: this is the one path that reaches a
        prompt, so it is the one place the schema is stripped of the metadata
        Pydantic writes for itself. ``public_spec`` is unchanged, and so is
        every other consumer of it.
        """
        return [
            _for_the_model(definition.public_spec())
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
    """The registry used by the demo platform.

    Two sources, one registry. The record tools answer questions about a single
    row; the analytics tools answer questions about the set. Both are static
    definitions declared in code and both pass through the same authorisation,
    so adding the second group changed what can be asked and nothing about who
    may ask it.
    """
    return ToolRegistry([*TOOL_DEFINITIONS, *ANALYTICS_TOOL_DEFINITIONS])
