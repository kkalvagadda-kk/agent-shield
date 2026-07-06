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
    return {
        "sub": sub,
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "team": assignment["team_name"] if assignment else None,
        "role": assignment["role"] if assignment else None,
    }
