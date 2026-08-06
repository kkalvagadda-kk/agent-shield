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

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user, require_user
from db import get_db
from rbac import PLATFORM_ROLES
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

logger = logging.getLogger(__name__)

# R1 GAP, CLOSED 2026-08-06. This module was NOT in the ten routers §1.4 enumerated —
# that list named `admin.py`, and these are a SEPARATE module mounting under the same
# /api/v1/admin prefix. The consequence, measured on the live cluster against 0.2.261:
# an ANONYMOUS caller could POST /api/v1/admin/users with role="platform-admin" and get
# 201 — unauthenticated to full platform admin in one request, immediately usable because
# R0 made the Keycloak user and its role row land together. GET /admin/users,
# /admin/teams-summary and /admin/identity-audit were all readable anonymously too.
# Found by suite-98's T-S98-005, which was written to assert something else entirely.
# No in-cluster machine caller reaches either router (checked), so both are protected
# outright. Authentication only — WHICH role may call these is R2.
router = APIRouter(
    prefix="/api/v1/admin/users",
    tags=["admin-users"],
    dependencies=[Depends(require_user)],
)
teams_router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin-teams"],
    dependencies=[Depends(require_user)],
)


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
    """Returns {user_sub: {team, role, assigned_by, assigned_at}} from local DB.

    `assigned_by` is carried so the FR-12 identity audit can report a stale row in
    full without a second query against the same table. Additive: existing readers
    (`_kc_to_response`, `patch_user`) take only `team`/`role`.
    """
    rows = await db.execute(
        text(
            "SELECT user_sub, team_name, role, assigned_by, assigned_at "
            "FROM user_team_assignments"
        )
    )
    return {
        r.user_sub: {
            "team": r.team_name,
            "role": r.role,
            "assigned_by": r.assigned_by,
            "assigned_at": r.assigned_at,
        }
        for r in rows
    }


async def _upsert_team(
    db: AsyncSession, user_sub: str, team_name: str, role: str, assigned_by: str | None
) -> None:
    """Write the caller's team + role assignment row.

    Does NOT commit — the CALLER owns the transaction boundary. That is what makes
    create_user atomic (a compensating kc_delete needs the failure to be visible
    before the response). An explicit boundary, not a `commit: bool` flag sniffed
    per call site (Decision 41).
    """
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


def _kc_to_response(kc_user: dict, team_info: dict | None, roles: list[str] | None = None) -> UserResponse:
    role = team_info["role"] if team_info else None
    if roles is not None:
        platform = [r for r in roles if r in PLATFORM_ROLES]
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
    try:
        # Realm-role failure is FATAL, not swallowed. A user whose Keycloak role and
        # DB row disagree is exactly the half-created state R0 exists to remove.
        await set_user_realm_role(kc_id, body.role)
        await _upsert_team(db, kc_id, body.team, body.role, assigned_by=assigned_by)
        await db.commit()
    except Exception as exc:
        await db.rollback()
        try:
            await kc_delete(kc_id)          # compensate: no orphan Keycloak user
        except Exception as cleanup_exc:
            logger.error("create_user: compensating kc_delete FAILED for %s: %s — "
                         "ORPHAN Keycloak user, see GET /api/v1/admin/identity-audit",
                         kc_id, cleanup_exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"User creation rolled back: {type(exc).__name__}: {exc}",
        )

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
        # `_upsert_team` no longer commits — patch owns its own boundary (Decision 41).
        await _upsert_team(db, kc_id, new_team, new_role, assigned_by="admin")
        await db.commit()
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


# ── Identity audit (FR-12) ─────────────────────────────────────────────────────

class OrphanUser(BaseModel):
    kc_id: str
    username: str
    email: Optional[str] = None


class StaleRow(BaseModel):
    user_sub: str
    team_name: str
    role: str
    assigned_by: Optional[str] = None
    assigned_at: Optional[str] = None


class IdentityAuditResponse(BaseModel):
    checked_at: str
    keycloak_user_count: int
    assignment_row_count: int
    orphan_users: list[OrphanUser]
    stale_rows: list[StaleRow]
    matched_count: int


# Mounted on `teams_router` (prefix /api/v1/admin), NOT on `router`, deliberately:
# `router` declares `GET /{kc_id}`, so a literal `/audit` sibling would depend on
# declaration order to avoid being shadowed. `/api/v1/admin/identity-audit` cannot
# collide.
@teams_router.get("/identity-audit", response_model=IdentityAuditResponse)
async def audit_identity(db: AsyncSession = Depends(get_db)) -> IdentityAuditResponse:
    """Cross-check Keycloak users against `user_team_assignments`, both directions.

    READ-ONLY. Reports, never deletes (spec OQ-2, resolved to option (a)) — deciding
    which side of a divergence is wrong is a judgement an operator makes, not one an
    audit endpoint should make on their behalf.

    `orphan_users` — a Keycloak user with no assignment row. This is the state R0
    makes illegal: `POST /api/v1/admin/users` is atomic and the bootstrap always
    writes its row, so a non-empty list means something created a user outside those
    paths, or a compensating `kc_delete` failed.

    `stale_rows` — a row whose `user_sub` is not a live Keycloak user. Litter, not a
    security hole: nobody can authenticate as a dead `sub`. It does mean "row count"
    stops equalling "admin count", which is why it is reported rather than ignored
    (G-R0-3). Nothing here deletes them.

    A row holding a REVIEWER SCOPE such as `agent:reviewer` whose `sub` is a live
    Keycloak user is `matched`, never litter (Decision 42 / V-5). `role` is a union of
    {global role} u {reviewer scope} and this endpoint does not adjudicate the
    vocabulary — only presence on both sides.

    Keycloak failure is a 502, not an empty result: an audit that silently reports
    zero orphans because it could not read Keycloak is worse than one that fails.
    """
    try:
        kc_users = await kc_list()
    except httpx.HTTPStatusError as e:
        raise _kc_error(e)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Keycloak unreachable: {e}")

    team_map = await _team_map(db)
    kc_ids = {u["id"] for u in kc_users}

    orphan_users = [
        OrphanUser(
            kc_id=u["id"],
            username=u.get("username", ""),
            email=u.get("email"),
        )
        for u in kc_users
        if u["id"] not in team_map
    ]
    stale_rows = [
        StaleRow(
            user_sub=sub,
            team_name=info["team"],
            role=info["role"],
            assigned_by=info.get("assigned_by"),
            assigned_at=info["assigned_at"].isoformat() if info.get("assigned_at") else None,
        )
        for sub, info in team_map.items()
        if sub not in kc_ids
    ]

    return IdentityAuditResponse(
        checked_at=datetime.now(timezone.utc).isoformat(),
        keycloak_user_count=len(kc_users),
        assignment_row_count=len(team_map),
        orphan_users=orphan_users,
        stale_rows=stale_rows,
        matched_count=len(kc_ids & set(team_map)),
    )
