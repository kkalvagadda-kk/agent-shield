"""
Internal run-start router — cluster-internal only (no public ingress).

Called by the scheduler service (cron fires) and the event gateway (webhooks)
to start a triggered agent run. Creates an agent_run row and dispatches the
input to the agent's deployed pod, then records completion.

  POST /api/v1/internal/runs/start
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from models import Agent, AgentRun, AgentTrigger, CompositeWorkflow, Deployment
from schemas import AgentRunResponse, InternalRunStartRequest
from workflow_orchestrator import dispatch_to_orchestrator_pod, orchestrate, resolve_member_names

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _team_namespace(team: str) -> str:
    return f"agents-{team.lower().replace(' ', '-')}"


async def _mark_agent_run_failed(
    run_id: str, error_message: str | None, agent_name: str, trigger_id=None
) -> None:
    """Mark an AgentRun failed + fire the failure alert. The durable-dispatch
    fail-closed path uses this so a runner that can't be reached fails loud, never hangs."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one_or_none()
        if run:
            run.status = "failed"
            run.error_message = error_message
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()
        try:
            from alerting import dispatch_failure_alert

            await dispatch_failure_alert(
                session,
                trigger_id=trigger_id,
                agent_name=agent_name,
                run_id=run_id,
                error_message=error_message,
            )
        except Exception as exc:  # alerting must never break run recording
            logger.error("failure-alert dispatch errored for run %s: %s", run_id, exc)


async def _dispatch_and_complete(
    run_id: str,
    agent_name: str,
    team: str,
    message: str,
    execution_shape: str,
    input_payload: dict | None,
    trigger_id=None,
) -> None:
    """Shape-aware production dispatch (WS-0 parity core).

    durable → the shared ``durable_dispatch.dispatch_durable_run`` (declarative-runner
      /run); step progress + terminal status arrive asynchronously at the internal
      step-update callback. A dispatch failure fails-closed (mark failed + alert).
    reactive → the existing synchronous /chat path, recording completion inline.

    The /run POST lives ONLY in durable_dispatch (same helper the sandbox path calls) —
    no scheduled/production copy to drift."""
    if execution_shape == "durable":
        from durable_dispatch import dispatch_durable_run, registry_internal_base

        callback = f"{registry_internal_base()}/api/v1/internal/runs/{run_id}/step-update"
        ok, err = await dispatch_durable_run(
            run_id=run_id,
            agent_name=agent_name,
            input_payload=input_payload,
            callback_url=callback,
        )
        if not ok:
            await _mark_agent_run_failed(run_id, err, agent_name, trigger_id)
        # durable success: the run stays 'running'; the step-update callback completes it.
        logger.info("internal run %s dispatched durable (accepted=%s)", run_id, ok)
        return

    # reactive: existing synchronous /chat path (unchanged).
    ns = _team_namespace(team)
    # Agent Service is named "{agent_name}-{environment}" on port 8080 (see
    # deploy-controller manifest_builder.build_service). Scheduled/event runs
    # target the production environment.
    url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080/chat"
    start = time.perf_counter()
    status_val, output, err = "completed", None, None
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(url, json={"message": message})
        if resp.status_code == 200:
            data = resp.json()
            output = data.get("output") or data.get("response") or json.dumps(data)
        else:
            status_val, err = "failed", f"agent returned {resp.status_code}: {resp.text[:300]}"
    except Exception as exc:  # network / pod not ready
        status_val, err = "failed", f"dispatch failed: {exc}"

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AgentRun).where(AgentRun.id == run_id))
        run = result.scalar_one_or_none()
        if run:
            run.status = status_val
            _out = _as_text(output)
            run.output = (_out[:4000] if _out else None)
            run.error_message = err
            run.latency_ms = elapsed_ms
            run.completed_at = datetime.now(timezone.utc)
            await session.commit()
        # Failure alerting (Phase 8): notify the trigger's alert_email.
        if status_val == "failed":
            try:
                from alerting import dispatch_failure_alert

                await dispatch_failure_alert(
                    session,
                    trigger_id=trigger_id,
                    agent_name=agent_name,
                    run_id=run_id,
                    error_message=err,
                )
            except Exception as exc:  # alerting must never break run recording
                logger.error("failure-alert dispatch errored for run %s: %s", run_id, exc)
    logger.info("internal run %s finished status=%s latency=%dms", run_id, status_val, elapsed_ms)


