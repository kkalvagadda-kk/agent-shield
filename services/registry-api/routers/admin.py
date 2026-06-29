"""
AgentShield Registry API — Admin router (Phase 9.1+, Phase 9.2)

Endpoints
---------
  POST /api/v1/admin/bundle/regenerate              — regenerate OPA bundle data.json from DB
  GET  /api/v1/admin/publish-requests               — list publish requests (?status=pending_review)
  POST /api/v1/admin/publish-requests/{id}/approve  — approve a publish request
  POST /api/v1/admin/publish-requests/{id}/reject   — reject a publish request
  POST /api/v1/admin/grants                         — create an asset grant directly
  GET  /api/v1/admin/grants                         — list active grants (?asset_id=...)
  DELETE /api/v1/admin/grants/{id}                  — revoke a grant
  GET  /api/v1/admin/approval-authority             — list approval authorities
  POST /api/v1/admin/approval-authority             — create approval authority
  DELETE /api/v1/admin/approval-authority/{id}      — revoke approval authority
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bundle_generator import generate_bundle_data
from db import get_db
from models import Agent, ApprovalAuthority, AssetGrant, GrantAudit, PublishRequest
from schemas import (
    ApprovalAuthorityCreate,
    ApprovalAuthorityResponse,
    AssetGrantCreate,
    AssetGrantResponse,
    GrantAuditResponse,
    PaginatedResponse,
    PublishRequestApprove,
    PublishRequestReject,
    PublishRequestResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])


# ---------------------------------------------------------------------------
# POST /bundle/regenerate
# ---------------------------------------------------------------------------
@router.post(
    "/bundle/regenerate",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Regenerate OPA bundle data.json",
)
async def regenerate_bundle(
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger an immediate OPA bundle data.json regeneration.

    The bundle generator queries all active agent identities and asset grants,
    builds the data.json payload, and patches the opa-bundle-data ConfigMap.
    OPA sidecars pick up the change within their polling interval (30–60s).

    This endpoint is useful after:
    - A new asset grant is created or revoked
    - An agent identity is manually provisioned or revoked
    - Any bulk change that the per-deploy trigger may have missed
    """
    # Generate the data now to validate DB connectivity
    try:
        bundle_data = await generate_bundle_data(db)
    except Exception as exc:
        logger.exception("Bundle generation failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Bundle generation failed: {exc}",
        ) from exc

    agent_count = len(bundle_data.get("agents", {}))
    team_count = len(bundle_data.get("grants", {}))

    # The actual ConfigMap patch requires a K8s client that is initialized in the
    # deploy-controller. The Registry API doesn't hold a K8s client by default.
    # Return the generated data in the response so the caller can inspect it,
    # and log a note that the ConfigMap patch is handled by the deploy-controller
    # via its periodic sync, or can be triggered manually by an admin.
    logger.info(
        "Bundle regenerated: %d agent identities, %d teams with grants",
        agent_count,
        team_count,
    )

    return {
        "status": "generated",
        "agent_identities": agent_count,
        "teams_with_grants": team_count,
        "note": (
            "Bundle data generated. The deploy-controller patches the ConfigMap "
            "after each agent deploy. For manual push, use the deploy-controller's "
            "admin endpoint or restart the bundle server pods."
        ),
        "bundle_data": bundle_data,
    }


