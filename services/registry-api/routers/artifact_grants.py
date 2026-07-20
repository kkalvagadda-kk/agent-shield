"""
Generic artifact role grants (Decision 25/30) — delegate `agent-admin` /
`approver` / `invoker` to a user, team, or application, scoped to one
artifact (an agent or a workflow).

  POST   /api/v1/artifacts/{artifact_type}/{artifact_id}/grants              — grant a role
  GET    /api/v1/artifacts/{artifact_type}/{artifact_id}/grants              — list active grants
  DELETE /api/v1/artifacts/{artifact_type}/{artifact_id}/grants/{grant_id}   — revoke (soft-delete)

Full request/response/error contract:
docs/plan/webhook-application-identity/contracts/artifact-grants.md

Why raw SQL, no ORM model
--------------------------
`artifact_role_grants` (migration 0044, widened by 0070) is deliberately
polymorphic: `grantee_id` is a bare TEXT column that means a user sub, a team
name, or an application UUID depending on `grantee_type`. No single
SQLAlchemy relationship/FK can describe that, so — per research.md §3 — this
table has never had an ORM model; `rbac.py`'s own queries against it
(`has_artifact_role`, `grant_creator_admin`, `get_user_artifact_roles`) are
already raw `sqlalchemy.text()`. This router is simply the first place that
WRITES it through a public HTTP surface instead of an internal RBAC helper,
and follows the same idiom.

Authorization
-------------
`POST`/`DELETE` require a real bearer token (`require_user`) and are gated by
`rbac.can_delegate_role`: platform-admin may grant/revoke any of the three
roles on any artifact; an agent-admin on the SPECIFIC artifact may
grant/revoke agent-admin/approver/invoker within that artifact's scope only;
anyone else gets 403. Revocation evaluates `can_delegate_role` against the
role read off the TARGET GRANT ROW, not the caller's intent, so a revoke call
can never be used to affect a role the caller couldn't grant. `GET` is an
unauthenticated read, matching this codebase's existing convention for
grant/trigger listing endpoints (`list_webhook_clients`, `list_triggers`) —
no secret material is ever present in a grant row.

Existence is checked before authorization on all three handlers (via
`_resolve_artifact`): an illegal `artifact_type` or a nonexistent artifact is
a structural precondition failure, not an authorization decision, and the
contract's own DELETE error table lists 404 ahead of 403 for exactly this
reason (see this file's `revoke_grant` for the same ordering applied to
grants).
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from rbac import can_delegate_role
from schemas import ArtifactRoleGrantCreate, ArtifactRoleGrantResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/artifacts", tags=["artifact-grants"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
async def _resolve_artifact(db: AsyncSession, artifact_type: str, artifact_id: uuid.UUID) -> None:
    """422 if `artifact_type` isn't a recognized kind; 404 if the row doesn't exist.

    Two literal, hand-written queries (not a dynamically interpolated table
    name) — the set of artifact kinds is fixed and small, and this keeps every
    SQL string in this file a static literal with bound parameters only.
    """
    if artifact_type == "agent":
        sql = text("SELECT 1 FROM agents WHERE id = :aid")
    elif artifact_type == "workflow":
        sql = text("SELECT 1 FROM workflows WHERE id = :aid")
    else:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"artifact_type must be one of 'agent', 'workflow'; got '{artifact_type}'",
        )
    row = await db.execute(sql, {"aid": artifact_id})
    if row.scalar_one_or_none() is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{artifact_type} '{artifact_id}' not found",
        )


async def _grantee_exists(db: AsyncSession, grantee_type: str, grantee_id: str) -> bool:
    """Resolve `grantee_id` against the table implied by `grantee_type`.

    - user        -> a row in user_team_assignments for this sub
    - team        -> a row in teams with this name
    - application -> a row in applications with this id
    """
    if grantee_type == "user":
        row = await db.execute(
            text("SELECT 1 FROM user_team_assignments WHERE user_sub = :gid"),
            {"gid": grantee_id},
        )
    elif grantee_type == "team":
        row = await db.execute(
            text("SELECT 1 FROM teams WHERE name = :gid"),
            {"gid": grantee_id},
        )
    elif grantee_type == "application":
        # Compare as text rather than parsing grantee_id as a UUID in Python —
        # a malformed id then simply matches no row (False -> 400), never a
        # ValueError the caller has to also catch.
        row = await db.execute(
            text("SELECT 1 FROM applications WHERE id::text = :gid"),
            {"gid": grantee_id},
        )
    else:
        # Unreachable via a real request: ArtifactRoleGrantCreate.grantee_type
        # is pattern-constrained to {user,team,application} at the Pydantic
        # layer, so FastAPI 422s before this function is ever called.
        return False
    return row.scalar_one_or_none() is not None


async def _application_name(db: AsyncSession, application_id: str) -> str | None:
    """grantee_label source for application grantees — None for user/team."""
    row = await db.execute(
        text("SELECT name FROM applications WHERE id::text = :aid"),
        {"aid": application_id},
    )
    return row.scalar_one_or_none()


# ---------------------------------------------------------------------------
# POST /api/v1/artifacts/{artifact_type}/{artifact_id}/grants
# ---------------------------------------------------------------------------
@router.post(
    "/{artifact_type}/{artifact_id}/grants",
    response_model=ArtifactRoleGrantResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_grant(
    artifact_type: str,
    artifact_id: uuid.UUID,
    body: ArtifactRoleGrantCreate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ArtifactRoleGrantResponse:
    """Grant `body.role` to `body.grantee_type`/`body.grantee_id` on this artifact."""
    await _resolve_artifact(db, artifact_type, artifact_id)

    caller_sub = claims.get("sub", "unknown")
    if not await can_delegate_role(db, caller_sub, artifact_id, body.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{caller_sub}' may not delegate role '{body.role}' on {artifact_type} '{artifact_id}'",
        )

    if not await _grantee_exists(db, body.grantee_type, body.grantee_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{body.grantee_type} '{body.grantee_id}' does not resolve to a real grantee",
        )

    try:
        result = await db.execute(
            text("""
                INSERT INTO artifact_role_grants
                    (artifact_type, artifact_id, role, grantee_type, grantee_id, granted_by)
                VALUES
                    (:artifact_type, :artifact_id, :role, :grantee_type, :grantee_id, :granted_by)
                RETURNING id, artifact_type, artifact_id, role, grantee_type, grantee_id,
                          granted_by, granted_at, revoked_at
            """),
            {
                "artifact_type": artifact_type,
                "artifact_id": artifact_id,
                "role": body.role,
                "grantee_type": body.grantee_type,
                "grantee_id": body.grantee_id,
                "granted_by": caller_sub,
            },
        )
    except IntegrityError:
        # uq_arg_active_grant — a non-revoked grant already exists for this
        # exact (artifact_id, role, grantee_type, grantee_id) tuple.
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"An active grant already exists for role '{body.role}' on "
                f"{body.grantee_type} '{body.grantee_id}' for {artifact_type} '{artifact_id}'"
            ),
        )

    grant = dict(result.mappings().one())

    # WS-4 upgrade-on-first-grant (design doc §9.4, test T-SYY-002). Granting an
    # application `invoker` on this artifact is the moment per-application signed auth
    # becomes possible, so EVERY webhook trigger on the artifact switches to
    # `client_signed` — in the SAME transaction as the grant insert (get_db commits on
    # handler return), so the stored mode and the grant can never disagree. Without
    # this, the trigger stays `auth_mode='token'` and the event-gateway's `token`
    # branch authenticates anyone holding the path token, never consulting the invoker
    # grant — i.e. the grant would buy nothing. This mirrors the retired
    # webhook_clients.create_webhook_client upgrade exactly, now driven by an invoker
    # grant instead of a webhook_clients row.
    #
    # ONE-WAY on purpose, matching that old path: revoke_grant never reverts to
    # `token`. A trigger whose last invoker grant is revoked stays `client_signed` and
    # correctly authenticates nobody (fail closed) rather than silently re-opening the
    # coarse per-trigger bearer token the operator upgraded away from.
    #
    # `artifact_type` is an explicit context parameter — already validated to
    # {agent,workflow} by _resolve_artifact above — mapped to the matching FK column by
    # a fixed two-way choice, never type-sniffed and never raw-interpolated from user
    # input. `artifact_id` is a bound parameter.
    if body.role == "invoker" and body.grantee_type == "application":
        trigger_fk = "agent_id" if artifact_type == "agent" else "workflow_id"
        flipped = await db.execute(
            text(f"""
                UPDATE agent_triggers
                   SET auth_mode = 'client_signed'
                 WHERE {trigger_fk} = :aid
                   AND trigger_type = 'webhook'
                   AND auth_mode <> 'client_signed'
            """),
            {"aid": artifact_id},
        )
        if flipped.rowcount:
            logger.info(
                "artifact_grants: invoker grant to application '%s' on %s:%s flipped "
                "%d webhook trigger(s) auth_mode token -> client_signed",
                body.grantee_id, artifact_type, artifact_id, flipped.rowcount,
            )

    grantee_label = None
    if body.grantee_type == "application":
        grantee_label = await _application_name(db, body.grantee_id)

    logger.info(
        "artifact_grants: %s granted role=%s to %s:%s on %s:%s",
        caller_sub, body.role, body.grantee_type, body.grantee_id, artifact_type, artifact_id,
    )
    return ArtifactRoleGrantResponse(**grant, grantee_label=grantee_label)


# ---------------------------------------------------------------------------
# GET /api/v1/artifacts/{artifact_type}/{artifact_id}/grants
# ---------------------------------------------------------------------------
@router.get(
    "/{artifact_type}/{artifact_id}/grants",
    response_model=list[ArtifactRoleGrantResponse],
)
async def list_grants(
    artifact_type: str,
    artifact_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[ArtifactRoleGrantResponse]:
    """List active (non-revoked) grants on one artifact, newest-first.

    Unauthenticated — matches this codebase's existing convention for
    grant/trigger listing endpoints (`list_webhook_clients`, `list_triggers`):
    no secret material is ever present in a grant row.
    """
    await _resolve_artifact(db, artifact_type, artifact_id)

    rows = await db.execute(
        text("""
            SELECT arg.id, arg.artifact_type, arg.artifact_id, arg.role,
                   arg.grantee_type, arg.grantee_id, arg.granted_by,
                   arg.granted_at, arg.revoked_at,
                   app.name AS grantee_label
            FROM artifact_role_grants arg
            LEFT JOIN applications app
                ON arg.grantee_type = 'application' AND app.id::text = arg.grantee_id
            WHERE arg.artifact_type = :artifact_type
              AND arg.artifact_id = :artifact_id
              AND arg.revoked_at IS NULL
            ORDER BY arg.granted_at DESC
        """),
        {"artifact_type": artifact_type, "artifact_id": artifact_id},
    )
    return [ArtifactRoleGrantResponse(**dict(r)) for r in rows.mappings().all()]


# ---------------------------------------------------------------------------
# DELETE /api/v1/artifacts/{artifact_type}/{artifact_id}/grants/{grant_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{artifact_type}/{artifact_id}/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    # response_model=None is REQUIRED, not decorative: without it FastAPI infers
    # a response field from the `-> None` return annotation and asserts at
    # import time ("Status code 204 must not have a response body"),
    # crash-looping the pod on startup. Same idiom as webhook_clients.py /
    # triggers.py / datasets.py's own 204 handlers.
    response_model=None,
)
async def revoke_grant(
    artifact_type: str,
    artifact_id: uuid.UUID,
    grant_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-delete one grant (sets `revoked_at`). Never a hard DELETE.

    Re-checks `can_delegate_role` against the TARGET GRANT's OWN role — read
    off the row first, not the caller's intent — so an agent-admin cannot use
    a revoke call to affect a role they would not be allowed to grant. A
    second revoke of an already-revoked grant is 404, not a silent no-op 204.
    """
    await _resolve_artifact(db, artifact_type, artifact_id)

    row = await db.execute(
        text("""
            SELECT id, role, revoked_at FROM artifact_role_grants
            WHERE id = :grant_id AND artifact_type = :artifact_type AND artifact_id = :artifact_id
        """),
        {"grant_id": grant_id, "artifact_type": artifact_type, "artifact_id": artifact_id},
    )
    grant = row.mappings().one_or_none()
    if grant is None or grant["revoked_at"] is not None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Grant '{grant_id}' not found or already revoked on {artifact_type} '{artifact_id}'",
        )

    caller_sub = claims.get("sub", "unknown")
    if not await can_delegate_role(db, caller_sub, artifact_id, grant["role"]):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"'{caller_sub}' may not revoke role '{grant['role']}' on {artifact_type} '{artifact_id}'",
        )

    await db.execute(
        text("UPDATE artifact_role_grants SET revoked_at = now() WHERE id = :grant_id"),
        {"grant_id": grant_id},
    )
    logger.info(
        "artifact_grants: %s revoked grant %s (role=%s) on %s:%s",
        caller_sub, grant_id, grant["role"], artifact_type, artifact_id,
    )
