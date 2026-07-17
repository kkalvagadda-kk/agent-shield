"""
AgentShield Registry API — Current user endpoint.

Endpoints
---------
  GET /api/v1/me  — returns the authenticated user's team assignment
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from models import UserProfile
from preferences import (
    UserPreferences,
    UserPreferencesUpdate,
    load_user_preferences,
)
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


# ---------------------------------------------------------------------------
# Response preferences (POC-3) — caller-scoped; user_id = caller.sub (no path id).
# ---------------------------------------------------------------------------
@router.get("/preferences", response_model=UserPreferences)
async def get_my_preferences(
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferences:
    """Return the caller's response preferences, or an all-null default if no row exists."""
    return await load_user_preferences(db, claims["sub"])


@router.put("/preferences", response_model=UserPreferences)
async def put_my_preferences(
    body: UserPreferencesUpdate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> UserPreferences:
    """Upsert the caller's preferences row (full replace of the five preset columns;
    an omitted field is stored as NULL). `updated_at` is server-managed. Out-of-vocab
    enum values are rejected as 422 by the Pydantic `UserPreferencesUpdate` body."""
    user_id = claims["sub"]
    values = body.model_dump()
    stmt = (
        pg_insert(UserProfile)
        .values(user_id=user_id, **values)
        .on_conflict_do_update(
            index_elements=[UserProfile.user_id],
            set_={**values, "updated_at": text("now()")},
        )
    )
    await db.execute(stmt)
    await db.commit()
    return await load_user_preferences(db, user_id)
