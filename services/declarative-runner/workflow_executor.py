"""
Workflow executor — parses WORKFLOW_JSON at module startup and builds a
LangGraph StateGraph from the node/edge definitions.

New workflow JSON format (canvas redesign):
    {
      "nodes": [
        {
          "id": "triage-agent",
          "type": "agent",
          "position": {"x": 100, "y": 200},
          "config": {
            "name": "triage-agent",
            "instructions": "Classify the user issue and route to the right team.",
            "model": "claude-sonnet-4-6",
            "risk": "low",
            "tool_ids": ["uuid-of-lookup-order-tool"],
            "skill_ids": ["uuid-of-order-skill"]
          }
        },
        {"id": "end", "type": "end", "position": {"x": 600, "y": 200}, "config": {"output_mapping": {}}}
      ],
      "edges": [
        {"id": "e1", "source": "triage-agent", "target": "end", "condition": "default"}
      ]
    }

Key changes from old schema:
  - Agent nodes declare tool_ids[] and skill_ids[] (refs to Registry records).
  - Only agents and end nodes appear in the graph.
  - Edges have an optional "condition" string for conditional routing.
  - Tool definitions are fetched from the Registry API at startup.

Backward compatibility:
  - Old schema (nodes with type "http_tool") is still supported.
  - If tool_ids is absent on an agent config, falls back to the old BFS-based
    approach to find embedded http_tool nodes.

The graph state is MessagesState (langgraph.graph.MessagesState).
"""
from __future__ import annotations

import base64
import json
import logging
import os
from collections import defaultdict
from typing import Any, AsyncIterator
from uuid import uuid4

from langchain_core.messages import HumanMessage  # type: ignore[import]
from langgraph.graph import END, START, StateGraph  # type: ignore[import]
from langgraph.graph.message import MessagesState  # type: ignore[import]
from langgraph.types import Command  # type: ignore[import]

from agentshield_sdk.checkpointer import get_checkpointer  # type: ignore[import]
from agentshield_sdk.safety_client import SafetyBlockedError, scan_input, scan_output  # type: ignore[import]
from agentshield_sdk.streaming import stream_events  # type: ignore[import]

from node_executors import AgentNodeExecutor, EndNodeExecutor, HttpToolNodeExecutor, PythonToolNodeExecutor

logger = logging.getLogger(__name__)

# Daemon/scheduled/webhook runs carry NO live user, so their "input" may be empty
# (a schedule can fire with no job spec at all). We must still never hand the LLM an
# empty user turn — providers reject non-whitespace-empty content — so when there is
# no real input we substitute a clean, instruction-oriented kickoff. The recorded run
# input stays "no input" (None); this is only the internal turn that drives the graph.
DAEMON_KICKOFF = (
    "Begin now. There is no user message for this run — act on your instructions "
    "and any job-spec or event payload you were given."
)


def daemon_kickoff_if_empty(message: str | None) -> str:
    """Return `message` unless it is blank/whitespace, in which case the daemon
    kickoff. Centralizes the 'never send an empty user turn' guard so every run
    entry point (reactive /chat, durable /run) stays consistent."""
    return message if (message or "").strip() else DAEMON_KICKOFF


def _make_langfuse_handler(trace_id: str | None, session_id: str | None = None):
    """Create a LangfuseCallbackHandler attached to an existing trace, or None."""
    if not trace_id:
        return None
    lf_pk = os.getenv("LANGFUSE_PUBLIC_KEY", "")
    lf_sk = os.getenv("LANGFUSE_SECRET_KEY", "")
    lf_host = os.getenv("LANGFUSE_HOST", "")
    if not lf_pk or not lf_sk or not lf_host:
        return None
    try:
        from langfuse.callback import CallbackHandler as LangfuseCallbackHandler
        return LangfuseCallbackHandler(
            trace_id=trace_id,
            session_id=session_id,
            public_key=lf_pk,
            secret_key=lf_sk,
            host=lf_host,
        )
    except Exception:
        return None


