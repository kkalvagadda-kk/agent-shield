"""
AgentShield Registry API — Current user endpoint.

Endpoints
---------
  GET /api/v1/me  — returns the authenticated user's team assignment
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from models import UserProfile
from schemas import ConversationSummary
from store_factory import get_conversation_store
from preferences import (
    UserPreferences,
    UserPreferencesUpdate,
    load_user_preferences,
)
from rbac import get_user_artifact_roles, get_user_global_role
from team_assets import fetch_team_asset_grants

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
    # ONE resolution path. This handler used to import rbac's private role normalizer
    # and answer the question itself — two independent answers to "what role is this"
    # is exactly how approvals._ADMIN_ROLES diverged
    # (docs/bugs/production-hitl-decide-403-authority.md). Decision 41.
    # Raises NoPlatformRole for a sub with no row; main.create_app answers 403.
    # (The private symbol is named nowhere in this file on purpose — CP2's
    # `grep -c` on it is the mechanical guard that the second path stays gone.)
    normalized_role = await get_user_global_role(db, sub)

    artifact_roles = await get_user_artifact_roles(db, sub, team)

    return {
        "sub": sub,
        "email": claims.get("email"),
        "preferred_username": claims.get("preferred_username"),
        "team": team,
        "role": normalized_role,
        "artifact_roles": artifact_roles,
    }


class MyTeamResponse(BaseModel):
    """The caller's own team and the assets shared with it."""

    team: Optional[str] = None
    namespace: Optional[str] = None
    grants: list[dict] = []


@router.get("/team", response_model=MyTeamResponse)
async def get_my_team(
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> MyTeamResponse:
    """The caller's team + the assets granted to it. Self-scoped; any authenticated role.

    R2 carved this out of `GET /api/v1/admin/teams-summary`, which is now
    platform-admin only. That endpoint answered TWO different questions with one
    payload: an admin census of every team, every member and every grant, and — for
    the Studio sidebar and My Agents — "what is shared with *me*". The second is
    needed by every role, so gating the census would have silently emptied
    "Shared With Me" for every contributor and consumer. Leaving it ungated instead
    would keep handing a `consumer` the whole org's team membership. Splitting is the
    only answer that is right on both counts.

    Deliberately returns NO member list. The old client had to `.find()` its own team
    inside an array of all teams by matching `members[].user_sub` against its `sub` —
    and that exact `.find()` on a non-array error envelope is what unmounted the whole
    app (docs/bugs/studio-blank-page-unauthed-fetch-teams-summary.md). Here the server
    already knows who is asking, so the client does no lookup and the shape it must
    trust is one field deep. Removing the caller's need to search removes the crash
    site, not just the crash.
    """
    sub = claims.get("sub")
    row = await db.execute(
        text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub"),
        {"sub": sub},
    )
    team = row.scalar_one_or_none()
    if not team:
        # R0 makes a missing row illegal, so this is a row that exists with a blank
        # team — not corruption, just a user parked outside any team. Empty, not 404:
        # the sidebar renders "Nothing shared yet" and stays up.
        return MyTeamResponse()

    ns_row = await db.execute(
        text("SELECT namespace FROM teams WHERE name = :name"), {"name": team}
    )
    namespace = ns_row.scalar_one_or_none()

    grants = await fetch_team_asset_grants(db, team_name=team)
    return MyTeamResponse(team=team, namespace=namespace, grants=grants.get(team, []))


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


@router.get("/conversations", response_model=list[ConversationSummary])
async def list_my_conversations(
    limit: int = 100,
    offset: int = 0,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationSummary]:
    """The caller's conversations across ALL agents (newest-first) — backs the
    standalone Conversations page. Caller-scoped (user_id = caller.sub); each row
    carries a derived `environment` so the page's All/Sandbox/Production filter is a
    pure client predicate. Continue is free: reusing a thread's session_id reloads
    its prior turns as context."""
    store = get_conversation_store()
    rows = await store.list_conversations(
        db, user_id=claims["sub"], limit=limit, offset=offset,
    )
    return [ConversationSummary.model_validate(r) for r in rows]
