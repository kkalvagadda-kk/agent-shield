"""Team → published-asset grants. ONE producer, two readers.

`asset_grants` answers "which teams may see and bind this published asset". It is
**visibility**, not authority (rbac-and-artifact-authorization.md §2) — do not confuse
it with `artifact_role_grants`, which is authority and lives in `rbac.py`.

Why this module exists rather than the query sitting inline where it is used: R2 split
that reader in two. `GET /api/v1/admin/teams-summary` is a full-org census and is now
platform-admin only; `GET /api/v1/me/team` is the self-scoped view the Studio sidebar
needs. Both must return the same grant shape from the same joins. Two hand-copied
LEFT JOIN blocks over four artifact tables is precisely the "two paths to one fact"
that let a raw-fetch copy of `/admin/teams-summary` sit unnoticed in the browser until
it blanked the app (docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md).
"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# The asset name is resolved by COALESCE across the four artifact tables because
# `asset_grants` stores only (asset_type, asset_id). A grant whose target row was
# hard-deleted degrades to the raw UUID rather than vanishing — an operator seeing a
# UUID in the grants list is a signal; a silently-shorter list is not.
_GRANTS_SQL = """
    SELECT ag.id, ag.asset_type, ag.grantee_team, ag.granted_at, ag.expires_at,
           COALESCE(a.name, t.name, s.name, w.name, ag.asset_id::text) AS asset_name
    FROM asset_grants ag
    LEFT JOIN agents a ON ag.asset_type = 'agent' AND a.id = ag.asset_id
    LEFT JOIN tools t ON ag.asset_type = 'tool' AND t.id = ag.asset_id
    LEFT JOIN skills s ON ag.asset_type = 'skill' AND s.id = ag.asset_id
    LEFT JOIN workflows w ON ag.asset_type = 'workflow' AND w.id = ag.asset_id
    WHERE ag.revoked_at IS NULL
"""


async def fetch_team_asset_grants(
    db: AsyncSession, team_name: str | None = None
) -> dict[str, list[dict]]:
    """Active asset grants keyed by grantee team.

    `team_name=None` returns every team (the admin census). Passing a team scopes the
    query in SQL rather than filtering the full result in Python — the point of the
    self-scoped endpoint is that other teams' grants never leave the database.
    """
    sql = _GRANTS_SQL
    params: dict[str, str] = {}
    if team_name is not None:
        sql += " AND ag.grantee_team = :team"
        params["team"] = team_name

    rows = await db.execute(text(sql), params)
    grants: dict[str, list[dict]] = {}
    for r in rows:
        grants.setdefault(r.grantee_team, []).append({
            "id": str(r.id),
            "asset_type": r.asset_type,
            "asset_name": r.asset_name,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        })
    return grants
