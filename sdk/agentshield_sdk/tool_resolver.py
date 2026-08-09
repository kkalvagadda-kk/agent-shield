"""
Tool resolver — fetches this agent's BOUND tool definitions at startup.

Resolves tool names (strings) into executable tool objects that the graph builder can wrap
with governance and bind to the LangGraph agent.

IT USED TO ASK THE WRONG QUESTION
---------------------------------
This module called `GET /api/v1/tools/?name=X` once per tool — the GLOBAL CATALOG, by name,
with no credential. That is a discovery question ("what tools exist called X"), and it forced
the registry to answer it with a visibility filter, which is a guess about intent: published?
own team? creator? Every answer was wrong for somebody, and the anonymous arm ended up
applying no filter at all so that pods would not die at startup — meaning any workload in the
cluster could enumerate every team's private tools.

It now calls `GET /api/v1/agents/{AGENT_NAME}/tools` — the BINDING question. That endpoint
already existed, returns the same `ToolResponse` rows joined on `agent_tools`, and carries no
publish filter, because a pod's authority over a tool has always been its binding and never
the catalog flag. It is also exactly the set OPA Gate 3 authorizes, so the registry and the
policy engine now agree by construction rather than by coincidence.

AND IT NOW PROVES WHO IT IS
---------------------------
The request carries the pod's projected Kubernetes ServiceAccount token
(audience `agentshield-registry-api`). registry-api verifies it via TokenReview and refuses
any request whose path names a different agent. Without that, `GET /agents/{name}/tools`
would let any pod read any agent's bindings by editing the path — and a self-asserted
`X-Agent-Name` header would be the same forgeable-attribution defect this repo has now
deleted three times.
"""
from __future__ import annotations

import logging
import pathlib
from typing import Any

import httpx

from . import config
from .tool_executor import HttpToolExecutor, McpToolExecutor, PythonToolExecutor

logger = logging.getLogger(__name__)

# Mounted by deploy-controller/manifest_builder.py as a projected bound token.
SA_TOKEN_PATH = "/var/run/secrets/agentshield/registry-token/token"


def _auth_headers() -> dict[str, str]:
    """The pod's ServiceAccount bearer, or {} if the projection is absent.

    Absent is survivable and must stay that way during the rollout: a pod built from a
    manifest that predates the third token projection still starts, and simply gets a 401
    that names the missing credential — which is a far better failure than a silent one.
    """
    try:
        token = pathlib.Path(SA_TOKEN_PATH).read_text().strip()
    except OSError:
        logger.warning(
            "No ServiceAccount token at %s — the registry will refuse this request. "
            "The pod manifest predates the agentshield-registry-api token projection.",
            SA_TOKEN_PATH,
        )
        return {}
    return {"Authorization": f"Bearer {token}"}


async def resolve_tools(tool_names: list[str]) -> list[Any]:
    """Resolve the agent's platform tool names into executable callables.

    Fetches this agent's BOUND tools in ONE request and matches the requested names against
    them. `tool_names` comes from the Agent definition in the pod's own code; the binding set
    comes from the registry. They should agree — and when they do not, that disagreement is
    the thing worth failing on, so it is checked rather than papered over.

    Raises:
        RuntimeError: if a requested tool is not bound to this agent in the registry.
    """
    if not tool_names:
        return []

    async with httpx.AsyncClient(
        base_url=config.AGENTSHIELD_REGISTRY_URL,
        timeout=15.0,
        headers=_auth_headers(),
    ) as client:
        resp = await client.get(
            f"/api/v1/agents/{config.AGENT_NAME}/tools",
            params={"limit": 200},
        )
        if resp.status_code == 401:
            raise RuntimeError(
                f"Registry refused this pod's ServiceAccount token when resolving tools for "
                f"agent '{config.AGENT_NAME}'. Expected a projected token at {SA_TOKEN_PATH} "
                f"with audience 'agentshield-registry-api'."
            )
        resp.raise_for_status()
        bound = {t["name"]: t for t in resp.json().get("items", [])}

    resolved: list[Any] = []
    for name in tool_names:
        tool_def = bound.get(name)
        if tool_def is None:
            # FAIL-CLOSED: never bind a tool the platform did not say this agent has.
            #
            # The old code fetched by name from the global catalog and had to verify the
            # registry returned the tool it asked for, because the `name` filter was once
            # silently ignored and `items[0]` bound the FIRST tool in the registry under the
            # requested name (observed: asking for 'http_echo' resolved a critical-risk OPA
            # fixture). Binding by the agent's own set removes that class entirely — there is
            # no arbitrary row to return — but the failure still has to be loud, because a
            # name in the code with no binding in the registry means the two disagree about
            # what this agent is allowed to do.
            raise RuntimeError(
                f"Tool '{name}' is not bound to agent '{config.AGENT_NAME}' in the platform "
                f"registry. Bound tools: {sorted(bound) or 'none'}. Bind it to the agent "
                f"(POST /api/v1/agents/{config.AGENT_NAME}/tools) — the pod does not choose "
                f"its own tool set."
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
    elif tool_type == "mcp_tool":
        # An external server ALWAYS output-scans (its content is untrusted); an
        # internal server follows its own scan_results flag. This is the single
        # place the two are reconciled — external overrides the flag to True.
        is_external = bool(tool_def.get("mcp_server_is_external"))
        scan_results = True if is_external else bool(tool_def.get("mcp_server_scan_results"))
        executor = McpToolExecutor(
            name=name,
            risk=risk,
            server_id=str(tool_def.get("mcp_server_id") or ""),
            # RAW upstream name the proxy calls tools/call with — NOT the
            # namespaced Tool.name the model sees.
            mcp_tool_name=tool_def.get("mcp_tool_name") or name,
            description=tool_def.get("description"),
            input_schema=tool_def.get("input_schema"),
            timeout_ms=tool_def.get("timeout_ms", 30_000),
            side_effecting=side_effecting,
            scan_results=scan_results,
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