class WorkflowExecutor:
    """Parses WORKFLOW_JSON and builds a compiled LangGraph StateGraph.

    Usage:
        executor = WorkflowExecutor()
        await executor.setup()   # fetches tools from Registry API + upgrades checkpointer

        result = await executor.run("Hello", thread_id="thread-123")
        async for chunk in executor.run_streamed("Hello", thread_id="thread-123"):
            print(chunk)
    """

    def __init__(self) -> None:
        raw = os.environ.get("WORKFLOW_JSON", "")
        if raw:
            # Support both plain JSON string and base64-encoded JSON.
            try:
                self.definition: dict | None = json.loads(raw)
            except json.JSONDecodeError:
                try:
                    self.definition = json.loads(base64.b64decode(raw).decode())
                except Exception as exc:
                    raise ValueError(
                        "WORKFLOW_JSON is neither valid JSON nor valid base64-encoded JSON. "
                        f"Decoding error: {exc}"
                    ) from exc
        else:
            # Simple agent mode — definition built at setup time from registry API.
            self.definition = None

        # Pre-fetched tool executors keyed by agent node id.
        # Populated by setup(); empty until then so the graph can still be
        # built synchronously with old-schema workflows.
        # Entries may be HttpToolNodeExecutor OR PythonToolNodeExecutor instances.
        self._agent_tool_executors: dict[str, list] = {}

        # Build an initial graph with MemorySaver so the object is usable
        # synchronously (skip if no definition yet — simple agent mode defers to setup).
        self.graph: Any = (
            self._build_compiled_graph(checkpointer=None)
            if self.definition
            else None
        )

    async def setup_simple_agent_mode(self) -> None:
        """Fetch agent config from registry API and build a minimal graph.

        Called at startup when WORKFLOW_JSON is not set. The agent's instructions,
        model, and bound tools are fetched from the registry using AGENT_NAME.
        """
        import httpx
        from config import AGENT_NAME, REGISTRY_API_URL, LLM_MODEL

        async with httpx.AsyncClient(base_url=REGISTRY_API_URL, timeout=10) as client:
            # Fetch agent metadata (instructions live in agent.metadata)
            resp = await client.get(f"/api/v1/agents/{AGENT_NAME}")
            resp.raise_for_status()
            agent = resp.json()

            metadata = agent.get("metadata") or {}
            instructions = metadata.get("instructions", "You are a helpful assistant.")
            model = metadata.get("model") or LLM_MODEL

            # Fetch bound tool IDs
            tool_ids: list[str] = []
            try:
                tools_resp = await client.get(
                    f"/api/v1/agents/{AGENT_NAME}/tools"
                )
                if tools_resp.status_code == 200:
                    data = tools_resp.json()
                    items = data.get("items", data) if isinstance(data, dict) else data
                    for t in items:
                        tid = t.get("id") or t.get("tool_id")
                        if tid:
                            tool_ids.append(str(tid))
            except Exception:
                logger.warning("Could not fetch tools for %s", AGENT_NAME)

        # Build minimal single-node definition
        self.definition = {
            "nodes": [
                {
                    "id": "agent",
                    "type": "agent",
                    "position": {"x": 100, "y": 200},
                    "config": {
                        "name": AGENT_NAME,
                        "instructions": instructions,
                        "model": model,
                        "risk": "low",
                        "tool_ids": tool_ids,
                        "skill_ids": [],
                    },
                },
                {
                    "id": "end",
                    "type": "end",
                    "position": {"x": 500, "y": 200},
                    "config": {"output_mapping": {}},
                },
            ],
            "edges": [
                {"id": "e1", "source": "agent", "target": "end"},
            ],
        }

        logger.info(
            "Simple agent mode: built graph for '%s' (%d tools)",
            AGENT_NAME, len(tool_ids),
        )

    # ------------------------------------------------------------------
    # Registry API fetch helpers (new schema)
    # ------------------------------------------------------------------

    async def _fetch_tools_for_agent(
        self, tool_ids: list, skill_ids: list
    ) -> list[dict]:
        """Fetch tool definitions from Registry API, flattening skill_ids first."""
        import httpx
        from config import REGISTRY_API_URL

        all_tool_ids = list(tool_ids)
        async with httpx.AsyncClient(base_url=REGISTRY_API_URL, timeout=10) as client:
            # Flatten skill_ids → their constituent tool_ids
            for skill_id in skill_ids:
                try:
                    resp = await client.get(f"/api/v1/skills/{skill_id}")
                    if resp.status_code == 200:
                        skill = resp.json()
                        all_tool_ids.extend(skill.get("tool_ids", []))
                    else:
                        logger.warning(
                            "Could not fetch skill %s (status %d)", skill_id, resp.status_code
                        )
                except Exception as exc:
                    logger.warning("Could not fetch skill %s: %s", skill_id, exc)

            # Fetch each tool definition
            tools = []
            for tool_id in all_tool_ids:
                try:
                    resp = await client.get(f"/api/v1/tools/{tool_id}")
                    if resp.status_code == 200:
                        tools.append(resp.json())
                    else:
                        logger.warning(
                            "Tool %s not found (status %d)", tool_id, resp.status_code
                        )
                except Exception as exc:
                    logger.warning("Could not fetch tool %s: %s", tool_id, exc)
            return tools

    def _tool_dict_to_executor(self, tool: dict):
        """Convert a ToolResponse dict from the Registry API to a tool executor.

        Returns HttpToolNodeExecutor for type=http and PythonToolNodeExecutor for type=python.
        """
        from config import PYTHON_EXECUTOR_URL

        tool_type = tool.get("type", "http")

        if tool_type == "python":
            config = {
                "name": tool.get("name", "python_tool"),
                "description": tool.get("description"),
                "python_code": tool.get("python_code", ""),
                "risk": tool.get("risk_level", "low"),
                "timeout_ms": tool.get("http_timeout_ms") or 10_000,
                # Eval v2 E-2: carry the registry's classification through to the
                # callable (the seam reads fn.side_effecting at the delivery edge).
                # Absent ⇒ None ⇒ fail-closed (mocked under eval_mode=record).
                "side_effecting": tool.get("side_effecting"),
            }
            return PythonToolNodeExecutor(config, executor_url=PYTHON_EXECUTOR_URL)

        # Default: HTTP tool
        config = dict(tool.get("config", {}))
        if "endpoint" not in config:
            config["endpoint"] = tool.get("config", {}).get("endpoint") or tool.get("http_url", "")
        if "method" not in config:
            config["method"] = tool.get("config", {}).get("method") or tool.get("http_method", "GET")
        if "name" not in config:
            config["name"] = tool.get("name", "http_tool")
        if "risk" not in config:
            config["risk"] = tool.get("risk_level", "low")
        if "headers" not in config:
            config["headers"] = tool.get("config", {}).get("headers") or tool.get("http_headers") or {}
        if "body_template" not in config:
            config["body_template"] = (
                tool.get("config", {}).get("body_template")
                or tool.get("http_body_template")
                or ""
            )
        if "auth_config_id" not in config:
            config["auth_config_id"] = tool.get("auth_config_id")
        if "description" not in config:
            config["description"] = tool.get("description")
        if "input_schema" not in config:
            config["input_schema"] = tool.get("input_schema")
        if "side_effecting" not in config:
            # Eval v2 E-2: without this the flag never reaches the callable on the
            # declarative-runner path, so EVERY tool (even a provably read-only GET)
            # read as unclassified and was mocked under eval_mode=record. The SDK's
            # tool_resolver already threads it; this is the same contract for the
            # runner's own executor builder (suite-74 T-S74-005).
            config["side_effecting"] = tool.get("side_effecting")
        return HttpToolNodeExecutor(config)

    async def _prefetch_agent_tools(self) -> None:
        """Fetch tool definitions from Registry API for all new-schema agent nodes."""
        for node in self.definition["nodes"]:
            if node["type"] != "agent":
                continue
            node_config = node["config"]
            tool_ids = node_config.get("tool_ids", [])
            skill_ids = node_config.get("skill_ids", [])
            # Only use Registry API fetch for new-schema agents (have tool_ids/skill_ids)
            if not tool_ids and not skill_ids:
                continue
            tool_dicts = await self._fetch_tools_for_agent(tool_ids, skill_ids)
            self._agent_tool_executors[node["id"]] = [
                self._tool_dict_to_executor(t) for t in tool_dicts
            ]
            logger.info(
                "_prefetch_agent_tools: agent=%s fetched %d tools",
                node["id"],
                len(self._agent_tool_executors[node["id"]]),
            )

    # ------------------------------------------------------------------
    # Conditional routing helpers (new schema)
    # ------------------------------------------------------------------

    def _is_end_node(self, node_id: str) -> bool:
        """Return True if *node_id* refers to an end-type node."""
        nodes_by_id: dict[str, dict] = {n["id"]: n for n in self.definition["nodes"]}
        node = nodes_by_id.get(node_id)
        return node is not None and node["type"] == "end"

    def _make_router(self, node_id: str, outgoing_edges: list[dict]):
        """
        Returns (router_fn, path_map) for add_conditional_edges.

        Routing logic: examine the last AI message content for condition keywords.
        If the message contains the condition string (case-insensitive), that edge is taken.
        If no condition matches, the 'default' edge (or first unconditional edge) is taken.
        """
        # Build condition → target map (exclude "default" sentinel)
        conditional = {
            e["condition"]: e["target"]
            for e in outgoing_edges
            if e.get("condition") and e["condition"] != "default"
        }
        default_target = next(
            (
                e["target"]
                for e in outgoing_edges
                if not e.get("condition") or e["condition"] == "default"
            ),
            END,
        )

        def resolve(target: str) -> str:
            return END if self._is_end_node(target) else target

        def router(state: MessagesState) -> str:
            last = state["messages"][-1] if state["messages"] else None
            content = str(getattr(last, "content", "")).lower() if last else ""
            for cond, target in conditional.items():
                if cond.lower() in content:
                    return resolve(target)
            return resolve(default_target)

        path_map = {cond: resolve(t) for cond, t in conditional.items()}
        path_map["default"] = resolve(default_target)

        return router, path_map

    # ------------------------------------------------------------------
    # Old-schema BFS helpers (backward compat)
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
                result.extend(
                    self._resolve_effective_successors(target_id, agent_owned_ids)
                )
            else:
                result.append(target_id)
        return result

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
    # Graph construction
    # ------------------------------------------------------------------

    def _is_new_schema(self) -> bool:
        """Return True if any agent node uses tool_ids/skill_ids (new canvas schema)."""
        for node in self.definition["nodes"]:
            if node["type"] == "agent":
                cfg = node.get("config", {})
                if cfg.get("tool_ids") is not None or cfg.get("skill_ids") is not None:
                    return True
        return False

    def _build_compiled_graph(self, checkpointer: Any = None) -> Any:
        """Build and compile the StateGraph from the workflow definition.

        Supports both old schema (http_tool nodes as edges) and new schema
        (tool_ids/skill_ids on agent config + conditional edge routing).

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

        use_new_schema = self._is_new_schema()

        if use_new_schema:
            return self._build_new_schema_graph(nodes_by_id, edges, checkpointer)
        else:
            return self._build_old_schema_graph(nodes_by_id, edges, checkpointer)

    def _build_new_schema_graph(
        self,
        nodes_by_id: dict[str, dict],
        edges: list[dict],
        checkpointer: Any,
    ) -> Any:
        """Build graph for new canvas schema (tool_ids + conditional routing)."""
        builder: StateGraph = StateGraph(MessagesState)

        # --- Add nodes ---
        for node in self.definition["nodes"]:
            node_id: str = node["id"]
            node_type: str = node["type"]
            node_config: dict = node["config"]

            if node_type == "agent":
                # Use pre-fetched executors if available (mix of Http + Python), else empty list
                tool_executors = self._agent_tool_executors.get(node_id, [])
                executor = AgentNodeExecutor(node_config, tool_executors)
                subgraph = executor.build_subgraph()
                builder.add_node(node_id, subgraph)

            elif node_type == "end":
                end_executor = EndNodeExecutor(node_config)
                builder.add_node(node_id, end_executor.execute)

            else:
                logger.warning("Unknown node type %r (node %s) — skipping", node_type, node_id)

        # --- Determine start nodes and add START edges ---
        incoming: set[str] = {e["target"] for e in edges}
        for node in self.definition["nodes"]:
            node_id = node["id"]
            node_type = node["type"]
            if node_id not in incoming and node_type != "end":
                builder.add_edge(START, node_id)
                logger.debug("Edge: START → %s", node_id)
            if node_type == "end":
                builder.add_edge(node_id, END)
                logger.debug("Edge: %s → END", node_id)

        # --- Add workflow edges (with conditional routing support) ---
        edges_by_source: dict[str, list[dict]] = defaultdict(list)
        for edge in edges:
            edges_by_source[edge["source"]].append(edge)

        for node_id, outgoing in edges_by_source.items():
            # Skip edges originating from end nodes (already wired to END above)
            src_node = nodes_by_id.get(node_id)
            if src_node and src_node["type"] == "end":
                continue

            has_conditions = any(
                e.get("condition") and e["condition"] != "default"
                for e in outgoing
            )
            is_multi = len(outgoing) > 1

            if is_multi or has_conditions:
                # Conditional routing
                router_fn, path_map = self._make_router(node_id, outgoing)
                builder.add_conditional_edges(node_id, router_fn, path_map)
                logger.debug(
                    "Conditional edges from %s: %s", node_id, list(path_map.keys())
                )
            else:
                # Simple unconditional edge
                target = outgoing[0]["target"]
                resolved = END if self._is_end_node(target) else target
                builder.add_edge(node_id, resolved)
                logger.debug("Edge: %s → %s", node_id, resolved)

        compiled = builder.compile(checkpointer=checkpointer)
        logger.info(
            "WorkflowExecutor compiled new-schema graph: %d nodes",
            len(self.definition["nodes"]),
        )
        return compiled

    def _build_old_schema_graph(
        self,
        nodes_by_id: dict[str, dict],
        edges: list[dict],
        checkpointer: Any,
    ) -> Any:
        """Build graph for old schema (http_tool nodes embedded as agent tools)."""
        agent_owned_ids = self._get_agent_owned_http_tool_ids()
        builder: StateGraph = StateGraph(MessagesState)

        # --- Add nodes ---
        for node in self.definition["nodes"]:
            node_id: str = node["id"]
            node_type: str = node["type"]
            node_config: dict = node["config"]

            if node_id in agent_owned_ids:
                continue

            if node_type == "agent":
                legacy_tool_executors = self._collect_http_tool_executors(
                    node_id, nodes_by_id, edges, agent_owned_ids
                )
                executor = AgentNodeExecutor(node_config, legacy_tool_executors)
                subgraph = executor.build_subgraph()
                builder.add_node(node_id, subgraph)

            elif node_type == "http_tool":
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

            if node_id not in incoming and node_type != "end":
                builder.add_edge(START, node_id)
                logger.debug("Edge: START → %s", node_id)

            if node_type == "end":
                builder.add_edge(node_id, END)
                logger.debug("Edge: %s → END", node_id)

            successors = self._resolve_effective_successors(node_id, agent_owned_ids)
            for succ_id in successors:
                if succ_id in agent_owned_ids:
                    continue
                builder.add_edge(node_id, succ_id)
                logger.debug("Edge: %s → %s", node_id, succ_id)

        compiled = builder.compile(checkpointer=checkpointer)
        logger.info(
            "WorkflowExecutor compiled old-schema graph: %d nodes (excl. %d agent-owned http_tools)",
            len(self.definition["nodes"]) - len(agent_owned_ids),
            len(agent_owned_ids),
        )
        return compiled

    # ------------------------------------------------------------------
    # Public async setup (fetches tools + upgrades checkpointer)
    # ------------------------------------------------------------------

    async def setup(self) -> None:
        """Rebuild the compiled graph with the environment-configured checkpointer.

        Also pre-fetches tool/skill definitions from the Registry API for
        new-schema agent nodes before rebuilding the graph.

        Call this once in the FastAPI lifespan *after* creating the WorkflowExecutor.
        """
        # Fetch tools from Registry API for new-schema workflows
        if self._is_new_schema():
            try:
                await self._prefetch_agent_tools()
            except Exception as exc:
                logger.warning(
                    "WorkflowExecutor.setup: tool prefetch failed (continuing with empty tools): %s",
                    exc,
                )

        checkpointer = await get_checkpointer()
        self.graph = self._build_compiled_graph(checkpointer)
        logger.info(
            "WorkflowExecutor.setup complete: checkpointer=%s",
            type(checkpointer).__name__,
        )

    # ------------------------------------------------------------------
    # Public invocation methods
    # ------------------------------------------------------------------

    async def run(
        self, message: str, thread_id: str | None = None, trace_id: str | None = None,
        memory_context: list[dict] | None = None,
    ) -> dict:
        """Invoke the workflow synchronously and return a response dict.

        Steps:
          1. Start/attach Langfuse trace.
          2. Safety scan of input.
          3. Graph ainvoke with the sanitised message.
          4. Safety scan of output.
          5. End trace and return response.

        Raises:
            SafetyBlockedError: if input or output is blocked.
        """
        from agentshield_sdk.tracing import tracer  # type: ignore[import]

        thread_id = thread_id or str(uuid4())
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")

        # 1. Start/attach trace
        trace_ctx = tracer.start_trace(
            name=f"workflow.{agent_name}",
            session_id=thread_id,
            agent_name=agent_name,
            trace_id=trace_id,
        )

        # 2. Input safety scan.
        scan_result = await scan_input(
            message, agent_name=agent_name, session_id=thread_id
        )
        # Daemon/scheduled runs may arrive with no user input — never build an empty
        # HumanMessage (the provider rejects it); drive the graph with a kickoff instead.
        safe_message = daemon_kickoff_if_empty(scan_result.sanitized_text)
        tracer.span(trace_ctx, "safety_scan_input",
                    input={"message_len": len(message)},
                    output={"sanitized": scan_result.sanitized_text != message})

        # 3. Invoke the graph.
        from langchain_core.messages import AIMessage
        history = []
        for m in (memory_context or []):
            if m.get("role") == "user":
                history.append(HumanMessage(content=m["content"]))
            elif m.get("role") == "assistant":
                history.append(AIMessage(content=m["content"]))
        config = {"configurable": {"thread_id": thread_id}}
        lf_handler = _make_langfuse_handler(trace_id, thread_id)
        if lf_handler:
            config["callbacks"] = [lf_handler]
        state = {"messages": history + [HumanMessage(content=safe_message)]}
        # Bind OpenInference/OTEL spans to a trace id derived from run_id (=trace_id)
        # so the LLM/tool spans land on the platform's trace, not a separate one.
        from agentshield_sdk.otel import otel_run_context
        with otel_run_context(trace_id):
            result = await self.graph.ainvoke(state, config)

        # 4. Extract last AI message.
        messages = result.get("messages", [])
        last_msg = messages[-1] if messages else None
        response_text: str = (
            last_msg.content
            if last_msg and hasattr(last_msg, "content")
            else ""
        )

        # 5. Output safety scan.
        out_scan = await scan_output(
            response_text, agent_name=agent_name, session_id=thread_id
        )

        tracer.end_trace(trace_ctx, output={"response_len": len(out_scan.clean_text)})
        return {"response": out_scan.clean_text, "thread_id": thread_id}

    async def run_streamed(
        self, message: str, thread_id: str | None = None, trace_id: str | None = None,
        memory_context: list[dict] | None = None,
    ) -> AsyncIterator[str]:
        """Stream workflow output as SSE-formatted strings.

        Args:
            memory_context: prior transcript turns (role/content, + author agent_name
                for a shared workflow transcript) injected as leading Human/AI
                messages so the streamed turn has conversational context — mirrors
                run(). The LangGraph checkpoint key stays ``thread_id``.

        Yields:
            SSE-formatted strings (``event: …\\ndata: …\\n\\n`` frames).

        Raises:
            SafetyBlockedError: if input scan blocks the message.
        """
        from agentshield_sdk.tracing import tracer  # type: ignore[import]

        thread_id = thread_id or str(uuid4())
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")

        trace_ctx = tracer.start_trace(
            name=f"workflow.{agent_name}.stream",
            session_id=thread_id,
            agent_name=agent_name,
            trace_id=trace_id,
        )

        # Safety scan before starting the stream — fail fast.
        scan_result = await scan_input(
            message, agent_name=agent_name, session_id=thread_id
        )
        # Daemon/scheduled runs may arrive with no user input — never build an empty
        # HumanMessage (the provider rejects it); drive the graph with a kickoff instead.
        safe_message = daemon_kickoff_if_empty(scan_result.sanitized_text)
        tracer.span(trace_ctx, "safety_scan_input",
                    input={"message_len": len(message)},
                    output={"sanitized": scan_result.sanitized_text != message})

        config = {"configurable": {"thread_id": thread_id}}
        lf_handler = _make_langfuse_handler(trace_id, thread_id)
        if lf_handler:
            config["callbacks"] = [lf_handler]

        # Inject prior transcript as leading messages (mirrors run()). For a shared
        # workflow transcript a turn may carry an author agent_name != this agent;
        # prefix that content with "[<author>]: " so the model attributes peers.
        from langchain_core.messages import AIMessage
        history = []
        for m in (memory_context or []):
            content = m["content"]
            author = m.get("agent_name")
            if author and author != agent_name and m.get("role") == "assistant":
                content = f"[{author}]: {content}"
            if m.get("role") == "user":
                history.append(HumanMessage(content=content))
            elif m.get("role") == "assistant":
                history.append(AIMessage(content=content))
        state = {"messages": history + [HumanMessage(content=safe_message)]}

        # Bind OTEL spans to the run_id-derived trace (see run()).
        from agentshield_sdk.otel import otel_run_context
        with otel_run_context(trace_id):
            async for sse_chunk in stream_events(self.graph, state, config):
                yield sse_chunk

        tracer.end_trace(trace_ctx, output={"streamed": True})

    async def resume(self, thread_id: str, decision: dict, trace_id: str | None = None) -> dict:
        """Resume a paused graph thread after a HITL decision.

        Args:
            thread_id: The LangGraph checkpoint thread to resume.
            decision:  Reviewer decision dict, e.g. ``{"decision": "approved"}``.
            trace_id:  Optional Langfuse trace to attach LLM observations to.

        Returns:
            dict with ``response`` and ``thread_id``.
        """
        agent_name = os.getenv("AGENT_NAME", "declarative-agent")
        config = {"configurable": {"thread_id": thread_id}}
        lf_handler = _make_langfuse_handler(trace_id, thread_id)
        if lf_handler:
            config["callbacks"] = [lf_handler]

        from agentshield_sdk.otel import otel_run_context
        with otel_run_context(trace_id):
            result = await self.graph.ainvoke(
                Command(resume=decision), config
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

    async def resume_stream(
        self, thread_id: str, decision: dict, trace_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Resume a paused graph and stream SSE events for the continuation."""
        config = {"configurable": {"thread_id": thread_id}}
        lf_handler = _make_langfuse_handler(trace_id, thread_id)
        if lf_handler:
            config["callbacks"] = [lf_handler]

        async for sse_chunk in stream_events(
            self.graph, Command(resume=decision), config
        ):
            yield sse_chunk
