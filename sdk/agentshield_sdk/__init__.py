"""
AgentShield SDK — build governed, observable AI agents with LangGraph.

Public API:
    Agent       — descriptor dataclass for an agent
    Runner      — runs an Agent (setup → run / run_streamed)
    tool        — decorator that marks a function as an agent tool (deprecated)
    build_graph — low-level graph builder (used internally by Runner)
    handoff     — send a message to another agent
"""
from .agent import Agent
from .runner import Runner
from .tool_decorator import tool
from .graph_builder import build_graph
from .handoff import handoff

__all__ = ["Agent", "Runner", "tool", "build_graph", "handoff"]
__version__ = "0.2.0"
