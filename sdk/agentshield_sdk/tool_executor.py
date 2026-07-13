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

logger = logging.getLogger(__name__)

PYTHON_EXECUTOR_URL: str = os.getenv(
    "AGENTSHIELD_PYTHON_EXECUTOR_URL", "http://python-executor:8080"
)

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
    ) -> None:
        self.name = name
        self.risk = risk
        self.method = method.upper()
        self.url = url
        self.headers = headers
        self.body_template = body_template
        self.description = description
        self.timeout_ms = timeout_ms

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
    ) -> None:
        self.name = name
        self.risk = risk
        self.python_code = python_code
        self.description = description
        self.timeout_ms = timeout_ms
        self.input_schema = input_schema

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