# ---------------------------------------------------------------------------
# GET /publish-requests
# ---------------------------------------------------------------------------
@router.get(
    "/publish-requests",
    response_model=PaginatedResponse[PublishRequestResponse],
    summary="List publish requests",
)
async def list_publish_requests(
    status_filter: Optional[str] = Query(
        None, alias="status", description="Filter by status (pending_review/approved/rejected)"
    ),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[PublishRequestResponse]:
    """Return a paginated list of publish requests, optionally filtered by status."""
    from sqlalchemy import func

    q = select(PublishRequest)
    count_q = select(func.count()).select_from(PublishRequest)

    if status_filter:
        q = q.where(PublishRequest.status == status_filter)
        count_q = count_q.where(PublishRequest.status == status_filter)

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(q.order_by(PublishRequest.submitted_at.desc()).limit(limit).offset(offset))
    ).scalars().all()

    return PaginatedResponse[PublishRequestResponse](
        items=[PublishRequestResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# POST /publish-requests/{id}/approve
# ---------------------------------------------------------------------------
@router.post(
    "/publish-requests/{request_id}/approve",
    summary="Approve a publish request",
)
async def approve_publish_request(
    request_id: uuid.UUID,
    body: PublishRequestApprove,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Approve a publish request.

    - Sets publish_request.status = 'approved'
    - Sets the asset's publish_status = 'published' (only agents supported for now)
    - Creates AssetGrant + GrantAudit records for each grantee_team in the body
    """
    result = await db.execute(
        select(PublishRequest).where(PublishRequest.id == request_id)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish request not found.")

    now = datetime.now(tz=timezone.utc)
    pr.status = "approved"
    pr.reviewed_by = x_user_sub
    pr.reviewed_at = now

    # Update the asset's publish_status to 'published'
    # Currently only 'agent' asset_type maps to the agents table
    if pr.asset_type == "agent":
        agent_result = await db.execute(
            select(Agent).where(Agent.id == pr.asset_id)
        )
        asset = agent_result.scalar_one_or_none()
        if asset is not None:
            asset.publish_status = "published"
            asset.updated_at = now

    # Create grants for each team
    grants_created = 0
    for team in body.grantee_teams:
        grant = AssetGrant(
            asset_id=pr.asset_id,
            asset_type=pr.asset_type,
            grantee_team=team,
            granted_by=x_user_sub,
            expires_at=body.expires_at,
        )
        db.add(grant)

        audit = GrantAudit(
            admin_id=x_user_sub,
            action="created",
            asset_id=pr.asset_id,
            grantee_team=team,
        )
        db.add(audit)
        grants_created += 1

    await db.flush()

    logger.info(
        "approve_publish_request: id=%s approved_by=%s grants_created=%d",
        request_id,
        x_user_sub,
        grants_created,
    )
    return {"approved": True, "grants_created": grants_created}


# ---------------------------------------------------------------------------
# POST /publish-requests/{id}/reject
# ---------------------------------------------------------------------------
@router.post(
    "/publish-requests/{request_id}/reject",
    summary="Reject a publish request",
)
async def reject_publish_request(
    request_id: uuid.UUID,
    body: PublishRequestReject,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Reject a publish request and revert the agent back to 'private'."""
    result = await db.execute(
        select(PublishRequest).where(PublishRequest.id == request_id)
    )
    pr = result.scalar_one_or_none()
    if pr is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Publish request not found.")

    now = datetime.now(tz=timezone.utc)
    pr.status = "rejected"
    pr.reviewed_by = x_user_sub
    pr.reviewed_at = now
    pr.review_notes = body.notes

    # Revert asset publish_status to 'private'
    if pr.asset_type == "agent":
        agent_result = await db.execute(
            select(Agent).where(Agent.id == pr.asset_id)
        )
        asset = agent_result.scalar_one_or_none()
        if asset is not None:
            asset.publish_status = "private"
            asset.updated_at = now

    await db.flush()

    logger.info(
        "reject_publish_request: id=%s rejected_by=%s", request_id, x_user_sub
    )
    return {"rejected": True}


# ---------------------------------------------------------------------------
# POST /grants
# ---------------------------------------------------------------------------
@router.post(
    "/grants",
    response_model=AssetGrantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an asset grant",
)
async def create_grant(
    body: AssetGrantCreate,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> AssetGrantResponse:
    """Directly create an asset grant for a team (bypasses publish workflow)."""
    grant = AssetGrant(
        asset_id=body.asset_id,
        asset_type=body.asset_type,
        grantee_team=body.grantee_team,
        granted_by=x_user_sub,
        expires_at=body.expires_at,
    )
    db.add(grant)

    audit = GrantAudit(
        admin_id=x_user_sub,
        action="created",
        asset_id=body.asset_id,
        grantee_team=body.grantee_team,
    )
    db.add(audit)

    await db.flush()
    await db.refresh(grant)

    logger.info(
        "create_grant: asset_id=%s team=%s granted_by=%s",
        body.asset_id,
        body.grantee_team,
        x_user_sub,
    )
    return AssetGrantResponse.model_validate(grant)


# ---------------------------------------------------------------------------
# GET /grants
# ---------------------------------------------------------------------------
@router.get(
    "/grants",
    response_model=PaginatedResponse[AssetGrantResponse],
    summary="List asset grants",
)
async def list_grants(
    asset_id: Optional[uuid.UUID] = Query(None, description="Filter by asset UUID"),
    grantee_team: Optional[str] = Query(None, description="Filter by grantee team"),
    include_revoked: bool = Query(False, description="Include revoked grants"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AssetGrantResponse]:
    """List asset grants with optional filters."""
    from sqlalchemy import func

    q = select(AssetGrant)
    count_q = select(func.count()).select_from(AssetGrant)

    if asset_id is not None:
        q = q.where(AssetGrant.asset_id == asset_id)
        count_q = count_q.where(AssetGrant.asset_id == asset_id)
    if grantee_team is not None:
        q = q.where(AssetGrant.grantee_team == grantee_team)
        count_q = count_q.where(AssetGrant.grantee_team == grantee_team)
    if not include_revoked:
        q = q.where(AssetGrant.revoked_at.is_(None))
        count_q = count_q.where(AssetGrant.revoked_at.is_(None))

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(q.order_by(AssetGrant.granted_at.desc()).limit(limit).offset(offset))
    ).scalars().all()

    return PaginatedResponse[AssetGrantResponse](
        items=[AssetGrantResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /grants/{id}/audit
# ---------------------------------------------------------------------------
@router.get(
    "/grants/{grant_id}/audit",
    response_model=PaginatedResponse[GrantAuditResponse],
    summary="List audit events for a grant",
)
async def list_grant_audit(
    grant_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[GrantAuditResponse]:
    """Return all GrantAudit rows linked to a specific grant's asset_id + grantee_team."""
    from sqlalchemy import func

    result = await db.execute(select(AssetGrant).where(AssetGrant.id == grant_id))
    grant = result.scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")

    base_q = select(GrantAudit).where(
        and_(GrantAudit.asset_id == grant.asset_id, GrantAudit.grantee_team == grant.grantee_team)
    )
    count_q = select(func.count()).select_from(GrantAudit).where(
        and_(GrantAudit.asset_id == grant.asset_id, GrantAudit.grantee_team == grant.grantee_team)
    )

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(base_q.order_by(GrantAudit.timestamp.desc()).limit(limit).offset(offset))
    ).scalars().all()

    return PaginatedResponse[GrantAuditResponse](
        items=[GrantAuditResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# DELETE /grants/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/grants/{grant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke an asset grant",
)
async def revoke_grant(
    grant_id: uuid.UUID,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-revoke an asset grant by setting revoked_at = now()."""
    result = await db.execute(select(AssetGrant).where(AssetGrant.id == grant_id))
    grant = result.scalar_one_or_none()
    if grant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grant not found.")
    if grant.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Grant already revoked.")

    now = datetime.now(tz=timezone.utc)
    grant.revoked_at = now

    audit = GrantAudit(
        admin_id=x_user_sub,
        action="revoked",
        asset_id=grant.asset_id,
        grantee_team=grant.grantee_team,
    )
    db.add(audit)
    await db.flush()

    logger.info("revoke_grant: id=%s revoked_by=%s", grant_id, x_user_sub)


# ---------------------------------------------------------------------------
# GET /approval-authority
# ---------------------------------------------------------------------------
@router.get(
    "/approval-authority",
    response_model=PaginatedResponse[ApprovalAuthorityResponse],
    summary="List approval authorities",
)
async def list_approval_authority(
    resource_type: Optional[str] = Query(None, description="Filter by resource type"),
    resource_id: Optional[str] = Query(None, description="Filter by resource ID"),
    include_revoked: bool = Query(False, description="Include revoked entries"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ApprovalAuthorityResponse]:
    """List approval authority records."""
    from sqlalchemy import func

    q = select(ApprovalAuthority)
    count_q = select(func.count()).select_from(ApprovalAuthority)

    if resource_type is not None:
        q = q.where(ApprovalAuthority.resource_type == resource_type)
        count_q = count_q.where(ApprovalAuthority.resource_type == resource_type)
    if resource_id is not None:
        q = q.where(ApprovalAuthority.resource_id == resource_id)
        count_q = count_q.where(ApprovalAuthority.resource_id == resource_id)
    if not include_revoked:
        q = q.where(ApprovalAuthority.revoked_at.is_(None))
        count_q = count_q.where(ApprovalAuthority.revoked_at.is_(None))

    total = (await db.execute(count_q)).scalar_one()
    rows = (
        await db.execute(q.order_by(ApprovalAuthority.granted_at.desc()).limit(limit).offset(offset))
    ).scalars().all()

    return PaginatedResponse[ApprovalAuthorityResponse](
        items=[ApprovalAuthorityResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# POST /approval-authority
# ---------------------------------------------------------------------------
@router.post(
    "/approval-authority",
    response_model=ApprovalAuthorityResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create approval authority record",
)
async def create_approval_authority(
    body: ApprovalAuthorityCreate,
    x_user_sub: str = Header(default="system", alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> ApprovalAuthorityResponse:
    """Register an approver (user or role) for a specific resource."""
    if not body.approver_user_id and not body.approver_role:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="At least one of approver_user_id or approver_role must be provided.",
        )

    aa = ApprovalAuthority(
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        approver_user_id=body.approver_user_id,
        approver_role=body.approver_role,
        granted_by=x_user_sub,
    )
    db.add(aa)
    await db.flush()
    await db.refresh(aa)

    logger.info(
        "create_approval_authority: resource=%s/%s granted_by=%s",
        body.resource_type,
        body.resource_id,
        x_user_sub,
    )
    return ApprovalAuthorityResponse.model_validate(aa)


# ---------------------------------------------------------------------------
# DELETE /approval-authority/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/approval-authority/{authority_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Revoke approval authority",
)
async def revoke_approval_authority(
    authority_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft-revoke an approval authority entry by setting revoked_at = now()."""
    result = await db.execute(
        select(ApprovalAuthority).where(ApprovalAuthority.id == authority_id)
    )
    aa = result.scalar_one_or_none()
    if aa is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval authority not found.")
    if aa.revoked_at is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Already revoked.")

    aa.revoked_at = datetime.now(tz=timezone.utc)
    await db.flush()

    logger.info("revoke_approval_authority: id=%s", authority_id)
