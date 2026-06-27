"""
AgentShield Registry API — OPA Decisions router.

Endpoints
---------
  POST /api/v1/opa-decisions/    — record an OPA evaluation result (audit log)
  GET  /api/v1/opa-decisions/    — query audit log (filterable by agent, decision)
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import OPADecision
from schemas import OPADecisionCreate, OPADecisionResponse, PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/opa-decisions", tags=["opa-decisions"])


# ---------------------------------------------------------------------------
# POST /api/v1/opa-decisions/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=OPADecisionResponse,
    summary="Record OPA evaluation result",
)
async def create_opa_decision(
    body: OPADecisionCreate,
    db: AsyncSession = Depends(get_db),
) -> OPADecisionResponse:
    """Append an OPA evaluation to the immutable audit log. Called by the OPA
    sidecar after every policy evaluation (allow, deny, or require_approval)."""
    decision = OPADecision(
        agent_name=body.agent_name,
        tool_name=body.tool_name,
        decision=body.decision,
        policy_version=body.policy_version,
        input_snapshot=body.input_snapshot,
        deny_reason=body.deny_reason,
        thread_id=body.thread_id,
        trace_id=body.trace_id,
    )
    db.add(decision)
    await db.flush()
    logger.info(
        "create_opa_decision: id=%s agent=%s tool=%s decision=%s",
        decision.id, decision.agent_name, decision.tool_name, decision.decision,
    )
    return OPADecisionResponse.model_validate(decision)


# ---------------------------------------------------------------------------
# GET /api/v1/opa-decisions/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[OPADecisionResponse],
    summary="Query OPA audit log",
)
async def list_opa_decisions(
    agent: Optional[str] = Query(None, description="Filter by agent_name"),
    decision: Optional[str] = Query(
        None,
        pattern="^(allow|deny|require_approval)$",
        description="Filter by decision outcome",
    ),
    thread_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[OPADecisionResponse]:
    q = select(OPADecision).order_by(OPADecision.decided_at.desc())
    if agent:
        q = q.where(OPADecision.agent_name == agent)
    if decision:
        q = q.where(OPADecision.decision == decision)
    if thread_id:
        q = q.where(OPADecision.thread_id == thread_id)

    count_q = q.with_only_columns(OPADecision.id)
    total = len((await db.execute(count_q)).all())

    rows = (await db.execute(q.limit(limit).offset(offset))).scalars().all()
    return PaginatedResponse(
        items=[OPADecisionResponse.model_validate(r) for r in rows],
        total=total,
    )
