"""
Eval Runner endpoints — manage evaluation runs against playground datasets.

Endpoints
---------
  POST /api/v1/playground/eval-runs              — create eval run (+ launch K8s Job stub)
  GET  /api/v1/playground/eval-runs              — list caller's eval runs
  GET  /api/v1/playground/eval-runs/{id}         — get one eval run
  POST /api/v1/playground/eval-runs/{id}/results — record per-item result (called by eval-runner Job)
  PATCH /api/v1/playground/eval-runs/{id}        — update status/scores (called by eval-runner Job)
  GET  /api/v1/playground/eval-runs/{id}/results — list results for an eval run
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import get_db
from models import EvalRun, EvalRunResult, PlaygroundDataset
from schemas import (
    EvalRunCreate,
    EvalRunResponse,
    EvalRunResultCreate,
    EvalRunResultResponse,
    EvalRunStatusUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/playground", tags=["eval-runner"])


async def _resolve_eval_run(
    eval_run_id: uuid.UUID, db: AsyncSession
) -> EvalRun:
    result = await db.execute(
        select(EvalRun).where(EvalRun.id == eval_run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


# ---------------------------------------------------------------------------
# POST /api/v1/playground/eval-runs
# ---------------------------------------------------------------------------
@router.post(
    "/eval-runs",
    status_code=status.HTTP_201_CREATED,
    response_model=EvalRunResponse,
    summary="Create and start an evaluation run",
)
async def create_eval_run(
    body: EvalRunCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> EvalRunResponse:
    """Create an EvalRun record. The eval-runner K8s Job will be launched
    (currently stubbed — logs intent, does not create the Job)."""
    caller = x_user_sub or "dev"

    # Validate dataset exists
    ds_result = await db.execute(
        select(PlaygroundDataset).where(PlaygroundDataset.id == body.dataset_id)
    )
    dataset = ds_result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    eval_run = EvalRun(
        user_id=caller,
        agent_name=body.agent_name,
        agent_version_id=body.agent_version_id,
        dataset_id=body.dataset_id,
        status="pending",
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(eval_run)
    await db.flush()

    # Stub: K8s Job creation deferred — log intent
    logger.info(
        "create_eval_run: would create K8s Job eval-runner-%s "
        "in agentshield-playground namespace with EVAL_RUN_ID=%s "
        "AGENT_NAME=%s DATASET_ID=%s",
        str(eval_run.id)[:8],
        eval_run.id,
        body.agent_name,
        body.dataset_id,
    )

    return EvalRunResponse.model_validate(eval_run)


# ---------------------------------------------------------------------------
# GET /api/v1/playground/eval-runs
# ---------------------------------------------------------------------------
@router.get(
    "/eval-runs",
    response_model=list[EvalRunResponse],
    summary="List evaluation runs",
)
async def list_eval_runs(
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    db: AsyncSession = Depends(get_db),
) -> list[EvalRunResponse]:
    q = select(EvalRun).order_by(EvalRun.created_at.desc())
    if x_user_sub:
        q = q.where(EvalRun.user_id == x_user_sub)
    result = await db.execute(q)
    return [EvalRunResponse.model_validate(r) for r in result.scalars().all()]


# ---------------------------------------------------------------------------
# GET /api/v1/playground/eval-runs/{eval_run_id}
# ---------------------------------------------------------------------------
@router.get(
    "/eval-runs/{eval_run_id}",
    response_model=EvalRunResponse,
    summary="Get an evaluation run",
)
async def get_eval_run(
    eval_run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> EvalRunResponse:
    run = await _resolve_eval_run(eval_run_id, db)
    return EvalRunResponse.model_validate(run)


# ---------------------------------------------------------------------------
# POST /api/v1/playground/eval-runs/{eval_run_id}/results
# ---------------------------------------------------------------------------
@router.post(
    "/eval-runs/{eval_run_id}/results",
    status_code=status.HTTP_201_CREATED,
    response_model=EvalRunResultResponse,
    summary="Record per-item eval result (called by eval-runner Job)",
)
async def create_eval_run_result(
    eval_run_id: uuid.UUID,
    body: EvalRunResultCreate,
    db: AsyncSession = Depends(get_db),
) -> EvalRunResultResponse:
    await _resolve_eval_run(eval_run_id, db)  # 404 guard
    result_row = EvalRunResult(
        eval_run_id=eval_run_id,
        dataset_item_idx=body.dataset_item_idx,
        input_message=body.input_message,
        response=body.response,
        judge_score=body.judge_score,
        judge_reasoning=body.judge_reasoning,
        passed=body.passed,
    )
    db.add(result_row)
    await db.flush()
    return EvalRunResultResponse.model_validate(result_row)


# ---------------------------------------------------------------------------
# PATCH /api/v1/playground/eval-runs/{eval_run_id}
# ---------------------------------------------------------------------------
@router.patch(
    "/eval-runs/{eval_run_id}",
    response_model=EvalRunResponse,
    summary="Update eval run status/scores (called by eval-runner Job)",
)
async def update_eval_run(
    eval_run_id: uuid.UUID,
    body: EvalRunStatusUpdate,
    db: AsyncSession = Depends(get_db),
) -> EvalRunResponse:
    run = await _resolve_eval_run(eval_run_id, db)
    run.status = body.status
    if body.total_items is not None:
        run.total_items = body.total_items
    if body.passed_count is not None:
        run.passed_count = body.passed_count
    if body.failed_count is not None:
        run.failed_count = body.failed_count
    if body.overall_score is not None:
        run.overall_score = body.overall_score
    if body.status in ("completed", "failed"):
        run.completed_at = datetime.now(tz=timezone.utc)
    await db.flush()
    logger.info(
        "update_eval_run: id=%s status=%s score=%s",
        run.id, run.status, run.overall_score,
    )
    return EvalRunResponse.model_validate(run)


# ---------------------------------------------------------------------------
# GET /api/v1/playground/eval-runs/{eval_run_id}/results
# ---------------------------------------------------------------------------
@router.get(
    "/eval-runs/{eval_run_id}/results",
    response_model=list[EvalRunResultResponse],
    summary="List results for an evaluation run",
)
async def list_eval_run_results(
    eval_run_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> list[EvalRunResultResponse]:
    await _resolve_eval_run(eval_run_id, db)  # 404 guard
    result = await db.execute(
        select(EvalRunResult)
        .where(EvalRunResult.eval_run_id == eval_run_id)
        .order_by(EvalRunResult.dataset_item_idx)
    )
    return [EvalRunResultResponse.model_validate(r) for r in result.scalars().all()]
