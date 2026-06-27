"""
@tool decorator — marks a callable as an agent tool with a risk level.

Usage:
    @tool(risk="low")
    def lookup_order(order_id: str) -> dict:
        ...

    @tool(risk="high")
    async def issue_refund(order_id: str, amount: float) -> dict:
        ...

The decorator attaches two metadata attributes:
    fn.risk       — "low" | "high"
    fn.tool_name  — the function's __name__ (used by OPA and HITL modules)

It does NOT wrap or alter the function's behaviour — it only tags it.
"""
from __future__ import annotations

from typing import Callable


def tool(risk: str = "low") -> Callable:
    """Return a decorator that tags a function as an AgentShield tool.

    Args:
        risk: Risk level for this tool. Must be ``"low"`` or ``"high"``.
              High-risk tools trigger HITL approval before execution.

    Returns:
        A decorator that attaches ``.risk`` and ``.tool_name`` to the function.

    Raises:
        AssertionError: If *risk* is not ``"low"`` or ``"high"``.
    """
    assert risk in ("low", "high"), (
        f"@tool(risk=...) must be 'low' or 'high', got: {risk!r}"
    )

    def decorator(fn: Callable) -> Callable:
        fn.risk = risk
        fn.tool_name = fn.__name__
        return fn

    return decorator
