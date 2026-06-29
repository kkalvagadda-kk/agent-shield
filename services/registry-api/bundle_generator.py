"""
OPA Bundle Generator — Phase 9.1

Generates the data.json file for the central OPA bundle server. This file is the
data source for all authorization decisions across all agent pods.

Schema:
    {
      "agents": {
        "<sa_subject>": {
          "tools": ["tool_a", "tool_b"],
          "team": "platform",
          "agent_class": "user_delegated"
        }
      },
      "grants": {
        "<team_name>": ["tool_a", "tool_b"]
      }
    }

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


async def generate_bundle_data(db: AsyncSession) -> dict[str, Any]:
    """
    Query the database and build the OPA bundle data structure.

    Includes only agents that have at least one active (non-revoked) identity
    AND an active deployment in running state.
    """
    # Fetch all active agent identities with their agent + tool snapshot
    # Join: agent_identities → agents → agent_versions (via deployments)
    rows = await db.execute(
        text("""
            SELECT
                ai.sa_subject,
                ai.sa_namespace,
                a.name         AS agent_name,
                a.team         AS agent_team,
                a.agent_class,
                av.tools       AS tool_snapshot
            FROM agent_identities ai
            JOIN agents a ON a.name = ai.agent_name
            JOIN deployments d ON d.id = ai.deployment_id
            JOIN agent_versions av ON av.id = d.version_id
            WHERE ai.revoked_at IS NULL
              AND d.status = 'running'
        """)
    )

    agents: dict[str, Any] = {}
    for row in rows.mappings():
        sa_subject = row["sa_subject"]
        tool_snapshot = row["tool_snapshot"] or []

        # tool_snapshot may be a list of tool names (strings) or dicts with "name" key
        tool_names: list[str] = []
        for t in tool_snapshot:
            if isinstance(t, str):
                tool_names.append(t)
            elif isinstance(t, dict):
                name = t.get("name") or t.get("tool_name")
                if name:
                    tool_names.append(name)

        agents[sa_subject] = {
            "tools": tool_names,
            "team": row["agent_team"],
            "agent_class": row["agent_class"] or "user_delegated",
        }

    # Fetch active asset grants (team → list of tool names)
    grant_rows = await db.execute(
        text("""
            SELECT grantee_team, asset_id AS tool_name
            FROM asset_grants
            WHERE asset_type = 'tool'
              AND revoked_at IS NULL
              AND (expires_at IS NULL OR expires_at > now())
        """)
    )

    grants: dict[str, list[str]] = {}
    for row in grant_rows.mappings():
        team = row["grantee_team"]
        tool = row["tool_name"]
        grants.setdefault(team, []).append(tool)

    # De-duplicate tools per team
    grants = {team: list(set(tools)) for team, tools in grants.items()}

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