async def _start_workflow_run(body: InternalRunStartRequest, db: AsyncSession) -> AgentRun:
    """Start a composite-workflow run: create the parent AgentRun + orchestrate
    member agents in a background task (Decision 22)."""
    wf = (await db.execute(
        select(CompositeWorkflow).where(CompositeWorkflow.id == body.workflow_id)
    )).scalar_one_or_none()
    if wf is None:
        raise HTTPException(status_code=404, detail=f"Workflow '{body.workflow_id}' not found.")
    if wf.status == "archived":
        raise HTTPException(status_code=422, detail="Cannot run an archived workflow.")
    member_names = await resolve_member_names(db, wf.id)
    if not member_names:
        raise HTTPException(status_code=422, detail="Workflow has no members to run.")

    # Resolve the run input. The scheduler fires with only a trigger_id (no
    # payload), so for schedule triggers we pull the per-trigger `input_payload`
    # — the reusable "job spec" that parameterizes this workflow's scheduled job.
    # The webhook path already sends the event body as trigger_payload.
    effective_payload = body.trigger_payload
    if effective_payload is None and body.trigger_id is not None:
        trig = (await db.execute(
            select(AgentTrigger).where(AgentTrigger.id == body.trigger_id)
        )).scalar_one_or_none()
        if trig is not None and trig.input_payload:
            effective_payload = trig.input_payload

    message = ""
    if effective_payload:
        message = effective_payload.get("message") or json.dumps(effective_payload)

    run = AgentRun(
        agent_name=wf.name,
        input=message[:4000] if message else None,
        context="production",
        status="queued",
        trigger_type=body.trigger_type,
        trigger_payload=effective_payload,
        run_by=body.run_by,
        team=wf.team,
        workflow_id=wf.id,
    )
    db.add(run)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(run.id),
        agent_name=wf.name,
        user_id=body.run_by or "system",
        context="production",
        input_message=message[:4000] if message else "",
    )
    if trace_id:
        run.langfuse_trace_id = trace_id

    await db.commit()
    await db.refresh(run)

    import asyncio

    # M6/D2: reactive workflow = synchronous, capped, no durable park. Run the
    # orchestrator in-request (skip the orchestrator pod + checkpoint), hold the
    # caller's connection under a hard wall-clock cap. Durable = the background path below.
    if wf.execution_shape == "reactive":
        reactive_timeout_s = float(os.getenv("WORKFLOW_REACTIVE_TIMEOUT_S", "120"))
        try:
            await asyncio.wait_for(
                orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration, shape="reactive"),
                timeout=reactive_timeout_s,
            )
        except asyncio.TimeoutError:
            from workflow_orchestrator import _fail_parent

            await _fail_parent(
                str(run.id),
                f"reactive workflow exceeded {reactive_timeout_s:.0f}s wall-clock cap",
            )
        await db.refresh(run)
        logger.info("start_internal_run: WORKFLOW(reactive) run_id=%s workflow=%s mode=%s members=%d",
                    run.id, wf.name, wf.orchestration, len(member_names))
        return run

    # durable: existing background path (orchestrator pod if deployed, else in-process task).
    from models import PublishedArtifact, ProductionDeployment, WorkflowMember

    prod_art = (await db.execute(
        select(PublishedArtifact).where(
            PublishedArtifact.source_id == wf.id,
            PublishedArtifact.type == "workflow",
        )
    )).scalar_one_or_none()
    dispatched = False
    if prod_art:
        prod_dep = (await db.execute(
            select(ProductionDeployment).where(
                ProductionDeployment.artifact_id == prod_art.id,
                ProductionDeployment.status == "running",
            )
        )).scalar_one_or_none()
        if prod_dep:
            wf_members = (await db.execute(
                select(WorkflowMember, Agent.name)
                .join(Agent, Agent.id == WorkflowMember.agent_id)
                .where(WorkflowMember.workflow_id == wf.id)
                .order_by(WorkflowMember.position.nulls_last())
            )).all()
            members_data = [
                {"agent_name": aname, "team": wf.team, "position": m.position}
                for (m, aname) in wf_members
            ]
            dispatched = await dispatch_to_orchestrator_pod(
                wf.name, wf.team, str(run.id), members_data, {"message": message}
            )

    if not dispatched:
        asyncio.create_task(orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration, shape="durable"))

    logger.info("start_internal_run: WORKFLOW run_id=%s workflow=%s mode=%s members=%d prod_pod=%s trace=%s",
                run.id, wf.name, wf.orchestration, len(member_names), dispatched, trace_id)
    return run


