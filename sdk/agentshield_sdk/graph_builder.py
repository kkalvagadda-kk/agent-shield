"""
Graph builder — constructs a governed LangGraph ReAct agent from an Agent descriptor.

Architecture:
- Uses ``create_react_agent`` as the base.
- Each tool is wrapped with an async governance layer that:
    1. Calls OPA to get a policy decision.
    2. If ``require_approval=True`` or the tool's risk is "high", calls
       ``hitl.require_approval()`` which internally calls LangGraph ``interrupt()``.
    3. If ``allow=False``, returns a denial string (tool is not executed).
    4. Otherwise executes the original tool function.
- Wrapped tools are converted to LangChain tools via @langchain_core.tools.tool
  so they can be bound to the LLM.
"""
from __future__ import annotations

import asyncio
import functools
import logging
from typing import Any

from . import config
from .agent import Agent
from .hitl import require_approval
from .llm import get_llm
from . import opa_client

logger = logging.getLogger(__name__)

# Registry mapping tool name → risk level so streaming.py can look it up.
_TOOL_RISK_REGISTRY: dict[str, str] = {}


def _get_tool_risk(tool_name: str) -> str:
    """Return the risk level for a tool name (populated during graph build)."""
    return _TOOL_RISK_REGISTRY.get(tool_name, "low")


def _wrap_tool_with_governance(fn: Any, agent_name: str) -> Any:
    """Return an async wrapper that injects OPA check + HITL before the tool runs.

    The wrapper preserves the original function's __name__, __doc__, and
    type-annotations so that LangChain's tool introspection works correctly.
    """
    @functools.wraps(fn)
    async def governed_tool(**kwargs: Any) -> Any:
        # 1. OPA decision.
        decision = await opa_client.check_tool(agent_name, fn.tool_name, kwargs)

        if not decision.allow:
            logger.info(
                "OPA denied tool=%s agent=%s reason=%s",
                fn.tool_name, agent_name, decision.reason,
            )
            return f"Tool '{fn.tool_name}' denied by policy: {decision.reason}"

        # 2. HITL for high-risk tools or when OPA requires approval.
        needs_approval = decision.require_approval or fn.risk == "high"
        if needs_approval:
            # require_approval internally calls interrupt(); the graph pauses
            # here and resumes when the reviewer acts.
            from contextvars import copy_context
            import contextvars
            thread_id = _current_thread_id.get("")
            approval_result = await require_approval(
                agent_name=agent_name,
                tool_name=fn.tool_name,
                tool_args=kwargs,
                thread_id=thread_id,
                risk=fn.risk,
                conversation_history=None,
            )
            if approval_result.get("decision") != "approved":
                reason = approval_result.get("reason", "rejected by reviewer")
                return f"Tool '{fn.tool_name}' was not approved: {reason}"

        # 3. Execute the original tool.
        if asyncio.iscoroutinefunction(fn):
            return await fn(**kwargs)
        return fn(**kwargs)

    # Copy metadata for LangChain introspection.
    governed_tool.__name__ = fn.__name__
    governed_tool.__doc__ = fn.__doc__
    governed_tool.risk = fn.risk
    governed_tool.tool_name = fn.tool_name
    # Copy annotations so LangChain can build the tool schema.
    governed_tool.__annotations__ = getattr(fn, "__annotations__", {})
    return governed_tool


# ContextVar used by governed_tool to access the current LangGraph thread_id.
# Set by the Runner before invoking the graph.
import contextvars
_current_thread_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "current_thread_id", default=""
)


def build_graph(agent: Agent, checkpointer: Any = None) -> Any:
    """Build and compile a governed LangGraph ReAct agent.

    Args:
        agent:        The Agent descriptor (name, instructions, tools, model).
        checkpointer: LangGraph checkpointer (AsyncPostgresSaver or MemorySaver).
                      If None, graph is stateless (no HITL resume support).

    Returns:
        A compiled LangGraph graph ready for ainvoke / astream_events.
    """
    from langgraph.prebuilt import create_react_agent  # type: ignore[import]
    from langchain_core.tools import tool as lc_tool  # type: ignore[import]

    # Resolve the LLM (uses agent.model override if set, else env var).
    llm = get_llm(model_override=agent.model)

    # Wrap each tool with governance and register its risk level.
    lc_tools: list[Any] = []
    for fn in agent.tools:
        _TOOL_RISK_REGISTRY[fn.tool_name] = fn.risk

        governed = _wrap_tool_with_governance(fn, agent.name)

        # Convert to a LangChain-compatible tool.
        # We use the lc_tool decorator with the function's name and docstring.
        lc_fn = lc_tool(governed)
        lc_tools.append(lc_fn)

    graph = create_react_agent(
        model=llm,
        tools=lc_tools,
        prompt=agent.instructions,
        checkpointer=checkpointer,
    )
    return graph
