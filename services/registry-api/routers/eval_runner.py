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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from observability_backend import get_observability_backend
from k8s import create_eval_job
from models import Agent, AgentTrigger, AgentVersion, CompositeWorkflow, Deployment as DeploymentModel, EvalRun, EvalRunResult, PlaygroundDataset, WorkflowDeployment, WorkflowVersion
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


def effective_pass_threshold(run: EvalRun) -> float:
    """Eval v2 E-6: THE publish threshold for one run — the single resolution.

    The threshold used to exist four times across three services (this gate, the
    eval-runner's per-item verdict, and the Studio's verdict + colour band), each
    defaulting to 0.7. They agreed, so nothing ever errored — and a per-run
    threshold wired to only the gate would have made the product LIE: a 0.85 run
    with `pass_threshold=0.9` would render "passed" and mark every item passed,
    while the gate silently refused to publish.

    `run.pass_threshold` is defaulted from the platform default at the single API
    write site (`create_eval_run`), so it is non-NULL on every row written since
    E-6. The None arm covers pre-E-6 rows only — the one legitimate fallback in
    the design, kept because those rows really do exist in the live DB.
    """
    if run.pass_threshold is not None:
        return float(run.pass_threshold)
    return EVAL_PASS_THRESHOLD


def eval_run_response(run: EvalRun) -> EvalRunResponse:
    """Serialize an EvalRun with its threshold ALREADY RESOLVED.

    The wire always carries a real number, so no consumer ever has to guess — which
    is the whole point: `eval_runs.pass_threshold` is nullable and 141 pre-E-6 rows
    are NULL, so a client that read the column raw would have to re-declare the 0.7
    default locally. That is exactly how the threshold came to exist four times
    across three services. Resolving it HERE keeps `effective_pass_threshold` the
    single answer and leaves the UI with nothing to decide.

    Use this instead of `EvalRunResponse.model_validate(run)` — a raw validate leaks
    the NULL back out and re-opens the guess.
    """
    resp = EvalRunResponse.model_validate(run)
    resp.pass_threshold = effective_pass_threshold(run)
    return resp


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
# Eval mode resolution + the launch compatibility guard (Eval v2 E-0 / E-3)
#
# A dataset's `mode` is its AUTHORING intent (which per-item schema its rows
# follow == which scorer branch runs). The EXECUTABLE has facts: it is a
# workflow or an agent; the agent has an `execution_shape` and may have armed
# triggers. The launch door must answer ONE question — "can this executable be
# evaluated the way this dataset is authored?" — from EXPLICIT facts, never by
# sniffing item keys or by priority fallthrough.
#
# E-3's load-bearing change: mode is NOT a pure function of the executable. An
# agent with BOTH a manual and a schedule trigger is legitimately evaluable
# BOTH ways (`durable` on its manual shape, `scheduled` on its job spec), so the
# pre-E-3 `resolved_mode != dataset.mode → 422` EQUALITY rule — which read only
# `Agent.execution_shape` and could therefore never yield 'scheduled' — rejected
# every scheduled dataset at launch. The dataset DECLARES the intent; the
# executable only has to be COMPATIBLE with it.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _ExecutableFacts:
    """The eval-relevant facts about the executable under evaluation.

    Read ONCE at the launch door and passed explicitly to both
    `_resolve_eval_mode` (the executable's natural/default eval mode, used for
    the diagnostic) and `_assert_mode_compatible` (the guard) — so neither
    re-queries and neither infers a fact the other already established.
    """

    is_workflow: bool
    execution_shape: Optional[str]
    has_schedule_trigger: bool
    has_webhook_trigger: bool


async def _load_executable_facts(
    agent_name: Optional[str],
    workflow_id: Optional[uuid.UUID],
    db: AsyncSession,
) -> _ExecutableFacts:
    """Read the executable's eval-relevant facts (one pass, no inference)."""
    if workflow_id is not None:
        return _ExecutableFacts(
            is_workflow=True,
            execution_shape=None,
            has_schedule_trigger=False,
            has_webhook_trigger=False,
        )

    agent_result = await db.execute(
        select(Agent.id, Agent.execution_shape).where(Agent.name == agent_name)
    )
    agent_row = agent_result.one_or_none()
    if agent_row is None:
        return _ExecutableFacts(
            is_workflow=False,
            execution_shape=None,
            has_schedule_trigger=False,
            has_webhook_trigger=False,
        )
    agent_id, execution_shape = agent_row

    # An ENABLED trigger is the fact that makes an agent evaluable on that
    # family — a disabled trigger fires nothing in production, so evaluating
    # against it would score a path the agent does not actually take.
    trig_result = await db.execute(
        select(AgentTrigger.trigger_type)
        .where(AgentTrigger.agent_id == agent_id)
        .where(AgentTrigger.enabled.is_(True))
    )
    trigger_types = set(trig_result.scalars().all())
    return _ExecutableFacts(
        is_workflow=False,
        execution_shape=execution_shape,
        has_schedule_trigger="schedule" in trigger_types,
        has_webhook_trigger="webhook" in trigger_types,
    )


