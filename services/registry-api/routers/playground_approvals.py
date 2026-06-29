"""
Playground HITL approval endpoints.

Self-approval only — no Slack, no approval_authority check.
Approvals with context='playground' are self-approved by the playground user.
"""

from __future__ import annotations

import logging
import uuid as _uuid_mod
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import Approval
from schemas import ApprovalResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["playground-approvals"])


# ---------------------------------------------------------------------------
# GET /api/v1/playground/approvals
# ---------------------------------------------------------------------------
@router.get(
    "/approvals",
    response_model=list[ApprovalResponse],
    summary="List playground HITL approvals",
)
async def list_playground_approvals(
    status_filter: Optional[str] = Query(None, alias="status"),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> list[ApprovalResponse]:
    """List pending playground approvals. No authority check needed — self-approval."""
    q = select(Approval).where(Approval.context == "playground")
    if status_filter:
        q = q.where(Approval.status == status_filter)
    # Future: filter by user_id when Approval gains a user_id column
    result = await db.execute(q)
    rows = result.scalars().all()
    return [ApprovalResponse.model_validate(a) for a in rows]


# ---------------------------------------------------------------------------
# POST /api/v1/playground/approvals/{approval_id}/decide
# ---------------------------------------------------------------------------
@router.post(
    "/approvals/{approval_id}/decide",
    status_code=status.HTTP_200_OK,
    summary="Self-approve a playground HITL approval",
)
async def decide_playground_approval(
    approval_id: str,
    body: dict[str, Any],
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Self-approve a playground HITL approval. No approval_authority check needed.
    The approval must have context='playground'; production approvals are rejected."""
    try:
        parsed_id = _uuid_mod.UUID(approval_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid approval_id format")

    result = await db.execute(select(Approval).where(Approval.id == parsed_id))
    approval = result.scalar_one_or_none()
    if not approval:
        raise HTTPException(status_code=404, detail="Approval not found")

    if approval.context != "playground":
        raise HTTPException(
            status_code=403,
            detail="Use production endpoint for non-playground approvals",
        )

    if approval.status != "pending":
        raise HTTPException(
            status_code=409,
            detail=f"Approval is already '{approval.status}'",
        )

    decision = body.get("decision")
    if decision not in ("approved", "denied"):
        raise HTTPException(
            status_code=422, detail="decision must be 'approved' or 'denied'"
        )

    now = datetime.now(tz=timezone.utc)
    approval.status = decision
    approval.reviewer_id = x_user_sub or "self"
    approval.decision_at = now
    approval.version = approval.version + 1
    await db.commit()

    logger.info(
        "decide_playground_approval: id=%s decision=%s reviewer=%s",
        approval.id, decision, approval.reviewer_id,
    )
    return {"decided": True, "decision": decision}
