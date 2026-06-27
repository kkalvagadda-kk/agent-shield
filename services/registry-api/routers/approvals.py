"""
AgentShield Registry API — Approvals router.

Endpoints
---------
  POST  /api/v1/approvals/           — create approval request (called by Safety Orchestrator)
  GET   /api/v1/approvals/           — list approvals (filterable by status, agent)
  GET   /api/v1/approvals/{id}       — get single approval (reviewer fetches before deciding)
  PATCH /api/v1/approvals/{id}       — approve/reject with optimistic lock
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Approval
from schemas import ApprovalCreate, ApprovalDecision, ApprovalResponse, PaginatedResponse


class ReopenRequest(BaseModel):
    timeout_seconds: int = Field(1800, ge=60, le=86400)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


async def _resolve(approval_id: uuid.UUID, db: AsyncSession) -> Approval:
    result = await db.execute(select(Approval).where(Approval.id == approval_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Approval '{approval_id}' not found.",
        )
    return row


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ApprovalResponse,
    summary="Create approval request",
)
async def create_approval(
    body: ApprovalCreate,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Create a HITL approval request. Called by the Safety Orchestrator when an OPA
    evaluation returns `require_approval`. `timeout_seconds` controls auto-expiry."""
    now = datetime.now(tz=timezone.utc)
    expires_at = now + timedelta(seconds=body.timeout_seconds)

    approval = Approval(
        agent_id=body.agent_id,
        agent_name=body.agent_name,
        team=body.team,
        thread_id=body.thread_id,
        tool_name=body.tool_name,
        tool_args=body.tool_args,
        risk_level=body.risk_level,
        trace_id=body.trace_id,
        expires_at=expires_at,
        session_id=body.session_id,
        opa_decision_id=body.opa_decision_id,
    )
    db.add(approval)
    await db.flush()
    logger.info(
        "create_approval: id=%s agent=%s tool=%s risk=%s expires=%s",
        approval.id, approval.agent_name, approval.tool_name,
        approval.risk_level, approval.expires_at,
    )
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[ApprovalResponse],
    summary="List approval requests",
)
async def list_approvals(
    agent_name: Optional[str] = Query(None, description="Filter by agent name"),
    status_filter: Optional[str] = Query(
        None, alias="status",
        pattern="^(pending|approved|rejected|timed_out)$",
    ),
    thread_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ApprovalResponse]:
    q = select(Approval).order_by(Approval.created_at.desc())
    if agent_name:
        q = q.where(Approval.agent_name == agent_name)
    if status_filter:
        q = q.where(Approval.status == status_filter)
    if thread_id:
        q = q.where(Approval.thread_id == thread_id)

    count_q = q.with_only_columns(Approval.id)
    total = len((await db.execute(count_q)).all())

    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return PaginatedResponse(
        items=[ApprovalResponse.model_validate(r) for r in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/approvals/{approval_id}
# ---------------------------------------------------------------------------
@router.get(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Get approval by ID",
)
async def get_approval(
    approval_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Fetch a single approval. Reviewers call this first to get the current
    `version` field needed for the optimistic-lock PATCH."""
    approval = await _resolve(approval_id, db)
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# PATCH /api/v1/approvals/{approval_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/{approval_id}",
    response_model=ApprovalResponse,
    summary="Approve or reject (optimistic lock)",
)
async def decide_approval(
    approval_id: uuid.UUID,
    body: ApprovalDecision,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Submit approve/reject. `version` in the request body must match the current
    row version to prevent concurrent reviewers from overwriting each other."""
    approval = await _resolve(approval_id, db)

    if approval.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Approval is already '{approval.status}' — cannot re-decide.",
        )

    now = datetime.now(tz=timezone.utc)
    if approval.expires_at < now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approval has expired.",
        )

    if approval.version != body.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Optimistic lock conflict: expected version {approval.version}, "
                f"got {body.version}. Fetch the latest and retry."
            ),
        )

    approval.status = body.decision
    approval.reviewer_id = body.reviewer_id
    approval.reviewer_notes = body.reviewer_notes
    approval.decision_at = now
    approval.version = approval.version + 1
    await db.flush()

    logger.info(
        "decide_approval: id=%s decision=%s reviewer=%s",
        approval.id, body.decision, body.reviewer_id,
    )
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# POST /api/v1/approvals/{approval_id}/reopen
# ---------------------------------------------------------------------------
@router.post(
    "/{approval_id}/reopen",
    response_model=ApprovalResponse,
    summary="Reopen a timed-out or rejected approval",
)
async def reopen_approval(
    approval_id: uuid.UUID,
    body: ReopenRequest,
    db: AsyncSession = Depends(get_db),
) -> ApprovalResponse:
    """Reset a timed-out or rejected approval back to 'pending' with a fresh expiry window.

    Only approvals in 'timed_out' or 'rejected' status may be reopened.
    Attempting to reopen a 'pending' or 'approved' approval returns 409."""
    approval = await _resolve(approval_id, db)

    if approval.status in ("pending", "approved"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Approval is '{approval.status}' — only 'timed_out' or 'rejected' "
                "approvals can be reopened."
            ),
        )

    now = datetime.now(tz=timezone.utc)
    approval.status = "pending"
    approval.expires_at = now + timedelta(seconds=body.timeout_seconds)
    approval.decision_at = None
    approval.reviewer_id = None
    approval.reviewer_notes = None
    approval.version = approval.version + 1
    await db.flush()

    logger.info(
        "reopen_approval: id=%s new_expires=%s",
        approval.id, approval.expires_at,
    )
    return ApprovalResponse.model_validate(approval)