def _resolve_eval_mode(facts: _ExecutableFacts) -> str:
    """The executable's NATURAL eval mode — what it evaluates as by default.

    A CompositeWorkflow executable → 'workflow'; an agent with an armed schedule
    trigger → 'scheduled' (its production entrypoint is the job spec, so that is
    what it naturally evaluates as — E-3); an agent with an armed webhook trigger
    → 'webhook' (its production entrypoint is the event it filters, so that is
    what it naturally evaluates as — E-4); otherwise its `execution_shape`
    ('reactive' | 'durable'), defaulting to 'reactive' when unresolvable
    (back-compat).

    This is a DIAGNOSTIC/default reader, not the guard: it names the mode the
    executable resolves to in the 422 raised by `_assert_mode_compatible`. The
    scoring mode is always the DATASET's authored `mode` (E-0) — an executable
    may be compatible with several.

    The schedule-before-webhook ORDER is therefore a diagnostic-only choice (which
    mode the 422 text names first). It gates nothing: `_assert_mode_compatible`
    reads the facts INDEPENDENTLY, so an agent carrying BOTH an armed schedule and
    an armed webhook trigger stays legitimately evaluable BOTH ways.
    """
    if facts.is_workflow:
        return "workflow"
    if facts.has_schedule_trigger:
        return "scheduled"
    if facts.has_webhook_trigger:
        return "webhook"
    if facts.execution_shape in ("reactive", "durable"):
        return facts.execution_shape
    return "reactive"


