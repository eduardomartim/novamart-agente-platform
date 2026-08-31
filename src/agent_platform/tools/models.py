"""Tool metadata types."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from ..models import AgentName, Capability, RiskLevel

ToolHandler = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """Everything the platform knows about a tool.

    Risk level and permissions live here, in platform-owned configuration, and
    never in anything a model can influence. The policy engine reads risk from
    this object; a model's opinion about how risky its own action is carries no
    weight.
    """

    name: str
    description: str
    risk_level: RiskLevel
    capability: Capability
    parameters: type[BaseModel]
    handler: ToolHandler
    allowed_agents: frozenset[AgentName] = field(default_factory=frozenset)
    requires_confirmation: bool = False
    #: Non-idempotent tools are never retried automatically after a failure.
    idempotent: bool = True
    timeout_seconds: float = 10.0
    simulated: bool = True

    def permits(self, agent: AgentName) -> bool:
        return agent in self.allowed_agents

    def public_spec(self) -> dict[str, Any]:
        """Metadata safe to show an agent when it is choosing an action.

        Deliberately excludes ``handler``: an agent receives a description of
        what exists, never a reference it could call.
        """
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
            "requires_confirmation": self.requires_confirmation,
            "parameters": self.parameters.model_json_schema(),
        }


class ToolError(RuntimeError):
    """Base class for tool-layer failures."""


class ToolNotRegisteredError(ToolError):
    """The requested tool does not exist in the registry."""


class ToolTimeoutError(ToolError):
    """The tool exceeded its configured timeout."""
