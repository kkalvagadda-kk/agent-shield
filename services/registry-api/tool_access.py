"""
Shared tool-grant resolver — the SINGLE source of truth for "may this team use
this tool?" (design §3b/§8).

`team_may_use_tool` is the sole implementation of the tool-grant rule. Two callers
use it and must not fork the logic:
  * the deploy gate (`routers/deployments.py`), and
  * the internal MCP authz endpoint (`routers/internal_mcp.py`).

Extracted verbatim from the deploy gate's former inline per-foreign-tool loop, so
the deploy path's `422 tool_grants_missing` semantics are unchanged.
"""
from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models import AssetGrant, Tool


async def team_may_use_tool(db: AsyncSession, team: str, tool_id: uuid.UUID) -> bool:
    """True iff `team` may use tool `tool_id`.

    Usable when the tool is own-team or team-less (``owner_team == team`` or
    ``owner_team is None``), OR an active cross-team grant exists:
    ``AssetGrant(asset_type='tool', asset_id=tool_id, grantee_team=team,
    revoked_at IS NULL)``.

    This mirrors — as the single shared resolver — the check the deploy gate ran
    inline. (The ``asset_type='tool'`` predicate is behavior-neutral: ``asset_id``
    is the tool's globally-unique PK, so a grant on that id can only be a tool
    grant; it makes the query explicit without changing any decision.)

    An unknown ``tool_id`` (no such Tool row) returns False — fail-closed. The
    deploy gate never passes a missing id (its tools are loaded rows); the internal
    endpoint pre-resolves the Tool, so this branch is purely defensive.
    """
    row = (
        await db.execute(select(Tool.owner_team).where(Tool.id == tool_id))
    ).first()
    if row is None:
        return False
    owner_team = row[0]
    if owner_team is None or owner_team == team:
        return True

    grant = (
        await db.execute(
            select(AssetGrant.id)
            .where(
                AssetGrant.asset_type == "tool",
                AssetGrant.asset_id == tool_id,
                AssetGrant.grantee_team == team,
                AssetGrant.revoked_at.is_(None),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return grant is not None
