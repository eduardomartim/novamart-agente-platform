"""Agent Platform: a reference implementation of controlled multi-agent execution.

The central invariant of this package is that no agent ever executes a tool
directly. Agents *propose* actions; the policy engine decides; the tool gateway
is the only component that can invoke a registered tool.
"""

__version__ = "0.1.0"
