"""
Agent dataclass — the top-level descriptor that a developer writes.

Example:
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
        tools:        List of callables decorated with ``@tool(risk=...)``.
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
            if not hasattr(t, "risk"):
                raise ValueError(
                    f"Tool '{getattr(t, '__name__', t)}' must be decorated with "
                    "@tool(risk='low') or @tool(risk='high')"
                )
            if t.risk not in ("low", "high"):
                raise ValueError(
                    f"Tool '{t.__name__}' has invalid risk value '{t.risk}'. "
                    "Must be 'low' or 'high'."
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
    def tool_names(self) -> list[str]:
        """Return a list of tool names registered on this agent."""
        return [t.tool_name for t in self.tools]

    @property
    def high_risk_tools(self) -> list[Any]:
        """Return only the tools marked risk='high'."""
        return [t for t in self.tools if t.risk == "high"]
