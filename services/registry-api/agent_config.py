"""THE deployment config snapshot for an agent version. One definition, auto-complete.

DESIGN PRINCIPLE (Karthik, 2026-07-16): **a change to ANY part of the agent
definition bumps the version.** The snapshot is NOT an allow-list of hand-picked
fields — it captures the WHOLE agent row and then subtracts an explicit, documented
set of NON-definition fields. So:

    * A NEW column added to the Agent model is version-affecting BY DEFAULT.
    * We never revise this logic when a field is added — the safe default is "include".
    * To make a field NOT bump the version, add it to `_EXCLUDED_FROM_VERSION` HERE,
      with a stated reason. Exclusion is the thing you have to justify, not inclusion.

WHY (the bug that forced the redesign): `config_snapshot` was an allow-list of three
fields — `instructions`, `tools`, `llm_provider_id`. `execution_shape` and
`agent_class` were added to the Agent model LATER and nobody updated the allow-list, so
editing them in the Settings tab and redeploying minted NO new version: the
change-detection diff compared a snapshot that did not contain the fields that changed.
`agent_class` selects the OPA identity flow (WS-2), so the version could not describe
what governed the running pod. An allow-list re-creates that trap every time a field is
added; a deny-list cannot. (It was also duplicated in two routers — versions.py and
deployments.py — the repo's #1 bug class; now one function.)

The edited settings DID still reach the pod (deploy-controller reads the live agent
row), so the original bug was version-integrity, not "settings ignored" — but a version
that omits the fields it exists to freeze is broken regardless.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import inspect as sa_inspect


# Agent columns that are NOT part of the deployable definition. A change to any of
# these must NOT mint a new version. This is the ONLY list to touch when the meaning
# of a field is operational rather than definitional — and every entry states why,
# because excluding a field from version history is a decision, not a default.
_EXCLUDED_FROM_VERSION: frozenset[str] = frozenset({
    # Identity — a version is OF an agent; the agent's identity is not its config.
    # `name` also keys the k8s Service, so a rename is an identity concern, not a
    # config revision.
    "id",
    "name",
    # Ownership — who owns the agent does not change how it runs or is governed.
    "team",
    "team_id",
    # Audit — `updated_at` changes on EVERY write (including a no-op status toggle);
    # capturing it would make every save differ and defeat the no-op-redeploy dedup.
    "created_at",
    "updated_at",
    "created_by",
    # Operational lifecycle — not the deployable definition. `status` is
    # active/archived; `publish_status` is moved by the publish flow, not by editing
    # what the agent IS.
    "status",
    "publish_status",
})


def _jsonable(value: Any) -> Any:
    """Coerce a column value into something JSONB storage + json.dumps comparison accept."""
    if isinstance(value, uuid.UUID):
        return str(value)
    return value


def build_config_snapshot(agent: Any) -> dict[str, Any]:
    """The COMPLETE definition snapshot frozen onto an AgentVersion.

    Shape: a FLAT dict — the historical one consumers already read. It is the
    `metadata` JSONB (instructions / tools / llm_provider_id / model …) flattened to
    the top level, with every non-excluded scalar column overlaid on top. Keeping the
    flat shape is deliberate: `production_reconciler` reads `config.get("tools")` /
    `config.get("agent_class")`, the Studio properties panel reads `config.instructions`,
    and nesting `metadata` would break all of them.

    Auto-complete by construction: the columns are reflected from the mapper, so a NEW
    Agent column is captured with **zero change here** — the trap that let
    `execution_shape` / `agent_class` fall out of an allow-list cannot recur. To exempt
    a field, add it to `_EXCLUDED_FROM_VERSION` above, with a reason.

    Columns win over metadata keys on collision (e.g. `llm_provider_id` exists in both):
    the column is the authoritative FK; the metadata copy is a UI mirror.

    Tools appear here (from `metadata.tools`, the historical behaviour) AND the
    authoritative `agent_tools` join is snapshotted separately into `AgentVersion.tools`
    and compared alongside this config by the deploy diff — both are covered.
    """
    # 1. metadata flattened — preserves the historical top-level keys.
    snapshot: dict[str, Any] = dict(agent.metadata_ or {})
    # 2. every non-excluded column overlaid (authoritative; adds execution_shape,
    #    agent_class, memory_enabled, description, … and any future column).
    mapper = sa_inspect(type(agent)).mapper
    for attr in mapper.column_attrs:
        col_name = attr.columns[0].name
        if col_name == "metadata" or col_name in _EXCLUDED_FROM_VERSION:
            continue
        snapshot[col_name] = _jsonable(getattr(agent, attr.key))
    return snapshot
