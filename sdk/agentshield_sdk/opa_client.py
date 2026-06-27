"""
OPA client — calls the OPA sidecar to get a per-tool policy decision.

Policy path convention (matches Rego package written by the deploy controller):
    /v1/data/agentshield/agent/{agent_name}

Input schema:
    {"input": {"tool": "<tool_name>", "args": {...}}}

Expected result schema:
    {"result": {"allow": bool, "require_approval": bool, "reason": str}}

Fallback behaviour:
- DEV_MODE (AGENTSHIELD_OPA_URL not explicitly set): uses mock_opa.
- PROD_MODE (OPA URL set but OPA unreachable): FAIL CLOSED — returns
  OPADecision(allow=False, require_approval=False, reason="opa_unreachable").
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx

from . import config, mock_opa


@dataclass
class OPADecision:
    allow: bool
    require_approval: bool
    reason: str


async def check_tool(
    agent_name: str, tool_name: str, args: dict
) -> OPADecision:
    """Return an OPA policy decision for a tool invocation.

    Args:
        agent_name: The agent's registered name (used to look up the policy path).
        tool_name:  The tool being invoked.
        args:       The tool's input arguments (used for attribute-based checks).

    Returns:
        OPADecision with allow/require_approval/reason fields.
    """
    # In dev mode (OPA URL not explicitly configured) use the mock.
    if config.DEV_MODE:
        raw = await mock_opa.check_tool(agent_name, tool_name, args)
        return OPADecision(**raw)

    url = (
        f"{config.AGENTSHIELD_OPA_URL}/v1/data/agentshield/agent/{agent_name}"
    )
    payload = {"input": {"tool": tool_name, "args": args}}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        # Fail closed: if OPA is unreachable in prod, deny the tool call.
        return OPADecision(
            allow=False,
            require_approval=False,
            reason="opa_unreachable",
        )

    result = data.get("result", {})
    return OPADecision(
        allow=bool(result.get("allow", False)),
        require_approval=bool(result.get("require_approval", False)),
        reason=str(result.get("reason", "policy_decision")),
    )
