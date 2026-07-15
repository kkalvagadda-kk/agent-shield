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

from auth_middleware import get_optional_user
from db import get_db
from observability_backend import get_observability_backend
from k8s import create_eval_job
from models import Agent, AgentVersion, CompositeWorkflow, Deployment as DeploymentModel, EvalRun, EvalRunResult, PlaygroundDataset, WorkflowDeployment, WorkflowVersion
from tracing import trace_eval_run_completed, trace_eval_run_created, trace_eval_run_result
from schemas import (
    EvalRunCreate,
    EvalRunResponse,
    EvalRunResultCreate,
    EvalRunResultResponse,
    EvalRunStatusUpdate,
)

logger = logging.getLogger(__name__)

# Score at or above this threshold automatically sets eval_passed=True on the
# associated AgentVersion.  Kept in sync with eval-runner/main.py _JUDGE_PASS_THRESHOLD.
EVAL_PASS_THRESHOLD = 0.7

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
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> EvalRunResponse:
    """Create an EvalRun record. The eval-runner K8s Job will be launched
    (currently stubbed — logs intent, does not create the Job)."""
    caller = (user or {}).get("sub") or x_user_sub or "dev"

    # Validate dataset exists
    ds_result = await db.execute(
        select(PlaygroundDataset).where(PlaygroundDataset.id == body.dataset_id)
    )
    dataset = ds_result.scalar_one_or_none()
    if not dataset:
        raise HTTPException(status_code=404, detail="Dataset not found")

    agent_name = body.agent_name
    version_id = body.agent_version_id
    workflow_id = body.workflow_id
    wf_version_id = body.workflow_version_id

    # Resolve from sandbox deployment (agent)
    if body.sandbox_deployment_id:
        dep_result = await db.execute(
            select(DeploymentModel, Agent.name)
            .join(Agent, DeploymentModel.agent_id == Agent.id)
            .where(DeploymentModel.id == body.sandbox_deployment_id)
        )
        row = dep_result.one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail="Sandbox deployment not found")
        dep, resolved_name = row
        agent_name = agent_name or resolved_name
        version_id = version_id or dep.version_id

    # Resolve from workflow deployment
    if body.workflow_deployment_id:
        wdep_result = await db.execute(
            select(WorkflowDeployment).where(WorkflowDeployment.id == body.workflow_deployment_id)
        )
        wdep = wdep_result.scalar_one_or_none()
        if not wdep:
            raise HTTPException(status_code=404, detail="Workflow deployment not found")
        workflow_id = workflow_id or wdep.workflow_id
        wf_version_id = wf_version_id or wdep.version_id
        wf_result = await db.execute(
            select(CompositeWorkflow.name).where(CompositeWorkflow.id == wdep.workflow_id)
        )
        wf_name = wf_result.scalar_one_or_none()
        agent_name = agent_name or wf_name

    # Fallback: resolve version from agent name if no deployment was provided
    if version_id is None and workflow_id is None and agent_name:
        agent_result = await db.execute(
            select(Agent.id).where(Agent.name == agent_name)
        )
        agent_row = agent_result.scalar_one_or_none()
        if agent_row:
            ver_result = await db.execute(
                select(AgentVersion)
                .where(AgentVersion.agent_id == agent_row)
                .order_by(AgentVersion.created_at.desc())
                .limit(1)
            )
            latest_ver = ver_result.scalar_one_or_none()
            if latest_ver:
                version_id = latest_ver.id

    if not agent_name:
        raise HTTPException(status_code=422, detail="Cannot resolve agent_name — provide agent_name, sandbox_deployment_id, or workflow_deployment_id")

    # Eval v2 E-0: resolve the run's interpretation `mode` from the executable.
    # A `mode` is a projection of the execution cube onto the eval-relevant
    # families (data-model.md §1): a CompositeWorkflow executable → 'workflow';
    # an agent → its `execution_shape` ('reactive' | 'durable'). Default
    # 'reactive' (back-compat) when the shape can't be resolved.
    if workflow_id is not None:
        resolved_mode = "workflow"
    else:
        shape_result = await db.execute(
            select(Agent.execution_shape).where(Agent.name == agent_name)
        )
        shape = shape_result.scalar_one_or_none()
        resolved_mode = shape if shape in ("reactive", "durable") else "reactive"

    # E-0 is behavior-neutral: scoring follows the DATASET's authoring `mode`. A
    # REACTIVE dataset scores ANY executable's response reactively (exactly as before
    # E-0, where any agent — reactive OR durable OR a workflow — could be evaluated
    # against a dataset). Only a NON-reactive dataset (E-1+) requires the executable's
    # interpretation mode to match, because its items need a mode-specific run (e.g. a
    # durable trajectory). So the mismatch guard fires ONLY for non-reactive datasets —
    # gating it on all datasets broke durable/workflow evals against the (backfilled)
    # reactive datasets, which is a regression, not the intended constraint.
    if dataset.mode != "reactive" and resolved_mode != dataset.mode:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Eval mode mismatch: the executable resolves to mode "
                f"'{resolved_mode}' but dataset '{dataset.name}' is authored as "
                f"mode '{dataset.mode}'. Evaluate a '{dataset.mode}'-mode "
                f"executable, or author a '{resolved_mode}'-mode dataset."
            ),
        )

    eval_run = EvalRun(
        user_id=caller,
        agent_name=agent_name,
        agent_version_id=version_id,
        workflow_id=workflow_id,
        workflow_version_id=wf_version_id,
        dataset_id=body.dataset_id,
        sandbox_deployment_id=body.sandbox_deployment_id,
        workflow_deployment_id=body.workflow_deployment_id,
        # The SCORING mode = the dataset's authoring mode (reactive for every dataset
        # today → reactive scoring, byte-identical to pre-E-0). The executable's
        # resolved_mode is only used for the non-reactive guard above.
        mode=dataset.mode,
        status="pending",
        started_at=datetime.now(tz=timezone.utc),
    )
    db.add(eval_run)
    await db.flush()

    trace_eval_run_created(
        run_id=str(eval_run.id),
        agent_name=agent_name,
        dataset_id=str(body.dataset_id),
        user_id=caller,
    )

    # Launch the eval-runner K8s Job; fail fast if it cannot be created.
    # The Job's MODE is the SCORING mode == dataset.mode (== EvalRun.mode above),
    # NOT the executable's resolved_mode. For a durable dataset the 422 guard has
    # already forced resolved_mode == dataset.mode == 'durable', so they agree; but
    # passing dataset.mode keeps MODE unambiguously the scorer selector (the runner
    # requests the durable RUN shape explicitly via execution_shape). resolved_mode
    # is only the executable-shape used by the mismatch guard above.
    try:
        await create_eval_job(
            eval_run_id=str(eval_run.id),
            agent_name=agent_name,
            dataset_id=str(body.dataset_id),
            workflow_id=str(workflow_id) if workflow_id else None,
            agent_version_id=str(version_id) if version_id else None,
            mode=dataset.mode,
        )
        eval_run.status = "running"
    except Exception as exc:
        logger.error("create_eval_run: K8s Job creation failed for run %s: %s", eval_run.id, exc)
        eval_run.status = "failed"
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to launch eval-runner Job: {exc}",
        )

    await db.commit()
    await db.refresh(eval_run)
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
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[EvalRunResponse]:
    caller = (user or {}).get("sub") or x_user_sub
    q = select(EvalRun).order_by(EvalRun.created_at.desc())
    if caller:
        q = q.where(EvalRun.user_id == caller)
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
    run = await _resolve_eval_run(eval_run_id, db)  # 404 guard
    trace_span_id = trace_eval_run_result(
        run_id=str(eval_run_id),
        item_idx=body.dataset_item_idx,
        score=body.judge_score,
        passed=body.passed,
        agent_name=run.agent_name or "",
        input_message=body.input_message,
        response=body.response,
        judge_reasoning=body.judge_reasoning,
    )
    result_row = EvalRunResult(
        eval_run_id=eval_run_id,
        dataset_item_idx=body.dataset_item_idx,
        input_message=body.input_message,
        expected_output=body.expected_output,
        response=body.response,
        judge_score=body.judge_score,
        judge_reasoning=body.judge_reasoning,
        passed=body.passed,
        langfuse_trace_id=trace_span_id,
        # Eval v2 E-0: composite-score evidence (all optional; reactive fills
        # dimension_scores={"response": x}). `judge_score` above stays the
        # composite gate input — unchanged.
        dimension_scores=body.dimension_scores,
        eval_detail=body.eval_detail,
        trigger_payload=body.trigger_payload,
        matched=body.matched,
        run_id=body.run_id,
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
        trace_eval_run_completed(
            run_id=str(eval_run_id),
            status=body.status,
            overall_score=body.overall_score,
        )
    # Auto-promote: if this run completed with a passing score, mark the
    # associated AgentVersion as eval_passed=True so the publish gate opens
    # without requiring a manual PATCH.
    if (
        body.status == "completed"
        and run.overall_score is not None
        and run.overall_score >= EVAL_PASS_THRESHOLD
        and run.agent_version_id is not None
    ):
        ver_result = await db.execute(
            select(AgentVersion).where(AgentVersion.id == run.agent_version_id)
        )
        version = ver_result.scalar_one_or_none()
        if version is not None:
            version.eval_passed = True
            logger.info(
                "auto-set eval_passed=True for version %s (score=%.2f >= %.2f)",
                version.id, run.overall_score, EVAL_PASS_THRESHOLD,
            )
    # Auto-promote workflow version
    if (
        body.status == "completed"
        and run.overall_score is not None
        and run.overall_score >= EVAL_PASS_THRESHOLD
        and run.workflow_version_id is not None
    ):
        wv_result = await db.execute(
            select(WorkflowVersion).where(WorkflowVersion.id == run.workflow_version_id)
        )
        wf_version = wv_result.scalar_one_or_none()
        if wf_version is not None:
            wf_version.eval_passed = True
            logger.info(
                "auto-set eval_passed=True for workflow version %s (score=%.2f >= %.2f)",
                wf_version.id, run.overall_score, EVAL_PASS_THRESHOLD,
            )
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
    obs = get_observability_backend()
    results = []
    for r in result.scalars().all():
        resp = EvalRunResultResponse.model_validate(r)
        resp.trace_url = obs.build_trace_url(resp.langfuse_trace_id)
        results.append(resp)
    return results
