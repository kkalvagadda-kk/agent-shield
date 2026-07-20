"""
Team applications (WS-4 successor, Decision 30) — reusable webhook-sending
identities, replacing the per-trigger `webhook_clients` allowlist.

  POST   /api/v1/teams/{team}/applications                    — create (secret shown ONCE)
  GET    /api/v1/teams/{team}/applications                    — list (never the secret)
  POST   /api/v1/teams/{team}/applications/{id}/rotate-secret — rotate (secret shown ONCE)
  PATCH  /api/v1/teams/{team}/applications/{id}                — enable/disable (kill switch)
  DELETE /api/v1/teams/{team}/applications/{id}                — hard delete + grant cascade

Full request/response/error contract:
docs/plan/webhook-application-identity/contracts/applications.md

Where an application fits
--------------------------
Creating an application grants it access to nothing — it is just a team-owned
identity that can hold a signing secret. An `agent-admin` separately grants it
`invoker` on a specific agent/workflow via `routers/artifact_grants.py`
(`grantee_type='application'`). This router owns only the identity's own
lifecycle (create/list/rotate/enable-disable/delete); the gateway's read path
(which artifact an application may call) lives entirely in
`artifact_role_grants`, not here.

Secret handling
---------------
The secret is generated here, returned exactly once (on create's 201 and on
rotate's 200), and stored Fernet-encrypted (`crypto.encrypt_json`) — the same
helper and the same `whsec_` prefix `routers/webhook_clients.py` already uses
for the predecessor concept, reused verbatim rather than reinvented. It is
**encrypted, not hashed**: the gateway must recompute `HMAC_SHA256(secret,
...)` to verify a signature, so the raw value must be recoverable server-side.
Reveal-once is guaranteed by the *type* system, not by handler discipline —
`ApplicationResponse` has no `secret` field, so no read path can leak it even
if someone adds one later.

Authorization
-------------
`POST` / `POST .../rotate-secret` / `PATCH` / `DELETE` all require a real
bearer token (`require_user`) and are gated by `rbac.can_create_application`,
which is deliberately **stricter** than `rbac.can_create_agent`: application
identity is team-scoped by design, so creation/rotation/kill-switch/delete
authority requires the caller's own team (from `user_team_assignments`) to
equal the `{team}` path segment, or platform-admin. `GET` (list) requires only
`require_user` — no team-membership check — matching the read-open convention
used elsewhere (`list_webhook_clients`, `list_triggers`, `list_grants`).

Delete cascade
--------------
`artifact_role_grants.grantee_id` is a polymorphic TEXT column — no DB-level
FK is possible against it (same reasoning `routers/artifact_grants.py`
documents for why that table has no ORM model at all). So deleting an
application hard-deletes its `artifact_role_grants` rows via one explicit,
application-code `DELETE ... WHERE grantee_type='application' AND
grantee_id=:id` statement, issued in the SAME transaction as the `applications`
row delete (no intermediate commit) — never a soft-revoke, since soft-revoking
a grant that points at an application id which no longer exists would leave a
permanently dangling, unresolvable row.
"""
from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from crypto import encrypt_json
from db import get_db
from models import Application
from rbac import can_create_application
from schemas import (
    ApplicationCreate,
    ApplicationCreatedResponse,
    ApplicationResponse,
    ApplicationRotateSecretResponse,
    ApplicationUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/teams/{team}/applications", tags=["applications"])

# `whsec_` prefix reused verbatim from `webhook_clients.py` — same
# operator-facing meaning ("this is a webhook signing secret") regardless of
# which table stores it. Do not invent a new prefix here.
_SECRET_PREFIX = "whsec_"


async def _require_gate(db: AsyncSession, caller_sub: str, team: str) -> None:
    if not await can_create_application(db, caller_sub, team):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{caller_sub}' may not manage applications for team '{team}'",
        )


async def _get_application(
    db: AsyncSession, team: str, application_id: uuid.UUID
) -> Application:
    result = await db.execute(
        select(Application).where(
            Application.id == application_id,
            Application.team_name == team,
        )
    )
    application = result.scalar_one_or_none()
    if not application:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Application '{application_id}' not found under team '{team}'",
        )
    return application