@router.post(
    "/runs/start",
    response_model=AgentRunResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Start a triggered agent run (cluster-internal)",
)
async def start_internal_run(
    body: InternalRunStartRequest,
    db: AsyncSession = Depends(_get_db),
) -> AgentRun:
    # Composite-workflow target (Decision 22): create a parent run + orchestrate.
    if body.workflow_id is not None:
        return await _start_workflow_run(body, db)

    # Resolve the agent + require a running production deployment.
    result = await db.execute(select(Agent).where(Agent.name == body.agent_name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{body.agent_name}' not found.")

    # Deployment has no agent_name/created_at columns — resolve via agent_id and
    # order by deployed_at (fixes a latent Phase 7 bug that errored on every
    # internal dispatch; only surfaced now that the event-gateway exercises it).
    dep_result = await db.execute(
        select(Deployment)
        .where(Deployment.agent_id == agent.id, Deployment.status == "running")
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    if not dep_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Agent '{body.agent_name}' has no running deployment to dispatch to.",
        )

    # Resolve the run input. The scheduler fires with only a trigger_id (no
    # payload), so for schedule triggers we pull the per-trigger `input_payload`
    # — the reusable "job spec" that parameterizes this agent's scheduled job.
    # The webhook path already sends the event body as trigger_payload.
    effective_payload = body.trigger_payload
    if effective_payload is None and body.trigger_id is not None:
        trig = (await db.execute(
            select(AgentTrigger).where(AgentTrigger.id == body.trigger_id)
        )).scalar_one_or_none()
        if trig is not None and trig.input_payload:
            effective_payload = trig.input_payload

    message = ""
    if effective_payload:
        message = effective_payload.get("message") or json.dumps(effective_payload)

    run = AgentRun(
        agent_name=body.agent_name,
        input=message[:4000] if message else None,
        context="production",
        status="running",
        trigger_type=body.trigger_type,
        trigger_payload=effective_payload,
        run_by=body.run_by,
        team=agent.team,
    )
    db.add(run)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(run.id),
        agent_name=body.agent_name,
        user_id=body.run_by or "system",
        context="production",
        input_message=message[:4000] if message else "",
    )
    if trace_id:
        run.langfuse_trace_id = trace_id

    await db.commit()
    await db.refresh(run)

    # Fire-and-forget dispatch; completion is recorded by _dispatch_and_complete.
    import asyncio
    asyncio.create_task(
        _dispatch_and_complete(
            str(run.id), body.agent_name, agent.team, message,
            agent.execution_shape, effective_payload, body.trigger_id,
        )
    )

    logger.info(
        "start_internal_run: run_id=%s agent=%s shape=%s trigger=%s by=%s",
        run.id, body.agent_name, agent.execution_shape, body.trigger_type, body.run_by,
    )
    return run


def _as_text(value: object) -> str | None:
    """Coerce a callback output field to plain text before it hits a text column.

    Defense-in-depth: the SDK normalizes message content to a string, but a callback
    from an older agent image (or a provider returning content blocks) can send a
    list like ``[{"type":"text","text":"refund"}]``. Writing that to a text column
    raises asyncpg DataError → 500 → the run fails at the callback. Join text blocks
    instead of trusting the wire type.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [
            b["text"] if isinstance(b, dict) and isinstance(b.get("text"), str)
            else b if isinstance(b, str) else ""
            for b in value
        ]
        joined = "".join(parts)
        return joined or None
    return str(value)


@router.post(
    "/runs/{run_id}/step-update",
    status_code=status.HTTP_200_OK,
    summary="Production durable-run step-update callback",
)
async def internal_step_update(
    run_id: str,
    body: dict,
    db: AsyncSession = Depends(_get_db),
) -> dict[str, str]:
    """Production twin of the playground step-update callback (parity — same wire shape,
    just targets AgentRun + its RunStep rows). The declarative-runner posts one per
    node/tool boundary plus a terminal one; this writes RunStep rows and completes the
    AgentRun on the terminal step. WS-1 extends this SAME callback with real per-node
    steps + HITL-park emit — WS-0 only needs the branch wired so run_steps appear for a
    production durable run."""
    from sqlalchemy import and_

    from models import RunStep

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    run = (await db.execute(select(AgentRun).where(AgentRun.id == parsed_id))).scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    step_number = body.get("step_number", 1)
    step_name = body.get("step_name", f"step-{step_number}")
    step_status = body.get("status", "running")
    approval_id = uuid.UUID(body["approval_id"]) if body.get("approval_id") else None
    now = datetime.now(timezone.utc)

    step = (await db.execute(
        select(RunStep).where(and_(RunStep.run_id == parsed_id, RunStep.step_number == step_number))
    )).scalar_one_or_none()
    if step:
        step.status = step_status
        step.name = step_name
        if step_status in ("completed", "failed"):
            step.completed_at = now
        if body.get("output"):
            step.output = _as_text(body["output"])
        if body.get("error_message"):
            step.error_message = body["error_message"]
        if approval_id:
            step.approval_id = approval_id
    else:
        db.add(RunStep(
            run_id=parsed_id,
            step_number=step_number,
            name=step_name,
            status=step_status,
            started_at=now if step_status == "running" else None,
            completed_at=now if step_status in ("completed", "failed") else None,
            output=_as_text(body.get("output")),
            error_message=body.get("error_message"),
            approval_id=approval_id,
        ))

    if step_status == "awaiting_approval":
        run.status = "awaiting_approval"
    elif body.get("run_completed"):
        run.status = step_status
        run.completed_at = now
        if body.get("output_text"):
            _ot = _as_text(body["output_text"])
            run.output = _ot[:4000] if _ot else None
        # Propagate the failing step's error onto the run itself. Without this the
        # run showed status='failed' with an EMPTY error_message — the real reason
        # (e.g. a tool 503) lived only on the step row, so a workflow parent could
        # only report a bare generic failure (docs/debugging/011, issue #2).
        if step_status == "failed" and body.get("error_message"):
            run.error_message = str(body["error_message"])[:2000]

    await db.commit()
    return {"status": "ok"}
