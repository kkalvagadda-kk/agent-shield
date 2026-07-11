"""
OPA Bundle Generator — Phase 9.1

Generates the data.json file for the central OPA bundle server. This file is the
data source for all authorization decisions across all agent pods.

Schema (Phase 9.1 completion — per-tool risk is REQUIRED by the unified policy):
    {
      "agents": {
        "<sa_subject>": {
          "tools": [ {"name": "tool_a", "risk": "low"},
                     {"name": "tool_b", "risk": "high"} ],
          "team": "platform",
          "agent_class": "user_delegated",
          "expected_sa_subject": "<sa_subject>",
          "sa_namespace": "agents-platform"
        }
      },
      "grants": {
        "<team_name>": [ {"name": "tool_c", "risk": "medium"} ]
      }
    }

The unified Rego policy (services/registry-api/opa_policy/agentshield.rego) maps a
tool's risk to an action (low/medium → allow, high → require_approval, critical/unknown
→ deny). Missing or unrecognized risk is defaulted to "critical" here (fail-closed).

The generator is called:
  1. By reconciler.py after each agent deployment (incremental trigger)
  2. Via POST /api/v1/admin/bundle/regenerate (manual trigger, e.g. after grant changes)

The generated data.json is patched directly into the opa-bundle-data ConfigMap in
agentshield-platform, which is mounted into the nginx bundle server pods. OPA sidecars
poll the bundle server every 30–60 seconds and pick up changes automatically.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from models import Agent, AgentIdentity, AgentVersion, Deployment

logger = logging.getLogger(__name__)

# The OPA sidecar ConfigMap key names
BUNDLE_DATA_CM_NAME = "opa-bundle-data"
BUNDLE_DATA_CM_NAMESPACE = "agentshield-platform"
BUNDLE_DATA_CM_KEY = "data.json"

# Recognized risk levels. Anything else (or missing) is treated as "critical"
# so the unified policy fails closed (see opa_policy/agentshield.rego).
_KNOWN_RISKS = frozenset({"low", "medium", "high", "critical"})


def _normalize_risk(value: Any) -> str:
    """Coerce a tool's risk into a known level, defaulting to 'critical'."""
    if isinstance(value, str) and value in _KNOWN_RISKS:
        return value
    return "critical"


