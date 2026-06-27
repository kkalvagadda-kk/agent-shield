"""
Node executors for the declarative workflow runner.

Each class handles one node type from the workflow JSON definition:
  - AgentNodeExecutor       — wraps tool nodes as tools and runs an LLM agent
  - HttpToolNodeExecutor    — makes an httpx HTTP call with {{variable}} substitution
  - PythonToolNodeExecutor  — calls python-executor microservice to run sandboxed code
  - EndNodeExecutor         — maps state fields to output per output_mapping config
"""
from __future__ import annotations

import inspect
import json
import logging
import re
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HttpToolNodeExecutor
# ---------------------------------------------------------------------------

class HttpToolNodeExecutor:
    """Makes an httpx HTTP call with {{variable}} substitution in URL/body.

    Supports GET, POST, PUT, DELETE methods.  Substitutes ``{{variable_name}}``
    placeholders in both ``endpoint`` and ``body_template`` from the provided
    state/kwargs dict.  Timeout is fixed at 10 s.
    """

    def __init__(self, node_config: dict) -> None:
        self.node_config = node_config
        self.name: str = node_config.get("name", "http_tool")
        self.endpoint: str = node_config.get("endpoint", "")
        self.method: str = node_config.get("method", "GET").upper()
        self.headers: dict = node_config.get("headers", {})
        self.body_template: str = node_config.get("body_template", "")
        self.risk: str = node_config.get("risk", "low")
        # auth_config_id is stored for future use (Phase 9+); not implemented here.
        self.auth_config_id: str | None = node_config.get("auth_config_id")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _substitute_vars(template: str, variables: dict) -> str:
        """Replace ``{{name}}`` placeholders with values from *variables*."""
        def replacer(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(variables.get(key, match.group(0)))

        return re.sub(r"\{\{(\w+)\}\}", replacer, template)

    # ------------------------------------------------------------------
    # Node execution (called as a LangGraph graph node)
    # ------------------------------------------------------------------

    async def execute(self, state: dict) -> dict:
        """Execute the HTTP call, substituting template vars from *state*.

        Returns a dict with the tool name → result so callers can look it up,
        plus a ``last_tool_result`` convenience key.
        """
        variables = dict(state)  # include all state fields as substitution context
        endpoint = self._substitute_vars(self.endpoint, variables)
        body = self._substitute_vars(self.body_template, variables) if self.body_template else None

        async with httpx.AsyncClient(timeout=10.0) as client:
            request_kwargs: dict[str, Any] = {"headers": self.headers}
            if body:
                try:
                    request_kwargs["json"] = json.loads(body)
                except json.JSONDecodeError:
                    request_kwargs["content"] = body.encode()

            http_fn = getattr(client, self.method.lower())
            resp = await http_fn(endpoint, **request_kwargs)
            resp.raise_for_status()

            try:
                result: Any = resp.json()
            except Exception:
                result = resp.text

        logger.debug("HttpToolNodeExecutor %s → %s %s = %s", self.name, self.method, endpoint, result)
        return {self.name: result, "last_tool_result": result}

    # ------------------------------------------------------------------
    # Tool callable factory (used by AgentNodeExecutor)
    # ------------------------------------------------------------------

    def as_tool_callable(self) -> Any:
        """Return an agentshield @tool-compatible callable for use inside an Agent.

        The returned function:
        - Has ``.risk`` and ``.tool_name`` attributes required by Agent.__post_init__
        - Has ``__signature__`` set to the extracted template variables so that
          LangChain's ``@tool`` decorator (used in build_graph) creates a proper schema
        - Makes the HTTP call when invoked, substituting kwargs as template vars
        """
        # Extract template variable names from endpoint and body_template.
        vars_in_endpoint = re.findall(r"\{\{(\w+)\}\}", self.endpoint)
        vars_in_body = re.findall(r"\{\{(\w+)\}\}", self.body_template or "")
        # Deduplicate while preserving order.
        seen: set[str] = set()
        all_vars: list[str] = []
        for v in vars_in_endpoint + vars_in_body:
            if v not in seen:
                seen.add(v)
                all_vars.append(v)

        executor = self  # capture for closure

        async def http_tool_fn(**kwargs: str) -> str:
            """Call the configured HTTP endpoint."""
            url = executor._substitute_vars(executor.endpoint, kwargs)
            body = executor._substitute_vars(executor.body_template, kwargs) if executor.body_template else None

            async with httpx.AsyncClient(timeout=10.0) as client:
                req_kwargs: dict[str, Any] = {"headers": executor.headers}
                if body:
                    try:
                        req_kwargs["json"] = json.loads(body)
                    except json.JSONDecodeError:
                        req_kwargs["content"] = body.encode()

                http_fn = getattr(client, executor.method.lower())
                resp = await http_fn(url, **req_kwargs)
                resp.raise_for_status()

                try:
                    return json.dumps(resp.json())
                except Exception:
                    return resp.text

        # Set identity metadata so Agent.__post_init__ validation passes.
        http_tool_fn.__name__ = self.name
        http_tool_fn.__doc__ = (
            f"Make a {self.method} request to {self.endpoint}. "
            "Pass the required parameters as keyword arguments."
        )
        http_tool_fn.risk = self.risk
        http_tool_fn.tool_name = self.name

        # Build a typed __signature__ so LangChain introspects the right schema.
        # inspect.signature() follows __wrapped__ chains, so wrapping via
        # functools.wraps in build_graph.py will transparently use this signature.
        if all_vars:
            params = [
                inspect.Parameter(v, inspect.Parameter.KEYWORD_ONLY, annotation=str)
                for v in all_vars
            ]
        else:
            # No template vars — accept an optional freeform string.
            params = [
                inspect.Parameter(
                    "params",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation="str | None",
                )
            ]
        http_tool_fn.__signature__ = inspect.Signature(
            params, return_annotation=str
        )
        http_tool_fn.__annotations__ = {v: str for v in all_vars}

        return http_tool_fn


# ---------------------------------------------------------------------------
# PythonToolNodeExecutor
# ---------------------------------------------------------------------------

class PythonToolNodeExecutor:
    """Calls the python-executor microservice to run sandboxed user-supplied Python code.

    The python-executor receives {code, args, timeout_ms} and returns {result, error}.
    This keeps arbitrary code execution isolated from the declarative runner process.
    """

    def __init__(self, node_config: dict, executor_url: str = "http://python-executor:8080") -> None:
        self.node_config = node_config
        self.name: str = node_config.get("name", "python_tool")
        self.python_code: str = node_config.get("python_code", "")
        self.risk: str = node_config.get("risk", "low")
        self.executor_url: str = executor_url
        self.timeout_ms: int = node_config.get("timeout_ms", 10_000)

    def as_tool_callable(self) -> Any:
        """Return an agentshield @tool-compatible callable that invokes the python-executor."""
        executor = self

        async def python_tool_fn(**kwargs: Any) -> str:
            """Call the python-executor microservice to run the tool code."""
            payload = {
                "code": executor.python_code,
                "args": kwargs,
                "timeout_ms": executor.timeout_ms,
            }
            async with httpx.AsyncClient(timeout=executor.timeout_ms / 1000.0 + 5) as client:
                resp = await client.post(f"{executor.executor_url}/execute", json=payload)
                resp.raise_for_status()
                data = resp.json()

            if data.get("error"):
                raise RuntimeError(f"python_tool error: {data['error']}")
            return data.get("result", "")

        python_tool_fn.__name__ = self.name
        python_tool_fn.__doc__ = f"Run Python tool '{self.name}'. Pass required arguments as keyword args."
        python_tool_fn.risk = self.risk
        python_tool_fn.tool_name = self.name

        # Generic signature — accepts freeform kwargs since we don't statically parse the code
        params = [
            inspect.Parameter(
                "kwargs",
                inspect.Parameter.VAR_KEYWORD,
                annotation=str,
            )
        ]
        python_tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        python_tool_fn.__annotations__ = {}

        return python_tool_fn


# ---------------------------------------------------------------------------
# AgentNodeExecutor
# ---------------------------------------------------------------------------

class AgentNodeExecutor:
    """Creates an agentshield_sdk.Agent from node config and runs it.

    HTTP tool nodes reachable from this agent node in the workflow graph are
    converted to ``@tool``-decorated callables and wired into the Agent as tools.
    The agent is invoked via ``agentshield_sdk.Runner``.
    """

    def __init__(self, node_config: dict, tool_executors: list) -> None:
        self.node_config = node_config
        self.tool_executors = tool_executors
        self._runner: Any = None  # lazily initialised on first execute() call

    async def _get_runner(self) -> Any:
        """Lazily initialise the Runner (async setup done once per executor)."""
        if self._runner is not None:
            return self._runner

        from agentshield_sdk import Agent, Runner

        tools = [ex.as_tool_callable() for ex in self.tool_executors]

        agent = Agent(
            name=self.node_config.get("name", "agent"),
            instructions=self.node_config.get(
                "instructions", "You are a helpful AI assistant."
            ),
            tools=tools,
            model=self.node_config.get("model") or None,
        )

        runner = Runner(agent)
        await runner.setup()
        self._runner = runner
        logger.info(
            "AgentNodeExecutor ready: agent=%s tools=%s",
            agent.name,
            [getattr(t, "tool_name", getattr(t, "__name__", "?")) for t in tools],
        )
        return self._runner

    async def execute(self, state: dict) -> dict:
        """Run the agent with the last human message from *state*.

        Extracts the most recent HumanMessage from state["messages"], invokes
        the inner Runner, and returns an AIMessage to append to the graph state.
        """
        from langchain_core.messages import AIMessage, HumanMessage  # type: ignore[import]
        from uuid import uuid4

        # Extract last human message.
        messages = state.get("messages", [])
        last_human: str | None = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_human = msg.content
                break

        if not last_human:
            logger.warning("AgentNodeExecutor.execute: no HumanMessage found in state")
            return {"messages": [AIMessage(content="No input message found.")]}

        runner = await self._get_runner()

        # Each agent invocation uses its own thread_id to avoid checkpoint
        # collisions with the outer workflow graph.
        inner_thread_id = str(uuid4())
        result = await runner.run(last_human, thread_id=inner_thread_id)

        response_text: str = result.get("response", "")
        logger.debug(
            "AgentNodeExecutor response (thread=%s): %s",
            inner_thread_id,
            response_text[:80],
        )
        return {"messages": [AIMessage(content=response_text)]}


# ---------------------------------------------------------------------------
# EndNodeExecutor
# ---------------------------------------------------------------------------

class EndNodeExecutor:
    """Maps state fields to output per output_mapping config.

    The ``output_mapping`` dict maps source keys (state field names or the
    special key ``"response"`` for the last AI message) to destination keys
    returned from this node.

    Example config:
        {"output_mapping": {"response": "output"}}

    This extracts the last AI message content and sets state["output"].
    """

    def __init__(self, node_config: dict) -> None:
        self.output_mapping: dict[str, str] = node_config.get("output_mapping", {})

    def execute(self, state: dict) -> dict:
        """Produce output dict by applying output_mapping to *state*."""
        from langchain_core.messages import AIMessage  # type: ignore[import]

        result: dict = {}
        messages = state.get("messages", [])

        for source_key, dest_key in self.output_mapping.items():
            if source_key in state:
                result[dest_key] = state[source_key]
            elif source_key == "response":
                # Special alias: extract last AI message content.
                last_ai_content = ""
                for msg in reversed(messages):
                    if isinstance(msg, AIMessage) and hasattr(msg, "content"):
                        last_ai_content = msg.content
                        break
                result[dest_key] = last_ai_content
            else:
                logger.debug(
                    "EndNodeExecutor: source key %r not found in state (skipping)",
                    source_key,
                )

        return result
