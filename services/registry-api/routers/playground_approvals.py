"""
Playground HITL approval endpoints.

List-only — the decide endpoint lives in playground.py (registered first, with
correct denied→rejected mapping and thread_id in the response).
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Header, Query
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
    result = await db.execute(q)
    rows = result.scalars().all()
    return [ApprovalResponse.model_validate(a) for a in rows]
