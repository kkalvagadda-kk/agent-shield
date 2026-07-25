"""
Tool executors — HTTP and Python executors for platform-managed tools.

Each executor produces a callable tagged with .risk and .tool_name that is
compatible with the SDK's governance wrapping in graph_builder.py.
"""
from __future__ import annotations

import inspect
import json
import logging
import os
import re
from typing import Any, Optional

import httpx

from . import config

logger = logging.getLogger(__name__)

PYTHON_EXECUTOR_URL: str = os.getenv(
    "AGENTSHIELD_PYTHON_EXECUTOR_URL", "http://python-executor:8080"
)


def _read_sa_token(path: str) -> str:
    """Read a projected SA token from disk (fresh each call — tokens rotate)."""
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()
    except OSError:
        return ""

# JSON-Schema primitive types -> Python annotations, for deriving a tool's
# model-facing parameters from its registered ``input_schema``.
_JSON_TYPE_TO_PY: dict[str, Any] = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "object": dict,
    "array": list,
}


def _params_from_input_schema(
    input_schema: Any,
) -> tuple[list[inspect.Parameter], dict[str, Any]] | None:
    """Build named keyword-only parameters from a JSON-Schema ``input_schema``.

    Returns ``(params, annotations)`` when the schema declares an object with
    ``properties``; ``None`` when it is absent/empty so the caller can fall back
    to a permissive signature. Required properties become mandatory params;
    optional ones get ``default=None`` and an ``Optional[...]`` annotation.
    """
    if not isinstance(input_schema, dict):
        return None
    props = input_schema.get("properties")
    if not isinstance(props, dict) or not props:
        return None
    required = set(input_schema.get("required") or [])
    params: list[inspect.Parameter] = []
    for name, defn in props.items():
        pytype = _JSON_TYPE_TO_PY.get((defn or {}).get("type"), str)
        if name in required:
            params.append(
                inspect.Parameter(name, inspect.Parameter.KEYWORD_ONLY, annotation=pytype)
            )
        else:
            params.append(
                inspect.Parameter(
                    name,
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Optional[pytype],
                )
            )
    return params, _annotations_from_params(params)


def _annotations_from_params(params: list[inspect.Parameter]) -> dict[str, Any]:
    """Derive ``__annotations__`` from a parameter list so the two can never drift.

    LangChain 1.x introspection (``create_schema_from_function`` -> pydantic
    ``validate_arguments``) does ``type_hints[name]`` for EVERY parameter and
    raises ``KeyError`` if any parameter — including ``**kwargs`` or a ``params``
    catch-all — has no annotation. Building annotations straight from the params
    makes that inconsistency structurally impossible.
    """
    return {
        p.name: p.annotation
        for p in params
        if p.annotation is not inspect.Parameter.empty
    }


