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
import os
import re
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)

# JSON-Schema primitive types -> Python annotations, for deriving a discovered
# MCP tool's model-facing parameters from its ``input_schema``.
_JSON_TYPE_TO_PY: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _read_sa_token(path: str) -> str:
    """Read a projected SA token from disk (fresh each call — tokens rotate)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""


def _mcp_params_from_schema(input_schema: Any) -> list[inspect.Parameter]:
    """Named keyword-only params from a discovered MCP tool's ``input_schema``.

    Falls back to a single annotated ``**kwargs`` param (so LangChain schema
    introspection never raises ``KeyError``) when no object schema is present.
    """
    props = input_schema.get("properties") if isinstance(input_schema, dict) else None
    if not isinstance(props, dict) or not props:
        return [inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD, annotation=str)]
    required = set(input_schema.get("required") or [])
    params: list[inspect.Parameter] = []
    for pname, defn in props.items():
        pytype = _JSON_TYPE_TO_PY.get((defn or {}).get("type"), str)
        if pname in required:
            params.append(
                inspect.Parameter(pname, inspect.Parameter.KEYWORD_ONLY, annotation=pytype)
            )
        else:
            # Use the schema's DECLARED default when it has one, so an omitted optional
            # carries the server's intended value rather than None. Tavily's tavily_search
            # types its optionals with real defaults (topic='general', max_results=5,
            # search_depth='basic') AND rejects None for them; forwarding None (the old
            # blanket default) fails every call. A param with no declared default keeps
            # default=None and is dropped from the wire args (see mcp_tool_fn) so the upstream
            # applies its own default.
            schema_default = (defn or {}).get("default", None)
            params.append(
                inspect.Parameter(
                    pname,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=schema_default,
                    annotation=Optional[pytype],
                )
            )
    return params


# ---------------------------------------------------------------------------
# HttpToolNodeExecutor
# ---------------------------------------------------------------------------

def _render_http_body(template: str, variables: dict) -> tuple[Any, bool]:
    """Render an HTTP tool body_template with ``{{var}}`` substitution.

    Returns ``(body, is_json)``. For a JSON template the STRUCTURE-SAFE path is
    taken: the template is parsed ONCE, then ``{{var}}`` placeholders are
    substituted inside the string *leaves* with real values — so a value that
    contains a quote, backslash or newline (e.g. an email body) can no longer
    break the surrounding JSON. Falls back to the legacy substitute-then-parse
    for non-JSON templates or placeholders outside quotes (e.g. ``{"n": {{n}}}``).

    Kept byte-identical to the SDK's ``tool_executor._render_http_body`` — the two
    services are separate images and already duplicate ``_substitute_vars``.
    """
    def _sub_str(s: str) -> str:
        return re.sub(
            r"\{\{(\w+)\}\}",
            lambda m: str(variables.get(m.group(1), m.group(0))),
            s,
        )

    try:
        parsed = json.loads(template)
    except (json.JSONDecodeError, TypeError):
        substituted = _sub_str(template)
        try:
            return json.loads(substituted), True
        except json.JSONDecodeError:
            return substituted, False

    def _walk(node: Any) -> Any:
        if isinstance(node, str):
            return _sub_str(node)
        if isinstance(node, dict):
            return {k: _walk(v) for k, v in node.items()}
        if isinstance(node, list):
            return [_walk(x) for x in node]
        return node

    return _walk(parsed), True


class HttpToolNodeExecutor:
    """Makes an httpx HTTP call with {{variable}} substitution in URL/body.

    Supports GET, POST, PUT, DELETE methods.  Substitutes ``{{variable_name}}``
    placeholders in both ``endpoint`` and ``body_template`` from the provided
    state/kwargs dict.  Timeout is fixed at 10 s.
    """

    def __init__(self, node_config: dict) -> None:
        self.node_config = node_config
        self.name: str = node_config.get("name", "http_tool")
        self.description: str | None = node_config.get("description")
        self.endpoint: str = node_config.get("endpoint", "")
        self.method: str = node_config.get("method", "GET").upper()
        self.headers: dict = node_config.get("headers", {})
        self.body_template: str = node_config.get("body_template", "")
        self.risk: str = node_config.get("risk", "low")
        # Eval v2 E-2: the registry's side-effect classification rides onto the
        # callable next to .risk/.tool_name, so the seam in graph_builder's
        # governed_tool reads `fn.side_effecting` at the delivery edge with no extra
        # lookup — the SAME contract the SDK tool_resolver builds. `.get` (not
        # `["…"]`) on purpose: absent ⇒ None ⇒ unclassifiable ⇒ the seam mocks it
        # under eval_mode=record (fail-closed). Only an explicit False (a provably
        # read-only tool) is delivered for real under record.
        self.side_effecting: bool | None = node_config.get("side_effecting")
        # The tool's declared parameter schema (JSON Schema object). When present it
        # is the AUTHORITATIVE source of the LLM-facing parameter names — see
        # _build_tool_fn — so a tool exposes structured params (order_id, amount)
        # even when its URL/body carry no {{placeholders}} (avoids the generic
        # single-`query` fallback that produced meaningless approval args).
        self.input_schema: dict | None = node_config.get("input_schema")
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

        resolved_headers = {
            k: self._substitute_vars(v, dict(os.environ)) if "{{" in str(v) else v
            for k, v in self.headers.items()
        }

        async with httpx.AsyncClient(timeout=10.0) as client:
            request_kwargs: dict[str, Any] = {"headers": resolved_headers}
            if self.body_template:
                body, is_json = _render_http_body(self.body_template, variables)
                if is_json:
                    request_kwargs["json"] = body
                else:
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

            resolved_headers = {
                k: executor._substitute_vars(v, dict(os.environ)) if "{{" in str(v) else v
                for k, v in executor.headers.items()
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                req_kwargs: dict[str, Any] = {"headers": resolved_headers}
                if executor.body_template:
                    body, is_json = _render_http_body(executor.body_template, kwargs)
                    if is_json:
                        req_kwargs["json"] = body
                    else:
                        req_kwargs["content"] = body.encode()
                elif kwargs and executor.method in ("POST", "PUT", "PATCH"):
                    # Schema-driven tool with no {{body_template}}: send the structured
                    # kwargs (order_id, amount, …) as the JSON body directly, so a tool
                    # authored with only an input_schema still POSTs its real arguments.
                    req_kwargs["json"] = dict(kwargs)

                http_fn = getattr(client, executor.method.lower())
                resp = await http_fn(url, **req_kwargs)
                resp.raise_for_status()

                try:
                    return json.dumps(resp.json())
                except Exception:
                    return resp.text

        # Set identity metadata so Agent.__post_init__ validation passes.
        http_tool_fn.__name__ = self.name
        http_tool_fn.__doc__ = self.description or (
            f"Make a {self.method} request to {self.endpoint}. "
            "Pass the required parameters as keyword arguments."
        )
        http_tool_fn.risk = self.risk
        http_tool_fn.tool_name = self.name
        http_tool_fn.side_effecting = self.side_effecting

        # Build a typed __signature__ so LangChain introspects the right schema.
        # inspect.signature() follows __wrapped__ chains, so wrapping via
        # functools.wraps in build_graph.py will transparently use this signature.
        # Parameter source, in priority order:
        #   1. input_schema.properties  — the tool's DECLARED structured params
        #   2. {{placeholders}} in the URL/body_template
        #   3. a single generic `query`  (last resort)
        # Deriving from input_schema (this class fix) is what makes the LLM — and thus
        # the HITL approval card — see real fields like order_id/amount, instead of the
        # meaningless single `query` blob a schema-carrying tool used to fall back to
        # when it had no {{placeholders}}.
        _JSON_PY = {"string": str, "number": float, "integer": int,
                    "boolean": bool, "object": dict, "array": list}
        schema_props: dict = {}
        required: set = set()
        if isinstance(self.input_schema, dict) and self.input_schema.get("type") == "object":
            schema_props = self.input_schema.get("properties") or {}
            required = set(self.input_schema.get("required") or [])

        if schema_props:
            sig_params = []
            annotations = {}
            for pname, pspec in schema_props.items():
                ann = _JSON_PY.get((pspec or {}).get("type"), str)
                if pname in required:
                    sig_params.append(inspect.Parameter(
                        pname, inspect.Parameter.KEYWORD_ONLY, annotation=ann))
                else:
                    sig_params.append(inspect.Parameter(
                        pname, inspect.Parameter.KEYWORD_ONLY, default=None, annotation=ann))
                annotations[pname] = ann
            http_tool_fn.__annotations__ = annotations
        elif all_vars:
            sig_params = [
                inspect.Parameter(v, inspect.Parameter.KEYWORD_ONLY, annotation=str)
                for v in all_vars
            ]
            http_tool_fn.__annotations__ = {v: str for v in all_vars}
        else:
            sig_params = [
                inspect.Parameter(
                    "query",
                    inspect.Parameter.KEYWORD_ONLY,
                    default="",
                    annotation=str,
                )
            ]
            http_tool_fn.__annotations__ = {"query": str}
        http_tool_fn.__signature__ = inspect.Signature(
            sig_params, return_annotation=str
        )

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
        self.description: str | None = node_config.get("description")
        self.python_code: str = node_config.get("python_code", "")
        self.risk: str = node_config.get("risk", "low")
        # Eval v2 E-2 — see HttpToolNodeExecutor: absent ⇒ None ⇒ fail-closed (mocked
        # under record). Same contract as the SDK tool_resolver stamps.
        self.side_effecting: bool | None = node_config.get("side_effecting")
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
        python_tool_fn.__doc__ = self.description or f"Run Python tool '{self.name}'. Pass required arguments as keyword args."
        python_tool_fn.risk = self.risk
        python_tool_fn.tool_name = self.name
        python_tool_fn.side_effecting = self.side_effecting

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
# McpToolNodeExecutor
# ---------------------------------------------------------------------------


class McpToolNodeExecutor:
    """Calls the platform MCP proxy to invoke a tool on an upstream MCP server.

    A SEPARATE implementation from the SDK's ``McpToolExecutor`` (the declarative
    runner and SDK agents are distinct runtimes) that speaks the SAME proxy wire
    contract and carries the SAME projected SA token. The proxy is the only
    egress hop — the runner never talks to the upstream MCP server directly. OPA
    authorization + Decision-27 de-anonymization are applied by the governance
    seam before this callable runs.
    """

    def __init__(self, node_config: dict, proxy_url: str, token_path: str) -> None:
        self.node_config = node_config
        self.name: str = node_config.get("name", "mcp_tool")
        self.description: str | None = node_config.get("description")
        self.risk: str = node_config.get("risk", "low")
        # Eval v2 E-2 — see HttpToolNodeExecutor: absent ⇒ None ⇒ fail-closed.
        self.side_effecting: bool | None = node_config.get("side_effecting")
        self.server_id: str = str(node_config.get("mcp_server_id") or "")
        # RAW upstream name the proxy calls tools/call with — NOT the namespaced
        # Tool.name the model sees.
        self.mcp_tool_name: str = node_config.get("mcp_tool_name") or self.name
        # Decision 27 — whether the governance seam output-scans the result.
        self.scan_results: bool = bool(node_config.get("scan_results", True))
        self.input_schema = node_config.get("input_schema")
        self.timeout_ms: int = node_config.get("timeout_ms", 30_000)
        self.proxy_url = proxy_url
        self.token_path = token_path

    def as_tool_callable(self) -> Any:
        """Return an agentshield @tool-compatible callable that calls the MCP proxy."""
        executor = self

        async def mcp_tool_fn(**kwargs: Any) -> str:
            """Call an upstream MCP tool through the platform MCP proxy."""
            token = _read_sa_token(executor.token_path)
            # Drop optional params the LLM omitted that have no schema default (LangChain
            # materializes them as None). Sending None to a server that types its optionals
            # makes it reject the call; an omitted optional must be ABSENT so the upstream
            # applies its own default. Params WITH a schema default already carry that default
            # (see _mcp_params_from_schema), so they are non-None and survive.
            mcp_arguments = {k: v for k, v in kwargs.items() if v is not None}
            payload = {
                "server_id": executor.server_id,
                "mcp_tool_name": executor.mcp_tool_name,
                "arguments": mcp_arguments,
                # session_id/agent_name are best-effort trace correlation only.
                "session_id": "",
                "agent_name": os.getenv("AGENT_NAME", "declarative-agent"),
            }
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            # WS-2 (C9): forward the PER-REQUEST acting user so the proxy pulls THAT user's
            # stored OAuth token (an external OAuth server needs the user driving THIS run,
            # not the pod's static identity). Read the request-scoped ContextVar that
            # _bind_user_context sets from the x-user-sub header (the same one governed_tool
            # uses for OPA); fall back to the static pod env AGENTSHIELD_USER_SUB only when it
            # is unset (a daemon/scheduled run with no per-request user). Sent ONLY when
            # non-empty — an empty user emits a byte-identical Phase-1 request.
            from config import USER_SUB
            acting_user = ""
            try:
                from agentshield_sdk.graph_builder import _current_user_context
                acting_user = (_current_user_context.get() or {}).get("user_id", "") or ""
            except Exception:  # noqa: BLE001 — no request context / SDK shape drift
                acting_user = ""
            acting_user = acting_user or USER_SUB
            if acting_user:
                headers["x-user-sub"] = acting_user
            timeout = executor.timeout_ms / 1000.0 + 5
            # FR-MCP-14: never raise out of a tool call — surface every failure as
            # a string. The proxy returns 200 + is_error for tool/transport
            # outcomes; 401/403/422 are real auth/body failures.
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{executor.proxy_url}/internal/tools/call",
                        json=payload,
                        headers=headers,
                    )
                if resp.status_code != 200:
                    return (
                        f"MCP tool '{executor.mcp_tool_name}' error: proxy returned "
                        f"HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                data = resp.json()
            except Exception as exc:  # network / timeout / JSON decode
                return f"MCP tool '{executor.mcp_tool_name}' error: {exc}"

            if data.get("is_error") or data.get("error"):
                return (
                    data.get("error")
                    or data.get("result")
                    or f"MCP tool '{executor.mcp_tool_name}' returned an error"
                )
            return data.get("result") or ""

        mcp_tool_fn.__name__ = self.name
        mcp_tool_fn.__doc__ = self.description or (
            f"Call the MCP tool '{self.mcp_tool_name}'. "
            "Pass required arguments as keyword args."
        )
        mcp_tool_fn.risk = self.risk
        mcp_tool_fn.tool_name = self.name
        mcp_tool_fn.side_effecting = self.side_effecting
        mcp_tool_fn.invocation_target = f"mcp-proxy:{self.server_id}/{self.mcp_tool_name}"
        # Decision 27 — read by the governance seam's output-scan gate.
        mcp_tool_fn.scan_results = self.scan_results

        params = _mcp_params_from_schema(self.input_schema)
        mcp_tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        mcp_tool_fn.__annotations__ = {
            p.name: p.annotation
            for p in params
            if p.annotation is not inspect.Parameter.empty
        }
        mcp_tool_fn.__annotations__["return"] = str

        return mcp_tool_fn


# ---------------------------------------------------------------------------
# AgentNodeExecutor
# ---------------------------------------------------------------------------

class AgentNodeExecutor:
    """Builds a governed LangGraph ReAct subgraph from node config.

    HTTP/Python tool nodes reachable from this agent node in the workflow graph
    are converted to ``@tool``-decorated callables, wrapped with OPA governance
    + HITL via the SDK's ``build_graph()``, and compiled into a subgraph.

    The subgraph is added directly as a node in the parent StateGraph (not
    wrapped in a function). This is critical for HITL: when ``interrupt()``
    fires inside the subgraph, LangGraph propagates the interrupt event to
    the parent graph's ``astream_events()`` stream. A nested Runner with its
    own checkpointer and ``ainvoke()`` would swallow the interrupt.
    """

    def __init__(self, node_config: dict, tool_executors: list) -> None:
        self.node_config = node_config
        self.tool_executors = tool_executors

    def build_subgraph(self) -> Any:
        """Build and return a governed ReAct subgraph (no checkpointer).

        The parent graph's checkpointer handles state persistence for all
        nodes including this subgraph. Passing checkpointer=None here ensures
        interrupt() propagates to the parent rather than being captured in a
        separate checkpoint namespace.
        """
        from agentshield_sdk import Agent
        from agentshield_sdk.graph_builder import build_graph

        tools = [ex.as_tool_callable() for ex in self.tool_executors]

        agent = Agent(
            name=self.node_config.get("name", "agent"),
            instructions=self.node_config.get(
                "instructions", "You are a helpful AI assistant."
            ),
            tools=tools,
            model=self.node_config.get("model") or None,
        )

        graph = build_graph(agent, checkpointer=None, resolved_tools=tools)
        logger.info(
            "AgentNodeExecutor subgraph built: agent=%s tools=%s",
            agent.name,
            [getattr(t, "tool_name", getattr(t, "__name__", "?")) for t in tools],
        )
        return graph


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
