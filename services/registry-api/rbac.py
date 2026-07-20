"""RBAC — single source of truth for all permission checks.

Provides FastAPI dependencies and policy-decision functions per the RBAC design
spec (docs/design/todo/rbac-design.md §5). All routers import from here rather
than implementing inline checks.

Phase 1 (this commit): module structure + permit-all stubs for
`require_global_role`. Enforcement tightens once role rename migration + frontend
guards land together.
"""
from __future__ import annotations

import logging
import uuid
from typing import Sequence

from fastapi import Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db

logger = logging.getLogger(__name__)

ROLE_HIERARCHY = {"viewer": 0, "contributor": 1, "platform-admin": 2}
_LEGACY_MAP = {"admin": "platform-admin", "operator": "contributor"}


def _normalize_role(raw: str | None) -> str:
    if raw is None:
        return "contributor"
    return _LEGACY_MAP.get(raw, raw)


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

async def get_user_global_role(db: AsyncSession, user_sub: str) -> str:
    row = await db.execute(
        text("SELECT role FROM user_team_assignments WHERE user_sub = :sub"),
        {"sub": user_sub},
    )
    r = row.scalar_one_or_none()
    return _normalize_role(r)


async def get_user_team(db: AsyncSession, user_sub: str) -> str | None:
    row = await db.execute(
        text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub"),
        {"sub": user_sub},
    )
    return row.scalar_one_or_none()


async def has_artifact_role(
    db: AsyncSession,
    user_sub: str,
    artifact_id: uuid.UUID,
    role: str,
    user_team: str | None = None,
) -> bool:
    """Check artifact_role_grants for an active grant."""
    sql = text("""
        SELECT 1 FROM artifact_role_grants
        WHERE artifact_id = :aid AND role = :role AND revoked_at IS NULL
          AND (
            (grantee_type = 'user' AND grantee_id = :sub)
            OR (grantee_type = 'team' AND grantee_id = :team)
          )
        LIMIT 1
    """)
    result = await db.execute(sql, {"aid": artifact_id, "role": role, "sub": user_sub, "team": user_team or ""})
    return result.scalar_one_or_none() is not None


# ---------------------------------------------------------------------------
# Policy decision functions
# ---------------------------------------------------------------------------

async def can_deploy_to_production(db: AsyncSession, user_sub: str, artifact_id: uuid.UUID) -> bool:
    role = await get_user_global_role(db, user_sub)
    if role == "platform-admin":
        return True
    team = await get_user_team(db, user_sub)
    return await has_artifact_role(db, user_sub, artifact_id, "agent-admin", team)


async def can_manage_artifact(db: AsyncSession, user_sub: str, artifact_id: uuid.UUID) -> bool:
    role = await get_user_global_role(db, user_sub)
    if role == "platform-admin":
        return True
    team = await get_user_team(db, user_sub)
    return await has_artifact_role(db, user_sub, artifact_id, "agent-admin", team)


async def can_approve_hitl(db: AsyncSession, user_sub: str, artifact_id: uuid.UUID) -> bool:
    role = await get_user_global_role(db, user_sub)
    if role == "platform-admin":
        return True
    team = await get_user_team(db, user_sub)
    return await has_artifact_role(db, user_sub, artifact_id, "approver", team)


async def can_use_playground(db: AsyncSession, user_sub: str) -> bool:
    role = await get_user_global_role(db, user_sub)
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["contributor"]


async def can_create_agent(db: AsyncSession, user_sub: str) -> bool:
    role = await get_user_global_role(db, user_sub)
    return ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["contributor"]


async def can_delegate_role(
    db: AsyncSession, caller_sub: str, artifact_id: uuid.UUID, target_role: str
) -> bool:
    role = await get_user_global_role(db, caller_sub)
    if role == "platform-admin":
        return True
    if target_role not in ("agent-admin", "approver", "invoker"):
        return False
    team = await get_user_team(db, caller_sub)
    return await has_artifact_role(db, caller_sub, artifact_id, "agent-admin", team)


async def can_create_application(db: AsyncSession, user_sub: str, team_name: str) -> bool:
    role = await get_user_global_role(db, user_sub)
    if role == "platform-admin":
        return True
    if ROLE_HIERARCHY.get(role, 0) < ROLE_HIERARCHY["contributor"]:
        return False
    return await get_user_team(db, user_sub) == team_name


# ---------------------------------------------------------------------------
# Auto-grant: insert agent-admin for artifact creator
# ---------------------------------------------------------------------------

async def grant_creator_admin(
    db: AsyncSession, artifact_type: str, artifact_id: uuid.UUID, creator_sub: str
) -> None:
    """Insert an agent-admin grant for the creator of a new artifact."""
    if creator_sub == "system":
        return
    await db.execute(
        text("""
            INSERT INTO artifact_role_grants (artifact_type, artifact_id, role, grantee_type, grantee_id, granted_by)
            VALUES (:atype, :aid, 'agent-admin', 'user', :sub, 'system:auto-grant')
            ON CONFLICT DO NOTHING
        """),
        {"atype": artifact_type, "aid": artifact_id, "sub": creator_sub},
    )


# ---------------------------------------------------------------------------
# List user's artifact roles (for /me enrichment)
# ---------------------------------------------------------------------------

async def get_user_artifact_roles(db: AsyncSession, user_sub: str, user_team: str | None = None) -> list[dict]:
    sql = text("""
        SELECT artifact_id, artifact_type, role
        FROM artifact_role_grants
        WHERE revoked_at IS NULL
          AND (
            (grantee_type = 'user' AND grantee_id = :sub)
            OR (grantee_type = 'team' AND grantee_id = :team)
          )
        ORDER BY granted_at DESC
    """)
    rows = await db.execute(sql, {"sub": user_sub, "team": user_team or ""})
    return [{"artifact_id": str(r.artifact_id), "artifact_type": r.artifact_type, "role": r.role} for r in rows]


# ---------------------------------------------------------------------------
# Enforcement flags
# ---------------------------------------------------------------------------

# Currently permit-all for trigger/webhook management checks (can_manage_artifact).
# Flip ENFORCE_TRIGGER_MGMT to True once frontend guards for trigger CRUD land.
ENFORCE_TRIGGER_MGMT: bool = False


# ---------------------------------------------------------------------------
# FastAPI dependency: require_global_role
# ---------------------------------------------------------------------------

def require_global_role(*allowed_roles: str):
    """Factory returning a FastAPI Depends that gates by global role.

    Currently permit-all with a warning log when the role doesn't match.
    Flip ENFORCE to True once frontend guards + role rename are deployed.
    """
    ENFORCE = False

    async def _check(
        claims: dict = Depends(require_user),
        db: AsyncSession = Depends(get_db),
    ) -> dict:
        sub = claims.get("sub", "unknown")
        role = await get_user_global_role(db, sub)
        if role not in allowed_roles:
            if ENFORCE:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=f"Requires one of {allowed_roles}; you have '{role}'.",
                )
            logger.warning(
                "rbac: %s has role '%s', needs %s — PERMITTED (enforcement off)",
                sub, role, allowed_roles,
            )
        claims["_global_role"] = role
        claims["_team"] = await get_user_team(db, sub)
        return claims

    return Depends(_check)