class HttpToolExecutor:
    """Executes an HTTP tool by calling its registered endpoint."""

    def __init__(
        self,
        name: str,
        risk: str,
        method: str,
        url: str,
        headers: dict,
        body_template: str,
        description: str | None = None,
        timeout_ms: int = 10_000,
        side_effecting: bool | None = None,
    ) -> None:
        self.name = name
        self.risk = risk
        self.method = method.upper()
        self.url = url
        self.headers = headers
        self.body_template = body_template
        self.description = description
        self.timeout_ms = timeout_ms
        # Eval v2 E-2 — the registry's classification (None = unclassifiable, which
        # the delivery seam treats as side-effecting: mocked, never invoked).
        self.side_effecting = side_effecting

    @staticmethod
    def _substitute_vars(template: str, variables: dict) -> str:
        """Replace {{name}} placeholders with values from variables."""
        def replacer(match: re.Match) -> str:
            key = match.group(1).strip()
            return str(variables.get(key, match.group(0)))
        return re.sub(r"\{\{(\w+)\}\}", replacer, template)

    def as_tool_callable(self) -> Any:
        """Return an async callable compatible with Agent/graph_builder."""
        vars_in_url = re.findall(r"\{\{(\w+)\}\}", self.url)
        vars_in_body = re.findall(r"\{\{(\w+)\}\}", self.body_template or "")
        seen: set[str] = set()
        all_vars: list[str] = []
        for v in vars_in_url + vars_in_body:
            if v not in seen:
                seen.add(v)
                all_vars.append(v)

        executor = self

        async def http_tool_fn(**kwargs: str) -> str:
            """Call the platform-registered HTTP tool endpoint."""
            url = executor._substitute_vars(executor.url, kwargs)
            body = (
                executor._substitute_vars(executor.body_template, kwargs)
                if executor.body_template
                else None
            )

            resolved_headers = {
                k: executor._substitute_vars(v, dict(os.environ)) if "{{" in str(v) else v
                for k, v in executor.headers.items()
            }

            timeout = executor.timeout_ms / 1000.0
            async with httpx.AsyncClient(timeout=timeout) as client:
                req_kwargs: dict[str, Any] = {"headers": resolved_headers}
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

        http_tool_fn.__name__ = self.name
        http_tool_fn.__doc__ = self.description or (
            f"Make a {self.method} request to {self.url}. "
            "Pass required parameters as keyword arguments."
        )
        http_tool_fn.risk = self.risk
        http_tool_fn.tool_name = self.name
        # Eval v2 E-2 — read by `graph_builder.governed_tool` at the delivery edge.
        # `invocation_target` is the downstream the record entry reports as
        # `would_have_invoked` (the request that was NOT sent).
        http_tool_fn.side_effecting = self.side_effecting
        http_tool_fn.invocation_target = f"{self.method} {self.url}"

        if all_vars:
            params = [
                inspect.Parameter(v, inspect.Parameter.KEYWORD_ONLY, annotation=str)
                for v in all_vars
            ]
        else:
            params = [
                inspect.Parameter(
                    "params",
                    inspect.Parameter.KEYWORD_ONLY,
                    default=None,
                    annotation=Optional[str],
                )
            ]
        http_tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        # Annotations MUST cover every signature param (incl. the no-vars `params`
        # catch-all) or LangChain schema introspection raises KeyError.
        http_tool_fn.__annotations__ = {
            **_annotations_from_params(params),
            "return": str,
        }

        return http_tool_fn


class PythonToolExecutor:
    """Executes a Python tool via the python-executor microservice."""

    def __init__(
        self,
        name: str,
        risk: str,
        python_code: str,
        description: str | None = None,
        timeout_ms: int = 10_000,
        input_schema: dict | None = None,
        side_effecting: bool | None = None,
    ) -> None:
        self.name = name
        self.risk = risk
        self.python_code = python_code
        self.description = description
        self.timeout_ms = timeout_ms
        self.input_schema = input_schema
        # Eval v2 E-2 — see HttpToolExecutor. Python tool code is opaque to the
        # platform, so the registry classifies it side-effecting by default.
        self.side_effecting = side_effecting

    def as_tool_callable(self) -> Any:
        """Return an async callable that invokes the python-executor."""
        executor = self

        async def python_tool_fn(**kwargs: Any) -> str:
            """Call the python-executor microservice to run sandboxed tool code."""
            payload = {
                "code": executor.python_code,
                "args": kwargs,
                "timeout_ms": executor.timeout_ms,
            }
            timeout = executor.timeout_ms / 1000.0 + 5
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(
                    f"{PYTHON_EXECUTOR_URL}/execute", json=payload
                )
                resp.raise_for_status()
                data = resp.json()

            if data.get("error"):
                raise RuntimeError(f"Python tool '{executor.name}' error: {data['error']}")
            return data.get("result", "")

        python_tool_fn.__name__ = self.name
        python_tool_fn.__doc__ = self.description or (
            f"Run Python tool '{self.name}'. Pass required arguments as keyword args."
        )
        python_tool_fn.risk = self.risk
        python_tool_fn.tool_name = self.name
        # Eval v2 E-2 — read by `graph_builder.governed_tool` at the delivery edge.
        python_tool_fn.side_effecting = self.side_effecting
        python_tool_fn.invocation_target = f"python-executor:{self.name}"

        # Prefer named parameters derived from the tool's registered input_schema
        # (gives the model a real, typed arg schema — the same treatment HTTP tools
        # get from their {{template}} variables). Fall back to an arbitrary-kwargs
        # signature when no input_schema is declared; the annotation on `kwargs` is
        # what keeps LangChain introspection from raising KeyError('kwargs').
        derived = _params_from_input_schema(self.input_schema)
        if derived is not None:
            params, annotations = derived
        else:
            params = [
                inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD, annotation=str)
            ]
            annotations = _annotations_from_params(params)

        python_tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        python_tool_fn.__annotations__ = {**annotations, "return": str}

        return python_tool_fn


