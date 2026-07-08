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
import time
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from models import Agent, AgentRun, AgentTrigger, CompositeWorkflow, Deployment
from schemas import AgentRunResponse, InternalRunStartRequest
from workflow_orchestrator import orchestrate, resolve_member_names

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/internal", tags=["internal"])


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


def _team_namespace(team: str) -> str:
    return f"agents-{team.lower().replace(' ', '-')}"


async def _dispatch_and_complete(
    run_id: str,
    agent_name: str,
    team: str,
    message: str,
    trigger_id=None,
) -> None:
    """Dispatch the run to the agent's production pod and record the outcome."""
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
            run.output = (output[:4000] if output else None)
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
    asyncio.create_task(orchestrate(str(run.id), wf.team, str(wf.id), message, wf.orchestration))
    logger.info("start_internal_run: WORKFLOW run_id=%s workflow=%s mode=%s members=%d trace=%s",
                run.id, wf.name, wf.orchestration, len(member_names), trace_id)
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
            str(run.id), body.agent_name, agent.team, message, body.trigger_id
        )
    )

    logger.info(
        "start_internal_run: run_id=%s agent=%s trigger=%s by=%s",
        run.id, body.agent_name, body.trigger_type, body.run_by,
    )
    return run
