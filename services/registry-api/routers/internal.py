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
from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from embedding_client import embed
from identity import (
    PrincipalResolutionError,
    resolve_principal,
    resolve_workflow_principal,
)
from models import (
    Agent,
    AgentKnowledgeBinding,
    AgentRun,
    AgentTrigger,
    CompositeWorkflow,
    Deployment,
    KnowledgeBase,
    KnowledgeSource,
)
from schemas import (
    AgentRunResponse,
    InternalRunStartRequest,
    KnowledgeCitation,
    KnowledgeSearchChunk,
    KnowledgeSearchResult,
    SearchRequest,
)
from store_factory import get_vector_store
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
        # Target the agent's OWN pod (same as the reactive branch below and the
        # playground/workflow-member callers), NOT dispatch_durable_run's default
        # shared declarative-runner Service — that Service does not exist for SDK/
        # declarative agent pods, so omitting runner_url DNS-fails and the run never
        # reaches the pod. Scheduled/event trigger runs target the production env.
        ns = _team_namespace(team)
        runner_url = f"http://{agent_name}-production.{ns}.svc.cluster.local:8080"
        ok, err = await dispatch_durable_run(
            run_id=run_id,
            agent_name=agent_name,
            input_payload=input_payload,
            callback_url=callback,
            runner_url=runner_url,
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

    # Load the trigger once (if any) — reused for the job-spec payload AND the identity
    # decision below (mirrors the agent path in start_internal_run). The scheduler fires
    # with only a trigger_id (no payload), so for schedule triggers we pull the per-trigger
    # `input_payload` — the reusable "job spec". The webhook path already sends the event
    # body as trigger_payload.
    trig = None
    if body.trigger_id is not None:
        trig = (await db.execute(
            select(AgentTrigger).where(AgentTrigger.id == body.trigger_id)
        )).scalar_one_or_none()

    effective_payload = body.trigger_payload
    if effective_payload is None and trig is not None and trig.input_payload:
        effective_payload = trig.input_payload

    message = ""
    if effective_payload:
        message = effective_payload.get("message") or json.dumps(effective_payload)

    # Resolve the acting principal for the workflow run (WS-2 T016 / D1) — the ONE identity
    # decision, shared with the agent path but sourcing the DAEMON service subject by the
    # workflow convention (workflows have no `agent_identities` row). No JWT caller on a
    # trigger-driven run → caller=None (never sniff agent_class): a daemon workflow runs
    # under the WORKFLOW's service identity (user_id empty — no live human); a user_delegated
    # workflow runs under the arming human (trigger.armed_by). This overrides the generic
    # transport `body.run_by` (e.g. "serviceaccount:scheduler") the dispatcher supplied.
    # Members INHERIT this run_by via workflow_orchestrator._run_step (child.run_by =
    # parent.run_by) — that inheritance IS the D1 actor_chain at the audit/run-tree layer;
    # propagating the workflow's class onto each MEMBER POD's OPA input is the deferred
    # identity-propagation initiative (the member pod builds its own OPA input from its env).
    # An interactive builder test-run keeps the caller via composite_workflows.start_workflow_run.
    try:
        principal = await resolve_workflow_principal(wf, caller=None, trigger=trig, db=db)
    except PrincipalResolutionError as exc:
        failed = AgentRun(
            agent_name=wf.name,
            input=message[:4000] if message else None,
            context="production",
            status="failed",
            trigger_type=body.trigger_type,
            trigger_payload=effective_payload,
            run_by=body.run_by,
            team=wf.team,
            workflow_id=wf.id,
            trigger_id=body.trigger_id,
            error_message=f"identity resolution failed (fail-closed): {exc}",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(failed)
        await db.commit()
        await db.refresh(failed)
        try:
            from alerting import dispatch_failure_alert

            await dispatch_failure_alert(
                db,
                trigger_id=body.trigger_id,
                agent_name=wf.name,
                run_id=str(failed.id),
                error_message=failed.error_message,
            )
        except Exception as alert_exc:  # alerting must never break run recording
            logger.error("failure-alert dispatch errored for denied workflow run %s: %s",
                         failed.id, alert_exc)
        logger.warning(
            "start_internal_run: WORKFLOW DENY (fail-closed) run=%s workflow=%s trigger=%s reason=%s",
            failed.id, wf.name, body.trigger_type, exc,
        )
        return failed

    run = AgentRun(
        agent_name=wf.name,
        input=message[:4000] if message else None,
        context="production",
        status="queued",
        trigger_type=body.trigger_type,
        trigger_payload=effective_payload,
        # Daemon workflow → the workflow's SERVICE identity subject; user_delegated → the
        # arming human. Members inherit this in _run_step (D1 actor_chain).
        run_by=principal.run_by,
        # POC-3: the live human's sub (EMPTY for a daemon) — the preference discriminator
        # a reactive member reads in _run_step_stream. Daemon (user_id=="") ⇒ no directive.
        user_id=principal.user_id,
        team=wf.team,
        workflow_id=wf.id,
        # Link the parent workflow run to its trigger so a daemon workflow member's
        # parked approval can resolve armed_by + reviewer-role config at read time
        # (WS-2 T011 — member walks parent_run_id → this parent → trigger_id).
        trigger_id=body.trigger_id,
    )
    db.add(run)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(run.id),
        agent_name=wf.name,
        user_id=principal.run_by or "system",
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

    # Load the trigger once (if any) — reused for both the job-spec payload and
    # the identity decision below. The webhook path sends the event body as
    # trigger_payload; the scheduler fires with only a trigger_id (no payload), so
    # for those we pull the per-trigger `input_payload` (the reusable "job spec").
    trig = None
    if body.trigger_id is not None:
        trig = (await db.execute(
            select(AgentTrigger).where(AgentTrigger.id == body.trigger_id)
        )).scalar_one_or_none()

    effective_payload = body.trigger_payload
    if effective_payload is None and trig is not None and trig.input_payload:
        effective_payload = trig.input_payload

    message = ""
    if effective_payload:
        message = effective_payload.get("message") or json.dumps(effective_payload)

    # Resolve the acting principal — the ONE identity decision (WS-2 R3), shared
    # with the interactive `/chat` path. No JWT caller on a trigger-driven run, so
    # pass caller=None explicitly (never sniff agent_class): a daemon runs under its
    # SERVICE identity (user_id empty — no live human); a user_delegated run runs
    # under the arming human (trigger.armed_by). resolve_principal RAISES for a
    # user_delegated trigger with no armer, or a daemon with no service identity —
    # we FAIL CLOSED (record a failed run, never dispatch, never downgrade to
    # service). The resolved principal feeds the OPA `user_identity_ok` floor:
    # `agent_class` reaches the pod's OPA input via the deploy-time env
    # (AGENTSHIELD_AGENT_CLASS) and `user_id` = principal.user_id (empty for a
    # daemon trigger-run). Threading principal.user_id/trigger_type onto the pod's
    # OPA input for the trigger dispatch is the identity-propagation initiative
    # (the durable /run + reactive /chat runner paths set no OPA user_context yet).
    try:
        principal = await resolve_principal(agent, caller=None, trigger=trig, db=db)
    except PrincipalResolutionError as exc:
        failed = AgentRun(
            agent_name=body.agent_name,
            input=message[:4000] if message else None,
            context="production",
            status="failed",
            trigger_type=body.trigger_type,
            trigger_payload=effective_payload,
            run_by=body.run_by,
            team=agent.team,
            error_message=f"identity resolution failed (fail-closed): {exc}",
            completed_at=datetime.now(timezone.utc),
        )
        db.add(failed)
        await db.commit()
        await db.refresh(failed)
        try:
            from alerting import dispatch_failure_alert

            await dispatch_failure_alert(
                db,
                trigger_id=body.trigger_id,
                agent_name=body.agent_name,
                run_id=str(failed.id),
                error_message=failed.error_message,
            )
        except Exception as alert_exc:  # alerting must never break run recording
            logger.error("failure-alert dispatch errored for denied run %s: %s", failed.id, alert_exc)
        logger.warning(
            "start_internal_run: DENY (fail-closed) run=%s agent=%s trigger=%s reason=%s",
            failed.id, body.agent_name, body.trigger_type, exc,
        )
        return failed

    run = AgentRun(
        agent_name=body.agent_name,
        input=message[:4000] if message else None,
        context="production",
        status="running",
        trigger_type=body.trigger_type,
        trigger_payload=effective_payload,
        run_by=principal.run_by,
        team=agent.team,
        # Link the run to the trigger that fired it. This is the read-time source of
        # `armed_by` + the daemon reviewer-role config for an approval's audit display
        # / reviewer routing (WS-2 T011, approvals.py `_derive_reviewer_audit`).
        trigger_id=body.trigger_id,
    )
    db.add(run)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=str(run.id),
        agent_name=body.agent_name,
        user_id=principal.run_by or "system",
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
        "start_internal_run: run_id=%s agent=%s class=%s shape=%s trigger=%s run_by=%s service=%s",
        run.id, body.agent_name, principal.agent_class, agent.execution_shape,
        body.trigger_type, principal.run_by, principal.is_service,
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

    # `run_steps.output` is a JSONB **dict** column (models.py RunStep.output:
    # Mapped[dict|None]) and RunStepResponse types it `dict[str, Any] | None`.
    # It is NOT a text column — do NOT run it through `_as_text` (that helper is for
    # the genuine text columns: AgentRun.output and output_text, below). Coercing the
    # dict to text stored a Python-repr string ("{'tool': 'get_weather', ...}"), which
    # made GET /agent-runs/{id}/steps fail response validation with a 500 and silently
    # emptied the Eval v2 per-member trajectory (the eval-runner swallows the error).
    # Accept only a dict, so the dict column can never hold a non-dict (illegal state
    # unrepresentable) — mirrors the playground step-update writer.
    _raw_out = body.get("output")
    step_out = _raw_out if isinstance(_raw_out, dict) else None

    step = (await db.execute(
        select(RunStep).where(and_(RunStep.run_id == parsed_id, RunStep.step_number == step_number))
    )).scalar_one_or_none()
    if step:
        step.status = step_status
        step.name = step_name
        if step_status in ("completed", "failed"):
            step.completed_at = now
        if step_out is not None:
            step.output = step_out
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
            output=step_out,
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


@router.post(
    "/knowledge/search",
    response_model=KnowledgeSearchResult,
    summary="Knowledge search backend (cluster-internal; the knowledge_search HTTP tool)",
)
async def internal_knowledge_search(
    body: SearchRequest,
    x_agent_team: str | None = Header(None, alias="X-Agent-Team"),
    x_agent_name: str | None = Header(None, alias="X-Agent-Name"),
    db: AsyncSession = Depends(_get_db),
) -> KnowledgeSearchResult:
    """The backend of the `knowledge_search` HTTP tool (contracts/endpoints.md).

    Identity is read ONLY from the headers the tool sets server-side from the pod's
    env (`X-Agent-Team` = AGENTSHIELD_AGENT_TEAM, `X-Agent-Name` = AGENT_NAME) — the
    model cannot set them. Tenancy is structural, not a guard bolted on:

      * Either header missing → 422 (never default the team).
      * `kb_id` is resolved SERVER-SIDE from `agent_knowledge_bindings` by
        (agent_name, team) — never from the request body/model.
      * An unbound agent, OR a header team that doesn't match the binding's team,
        finds no binding row → `{chunks:[], citations:[]}` (fail-closed, no widening).
      * `VectorStore.search` then RE-enforces (team, kb_id) as required predicates.
    """
    # Fail-closed: never default the team. A missing identity header is a 422, not
    # a broad search.
    if not x_agent_team or not x_agent_name:
        raise HTTPException(
            status_code=422,
            detail="X-Agent-Team and X-Agent-Name headers are required",
        )

    team = x_agent_team
    agent_name = x_agent_name

    # Resolve ALL the agent's bound KBs server-side (an agent may be bound to one OR
    # MORE). The JOIN filters on b.team = :team, so a mismatched team header (or an
    # unbound agent) yields no bindings → empty. Isolation stays structural: each KB is
    # searched via a SEPARATE VectorStore.search(team, kb_id) call — the S5 atom — and
    # results are merged here. The store never sees a multi-KB query, so any adapter
    # behind the VectorStore port works unchanged.
    kb_ids = (
        await db.execute(
            select(AgentKnowledgeBinding.kb_id)
            .join(Agent, Agent.id == AgentKnowledgeBinding.agent_id)
            .where(Agent.name == agent_name, AgentKnowledgeBinding.team == team)
        )
    ).scalars().all()
    if not kb_ids:
        logger.info(
            "internal_knowledge_search: no binding for agent=%s team=%s — fail-closed empty",
            agent_name, team,
        )
        return KnowledgeSearchResult(chunks=[], citations=[], note="no knowledge base attached")

    query = (body.query or "").strip()
    if not query:
        return KnowledgeSearchResult(chunks=[], citations=[])

    q_vec = (await embed([query]))[0]
    store = get_vector_store()
    # Fan out across the agent's KBs (each call single-KB, isolation-scoped), then merge
    # by score and keep the global top-k. Each hit is tagged with its kb_id for citations.
    scored: list = []  # list[tuple[uuid.UUID, SearchHit]]
    for kid in kb_ids:
        for h in await store.search(
            db, team=team, kb_id=str(kid), query_embedding=q_vec, k=body.k, query_text=query
        ):
            scored.append((kid, h))
    if not scored:
        return KnowledgeSearchResult(chunks=[], citations=[])
    scored.sort(key=lambda pair: pair[1]["score"], reverse=True)
    scored = scored[: body.k]

    # KB names (only the KBs that produced hits) + source filenames — for citation chips.
    hit_kb_ids = list({kid for (kid, _h) in scored})
    kb_name_by_id = {
        str(rid): name
        for (rid, name) in (
            await db.execute(select(KnowledgeBase.id, KnowledgeBase.name).where(KnowledgeBase.id.in_(hit_kb_ids)))
        ).all()
    }
    source_ids = list({h["source_id"] for (_kid, h) in scored})
    fname_rows = (
        await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.filename).where(
                KnowledgeSource.id.in_([uuid.UUID(s) for s in source_ids])
            )
        )
    ).all()
    filenames = {str(rid): fname for (rid, fname) in fname_rows}

    chunks: list[KnowledgeSearchChunk] = []
    citations: list[KnowledgeCitation] = []
    seen: set[tuple[str, str]] = set()
    for (kid, h) in scored:
        source = filenames.get(h["source_id"], "unknown")
        kb_name = kb_name_by_id.get(str(kid), "knowledge base")
        chunks.append(
            KnowledgeSearchChunk(
                content=h["content"], source=source, kb=kb_name, score=h["score"]
            )
        )
        key = (source, kb_name)
        if key not in seen:
            seen.add(key)
            citations.append(KnowledgeCitation(source=source, kb=kb_name))

    logger.info(
        "internal_knowledge_search: agent=%s team=%s kbs=%d hits=%d",
        agent_name, team, len(kb_ids), len(chunks),
    )
    return KnowledgeSearchResult(chunks=chunks, citations=citations)
