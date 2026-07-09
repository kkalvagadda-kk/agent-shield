"""
AgentShield Registry API — Current user endpoint.

Endpoints
---------
  GET /api/v1/me  — returns the authenticated user's team assignment
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from rbac import get_user_artifact_roles, _normalize_role

router = APIRouter(prefix="/api/v1/me", tags=["me"])


@router.get("")
async def get_me(
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
):
    """Return the current user's profile: sub, team, and role."""
    sub = claims.get("sub")
    row = await db.execute(
        text("SELECT team_name, role FROM user_team_assignments WHERE user_sub = :sub"),
        {"sub": sub},
    )
    assignment = row.mappings().first()
    team = assignment["team_name"] if assignment else None
    raw_role = assignment["role"] if assignment else None
    normalized_role = _normalize_role(raw_role)

    artifact_roles = await get_user_artifact_roles(db, sub, team)

    return {
        "sub": sub,
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "team": team,
        "role": normalized_role,
        "artifact_roles": artifact_roles,
    }
