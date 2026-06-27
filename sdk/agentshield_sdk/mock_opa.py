"""
Mock OPA client — used when AGENTSHIELD_OPA_URL is not explicitly set
(i.e., in local dev mode).

Every tool call is allowed with no approval required.  This lets developers
iterate without a running OPA sidecar.
"""
from __future__ import annotations


async def check_tool(agent_name: str, tool_name: str, args: dict) -> dict:
    """Mock OPA decision — always allows, never requires approval.

    Returns:
        dict with ``allow=True``, ``require_approval=False``, ``reason="mock"``.
    """
    return {"allow": True, "require_approval": False, "reason": "mock"}
