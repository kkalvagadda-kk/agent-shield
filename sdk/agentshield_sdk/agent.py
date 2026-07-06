"""
Agent dataclass — the top-level descriptor that a developer writes.

Example (platform tools — recommended):
    agent = Agent(
        name="order-agent",
        instructions="You help with order status and refunds.",
        tools=["lookup_order", "issue_refund"],
    )

Example (legacy inline tools — deprecated):
    agent = Agent(
        name="order-agent",
        instructions="You help with order status and refunds.",
        tools=[lookup_order, issue_refund],
    )
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Agent:
    """Describes an AI agent: its identity, instructions, tools, and handoffs.

    Attributes:
        name:         Unique agent name. Must match the AGENT_NAME env var when
                      deployed; used for OPA policy lookup and Langfuse traces.
        instructions: System prompt injected at the start of every conversation.
        tools:        List of platform tool names (strings) or callables decorated
                      with ``@tool(risk=...)``. String references are resolved from
                      the platform registry at startup via tool_resolver.
        model:        Optional model override. If set, takes precedence over the
                      ``LLM_MODEL`` env var.
        handoffs:     List of target agent names this agent is allowed to
                      delegate to via ``handoff()``.
    """

    name: str
    instructions: str
    tools: list[Any]
    model: str | None = None
    handoffs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.name or not self.name.strip():
            raise ValueError("Agent.name must be a non-empty string")
        if not self.instructions or not self.instructions.strip():
            raise ValueError("Agent.instructions must be a non-empty string")

        for t in self.tools:
            if isinstance(t, str):
                continue
            if not hasattr(t, "risk"):
                raise ValueError(
                    f"Tool '{getattr(t, '__name__', t)}' must be decorated with "
                    "@tool(risk='low') or @tool(risk='high'), or be a string "
                    "referencing a platform-registered tool."
                )
            if t.risk not in ("low", "high", "medium", "critical"):
                raise ValueError(
                    f"Tool '{t.__name__}' has invalid risk value '{t.risk}'. "
                    "Must be 'low', 'medium', 'high', or 'critical'."
                )

        # Deduplicate handoffs while preserving order
        seen: set[str] = set()
        deduped: list[str] = []
        for h in self.handoffs:
            if h not in seen:
                seen.add(h)
                deduped.append(h)
        self.handoffs = deduped

    @property
    def platform_tool_names(self) -> list[str]:
        """Return tool names that need to be resolved from the platform registry."""
        return [t for t in self.tools if isinstance(t, str)]

    @property
    def inline_tools(self) -> list[Any]:
        """Return legacy inline tool callables (deprecated path)."""
        return [t for t in self.tools if not isinstance(t, str)]

    @property
    def tool_names(self) -> list[str]:
        """Return a list of all tool names registered on this agent."""
        names: list[str] = []
        for t in self.tools:
            if isinstance(t, str):
                names.append(t)
            else:
                names.append(t.tool_name)
        return names

    @property
    def high_risk_tools(self) -> list[Any]:
        """Return only the inline tools marked risk='high'."""
        return [t for t in self.tools if not isinstance(t, str) and t.risk == "high"]
