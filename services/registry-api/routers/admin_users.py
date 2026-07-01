"""Admin user management — Keycloak-backed, team assignment in local DB.

Endpoints:
  GET    /api/v1/admin/users
  POST   /api/v1/admin/users
  GET    /api/v1/admin/users/{kc_id}
  PATCH  /api/v1/admin/users/{kc_id}
  DELETE /api/v1/admin/users/{kc_id}
  POST   /api/v1/admin/users/{kc_id}/reset-password
  GET    /api/v1/admin/teams-summary
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from keycloak_client import (
    create_user as kc_create,
    delete_user as kc_delete,
    get_user as kc_get,
    get_user_realm_roles,
    list_users as kc_list,
    reset_password as kc_reset_password,
    set_user_realm_role,
    update_user as kc_update,
)

router = APIRouter(prefix="/api/v1/admin/users", tags=["admin-users"])
teams_router = APIRouter(prefix="/api/v1/admin", tags=["admin-teams"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    first_name: str = ""
    last_name: str = ""
    temp_password: str
    team: str
    role: str = "operator"


class UserPatch(BaseModel):
    team: Optional[str] = None
    role: Optional[str] = None
    enabled: Optional[bool] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class ResetPasswordRequest(BaseModel):
    new_password: str
    temporary: bool = True


class UserResponse(BaseModel):
    kc_id: str
    username: str
    email: str
    first_name: str
    last_name: str
    enabled: bool
    team: Optional[str]
    role: Optional[str]
    created_at: Optional[int]


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _team_map(db: AsyncSession) -> dict[str, dict]:
    """Returns {user_sub: {team_name, role, assigned_at}} from local DB."""
    rows = await db.execute(
        text("SELECT user_sub, team_name, role, assigned_at FROM user_team_assignments")
    )
    return {
        r.user_sub: {"team": r.team_name, "role": r.role, "assigned_at": r.assigned_at}
        for r in rows
    }


async def _upsert_team(
    db: AsyncSession, user_sub: str, team_name: str, role: str, assigned_by: str | None
) -> None:
    await db.execute(
        text("""
            INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at)
            VALUES (:sub, :team, :role, :by, now())
            ON CONFLICT (user_sub) DO UPDATE
              SET team_name   = EXCLUDED.team_name,
                  role        = EXCLUDED.role,
                  assigned_by = EXCLUDED.assigned_by,
                  assigned_at = now()
        """),
        {"sub": user_sub, "team": team_name, "role": role, "by": assigned_by},
    )
    await db.commit()


def _kc_to_response(kc_user: dict, team_info: dict | None, roles: list[str] | None = None) -> UserResponse:
    platform_roles = {"admin", "operator", "viewer"}
    role = team_info["role"] if team_info else None
    if roles is not None:
        platform = [r for r in roles if r in platform_roles]
        role = platform[0] if platform else role
    return UserResponse(
        kc_id=kc_user["id"],
        username=kc_user.get("username", ""),
        email=kc_user.get("email", ""),
        first_name=kc_user.get("firstName", ""),
        last_name=kc_user.get("lastName", ""),
        enabled=kc_user.get("enabled", False),
        team=team_info["team"] if team_info else None,
        role=role,
        created_at=kc_user.get("createdTimestamp"),
    )


def _kc_error(exc: httpx.HTTPStatusError) -> HTTPException:
    if exc.response.status_code == 404:
        return HTTPException(status_code=404, detail="User not found in Keycloak")
    if exc.response.status_code == 409:
        return HTTPException(status_code=409, detail="Username or email already exists")
    return HTTPException(status_code=502, detail=f"Keycloak error: {exc.response.text[:200]}")


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("", response_model=list[UserResponse])
async def list_users(db: AsyncSession = Depends(get_db)):
    try:
        kc_users = await kc_list()
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Keycloak unreachable: {e}")

    team_map = await _team_map(db)
    return [_kc_to_response(u, team_map.get(u["id"])) for u in kc_users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    caller: dict | None = Depends(get_optional_user),
):
    try:
        kc_id = await kc_create(
            username=body.username,
            email=body.email,
            first_name=body.first_name,
            last_name=body.last_name,
            temp_password=body.temp_password,
        )
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)

    assigned_by = caller.get("preferred_username", "admin") if caller else "admin"
    await _upsert_team(db, kc_id, body.team, body.role, assigned_by=assigned_by)

    try:
        await set_user_realm_role(kc_id, body.role)
    except Exception:
        pass  # realm role is best-effort; team assignment already saved

    try:
        kc_user = await kc_get(kc_id)
    except Exception:
        kc_user = {"id": kc_id, "username": body.username, "email": body.email,
                   "firstName": body.first_name, "lastName": body.last_name, "enabled": True}

    return _kc_to_response(kc_user, {"team": body.team, "role": body.role})


@router.get("/{kc_id}", response_model=UserResponse)
async def get_user(kc_id: str, db: AsyncSession = Depends(get_db)):
    try:
        kc_user = await kc_get(kc_id)
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)

    team_map = await _team_map(db)
    roles = await get_user_realm_roles(kc_id)
    return _kc_to_response(kc_user, team_map.get(kc_id), roles)


@router.patch("/{kc_id}", response_model=UserResponse)
async def patch_user(kc_id: str, body: UserPatch, db: AsyncSession = Depends(get_db)):
    kc_fields: dict = {}
    if body.enabled is not None:
        kc_fields["enabled"] = body.enabled
    if body.first_name is not None:
        kc_fields["firstName"] = body.first_name
    if body.last_name is not None:
        kc_fields["lastName"] = body.last_name

    if kc_fields:
        try:
            await kc_update(kc_id, **kc_fields)
        except httpx.HTTPStatusError as e:
            raise _kc_error(e)

    team_map = await _team_map(db)
    current = team_map.get(kc_id, {})
    new_team = body.team or current.get("team") or ""
    new_role = body.role or current.get("role") or "operator"

    if body.team or body.role:
        await _upsert_team(db, kc_id, new_team, new_role, assigned_by="admin")
        if body.role:
            try:
                await set_user_realm_role(kc_id, new_role)
            except Exception:
                pass

    try:
        kc_user = await kc_get(kc_id)
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)

    return _kc_to_response(kc_user, {"team": new_team, "role": new_role})


@router.delete("/{kc_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(kc_id: str, db: AsyncSession = Depends(get_db)):
    try:
        await kc_delete(kc_id)
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)
    await db.execute(
        text("DELETE FROM user_team_assignments WHERE user_sub = :sub"), {"sub": kc_id}
    )
    await db.commit()


@router.post("/{kc_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(kc_id: str, body: ResetPasswordRequest):
    try:
        await kc_reset_password(kc_id, body.new_password, body.temporary)
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)


# ── Teams summary (members + grants per team) ──────────────────────────────────

@teams_router.get("/teams-summary")
async def teams_summary(db: AsyncSession = Depends(get_db)):
    teams_rows = await db.execute(text("SELECT id, name, namespace FROM teams ORDER BY name"))
    teams = [{"id": str(r.id), "name": r.name, "namespace": r.namespace} for r in teams_rows]

    assignments = await db.execute(
        text("SELECT user_sub, team_name, role FROM user_team_assignments")
    )
    team_members: dict[str, list] = {}
    for r in assignments:
        team_members.setdefault(r.team_name, []).append({"user_sub": r.user_sub, "role": r.role})

    grants_rows = await db.execute(
        text("""
            SELECT ag.id, ag.asset_type, ag.grantee_team, ag.granted_at, ag.expires_at,
                   COALESCE(a.name, t.name, s.name, w.name, ag.asset_id::text) AS asset_name
            FROM asset_grants ag
            LEFT JOIN agents a ON ag.asset_type = 'agent' AND a.id = ag.asset_id
            LEFT JOIN tools t ON ag.asset_type = 'tool' AND t.id = ag.asset_id
            LEFT JOIN skills s ON ag.asset_type = 'skill' AND s.id = ag.asset_id
            LEFT JOIN workflows w ON ag.asset_type = 'workflow' AND w.id = ag.asset_id
            WHERE ag.revoked_at IS NULL
        """)
    )
    team_grants: dict[str, list] = {}
    for r in grants_rows:
        team_grants.setdefault(r.grantee_team, []).append({
            "id": str(r.id),
            "asset_type": r.asset_type,
            "asset_name": r.asset_name,
            "granted_at": r.granted_at.isoformat() if r.granted_at else None,
            "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        })

    return [
        {
            **t,
            "members": team_members.get(t["name"], []),
            "grants": team_grants.get(t["name"], []),
        }
        for t in teams
    ]