async def generate_bundle_data(db: AsyncSession) -> dict[str, Any]:
    """
    Query the database and build the OPA bundle data structure.

    Includes only agents that have at least one active (non-revoked) identity
    AND an active deployment in running state.
    """
    # Fetch all active agent identities with their agent + tool snapshot, for BOTH
    # environments. Sandbox identities resolve via the `deployments` table +
    # agent_versions; production identities live in a separate `production_deployments`
    # table and take their tool scope from published_versions.config_snapshot->'tools'.
    # A row sets exactly one of deployment_id / production_deployment_id, so the two
    # legs are disjoint and UNION ALL is safe. Without the production leg, production
    # SA subjects never enter the bundle and every production tool call fails closed
    # (agent_unauthenticated) — see docs/design/sandbox-production-parity-architecture.md.
    rows = await db.execute(
        text("""
            SELECT
                ai.sa_subject,
                ai.sa_namespace,
                a.name         AS agent_name,
                a.team         AS agent_team,
                a.agent_class,
                a.execution_shape,
                av.tools       AS tool_snapshot
            FROM agent_identities ai
            JOIN agents a ON a.name = ai.agent_name
            JOIN deployments d ON d.id = ai.deployment_id
            JOIN agent_versions av ON av.id = d.version_id
            WHERE ai.revoked_at IS NULL
              AND d.status IN ('deploying', 'running')

            UNION ALL

            SELECT
                ai.sa_subject,
                ai.sa_namespace,
                a.name         AS agent_name,
                a.team         AS agent_team,
                a.agent_class,
                a.execution_shape,
                (pv.config_snapshot -> 'tools') AS tool_snapshot
            FROM agent_identities ai
            JOIN agents a ON a.name = ai.agent_name
            JOIN production_deployments pd ON pd.id = ai.production_deployment_id
            JOIN published_versions pv ON pv.id = pd.version_id
            WHERE ai.revoked_at IS NULL
              AND pd.status IN ('deploying', 'running')
        """)
    )

    agents: dict[str, Any] = {}
    for row in rows.mappings():
        sa_subject = row["sa_subject"]
        tool_snapshot = row["tool_snapshot"] or []
        # A JSONB column decodes to a Python list, but a computed JSONB expression
        # (config_snapshot -> 'tools', production leg) can come back as a JSON string
        # depending on the driver codec — parse it so both legs behave identically.
        if isinstance(tool_snapshot, str):
            try:
                tool_snapshot = json.loads(tool_snapshot)
            except (ValueError, TypeError):
                tool_snapshot = []
        if not isinstance(tool_snapshot, list):
            tool_snapshot = []

        # tool_snapshot may be a list of tool names (strings) or dicts carrying
        # "name" + "risk". Emit {name, risk} objects; a bare string or a missing
        # risk defaults to "critical" (fail-closed).
        tools: list[dict[str, str]] = []
        for t in tool_snapshot:
            if isinstance(t, str):
                tools.append({"name": t, "risk": "critical"})
            elif isinstance(t, dict):
                name = t.get("name") or t.get("tool_name")
                if name:
                    tools.append({"name": name, "risk": _normalize_risk(t.get("risk"))})

        agents[sa_subject] = {
            "tools": tools,
            "team": row["agent_team"],
            "agent_class": row["agent_class"] or "user_delegated",
            "execution_shape": row["execution_shape"] or "reactive",
            # expected_sa_subject enables OPA to do bidirectional validation:
            # not just "is this SA in the bundle" but "does the claimed subject
            # match what's registered for this agent". Prevents an agent from
            # presenting a different agent's SA subject in its token claim.
            "expected_sa_subject": sa_subject,
            "sa_namespace": row["sa_namespace"],
        }

    # Fetch active asset grants and resolve each granted tool's name + risk by
    # joining the tools registry (asset_grants.asset_id is a tools.id UUID, not a
    # name — the old `asset_id AS tool_name` alias never matched a tool name).
    grant_rows = await db.execute(
        text("""
            SELECT g.grantee_team          AS grantee_team,
                   t.name                  AS tool_name,
                   t.risk_level            AS risk
            FROM asset_grants g
            JOIN tools t ON t.id = g.asset_id
            WHERE g.asset_type = 'tool'
              AND g.revoked_at IS NULL
              AND (g.expires_at IS NULL OR g.expires_at > now())
        """)
    )

    # team -> {tool_name: {"name", "risk"}} (dedup by tool name per team)
    grants_by_team: dict[str, dict[str, dict[str, str]]] = {}
    for row in grant_rows.mappings():
        team = row["grantee_team"]
        name = row["tool_name"]
        if not name:
            continue
        grants_by_team.setdefault(team, {})[name] = {
            "name": name,
            "risk": _normalize_risk(row["risk"]),
        }

    grants: dict[str, list[dict[str, str]]] = {
        team: list(tools.values()) for team, tools in grants_by_team.items()
    }

    bundle_data = {"agents": agents, "grants": grants}
    logger.info(
        "Generated OPA bundle: %d agent identities, %d teams with grants",
        len(agents),
        len(grants),
    )
    return bundle_data


async def push_bundle_to_configmap(
    db: AsyncSession,
    k8s_client_instance: Any,  # K8sClient from deploy-controller (passed in)
) -> None:
    """Generate bundle data and patch the opa-bundle-data ConfigMap."""
    data = await generate_bundle_data(db)
    data_json = json.dumps(data, indent=2)

    # patch_configmap_data is a sync method on K8sClient — run in thread executor
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: k8s_client_instance.patch_configmap_data(
            namespace=BUNDLE_DATA_CM_NAMESPACE,
            name=BUNDLE_DATA_CM_NAME,
            key=BUNDLE_DATA_CM_KEY,
            value=data_json,
        ),
    )
    logger.info("Patched ConfigMap %s/%s", BUNDLE_DATA_CM_NAMESPACE, BUNDLE_DATA_CM_NAME)