def _assert_mode_compatible(dataset_mode: str, dataset_name: str, facts: _ExecutableFacts) -> None:
    """Raise 422 unless the executable can be evaluated as `dataset_mode`.

    One explicit rule per mode — no equality shortcut, no key-sniffing, no
    priority fallthrough:

      - **reactive** — always compatible. ANY executable (reactive, durable, or a
        workflow) has a final response to score reactively; this is the pre-E-0
        behavior and gating it broke durable/workflow evals against backfilled
        reactive datasets. Unchanged.
      - **durable** — requires `execution_shape == 'durable'`: the items need a
        real `run_steps` trajectory, which only a durable run produces. Unchanged.
      - **scheduled** — requires an armed (enabled) `schedule` trigger on the
        agent. The dataset's `job_spec` is the shape of THAT trigger's
        `input_payload`; with no schedule armed there is no job-spec entrypoint to
        evaluate, so scoring one would be fiction. The inner shape
        (reactive/durable) is deliberately NOT constrained — E-3 scores both.
      - **workflow** — requires a workflow executable (run-tree/member-path items).
      - **webhook** — requires an armed (enabled) `webhook` trigger on the agent.
        The dataset's `trigger_payload` is fired at THAT trigger's real
        `filter_conditions` through the real `test-event` door; with no webhook
        armed there is no filter to decide anything, so scoring a filter decision
        would be fiction. Like scheduled, the inner shape (reactive/durable) is
        deliberately NOT constrained — E-4 scores both.
    """
    resolved = _resolve_eval_mode(facts)

    def _reject(reason: str) -> None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"Eval mode mismatch: dataset '{dataset_name}' is authored as mode "
                f"'{dataset_mode}' but the executable resolves to mode '{resolved}' "
                f"— {reason}"
            ),
        )

    if dataset_mode == "reactive":
        return
    if dataset_mode == "durable":
        if facts.is_workflow or facts.execution_shape != "durable":
            _reject(
                "a 'durable' dataset scores a run's real run_steps trajectory, so it "
                "requires an agent with execution_shape='durable'"
            )
        return
    if dataset_mode == "scheduled":
        if facts.is_workflow:
            _reject(
                "a 'scheduled' dataset evaluates an agent's job-spec schedule; "
                "workflow-level schedule eval is not supported"
            )
        if not facts.has_schedule_trigger:
            _reject(
                "a 'scheduled' dataset feeds its job_spec to the agent's schedule "
                "entrypoint — arm a schedule trigger on this agent first"
            )
        return
    if dataset_mode == "workflow":
        if not facts.is_workflow:
            _reject("a 'workflow' dataset requires a workflow executable")
        return
    if dataset_mode == "webhook":
        if facts.is_workflow:
            _reject(
                "a 'webhook' dataset evaluates an agent's webhook filter decision; "
                "workflow-level webhook eval is not supported"
            )
        if not facts.has_webhook_trigger:
            _reject(
                "a 'webhook' dataset fires its trigger_payload at the agent's webhook "
                "filter — arm a webhook trigger on this agent first"
            )
        return
    # An unknown mode is unrepresentable: `DatasetMode` + the DB CHECK on
    # playground_datasets.mode constrain it to the five above. Fail closed.
    _reject(f"unknown dataset mode '{dataset_mode}'")


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

    # Eval v2 E-0/E-3: read the executable's eval-relevant facts ONCE, then assert
    # the DATASET's authored mode is compatible with them. `_resolve_eval_mode`
    # names the executable's natural mode (workflow → 'workflow'; an armed
    # schedule trigger → 'scheduled' — E-3; else `execution_shape`) for the
    # diagnostic; `_assert_mode_compatible` holds one explicit rule per mode.
    # Scoring still follows the dataset's `mode` (E-0) — an executable can be
    # compatible with more than one (a durable agent with a schedule armed is
    # legitimately evaluable both `durable` and `scheduled`).
    facts = await _load_executable_facts(agent_name, workflow_id, db)
    _assert_mode_compatible(dataset.mode, dataset.name, facts)

    eval_run = EvalRun(
        user_id=caller,
        agent_name=agent_name,
        agent_version_id=version_id,
        workflow_id=workflow_id,
        workflow_version_id=wf_version_id,
        dataset_id=body.dataset_id,
        sandbox_deployment_id=body.sandbox_deployment_id,
        workflow_deployment_id=body.workflow_deployment_id,
        # The SCORING mode = the dataset's authoring mode — it is the dataset that
        # declares which per-item schema/scorer branch applies. The executable's
        # natural mode (`_resolve_eval_mode`) only feeds the compatibility guard's
        # diagnostic above; it is never the scorer selector.
        mode=dataset.mode,
        # Eval v2 E-6: the PASS POLICY, written HERE and only here.
        #
        # E-0 added `pass_threshold`/`dimension_weights` with a forward promise and
        # neither a writer nor a reader — the column was NULL in every row ever
        # written, which made every downstream `if run.pass_threshold is not None`
        # a dead branch. The platform default is applied at this SINGLE write site,
        # so the column is NEVER NULL on a new row and the gate / the runner's
        # per-item verdict / the UI all read one resolved number instead of each
        # re-declaring 0.7 (which is exactly how four copies of the threshold came
        # to exist across three services).
        pass_threshold=(
            body.pass_threshold
            if body.pass_threshold is not None
            else EVAL_PASS_THRESHOLD
        ),
        # NULL stays meaningful: "use the scorer branch's default weights".
        dimension_weights=body.dimension_weights,
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
    # NOT the executable's natural mode. MODE is unambiguously the scorer selector;
    # the runner requests the RUN shape it needs explicitly (durable via
    # `execution_shape`; scheduled via job_spec → input_payload + trigger_type).
    # E-3: a 'scheduled' dataset now reaches here (the compatibility guard admits it
    # once a schedule trigger is armed) ⇒ MODE=scheduled reaches the runner.
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
    return eval_run_response(eval_run)


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
    return [eval_run_response(r) for r in result.scalars().all()]


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
    return eval_run_response(run)


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
    # Eval v2 E-6: resolve the run's pass policy ONCE, here, and use it for BOTH
    # gate arms and BOTH log lines below.
    #
    # Two independent `if run.pass_threshold is not None` expressions would
    # re-create — inside a single function — the very copy problem E-6 exists to
    # kill (the publish threshold had been re-declared four times across three
    # services, all defaulting to 0.7, so they agreed and nothing ever errored).
    effective_threshold = effective_pass_threshold(run)
    # Auto-promote: if this run completed with a passing score, mark the
    # associated AgentVersion as eval_passed=True so the publish gate opens
    # without requiring a manual PATCH.
    if (
        body.status == "completed"
        and run.overall_score is not None
        and run.overall_score >= effective_threshold
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
                version.id, run.overall_score, effective_threshold,
            )
    # Auto-promote workflow version
    if (
        body.status == "completed"
        and run.overall_score is not None
        and run.overall_score >= effective_threshold
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
                wf_version.id, run.overall_score, effective_threshold,
            )
    await db.flush()
    logger.info(
        "update_eval_run: id=%s status=%s score=%s",
        run.id, run.status, run.overall_score,
    )
    return eval_run_response(run)


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