# ---------------------------------------------------------------------------
# POST /api/v1/teams/{team}/applications
# ---------------------------------------------------------------------------
@router.post(
    "/",
    response_model=ApplicationCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_application(
    team: str,
    body: ApplicationCreate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ApplicationCreatedResponse:
    """Register a new team-owned application. Zero grants on creation — per
    design doc Flow A, an agent-admin must separately grant it `invoker` via
    `POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants`.

    The plaintext secret is returned HERE AND NOWHERE ELSE, ever.
    """
    caller_sub = claims.get("sub", "unknown")
    await _require_gate(db, caller_sub, team)

    secret = _SECRET_PREFIX + secrets.token_urlsafe(32)
    application = Application(
        team_name=team,
        name=body.name,
        secret_encrypted=encrypt_json({"secret": secret}),
        created_by=caller_sub,
    )
    db.add(application)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        # UNIQUE(team_name, name) — uq_applications_team_name.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Application '{body.name}' already exists for team '{team}'",
        )

    await db.commit()
    await db.refresh(application)
    logger.info("created application '%s' for team '%s'", body.name, team)
    # Constructed explicitly (not from_attributes): the secret exists only in
    # this local variable and is never read back off the row.
    return ApplicationCreatedResponse(
        id=application.id,
        name=application.name,
        secret=secret,
        created_at=application.created_at,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/teams/{team}/applications
# ---------------------------------------------------------------------------
@router.get("/", response_model=list[ApplicationResponse])
async def list_applications(
    team: str,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> list[Application]:
    """List a team's applications — never a secret. Authenticated-only, no
    team-membership check: any authenticated user can see which applications
    exist (this is what the Studio "Invoke access" grant picker calls)."""
    result = await db.execute(
        select(Application)
        .where(Application.team_name == team)
        .order_by(Application.created_at)
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# POST /api/v1/teams/{team}/applications/{application_id}/rotate-secret
# ---------------------------------------------------------------------------
@router.post(
    "/{application_id}/rotate-secret",
    response_model=ApplicationRotateSecretResponse,
)
async def rotate_secret(
    team: str,
    application_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ApplicationRotateSecretResponse:
    """Rotate — one action, every `invoker` grant this application holds
    (across however many artifacts) requires the new secret on the very next
    request. The OLD secret is not retrievable through any endpoint."""
    caller_sub = claims.get("sub", "unknown")
    await _require_gate(db, caller_sub, team)
    application = await _get_application(db, team, application_id)

    secret = _SECRET_PREFIX + secrets.token_urlsafe(32)
    application.secret_encrypted = encrypt_json({"secret": secret})
    application.rotated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(application)
    logger.info(
        "rotated secret for application '%s' (team '%s')", application.name, team
    )
    return ApplicationRotateSecretResponse(
        id=application.id,
        secret=secret,
        rotated_at=application.rotated_at,
    )


# ---------------------------------------------------------------------------
# PATCH /api/v1/teams/{team}/applications/{application_id}
# ---------------------------------------------------------------------------
@router.patch("/{application_id}", response_model=ApplicationResponse)
async def update_application(
    team: str,
    application_id: uuid.UUID,
    body: ApplicationUpdate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> Application:
    """Kill switch. `enabled=false` denies this application on EVERY artifact
    it holds `invoker` on, simultaneously, on the next gateway request —
    independent of and orthogonal to revoking any one `artifact_role_grants`
    row. `enabled=true` re-enables without needing to re-grant anything that
    was never revoked. Read live on every gateway request — no cache."""
    caller_sub = claims.get("sub", "unknown")
    await _require_gate(db, caller_sub, team)
    application = await _get_application(db, team, application_id)

    application.enabled = body.enabled
    await db.commit()
    await db.refresh(application)
    logger.info(
        "set application '%s' (team '%s') enabled=%s",
        application.name, team, body.enabled,
    )
    return application


# ---------------------------------------------------------------------------
# DELETE /api/v1/teams/{team}/applications/{application_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{application_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # response_model=None is REQUIRED, not decorative: without it FastAPI infers
    # a response field from the `-> None` return annotation and asserts at
    # import time ("Status code 204 must not have a response body"),
    # crash-looping the pod on startup. Same idiom as webhook_clients.py /
    # triggers.py / artifact_grants.py's own 204 handlers.
    response_model=None,
)
async def delete_application(
    team: str,
    application_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Hard delete — cascades grants. Because `artifact_role_grants.grantee_id`
    is a polymorphic TEXT column (no DB FK is possible against it), the
    cascade is explicit application code, issued in the SAME transaction as
    the `applications` row delete: a dangling grant pointing at an id that no
    longer resolves to any application would be a permanently unresolvable
    row, so this is a hard delete, matching the application's own hard-delete
    semantics — not a soft-revoke."""
    caller_sub = claims.get("sub", "unknown")
    await _require_gate(db, caller_sub, team)
    application = await _get_application(db, team, application_id)

    await db.delete(application)
    await db.execute(
        text(
            "DELETE FROM artifact_role_grants "
            "WHERE grantee_type = 'application' AND grantee_id = :gid"
        ),
        {"gid": str(application_id)},
    )
    await db.commit()
    logger.info(
        "deleted application '%s' (team '%s') and its artifact_role_grants",
        application.name, team,
    )
