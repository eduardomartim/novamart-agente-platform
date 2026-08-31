"""Least-privilege authorisation.

Authorisation is decided by two independent checks that must *both* pass:

1. the tool's own ``allowed_agents`` list, and
2. the capability matrix below.

The redundancy is deliberate. If someone later adds ``RESEARCHER`` to the
``update_record`` allow-list by mistake, the capability matrix still refuses,
because the researcher role holds no ``WRITE_DATA`` capability. A single
misconfiguration cannot escalate privilege on its own.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from ..models import AgentName, Capability
from ..tools.models import ToolDefinition

#: The matrix from the blueprint, expressed as the authoritative source.
#: Router deliberately holds no capabilities: it classifies and nothing else.
AGENT_CAPABILITIES: Final[dict[AgentName, frozenset[Capability]]] = {
    AgentName.ROUTER: frozenset(),
    AgentName.RESEARCHER: frozenset({Capability.SEARCH, Capability.READ_DATA}),
    AgentName.EXECUTOR: frozenset(
        {
            Capability.SEARCH,
            Capability.READ_DATA,
            Capability.WRITE_DATA,
            Capability.SEND_MESSAGE,
        }
    ),
    AgentName.VALIDATOR: frozenset({Capability.READ_DATA}),
    #: The answerer turns documents that were already retrieved into prose. It
    #: reads nothing itself and proposes nothing, so it needs no capability --
    #: not even READ_DATA, because it never reaches a tool to read with.
    AgentName.ANSWERER: frozenset(),
}

#: No role holds DELETE. Destructive capability is not merely unassigned by
#: oversight; it is unassigned on purpose, and the tests assert it stays that way.
UNASSIGNED_CAPABILITIES: Final[frozenset[Capability]] = frozenset({Capability.DELETE})


@dataclass(frozen=True, slots=True)
class AuthorizationResult:
    authorized: bool
    reason: str


def capabilities_for(agent: AgentName) -> frozenset[Capability]:
    return AGENT_CAPABILITIES.get(agent, frozenset())


def authorize(agent: AgentName, tool: ToolDefinition) -> AuthorizationResult:
    """Check *agent* against *tool* using both independent gates."""
    if not tool.permits(agent):
        allowed = ", ".join(sorted(a.value for a in tool.allowed_agents)) or "none"
        return AuthorizationResult(
            authorized=False,
            reason=(
                f"agent {agent.value!r} is not in the allow-list for tool "
                f"{tool.name!r} (allowed: {allowed})"
            ),
        )

    if tool.capability not in capabilities_for(agent):
        return AuthorizationResult(
            authorized=False,
            reason=(
                f"agent {agent.value!r} does not hold the "
                f"{tool.capability.value!r} capability required by tool {tool.name!r}"
            ),
        )

    return AuthorizationResult(
        authorized=True,
        reason=f"agent {agent.value!r} holds {tool.capability.value!r} for tool {tool.name!r}",
    )


def describe_matrix() -> dict[str, dict[str, bool]]:
    """Render the permission matrix for documentation and the dashboard."""
    return {
        agent.value: {
            capability.value: capability in capabilities_for(agent)
            for capability in Capability
        }
        for agent in AgentName
    }
