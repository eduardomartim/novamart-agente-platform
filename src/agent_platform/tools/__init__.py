"""Tool registry, execution guard and simulated tools.

``ToolGateway`` is intentionally *not* re-exported here. The gateway sits above
the guardrails layer (it consumes the policy engine), while ``tools.models`` is
a low-level type module that the guardrails layer imports. Re-exporting the
gateway from this package would make importing a tool type pull in the whole
policy stack and create an import cycle.

Import it from its own module instead::

    from agent_platform.tools.gateway import ToolGateway
"""

from __future__ import annotations

from .execution import DirectToolInvocationError, gateway_execution, is_inside_gateway
from .models import ToolDefinition, ToolNotRegisteredError, ToolTimeoutError
from .registry import ToolRegistry, default_registry

__all__ = [
    "DirectToolInvocationError",
    "ToolDefinition",
    "ToolNotRegisteredError",
    "ToolRegistry",
    "ToolTimeoutError",
    "default_registry",
    "gateway_execution",
    "is_inside_gateway",
]
