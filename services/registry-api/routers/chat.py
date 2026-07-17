"""
AgentShield Registry API — Consumer chat proxy router

Endpoints
---------
  POST /api/v1/agents/{name}/chat               — start a chat session (returns run_id + stream_url)
  GET  /api/v1/agents/{name}/chat/{run_id}/stream — SSE token stream proxied from the live agent pod

Access model
------------
- Caller must be authenticated (JWT via require_user).
- Agent owner team can always chat with their own agent.
- Other teams require an active, non-expired AssetGrant on the agent.
- Agent must have a 'running' Deployment; otherwise 503.

Streaming
---------
The stream endpoint proxies to the live agent pod's POST /chat/stream endpoint,
translating the runner's named SSE events (text_delta, done, error, approval_requested)
into unnamed data-only frames that the EventSource frontend expects.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import require_user
from db import get_db
from identity import resolve_principal
from models import Agent, AgentRun, AssetGrant, Deployment, PlaygroundRun
from pod_stream import stream_pod_chat_frames
from preferences import compose_directive_for_user

deployment_chat_router = APIRouter(prefix="/api/v1/agents", tags=["deployment-chat"])

router = APIRouter(prefix="/api/v1/agents", tags=["consumer-chat"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    context: Optional[str] = None  # "production" or "playground"; determines deployment lookup
    deployment_id: Optional[str] = None  # pin to this exact deployment; prod uses ProductionDeployment.id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _caller_team(db: AsyncSession, user_sub: str) -> Optional[str]:
    """Return the team name for the given user_sub, or None if unassigned."""
    row = await db.execute(
        text("SELECT team_name FROM user_team_assignments WHERE user_sub = :sub LIMIT 1"),
        {"sub": user_sub},
    )
    r = row.first()
    return r.team_name if r else None


async def _resolve_session_id(
    db: AsyncSession, supplied: Optional[str], user_sub: str
) -> str:
    """Resolve the session_id a chat turn binds to, fail-closed (thread-ownership.md, S6).

    - Empty supplied session OR unauthenticated caller → mint a fresh session_id.
      There is no binding to prove, and an ambiguous identity must never bind to a
      shared session.
    - Supplied session already owned by a DIFFERENT user → 403. A session first used
      by user A cannot be replayed by user B to read A's conversation.
    - Supplied session owned by the same user (or not yet used) → allowed; this POST
      establishes/continues ownership by writing a run with user_id=user_sub.
    """
    if not supplied or not user_sub:
        return str(uuid.uuid4())
    owner = (
        await db.execute(
            select(PlaygroundRun.user_id)
            .where(PlaygroundRun.session_id == supplied)
            .limit(1)
        )
    ).scalar_one_or_none()
    if owner is not None and owner != user_sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your session.",
        )
    return supplied


async def _has_grant(db: AsyncSession, agent_id: uuid.UUID, team: str) -> bool:
    """Return True if team holds an active, non-expired grant on the given agent."""
    now = datetime.now(tz=timezone.utc)
    result = await db.execute(
        select(AssetGrant)
        .where(
            AssetGrant.asset_id == agent_id,
            AssetGrant.asset_type == "agent",
            AssetGrant.grantee_team == team,
            AssetGrant.revoked_at.is_(None),
        )
        .limit(1)
    )
    grant = result.scalar_one_or_none()
    if grant is None:
        return False
    if grant.expires_at and grant.expires_at.replace(tzinfo=timezone.utc) < now:
        return False
    return True


async def _running_deployment(
    db: AsyncSession, agent_id: uuid.UUID, context: str = "playground"
) -> Optional[Deployment]:
    """Return the running deployment for the agent in the given context.

    Args:
        context: "production" searches production_deployments only.
                 "playground" searches the sandbox Deployment table only.

    Each context is a separate, explicit code path — no fallthrough.
    """
    if context == "production":
        return await _running_production_deployment(db, agent_id)
    else:
        return await _running_sandbox_deployment(db, agent_id)


async def _running_sandbox_deployment(
    db: AsyncSession, agent_id: uuid.UUID
) -> Optional[Deployment]:
    """Return the most recent running sandbox deployment."""
    result = await db.execute(
        select(Deployment)
        .where(Deployment.agent_id == agent_id, Deployment.status == "running")
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _synth_prod_deployment(
    prod_dep: Any, agent_id: Optional[uuid.UUID], agent_name: str
) -> Deployment:
    """Build a Deployment-like object from a ProductionDeployment row.

    Production runs one k8s Deployment per agent (``{agent}-production`` in
    ``production-{agent}``); redeploys roll that same Deployment. The synthesized
    object carries the k8s coordinates downstream proxy code needs.
    """
    dep = Deployment(
        id=prod_dep.id,
        agent_id=agent_id,
        version_id=prod_dep.version_id,
        environment="production",
        status="running",
        k8s_namespace=prod_dep.namespace,
        k8s_deployment_name=f"{agent_name}-production",
    )
    dep.deployed_at = prod_dep.deployed_at
    return dep


async def _running_production_deployment(
    db: AsyncSession, agent_id: uuid.UUID
) -> Optional[Deployment]:
    """Return the running production deployment, synthesized as a Deployment object.

    Looks up: Agent → PublishedArtifact (by name) → ProductionDeployment (running).
    Returns None if any link in the chain is missing.
    """
    from models import ProductionDeployment, PublishedArtifact, Agent as AgentModel

    agent_row = await db.execute(select(AgentModel).where(AgentModel.id == agent_id))
    agent = agent_row.scalar_one_or_none()
    if not agent:
        return None

    art_result = await db.execute(
        select(PublishedArtifact).where(PublishedArtifact.name == agent.name)
    )
    artifact = art_result.scalar_one_or_none()
    if not artifact:
        return None

    prod_result = await db.execute(
        select(ProductionDeployment)
        .where(ProductionDeployment.artifact_id == artifact.id, ProductionDeployment.status == "running")
        .order_by(ProductionDeployment.deployed_at.desc())
        .limit(1)
    )
    prod_dep = prod_result.scalar_one_or_none()
    if not prod_dep:
        return None

    return _synth_prod_deployment(prod_dep, agent_id, agent.name)


async def _production_deployment_by_id(
    db: AsyncSession, prod_dep_id: uuid.UUID
) -> Optional[Deployment]:
    """Resolve one specific ProductionDeployment by id (must be running).

    Used to pin a run to the exact production deployment it was started against,
    rather than re-resolving "most recent running".
    """
    from models import ProductionDeployment, PublishedArtifact

    prod_row = await db.execute(
        select(ProductionDeployment).where(ProductionDeployment.id == prod_dep_id)
    )
    prod_dep = prod_row.scalar_one_or_none()
    if not prod_dep or prod_dep.status != "running":
        return None

    art_row = await db.execute(
        select(PublishedArtifact).where(PublishedArtifact.id == prod_dep.artifact_id)
    )
    artifact = art_row.scalar_one_or_none()
    if not artifact:
        return None

    ag_row = await db.execute(select(Agent).where(Agent.name == artifact.name))
    agent = ag_row.scalar_one_or_none()
    return _synth_prod_deployment(prod_dep, agent.id if agent else None, artifact.name)


async def _deployment_for_run(
    db: AsyncSession, run: PlaygroundRun
) -> Optional[Deployment]:
    """Return the exact deployment this run was pinned to at creation time.

    The run records its target when it starts — ``production_deployment_id`` for
    production runs, ``deployment_id`` for sandbox. The stream/resume path MUST
    resolve the pod from that stored id and never re-resolve "most recent
    running": a redeploy or a second running deployment landing between POST and
    stream would otherwise proxy the run to the wrong pod (and, for HITL resume,
    to a pod that doesn't hold the thread's checkpoint).
    """
    if run.production_deployment_id:
        return await _production_deployment_by_id(db, run.production_deployment_id)
    if run.deployment_id:
        res = await db.execute(
            select(Deployment).where(Deployment.id == run.deployment_id)
        )
        return res.scalar_one_or_none()
    return None


async def _pinned_deployment(
    db: AsyncSession,
    agent: Agent,
    dep_id_str: str,
    is_production: bool,
) -> Optional[Deployment]:
    """Resolve a caller-supplied deployment id, scoped to ``agent`` and running.

    Returns None if the id is malformed, not running, or belongs to a different
    agent — the caller turns that into a 404 so a chat can never be pinned to
    another agent's pod.
    """
    try:
        dep_id = uuid.UUID(dep_id_str)
    except (ValueError, TypeError):
        return None

    if is_production:
        dep = await _production_deployment_by_id(db, dep_id)
        # _production_deployment_by_id derives agent_id from the artifact name;
        # reject a prod deployment that resolves to a different agent.
        if dep and dep.agent_id is not None and dep.agent_id != agent.id:
            return None
        return dep

    res = await db.execute(
        select(Deployment).where(
            Deployment.id == dep_id,
            Deployment.agent_id == agent.id,
            Deployment.status == "running",
        )
    )
    return res.scalar_one_or_none()


async def _complete_chat_run(
    run_id: str, output_text: str, trace_id: str | None, agent_run_id: str | None = None
) -> None:
    """Background: mark the consumer chat run as completed and update Langfuse."""
    from db import AsyncSessionLocal

    now = datetime.now(tz=timezone.utc)
    # Captured for the fire-and-forget judge below (see end of function).
    judge_input: str | None = None
    judge_agent_name: str | None = None
    judge_team: str | None = None
    async with AsyncSessionLocal() as session:
        try:
            parsed = uuid.UUID(run_id)
            result = await session.execute(
                select(PlaygroundRun).where(PlaygroundRun.id == parsed)
            )
            run = result.scalar_one_or_none()
            if run:
                run.status = "completed"
                run.completed_at = now
                if output_text:
                    run.output_text = output_text
                judge_input = run.input_message
                judge_agent_name = run.agent_name
                await session.commit()
        except Exception as exc:
            logger.warning("_complete_chat_run: %s: %s", run_id, exc)
            return

    if agent_run_id:
        async with AsyncSessionLocal() as session:
            try:
                ar_result = await session.execute(
                    select(AgentRun).where(AgentRun.id == uuid.UUID(agent_run_id))
                )
                ar = ar_result.scalar_one_or_none()
                if ar:
                    ar.status = "completed"
                    ar.completed_at = now
                    ar.output = output_text[:4000] if output_text else None
                    if ar.started_at:
                        ar.latency_ms = int((now - ar.started_at).total_seconds() * 1000)
                    # The agent's own team owns the LLM provider the judge calls.
                    judge_team = ar.team
                    await session.commit()
            except Exception as exc:
                logger.warning("_complete_chat_run agent_run: %s: %s", agent_run_id, exc)

    if trace_id:
        from tracing import trace_complete_run
        trace_complete_run(run_id=trace_id, status="completed", output_text=output_text)

    # Fire-and-forget LLM-as-Judge (same scorer as playground). Scores this turn
    # and writes judge_score to BOTH the PlaygroundRun and the trace's AgentRun
    # (via langfuse_trace_id), so the production catalog runs table shows a Score.
    # Costs one extra LLM call per chat turn — an explicit product decision.
    if judge_input and output_text and trace_id and judge_team and judge_agent_name:
        try:
            from judge import score_run
            asyncio.create_task(
                score_run(
                    run_id=uuid.UUID(run_id),
                    agent_name=judge_agent_name,
                    input_text=judge_input,
                    output_text=output_text,
                    team=judge_team,
                    langfuse_trace_id=trace_id,
                )
            )
        except Exception as exc:
            logger.debug("_complete_chat_run: could not launch judge for %s: %s", run_id, exc)


async def _proxy_agent_stream(
    service_url: str,
    message: str,
    run_id: str,
    conversation_id: str,
    trace_id: str | None = None,
    user_id: str = "",
    user_team: str = "",
    deployment_id: str = "",
    author: str = "",
    user_directive: str | None = None,
) -> AsyncGenerator[str, None]:
    """Proxy the live agent pod's /chat/stream SSE output.

    The pod body carries BOTH keys (chat-stream-memory contract):
      thread_id       = session_id  → the LangGraph checkpoint key. This is the
                        POC-0 fix: it was ``run_id`` (a fresh id per turn), so
                        nothing threaded across turns; keying on the session lets
                        the checkpointer + transcript accumulate the conversation.
      conversation_id = session_id  → the transcript key (equal to thread_id for
                        chat; they differ only for workflow members).
      scope           = "agent".
    ``run_id`` stays the client-facing correlation id in the emitted SSE frames.

    Delegates the per-pod SSE parsing to the ONE shared reader
    ``stream_pod_chat_frames`` (No-Bandaid: the same reader the workflow stream uses).
    Each normalized frame dict is serialized for the EventSource frontend; the reader
    already tags every frame with ``author`` and no longer drops ``tool_call`` frames
    (the L473 drop is gone — single-agent chat now surfaces tool chips). This function
    owns the run-level ``done`` (the reader never emits it).
    """
    def _emit(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    try:
        # thread_id == conversation_id == session_id for single-agent chat (they
        # differ only for workflow members). scope="agent" → no rationale frames.
        async for frame in stream_pod_chat_frames(
            service_url,
            message=message,
            thread_id=conversation_id,
            conversation_id=conversation_id,
            scope="agent",
            author=author,
            trace_id=trace_id,
            user_id=user_id,
            user_team=user_team,
            deployment_id=deployment_id,
            user_directive=user_directive,
        ):
            yield _emit(frame)
        yield _emit({"type": "done", "run_id": run_id})
    except asyncio.CancelledError:
        logger.info("Client disconnected during stream for run_id=%s", run_id)
    except Exception as exc:
        logger.exception("Unexpected error proxying to agent pod %s", service_url)
        yield _emit({"type": "error", "message": str(exc)})
        yield _emit({"type": "done", "run_id": run_id})


async def _create_traced_chat_run(
    db: AsyncSession,
    *,
    agent: Agent,
    deployment: Deployment,
    user_sub: str,
    run_by: str,
    preferred_username: str | None,
    caller_team: str | None,
    message: str,
    session_id: str,
    context: str,
    is_production: bool,
) -> tuple[PlaygroundRun, AgentRun, str | None]:
    """Create the PlaygroundRun + AgentRun rows for one chat turn and open a
    Langfuse root trace, wiring the trace_id onto both rows.

    Single source of truth for chat-run creation + tracing. Both ``start_chat``
    and ``start_deployment_chat`` call this so the two paths can never drift —
    they did: ``start_deployment_chat`` created runs with no trace at all, which
    is why deployment-pinned chats showed an empty Trace column.

    The Langfuse trace's ``user_id`` is the human-readable ``preferred_username``
    (falling back to the sub); the ``PlaygroundRun``/``AgentRun`` ``user_id`` FK
    columns keep the raw sub. Deployment id + environment are tagged on the trace
    so instances of the same agent are distinguishable.
    """
    now = datetime.now(tz=timezone.utc)

    run = PlaygroundRun(
        user_id=user_sub,
        agent_name=agent.name,
        context=context,
        sandbox=not is_production,
        # FK column must match the table the id belongs to: production ids live
        # in `production_deployments`, sandbox in `deployments` — never cross them.
        deployment_id=deployment.id if not is_production else None,
        production_deployment_id=deployment.id if is_production else None,
        session_id=session_id,
        requested_by_username=preferred_username,
        requested_by_team=caller_team,
        input_message=message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    agent_run = AgentRun(
        agent_name=agent.name,
        session_id=session_id,
        user_id=user_sub,
        input=message,
        context=context,
        trigger_type="api",
        # run_by is the resolved principal (WS-2 R3). For an interactive chat a JWT
        # caller is always present, so resolve_principal returns the caller sub —
        # identical to the old inline `run_by=user_sub`, but now via the single
        # identity decision point (a daemon agent's /chat still runs under the
        # caller: R3 floor, not a cap). user_id below stays the live human's sub.
        run_by=run_by,
        team=agent.team,
        status="running",
        started_at=now,
        production_deployment_id=deployment.id if is_production else None,
        sandbox_deployment_id=deployment.id if not is_production else None,
    )
    db.add(agent_run)
    await db.flush()

    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=run_id,
        agent_name=agent.name,
        user_id=preferred_username or user_sub,
        context=context,
        input_message=message,
        deployment_id=str(deployment.id),
        environment=deployment.environment,
    )
    if trace_id:
        run.langfuse_trace_id = trace_id
        agent_run.langfuse_trace_id = trace_id
        await db.flush()

    await db.commit()
    return run, agent_run, trace_id


# ---------------------------------------------------------------------------
# POST /api/v1/agents/{name}/chat
# ---------------------------------------------------------------------------

@router.post(
    "/{name}/chat",
    summary="Start a consumer chat session with an agent",
    response_description="Run metadata including stream_url",
)
async def start_chat(
    name: str,
    body: AgentChatRequest,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Initiate a chat run against a production-deployed agent.

    Returns a ``run_id`` and a ``stream_url`` to connect to the SSE stream.
    The caller must hold a valid JWT; their team membership is resolved from
    ``user_team_assignments``.  Callers from the agent's owner team bypass the
    grant check.  All other callers need an active ``AssetGrant``.
    """
    # -- Resolve agent --------------------------------------------------------
    result = await db.execute(
        select(Agent).where(Agent.name == name, Agent.status == "active")
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )

    # -- Resolve caller team --------------------------------------------------
    user_sub = caller.get("sub", "")
    caller_team = await _caller_team(db, user_sub)

    # -- Access check ---------------------------------------------------------
    if caller_team != agent.team:
        if not caller_team:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="User has no team assignment.",
            )
        if not await _has_grant(db, agent.id, caller_team):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Team '{caller_team}' does not have access to agent '{name}'.",
            )

    # -- Resolve context (production vs playground) ------------------------------
    chat_context = body.context or "playground"
    is_production = chat_context == "production"

    # -- Resolve the target deployment ----------------------------------------
    # If the caller pinned an explicit deployment (e.g. launched from a specific
    # fleet row), bind the run to exactly that deployment. Otherwise fall back to
    # the single running deployment for the context.
    if body.deployment_id:
        deployment = await _pinned_deployment(
            db, agent, body.deployment_id, is_production
        )
        if not deployment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Deployment '{body.deployment_id}' is not a running deployment of agent '{name}'.",
            )
    else:
        deployment = await _running_deployment(db, agent.id, context=chat_context)
        if not deployment:
            env_label = "production" if is_production else "playground"
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Agent '{name}' has no running {env_label} deployment.",
            )

    # Fail-closed session binding: a supplied session owned by another user is
    # rejected before we create any run under it (thread-ownership.md, S6).
    session_id = await _resolve_session_id(db, body.session_id, user_sub)

    # -- Resolve the acting principal (WS-2 R3) -------------------------------
    # Interactive run → a JWT caller is present, so pass caller=<jwt user> (never
    # sniff agent_class). resolve_principal returns the caller as the principal for
    # ANY agent class — a daemon agent's /chat run still runs under the caller
    # (identity floor, not a cap). This is the same single decision point the
    # trigger path uses with caller=None.
    principal = await resolve_principal(agent, caller=caller, trigger=None, db=db)

    # -- Record the run + open the Langfuse trace (shared with deployment chat) --
    run, agent_run, trace_id = await _create_traced_chat_run(
        db,
        agent=agent,
        deployment=deployment,
        user_sub=user_sub,
        run_by=principal.run_by,
        preferred_username=caller.get("preferred_username"),
        caller_team=caller_team,
        message=body.message,
        session_id=session_id,
        context=chat_context,
        is_production=is_production,
    )
    run_id = str(run.id)
    agent_run_id = str(agent_run.id)

    logger.info(
        "chat: run_id=%s agent_run_id=%s agent=%s user=%s team=%s deployment=%s trace=%s",
        run_id, agent_run_id, name, user_sub, caller_team, deployment.id, trace_id,
    )

    return {
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "session_id": session_id,
        "stream_url": f"/api/v1/agents/{name}/chat/{run_id}/stream",
        "agent_name": name,
        "deployment_id": str(deployment.id),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{name}/chat/{run_id}/stream
# ---------------------------------------------------------------------------

@router.get(
    "/{name}/chat/{run_id}/stream",
    summary="SSE token stream proxied from the live agent pod",
    response_class=StreamingResponse,
)
async def stream_chat(
    name: str,
    run_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream the agent's response for a previously-started chat run as SSE.

    Proxies to the live declarative-runner pod via its internal K8s Service.
    Only the user who initiated the run may connect to its stream.
    """
    # -- Validate run_id format -----------------------------------------------
    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid run_id format.",
        )

    # -- Fetch run record -----------------------------------------------------
    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()

    if not run or run.agent_name != name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat run not found.",
        )

    # -- Ownership check ------------------------------------------------------
    if run.user_id != caller.get("sub", ""):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not your chat run.",
        )

    # -- Resolve the deployment this run was pinned to -------------------------
    # Read the pod from the id stored on the run at POST time — never re-resolve
    # "most recent running", which can race a redeploy and hit the wrong pod.
    deployment = await _deployment_for_run(db, run)

    if not deployment or not deployment.k8s_deployment_name:
        async def _no_deploy() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'type': 'error', 'message': f'Agent {name} has no running deployment.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"

        return StreamingResponse(
            _no_deploy(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    service_url = (
        f"http://{deployment.k8s_deployment_name}.{deployment.k8s_namespace}:8080"
    )
    trace_id = run.langfuse_trace_id
    # Resolve identity now (the DB session closes before the stream generator
    # runs). conversation_id = run.session_id is the transcript/checkpoint key.
    conversation_id = run.session_id or run_id
    owner_team = await _caller_team(db, run.user_id or "") or ""
    deployment_id = str(deployment.id)
    # POC-3: compose the caller's advisory preference directive now, while the session
    # is open (it closes before the stream generator runs). run.user_id is always the
    # interactive caller here, so a profile — if any — always applies (daemon ⇒ None).
    user_directive = await compose_directive_for_user(db, run.user_id or "")

    # Find the corresponding AgentRun for production tracking
    ar_result = await db.execute(
        select(AgentRun).where(
            AgentRun.agent_name == name,
            AgentRun.context == "production",
            AgentRun.user_id == caller.get("sub", ""),
        ).order_by(AgentRun.started_at.desc()).limit(1)
    )
    ar = ar_result.scalar_one_or_none()
    agent_run_id = str(ar.id) if ar else None

    logger.info(
        "stream: run_id=%s agent=%s service_url=%s trace=%s",
        run_id, name, service_url, trace_id,
    )

    async def _stream_and_complete() -> AsyncGenerator[str, None]:
        output_parts: list[str] = []
        async for chunk in _proxy_agent_stream(
            service_url, run.input_message or "", run_id,
            conversation_id=conversation_id, trace_id=trace_id,
            user_id=run.user_id or "", user_team=owner_team,
            deployment_id=deployment_id, author=name,
            user_directive=user_directive,
        ):
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("type") == "token":
                        output_parts.append(ev.get("content", ""))
                except Exception:
                    pass
            yield chunk
        asyncio.get_event_loop().create_task(
            _complete_chat_run(run_id, "".join(output_parts), trace_id, agent_run_id)
        )

    return StreamingResponse(
        _stream_and_complete(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# POST /api/v1/agents/{name}/deployments/{dep_id}/chat
# Deployment-pinned chat: routes directly to the specified deployment's pod
# rather than re-resolving "most recent running" deployment.
# ---------------------------------------------------------------------------

class DeploymentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


@deployment_chat_router.post(
    "/{name}/deployments/{dep_id}/chat",
    summary="Start a chat session pinned to a specific deployment",
)
async def start_deployment_chat(
    name: str,
    dep_id: str,
    body: DeploymentChatRequest,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Start a chat run pinned to an exact deployment — no ambiguous re-resolution."""
    try:
        parsed_dep_id = uuid.UUID(dep_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid deployment ID.")

    result = await db.execute(
        select(Agent).where(Agent.name == name, Agent.status == "active")
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Agent '{name}' not found.")

    dep_result = await db.execute(
        select(Deployment).where(Deployment.id == parsed_dep_id, Deployment.agent_id == agent.id)
    )
    deployment = dep_result.scalar_one_or_none()
    if not deployment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Deployment not found for this agent.")
    if deployment.status != "running":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Deployment is not running (status: {deployment.status}).",
        )

    user_sub = caller.get("sub", "")
    caller_team = await _caller_team(db, user_sub)
    # Fail-closed session binding (thread-ownership.md, S6).
    session_id = await _resolve_session_id(db, body.session_id, user_sub)

    # Same single identity decision as start_chat: caller-present → the caller is
    # the principal (any agent class). Keeps run_by attribution consistent across
    # both interactive entry points instead of re-deriving user_sub inline.
    principal = await resolve_principal(agent, caller=caller, trigger=None, db=db)

    # Deployment-pinned chat is sandbox-only today. Use the shared helper so this
    # path gets the same Langfuse trace as start_chat — it previously created runs
    # with no trace, which is why deployment-pinned chats had an empty Trace column.
    run, agent_run, trace_id = await _create_traced_chat_run(
        db,
        agent=agent,
        deployment=deployment,
        user_sub=user_sub,
        run_by=principal.run_by,
        preferred_username=caller.get("preferred_username"),
        caller_team=caller_team,
        message=body.message,
        session_id=session_id,
        context="playground",
        is_production=False,
    )
    run_id = str(run.id)
    agent_run_id = str(agent_run.id)

    logger.info(
        "deployment_chat: run_id=%s agent_run_id=%s agent=%s deployment=%s trace=%s",
        run_id, agent_run_id, name, deployment.id, trace_id,
    )

    return {
        "run_id": run_id,
        "agent_run_id": agent_run_id,
        "session_id": session_id,
        "stream_url": f"/api/v1/agents/{name}/deployments/{dep_id}/chat/{run_id}/stream",
        "agent_name": name,
        "deployment_id": dep_id,
    }


@deployment_chat_router.get(
    "/{name}/deployments/{dep_id}/chat/{run_id}/stream",
    summary="SSE stream pinned to a specific deployment",
    response_class=StreamingResponse,
)
async def stream_deployment_chat(
    name: str,
    dep_id: str,
    run_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Stream pinned to the exact deployment — never re-resolves."""
    try:
        parsed_id = uuid.UUID(run_id)
        parsed_dep_id = uuid.UUID(dep_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid ID format.")

    result = await db.execute(select(PlaygroundRun).where(PlaygroundRun.id == parsed_id))
    run = result.scalar_one_or_none()
    if not run or run.agent_name != name:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat run not found.")
    if run.user_id != caller.get("sub", ""):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not your chat run.")

    # The path dep_id must match the deployment this run was actually pinned to,
    # or a caller could stream their own run against an unrelated pod.
    if run.deployment_id != parsed_dep_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Deployment does not match this chat run.",
        )

    deployment = await _deployment_for_run(db, run)

    if not deployment or not deployment.k8s_deployment_name:
        async def _no_deploy() -> AsyncGenerator[str, None]:
            yield f"data: {json.dumps({'type': 'error', 'message': 'Deployment not reachable.'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"

        return StreamingResponse(
            _no_deploy(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    service_url = f"http://{deployment.k8s_deployment_name}.{deployment.k8s_namespace}:8080"

    ar_result = await db.execute(
        select(AgentRun).where(
            AgentRun.agent_name == name,
            AgentRun.sandbox_deployment_id == parsed_dep_id,
            AgentRun.user_id == caller.get("sub", ""),
        ).order_by(AgentRun.started_at.desc()).limit(1)
    )
    ar = ar_result.scalar_one_or_none()
    agent_run_id = str(ar.id) if ar else None

    # Propagate the trace id to the agent pod (X-AgentShield-Trace-ID header, set
    # inside _proxy_agent_stream) so its spans attach to this run's root trace,
    # and into _complete_chat_run so the trace is closed out — both were missing
    # here, so deployment-pinned runs produced no trace at all.
    trace_id = run.langfuse_trace_id
    # Resolve identity before the DB session closes (stream generator runs later).
    conversation_id = run.session_id or run_id
    owner_team = await _caller_team(db, run.user_id or "") or ""
    deployment_id = str(deployment.id)
    # POC-3: compose the caller's advisory directive while the session is open (it
    # closes before the stream generator). run.user_id = interactive caller (daemon ⇒ None).
    user_directive = await compose_directive_for_user(db, run.user_id or "")

    async def _stream_and_complete() -> AsyncGenerator[str, None]:
        output_parts: list[str] = []
        async for chunk in _proxy_agent_stream(
            service_url, run.input_message or "", run_id,
            conversation_id=conversation_id, trace_id=trace_id,
            user_id=run.user_id or "", user_team=owner_team,
            deployment_id=deployment_id, author=name,
            user_directive=user_directive,
        ):
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("type") == "token":
                        output_parts.append(ev.get("content", ""))
                except Exception:
                    pass
            yield chunk
        asyncio.get_event_loop().create_task(
            _complete_chat_run(run_id, "".join(output_parts), trace_id, agent_run_id)
        )

    return StreamingResponse(
        _stream_and_complete(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{name}/chat/{run_id}/resume-stream
# ---------------------------------------------------------------------------

@router.get(
    "/{name}/chat/{run_id}/resume-stream",
    summary="SSE stream for resumed output after HITL approval",
    response_class=StreamingResponse,
)
async def resume_stream_chat(
    name: str,
    run_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """After a production HITL approval, stream the resumed agent output as SSE.

    Reads the decided approval from DB, resolves the agent pod, and proxies
    POST /resume/{thread_id}/stream back to the consumer."""
    from models import Approval

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(
        select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    user_sub = caller.get("sub", "")
    if run.user_id != user_sub:
        raise HTTPException(status_code=403, detail="Not your run")

    thread_id = run_id

    approval_result = await db.execute(
        select(Approval)
        .where(
            Approval.thread_id == thread_id,
            Approval.status.in_(["approved", "rejected"]),
        )
        .order_by(Approval.decision_at.desc())
        .limit(1)
    )
    approval = approval_result.scalar_one_or_none()
    if not approval:
        raise HTTPException(
            status_code=404,
            detail="No decided approval found for this run",
        )

    agent_result = await db.execute(select(Agent).where(Agent.name == name))
    agent = agent_result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Resume MUST hit the same pod the run started on — the HITL thread's
    # checkpoint lives there. Pin to the run's stored deployment id.
    deployment = await _deployment_for_run(db, run)
    if not deployment:
        def _no_deploy():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No running deployment'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"
        return StreamingResponse(_no_deploy(), media_type="text/event-stream")

    service_url = f"http://{deployment.k8s_deployment_name}.{deployment.k8s_namespace}:8080"
    decision_str = approval.status
    reviewer = approval.reviewer_id or "unknown"
    # Resume spans (the post-approval continuation) must attach to the same root
    # trace and close it out — otherwise a HITL run's second half is untraced.
    trace_id = run.langfuse_trace_id

    def _emit(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    async def _proxy_resume() -> AsyncGenerator[str, None]:
        timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)
        resume_headers = {"Accept": "text/event-stream"}
        if trace_id:
            resume_headers["X-AgentShield-Trace-ID"] = trace_id
        try:
            async with httpx.AsyncClient() as client:
                async with client.stream(
                    "POST",
                    f"{service_url}/resume/{thread_id}/stream",
                    json={
                        "decision": decision_str,
                        "reviewer_id": reviewer,
                        "reason": approval.reviewer_notes,
                    },
                    headers=resume_headers,
                    timeout=timeout,
                ) as response:
                    if response.status_code != 200:
                        err = await response.aread()
                        yield _emit({"type": "error", "message": f"Agent pod returned {response.status_code}: {err.decode()[:200]}"})
                        yield _emit({"type": "done", "run_id": run_id})
                        return

                    current_event: Optional[str] = None
                    current_data: Optional[str] = None
                    output_parts: list[str] = []

                    async for line in response.aiter_lines():
                        if line.startswith("event:"):
                            current_event = line[len("event:"):].strip()
                        elif line.startswith("data:"):
                            current_data = line[len("data:"):].strip()
                        elif line == "":
                            if current_data is not None:
                                try:
                                    payload = json.loads(current_data)
                                except json.JSONDecodeError:
                                    payload = {}

                                if current_event == "text_delta":
                                    output_parts.append(payload.get("content", ""))
                                    # POC-2: resume is a one-speaker continuation of
                                    # the same agent — attribute to {name}.
                                    yield _emit({"type": "token", "content": payload.get("content", ""), "author": name})
                                elif current_event == "done":
                                    yield _emit({"type": "done", "run_id": run_id})
                                elif current_event == "error":
                                    yield _emit({"type": "error", "message": payload.get("message", "Agent error")})
                                    yield _emit({"type": "done", "run_id": run_id})
                                elif current_event == "tool_call_start":
                                    yield _emit({"type": "tool_call_start", **payload})
                                elif current_event == "tool_call_end":
                                    yield _emit({"type": "tool_call_end", **payload})
                                elif current_event == "approval_requested":
                                    # A LATER-turn tool call re-interrupted during
                                    # resume. Forward it so the chat surfaces the
                                    # next approval instead of the stream ending
                                    # silently (which read as "connection lost").
                                    yield _emit({"type": "approval_requested", **payload})

                            current_event = None
                            current_data = None

                    asyncio.get_event_loop().create_task(
                        _complete_chat_run(run_id, "".join(output_parts), trace_id, None)
                    )

        except httpx.ConnectError:
            yield _emit({"type": "error", "message": "Agent pod is unreachable."})
            yield _emit({"type": "done", "run_id": run_id})
        except Exception as exc:
            logger.exception("resume_stream_chat: error proxying to agent pod")
            yield _emit({"type": "error", "message": str(exc)})
            yield _emit({"type": "done", "run_id": run_id})

    return StreamingResponse(
        _proxy_resume(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{name}/chat/{run_id}/approval-status
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/chat/{run_id}/approval-status",
    summary="Poll the HITL approval status for a chat run (requester-scoped)",
)
async def chat_approval_status(
    name: str,
    run_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the latest approval decision for this chat run's thread.

    Scoped to the run's owner — the person who started the chat can watch the
    status of their own approval without needing reviewer authority (that gate
    lives on PATCH /approvals/{id}). The chat page polls this so it can auto-
    resume the moment a reviewer decides in the HITL console.
    """
    from models import Approval

    try:
        parsed_id = uuid.UUID(run_id)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid run_id format")

    result = await db.execute(select(PlaygroundRun).where(PlaygroundRun.id == parsed_id))
    run = result.scalar_one_or_none()
    if not run or run.agent_name != name:
        raise HTTPException(status_code=404, detail="Chat run not found.")
    if run.user_id != caller.get("sub", ""):
        raise HTTPException(status_code=403, detail="Not your chat run.")

    approval_result = await db.execute(
        select(Approval)
        .where(Approval.thread_id == run_id)
        .order_by(Approval.created_at.desc())
        .limit(1)
    )
    approval = approval_result.scalar_one_or_none()
    if not approval:
        return {"run_id": run_id, "status": "none", "approval_id": None}

    return {
        "run_id": run_id,
        "approval_id": str(approval.id),
        "status": approval.status,  # pending | approved | rejected | timed_out
        "tool": approval.tool_name,
        "risk": approval.risk_level,
        "reasoning": approval.reasoning,
        "reviewer_id": approval.reviewer_id,
        "decided": approval.status in ("approved", "rejected"),
    }


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{name}/chat/session/{session_id}/approvals
# ---------------------------------------------------------------------------
@router.get(
    "/{name}/chat/session/{session_id}/approvals",
    summary="List HITL approvals for a chat session (requester-scoped)",
)
async def session_approvals(
    name: str,
    session_id: str,
    caller: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List the approvals for a whole conversation (session), owned by the caller.

    Feeds the sandbox self-approve panel. A conversation is many per-turn runs
    sharing one session_id; each run's id is an approval's thread_id. Today the
    graph interrupts at the first high-risk tool so there is usually one pending
    row, but the list shape is forward-proof for conversation history once
    conversations are persisted.
    """
    from models import Approval

    user_sub = caller.get("sub", "")

    # Runs in this session owned by the caller — the thread_ids to look up, plus
    # the requester provenance (username/team) to show WHO asked on each row.
    run_rows = (
        await db.execute(
            select(
                PlaygroundRun.id,
                PlaygroundRun.requested_by_username,
                PlaygroundRun.requested_by_team,
            ).where(
                PlaygroundRun.session_id == session_id,
                PlaygroundRun.agent_name == name,
                PlaygroundRun.user_id == user_sub,
            )
        )
    ).all()
    run_ids = [str(r.id) for r in run_rows]
    if not run_ids:
        return {"session_id": session_id, "approvals": []}
    prov = {
        str(r.id): (r.requested_by_username, r.requested_by_team) for r in run_rows
    }

    approvals = (
        await db.execute(
            select(Approval)
            .where(Approval.thread_id.in_(run_ids))
            .order_by(Approval.created_at.desc())
        )
    ).scalars().all()

    def _row(a) -> dict[str, Any]:
        username, team = prov.get(a.thread_id, (None, None))
        return {
            "approval_id": str(a.id),
            "run_id": a.thread_id,
            "status": a.status,
            "tool": a.tool_name,          # WHAT (tool)
            "args": a.tool_args or {},    # WHAT (arguments)
            "risk": a.risk_level,
            "reasoning": a.reasoning,     # WHY (best-effort LLM reason)
            "requested_by": username,     # WHO
            "requested_by_team": team,
            "context": a.context,
            "created_at": a.created_at.isoformat() if a.created_at else None,
            "decided": a.status in ("approved", "rejected"),
        }

    return {"session_id": session_id, "approvals": [_row(a) for a in approvals]}
