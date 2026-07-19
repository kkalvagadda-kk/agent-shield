"""
Tool resolver — fetches tool definitions from the Registry API at startup.

Resolves tool names (strings) into executable tool objects that the graph
builder can wrap with governance and bind to the LangGraph agent.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from . import config
from .tool_executor import HttpToolExecutor, PythonToolExecutor

logger = logging.getLogger(__name__)


async def resolve_tools(tool_names: list[str]) -> list[Any]:
    """Resolve a list of platform tool names into executable tool callables.

    Fetches each tool's full definition from the Registry API and returns
    a list of callables tagged with .risk and .tool_name (compatible with
    Agent.__post_init__ validation and graph_builder governance wrapping).

    Raises:
        RuntimeError: If a tool name cannot be found in the registry.
    """
    if not tool_names:
        return []

    resolved: list[Any] = []
    async with httpx.AsyncClient(
        base_url=config.AGENTSHIELD_REGISTRY_URL,
        timeout=15.0,
    ) as client:
        for name in tool_names:
            resp = await client.get(
                "/api/v1/tools/",
                params={"name": name, "limit": 1},
            )
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
            if not items:
                raise RuntimeError(
                    f"Tool '{name}' not found in the platform registry. "
                    f"Register it at {config.AGENTSHIELD_REGISTRY_URL}/api/v1/tools/ first."
                )
            tool_def = items[0]
            # FAIL-CLOSED: never bind a tool the platform didn't ask for. The `name`
            # filter above was silently ignored by the API until it was added as a real
            # query param — FastAPI drops unknown params, so this call returned the
            # FIRST tool in the registry and `items[0]` bound it under the requested
            # name (observed: asking for 'http_echo' resolved a critical-risk OPA
            # fixture). A wrong tool is a governance breach, not a glitch: the agent
            # would call it under the risk/approval policy of the name it asked for.
            # Verify rather than trust, so a filter regression can never re-open this.
            if tool_def.get("name") != name:
                raise RuntimeError(
                    f"Tool resolution mismatch: asked the registry for '{name}' but it "
                    f"returned '{tool_def.get('name')}'. Refusing to bind the wrong tool."
                )
            callable_ = _build_executor(tool_def)
            resolved.append(callable_)
            logger.info(
                "Resolved tool '%s' (type=%s, risk=%s, side_effecting=%s)",
                name, tool_def.get("type"), tool_def.get("risk_level", "low"),
                getattr(callable_, "side_effecting", None),
            )

    return resolved


def _build_executor(tool_def: dict) -> Any:
    """Build an executable tool callable from a registry tool definition."""
    tool_type = tool_def.get("type", "http")
    name = tool_def["name"]
    risk = tool_def.get("risk_level", "low")
    # Eval v2 E-2: the registry's classification rides onto the callable next to
    # .risk/.tool_name, so `governed_tool` reads `fn.side_effecting` at the delivery
    # edge with no extra lookup. `.get` (not `["…"]`) on purpose: a registry too old
    # to serve the field yields None = unclassifiable, which the seam treats as
    # side-effecting (mocked, never invoked) under record — fail-closed.
    side_effecting = tool_def.get("side_effecting")

    if tool_type == "python":
        executor = PythonToolExecutor(
            name=name,
            risk=risk,
            python_code=tool_def.get("python_code", ""),
            description=tool_def.get("description"),
            timeout_ms=tool_def.get("timeout_ms", 10_000),
            input_schema=tool_def.get("input_schema"),
            side_effecting=side_effecting,
        )
    else:
        executor = HttpToolExecutor(
            name=name,
            risk=risk,
            method=tool_def.get("http_method", "GET"),
            url=tool_def.get("http_url", ""),
            headers=tool_def.get("http_headers") or {},
            body_template=tool_def.get("http_body_template") or "",
            description=tool_def.get("description"),
            timeout_ms=tool_def.get("http_timeout_ms", 10_000),
            side_effecting=side_effecting,
        )

    return executor.as_tool_callable()