class McpToolExecutor:
    """Executes an MCP tool by calling the platform MCP proxy.

    The proxy is the ONLY egress hop to an upstream MCP server — the agent pod
    never speaks to the external server directly. Governance (OPA authorization +
    Decision-27 de-anonymization) is applied by ``graph_builder.governed_tool``
    BEFORE this callable runs, so ``arguments`` here are already authorized and
    de-anonymized. Mirrors ``HttpToolExecutor``/``PythonToolExecutor`` so the
    governance seam treats all three identically.
    """

    def __init__(
        self,
        name: str,
        risk: str,
        server_id: str,
        mcp_tool_name: str,
        description: str | None = None,
        input_schema: dict | None = None,
        timeout_ms: int = 30_000,
        side_effecting: bool | None = None,
        scan_results: bool = True,
    ) -> None:
        self.name = name                    # namespaced Tool.name ({server}__{tool})
        self.risk = risk
        self.server_id = server_id          # Tool.mcp_server_id — the proxy route target
        self.mcp_tool_name = mcp_tool_name  # RAW upstream name (Tool.mcp_tool_name)
        self.description = description
        self.input_schema = input_schema
        self.timeout_ms = timeout_ms
        self.side_effecting = side_effecting
        # Whether governed_tool should output-scan this tool's result (Decision 27).
        # True for every external server; for internal servers it follows the
        # server's scan_results flag. Computed by the resolver, read at the gate.
        self.scan_results = scan_results

    def as_tool_callable(self) -> Any:
        """Return an async callable compatible with Agent/graph_builder."""
        executor = self

        async def mcp_tool_fn(**kwargs: Any) -> str:
            """Call an upstream MCP tool through the platform MCP proxy."""
            # DEV_MODE: no proxy/token in local dev — return a deterministic mock
            # so agent graphs run without cluster infra (mirrors mock_opa/mock_safety).
            if config.DEV_MODE:
                return (
                    f"[dev-mode mock] mcp_tool {executor.mcp_tool_name} "
                    f"(server {executor.server_id}) called with {kwargs}"
                )

            token = _read_sa_token(config.AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH)
            payload = {
                "server_id": executor.server_id,
                "mcp_tool_name": executor.mcp_tool_name,
                "arguments": kwargs,
                # session_id/agent_name are best-effort trace correlation only.
                "session_id": config.AGENT_ID or "",
                "agent_name": config.AGENT_NAME,
            }
            headers = {"Authorization": f"Bearer {token}"} if token else {}
            # WS-C (FR-MCP-21): forward the acting user's identity so the proxy can
            # route on_behalf_of servers. Sent ONLY when non-empty — a daemon agent
            # (no AGENTSHIELD_USER_SUB) emits a byte-identical Phase-1 request.
            if config.USER_SUB:
                headers["x-user-sub"] = config.USER_SUB
            timeout = executor.timeout_ms / 1000.0 + 5
            # FR-MCP-14: a tool call must NEVER raise out of the executor — every
            # failure (auth, transport, protocol, upstream tool error) is surfaced
            # as a string the agent can read, exactly like a tool that returned an
            # error message. The proxy already returns 200 + is_error for tool /
            # transport outcomes; 401/403/422 are real auth/body failures.
            try:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(
                        f"{config.AGENTSHIELD_MCP_PROXY_URL}/internal/tools/call",
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
            "Pass required arguments as keyword arguments."
        )
        mcp_tool_fn.risk = self.risk
        mcp_tool_fn.tool_name = self.name
        # Eval v2 E-2 — read by `graph_builder.governed_tool` at the delivery edge.
        mcp_tool_fn.side_effecting = self.side_effecting
        mcp_tool_fn.invocation_target = f"mcp-proxy:{self.server_id}/{self.mcp_tool_name}"
        # Decision 27 — read by governed_tool's output-scan gate (P11).
        mcp_tool_fn.scan_results = self.scan_results

        # Prefer named params derived from the tool's registered input_schema (the
        # discovered MCP tool schema), else a permissive **kwargs signature. The
        # annotation on `kwargs` is what keeps LangChain introspection from raising
        # KeyError('kwargs').
        derived = _params_from_input_schema(self.input_schema)
        if derived is not None:
            params, annotations = derived
        else:
            params = [
                inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD, annotation=str)
            ]
            annotations = _annotations_from_params(params)

        mcp_tool_fn.__signature__ = inspect.Signature(params, return_annotation=str)
        mcp_tool_fn.__annotations__ = {**annotations, "return": str}

        return mcp_tool_fn
