"""
Workflow executor — parses WORKFLOW_JSON at module startup and builds a
LangGraph StateGraph from the node/edge definitions.

Workflow JSON format:
    {
      "nodes": [
        {"id": "node-1", "type": "agent",     "config": {...}},
        {"id": "node-2", "type": "http_tool", "config": {...}},
        {"id": "node-3", "type": "end",        "config": {"output_mapping": {...}}}
      ],
      "edges": [
        {"source": "node-1", "target": "node-2"},
        {"source": "node-2", "target": "node-3"}
      ]
    }

Build rules:
  1. Find the start node (no incoming edges, type != "end") → add START → node edge.
  2. For each "end" node → add node → END edge.
  3. For each "agent" node, collect all "http_tool" nodes reachable from it
     (by following edges) and pass them as embedded tools.  These nodes are
     NOT added to the StateGraph as independent nodes; they live inside the
     agent's Runner instead.
  4. All other nodes ("agent", standalone "http_tool", "end") are StateGraph
     nodes connected by workflow edges — collapsing through any agent-owned
     http_tool nodes.

The graph state is MessagesState (langgraph.graph.MessagesState).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Any, AsyncIterator
from uuid import uuid4

from langchain_core.messages import HumanMessage  # type: ignore[import]
from langgraph.graph import END, START, StateGraph  # type: ignore[import]
from langgraph.graph.message import MessagesState  # type: ignore[import]

from agentshield_sdk.checkpointer import get_checkpointer  # type: ignore[import]
from agentshield_sdk.safety_client import SafetyBlockedError, scan_input, scan_output  # type: ignore[import]
from agentshield_sdk.streaming import stream_events  # type: ignore[import]

from node_executors import AgentNodeExecutor, EndNodeExecutor, HttpToolNodeExecutor

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Parses WORKFLOW_JSON and builds a compiled LangGraph StateGraph.

    Usage:
        executor = WorkflowExecutor()
        checkpointer = await get_checkpointer()
        executor.graph = executor._build_compiled_graph(checkpointer)

        result = await executor.run("Hello", thread_id="thread-123")
        async for chunk in executor.run_streamed("Hello", thread_id="thread-123"):
            print(chunk)
    """

    def __init__(self) -> None:
        raw = os.environ["WORKFLOW_JSON"]
        # Support both plain JSON string and base64-encoded JSON.
        try:
            self.definition: dict = json.loads(raw)
        except json.JSONDecodeError:
            try:
                self.definition = json.loads(base64.b64decode(raw).decode())
            except Exception as exc:
                raise ValueError(
                    "WORKFLOW_JSON is neither valid JSON nor valid base64-encoded JSON. "
                    f"Decoding error: {exc}"
                ) from exc

        # Build an initial graph with MemorySaver so the object is usable
        # synchronously.  Callers should await executor.setup() to replace this
        # with an AsyncPostgresSaver when DIRECT_DATABASE_URL is set.
        self.graph: Any = self._build_compiled_graph(checkpointer=None)

    # ------------------------------------------------------------------
    # Graph construction helpers
    # ------------------------------------------------------------------

    def _get_agent_owned_http_tool_ids(self) -> set[str]:
        """Return IDs of http_tool nodes reachable (directly or transitively)
        from any agent node by following edges.

        These nodes are embedded as tools inside the agent rather than added
        as independent StateGraph nodes.
        """
        nodes_by_id: dict[str, dict] = {n["id"]: n for n in self.definition["nodes"]}
        edges: list[dict] = self.definition["edges"]
        agent_owned: set[str] = set()

        for node in self.definition["nodes"]:
            if node["type"] != "agent":
                continue
            # BFS from this agent node, collecting reachable http_tool nodes.
            queue: list[str] = [node["id"]]
            visited: set[str] = set()
            while queue:
                current = queue.pop(0)
                for edge in edges:
                    if edge["source"] != current:
                        continue
                    target_id = edge["target"]
                    if target_id in visited:
                        continue
                    visited.add(target_id)
                    target_node = nodes_by_id.get(target_id)
                    if target_node and target_node["type"] == "http_tool":
                        agent_owned.add(target_id)
                        queue.append(target_id)

        return agent_owned

    def _resolve_effective_successors(
        self,
        node_id: str,
        agent_owned_ids: set[str],
    ) -> list[str]:
        """Return StateGraph-level successors of *node_id*, collapsing through
        any agent-owned http_tool nodes (which are embedded as tools, not graph
        nodes).
        """
        edges = self.definition["edges"]
        direct_targets = [e["target"] for e in edges if e["source"] == node_id]
        result: list[str] = []
        for target_id in direct_targets:
            if target_id in agent_owned_ids:
                # Collapse: find the effective successors of the internalized node.
                result.extend(
                    self._resolve_effective_successors(target_id, agent_owned_ids)
                )
            else:
                result.append(target_id)
        return result

    def _build_compiled_graph(self, checkpointer: Any = None) -> Any:
        """Build and compile the StateGraph from the workflow definition.

        Args:
            checkpointer: LangGraph checkpointer (AsyncPostgresSaver / MemorySaver).
                          If None, falls back to MemorySaver so the graph can be
                          built synchronously in __init__.

        Returns:
            A compiled LangGraph graph.
        """
        if checkpointer is None:
            from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
            checkpointer = MemorySaver()

        nodes_by_id: dict[str, dict] = {n["id"]: n for n in self.definition["nodes"]}
        edges: list[dict] = self.definition["edges"]

        # Identify which http_tool nodes are owned by agent nodes.
        agent_owned_ids = self._get_agent_owned_http_tool_ids()

        builder: StateGraph = StateGraph(MessagesState)

        # --- Add nodes ---
        for node in self.definition["nodes"]:
            node_id: str = node["id"]
            node_type: str = node["type"]
            node_config: dict = node["config"]

            if node_id in agent_owned_ids:
                # Agent-owned http_tool — embedded as a tool, not a graph node.
                continue

            if node_type == "agent":
                # Collect reachable http_tool executors as embedded tools.
                reachable_http_tool_ids = [
                    tid
                    for tid in agent_owned_ids
                    if any(
                        e["source"] == node_id and e["target"] == tid
                        for e in self._expand_reachable_http_tools(node_id, edges, agent_owned_ids)
                    )
                ]
                # Build HttpToolNodeExecutors for all agent-owned http_tool nodes
                # reachable from this agent node.
                http_tool_executors = self._collect_http_tool_executors(
                    node_id, nodes_by_id, edges, agent_owned_ids
                )
                executor = AgentNodeExecutor(node_config, http_tool_executors)
                builder.add_node(node_id, executor.execute)

            elif node_type == "http_tool":
                # Standalone http_tool (not reachable from any agent node).
                executor = HttpToolNodeExecutor(node_config)  # type: ignore[assignment]
                builder.add_node(node_id, executor.execute)

            elif node_type == "end":
                end_executor = EndNodeExecutor(node_config)
                builder.add_node(node_id, end_executor.execute)

            else:
                logger.warning("Unknown node type %r (node %s) — skipping", node_type, node_id)

        # --- Add edges ---
        incoming: set[str] = {e["target"] for e in edges}
        for node in self.definition["nodes"]:
            node_id = node["id"]
            node_type = node["type"]

            if node_id in agent_owned_ids:
                continue

            # START → start nodes (no incoming edges, type != "end").
            if node_id not in incoming and node_type != "end":
                builder.add_edge(START, node_id)
                logger.debug("Edge: START → %s", node_id)

            # end nodes → END.
            if node_type == "end":
                builder.add_edge(node_id, END)
                logger.debug("Edge: %s → END", node_id)

            # Workflow edges (collapsing through agent-owned http_tool nodes).
            successors = self._resolve_effective_successors(node_id, agent_owned_ids)
            for succ_id in successors:
                if succ_id in agent_owned_ids:
                    continue
                builder.add_edge(node_id, succ_id)
                logger.debug("Edge: %s → %s", node_id, succ_id)

        compiled = builder.compile(checkpointer=checkpointer)
        logger.info(
            "WorkflowExecutor compiled graph: %d nodes (excl. %d agent-owned http_tools)",
            len(self.definition["nodes"]) - len(agent_owned_ids),
            len(agent_owned_ids),
        )
        return compiled

    def _expand_reachable_http_tools(
        self,
        start_id: str,
        edges: list[dict],
        agent_owned_ids: set[str],
    ) -> list[dict]:
        """Return all edge dicts from *start_id* that lead into agent_owned_ids."""
        result = []
        for edge in edges:
            if edge["source"] == start_id and edge["target"] in agent_owned_ids:
                result.append(edge)
        return result

    def _collect_http_tool_executors(
        self,
        agent_node_id: str,
        nodes_by_id: dict[str, dict],
        edges: list[dict],
        agent_owned_ids: set[str],
    ) -> list[HttpToolNodeExecutor]:
        """BFS from *agent_node_id* collecting all reachable agent-owned http_tool executors."""
        executors: list[HttpToolNodeExecutor] = []
        visited: set[str] = set()
        queue: list[str] = [agent_node_id]
        while queue:
            current = queue.pop(0)
            for edge in edges:
                if edge["source"] != current:
                    continue
                target_id = edge["target"]
                if target_id in visited or target_id not in agent_owned_ids:
                    continue
                visited.add(target_id)
                target_node = nodes_by_id.get(target_id)
                if target_node:
                    executors.append(HttpToolNodeExecutor(target_node["config"]))
                    queue.append(target_id)
        return executors

    # ------------------------------------------------------------------
    # Public async setup (replaces MemorySaver with Postgres checkpointer)
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Rebuild the compiled graph with the environment-configured checkpointer.

        Call this once in the FastAPI lifespan *after* creating the WorkflowExecutor
        to upgrade from MemorySaver (set in __init__) to AsyncPostgresSaver when
        DIRECT_DATABASE_URL is configured.
        """
        checkpointer = await get_checkpointer()
        self.graph = self._build_compiled_graph(checkpointer)
        logger.info(
            "WorkflowExecutor.setup complete: checkpointer=%s",
            type(checkpointer).__name__,
        )

    # ------------------------------------------------------------------
    # Public invocation methods
    # ------------------------------------------------------------------

    async def run(self, message: str, thread_id: str | None = None) -> dict:
        """Invoke the workflow synchronously and return a response dict.

        Steps:
          1. Safety scan of input.
          2. Graph ainvoke with the sanitised message.
          3. Safety scan of output.
          4. Return {"response": ..., "thread_id": ...}.

        Raises:
            SafetyBlockedError: if input or output is blocked.
        """
        thread_id = thread_id or str(uuid4())
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")

        # 1. Input safety scan.
        scan_result = await scan_input(
            message, agent_name=agent_name, session_id=thread_id
        )
        safe_message = scan_result.sanitized_text

        # 2. Invoke the graph.
        config = {"configurable": {"thread_id": thread_id}}
        state = {"messages": [HumanMessage(content=safe_message)]}
        result = await self.graph.ainvoke(state, config)

        # 3. Extract last AI message.
        messages = result.get("messages", [])
        last_msg = messages[-1] if messages else None
        response_text: str = (
            last_msg.content
            if last_msg and hasattr(last_msg, "content")
            else ""
        )

        # 4. Output safety scan.
        out_scan = await scan_output(
            response_text, agent_name=agent_name, session_id=thread_id
        )

        return {"response": out_scan.clean_text, "thread_id": thread_id}

    async def run_streamed(
        self, message: str, thread_id: str | None = None
    ) -> AsyncIterator[str]:
        """Stream workflow output as SSE-formatted strings.

        Yields:
            SSE-formatted strings (``event: …\\ndata: …\\n\\n`` frames).

        Raises:
            SafetyBlockedError: if input scan blocks the message.
        """
        thread_id = thread_id or str(uuid4())
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")

        # Safety scan before starting the stream — fail fast.
        scan_result = await scan_input(
            message, agent_name=agent_name, session_id=thread_id
        )
        safe_message = scan_result.sanitized_text

        config = {"configurable": {"thread_id": thread_id}}
        state = {"messages": [HumanMessage(content=safe_message)]}

        async for sse_chunk in stream_events(self.graph, state, config):
            yield sse_chunk

    async def resume(self, thread_id: str, decision: dict) -> dict:
        """Resume a paused graph thread after a HITL decision.

        Args:
            thread_id: The LangGraph checkpoint thread to resume.
            decision:  Reviewer decision dict, e.g. ``{"decision": "approved"}``.

        Returns:
            dict with ``response`` and ``thread_id``.
        """
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")
        config = {"configurable": {"thread_id": thread_id}}

        result = await self.graph.ainvoke(
            {"messages": [], "resume": decision}, config
        )

        messages = result.get("messages", [])
        last_msg = messages[-1] if messages else None
        response_text: str = (
            last_msg.content
            if last_msg and hasattr(last_msg, "content")
            else ""
        )

        out_scan = await scan_output(
            response_text, agent_name=agent_name, session_id=thread_id
        )
        return {"response": out_scan.clean_text, "thread_id": thread_id}
