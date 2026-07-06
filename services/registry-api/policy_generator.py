"""
OPA policy generator.

Takes an AgentVersion's tools list, produces Rego policy text, persists it to
agent_policies, and writes (or updates) the Kubernetes ConfigMap that the OPA
sidecar loads at deploy time.

Risk levels → OPA actions:
  low    → allow
  medium → log (allow but emit audit event)
  high   → require_approval (HITL gate via approvals API)
  critical → deny
"""

import logging
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import AgentPolicy, AgentVersion

logger = logging.getLogger(__name__)

_RISK_ACTION: dict[str, str] = {
    "low": "allow",
    "medium": "log",
    "high": "require_approval",
    "critical": "deny",
}


def _render_rego(agent_name: str, tools: list[dict[str, Any]]) -> str:
    package_name = agent_name.replace("-", "_")
    lines = [
        f"package agentshield.agent.{package_name}",
        "",
        "import future.keywords.if",
        "import future.keywords.in",
        "",
        "# Default deny — fail-closed",
        "default allow = false",
        "default action = \"deny\"",
        "",
    ]

    for tool in tools:
        name = tool.get("name", "")
        risk = tool.get("risk", "low")
        action = _RISK_ACTION.get(risk, "deny")
        safe_name = name.replace("-", "_")
        lines += [
            f"# {name} (risk={risk})",
            f'allow if {{ input.tool_name == "{name}"; action == "allow" }}',
            f'action = "{action}" if {{ input.tool_name == "{name}" }}',
            "",
        ]

    # Fallback: allow low-risk tools not explicitly listed
    lines += [
        "# Unlisted tools fall through to default deny",
        'allow if { action == "log" }',
    ]

    return "\n".join(lines)


def _build_risk_map(tools: list[dict[str, Any]]) -> dict[str, str]:
    return {t["name"]: t.get("risk", "low") for t in tools if "name" in t}


def _build_tool_allowlist(tools: list[dict[str, Any]]) -> list[str]:
    return [t["name"] for t in tools if t.get("risk", "low") in ("low", "medium")]


async def generate_and_store(
    session: AsyncSession,
    agent_id: uuid.UUID,
    agent_name: str,
    version: AgentVersion,
    namespace: str = "agents-platform",
) -> AgentPolicy:
    tools: list[dict[str, Any]] = version.tools or []
    rego = _render_rego(agent_name, tools)
    risk_map = _build_risk_map(tools)
    allowlist = _build_tool_allowlist(tools)
    configmap_name = f"{agent_name}-policy"

    # Upsert agent_policies row (unique on agent_id)
    stmt = (
        pg_insert(AgentPolicy)
        .values(
            id=uuid.uuid4(),
            agent_id=agent_id,
            rego_policy=rego,
            tool_allowlist=allowlist,
            risk_map=risk_map,
            configmap_name=configmap_name,
            generated_from_version_id=version.id,
        )
        .on_conflict_do_update(
            index_elements=["agent_id"],
            set_={
                "rego_policy": rego,
                "tool_allowlist": allowlist,
                "risk_map": risk_map,
                "configmap_name": configmap_name,
                "generated_from_version_id": version.id,
                "version": AgentPolicy.version + 1,
            },
        )
        .returning(AgentPolicy)
    )
    result = await session.execute(stmt)
    policy = result.scalar_one()

    # Phase 9.1 completion: per-agent ConfigMap is retired — OPA sidecars now poll
    # the unified bundle from the central bundle server. The DB write above is kept
    # for audit (risk_map, tool_allowlist) but the ConfigMap write is a no-op.

    return policy
