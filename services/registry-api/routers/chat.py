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
from models import Agent, AgentRun, AssetGrant, Deployment, PlaygroundRun

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

    # Synthesize a Deployment-like object so downstream proxy code works
    dep = Deployment(
        id=prod_dep.id,
        agent_id=agent_id,
        version_id=prod_dep.version_id,
        environment="production",
        status="running",
        k8s_namespace=prod_dep.namespace,
        k8s_deployment_name=f"{agent.name}-production",
    )
    dep.deployed_at = prod_dep.deployed_at
    return dep


async def _complete_chat_run(
    run_id: str, output_text: str, trace_id: str | None, agent_run_id: str | None = None
) -> None:
    """Background: mark the consumer chat run as completed and update Langfuse."""
    from db import AsyncSessionLocal

    now = datetime.now(tz=timezone.utc)
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
                    await session.commit()
            except Exception as exc:
                logger.warning("_complete_chat_run agent_run: %s: %s", agent_run_id, exc)

    if trace_id:
        from tracing import trace_complete_run
        trace_complete_run(run_id=trace_id, status="completed", output_text=output_text)


async def _proxy_agent_stream(
    service_url: str,
    message: str,
    run_id: str,
    trace_id: str | None = None,
) -> AsyncGenerator[str, None]:
    """Proxy the live agent pod's /chat/stream SSE output.

    Translates named SSE events from the declarative-runner into unnamed
    data-only frames that the EventSource frontend expects:
      text_delta         → {"type": "token",              "content": "..."}
      done               → {"type": "done",                "run_id": "..."}
      error              → {"type": "error",               "message": "..."} + done
      approval_requested → {"type": "approval_requested",  ...}
    """
    target = f"{service_url}/chat/stream"
    timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)

    def _emit(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    try:
        req_headers: dict[str, str] = {"Content-Type": "application/json"}
        if trace_id:
            req_headers["X-AgentShield-Trace-ID"] = trace_id

        async with httpx.AsyncClient() as client:
            async with client.stream(
                "POST",
                target,
                json={"message": message, "thread_id": run_id},
                headers=req_headers,
                timeout=timeout,
            ) as response:
                if response.status_code != 200:
                    logger.error(
                        "Agent pod %s returned HTTP %d", target, response.status_code
                    )
                    yield _emit({"type": "error", "message": f"Agent returned HTTP {response.status_code}"})
                    yield _emit({"type": "done", "run_id": run_id})
                    return

                current_event: Optional[str] = None
                current_data: Optional[str] = None

                async for line in response.aiter_lines():
                    if line.startswith("event:"):
                        current_event = line[len("event:"):].strip()
                    elif line.startswith("data:"):
                        current_data = line[len("data:"):].strip()
                    elif line == "":
                        # End of SSE frame — translate and emit
                        if current_data is not None:
                            try:
                                payload = json.loads(current_data)
                            except json.JSONDecodeError:
                                payload = {}

                            if current_event == "text_delta":
                                yield _emit({"type": "token", "content": payload.get("content", "")})
                            elif current_event == "done":
                                yield _emit({"type": "done", "run_id": run_id})
                            elif current_event == "error":
                                yield _emit({"type": "error", "message": payload.get("message", "Agent error")})
                                yield _emit({"type": "done", "run_id": run_id})
                            elif current_event == "approval_requested":
                                yield _emit({"type": "approval_requested", **payload})
                            # tool_call_start / tool_call_end are informational — skip for consumer chat

                        current_event = None
                        current_data = None

    except httpx.ConnectError:
        logger.error("Cannot reach agent pod at %s", target)
        yield _emit({"type": "error", "message": "Agent pod is unreachable. It may still be starting."})
        yield _emit({"type": "done", "run_id": run_id})
    except asyncio.CancelledError:
        logger.info("Client disconnected during stream for run_id=%s", run_id)
    except Exception as exc:
        logger.exception("Unexpected error proxying to agent pod %s", target)
        yield _emit({"type": "error", "message": str(exc)})
        yield _emit({"type": "done", "run_id": run_id})


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

    # -- Require a running deployment -----------------------------------------
    deployment = await _running_deployment(db, agent.id, context=chat_context)
    if not deployment:
        env_label = "production" if is_production else "playground"
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent '{name}' has no running {env_label} deployment.",
        )

    session_id = body.session_id or str(uuid.uuid4())

    # -- Record the run -------------------------------------------------------
    now = datetime.now(tz=timezone.utc)
    run = PlaygroundRun(
        user_id=user_sub,
        agent_name=name,
        context=chat_context,
        sandbox=not is_production,
        deployment_id=deployment.id,
        session_id=session_id,
        requested_by_username=caller.get("preferred_username"),
        requested_by_team=caller_team,
        input_message=body.message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    # Create AgentRun — production_deployment_id only set for production context
    agent_run = AgentRun(
        agent_name=name,
        session_id=session_id,
        user_id=user_sub,
        input=body.message,
        context=chat_context,
        trigger_type="api",
        run_by=user_sub,
        team=agent.team,
        status="running",
        started_at=now,
        production_deployment_id=deployment.id if is_production else None,
        sandbox_deployment_id=deployment.id if not is_production else None,
    )
    db.add(agent_run)
    await db.flush()
    agent_run_id = str(agent_run.id)

    # Create Langfuse root trace for this consumer chat run
    from tracing import trace_create_run
    trace_id = trace_create_run(
        run_id=run_id,
        agent_name=name,
        user_id=user_sub,
        context="production",
        input_message=body.message,
    )
    if trace_id:
        run.langfuse_trace_id = trace_id
        agent_run.langfuse_trace_id = trace_id
        await db.flush()

    await db.commit()

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

    # -- Resolve agent + deployment -------------------------------------------
    # Use the context stored on the run record to search the correct table
    chat_context = run.context or "playground"

    agent_result = await db.execute(
        select(Agent).where(Agent.name == name, Agent.status == "active")
    )
    agent = agent_result.scalar_one_or_none()

    deployment: Optional[Deployment] = None
    if agent:
        deployment = await _running_deployment(db, agent.id, context=chat_context)

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
            service_url, run.input_message or "", run_id, trace_id=trace_id
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
    session_id = body.session_id or str(uuid.uuid4())
    now = datetime.now(tz=timezone.utc)

    run = PlaygroundRun(
        user_id=user_sub,
        agent_name=name,
        context="playground",
        sandbox=True,
        deployment_id=deployment.id,
        session_id=session_id,
        requested_by_username=caller.get("preferred_username"),
        requested_by_team=caller_team,
        input_message=body.message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    agent_run = AgentRun(
        agent_name=name,
        session_id=session_id,
        user_id=user_sub,
        input=body.message,
        context="playground",
        trigger_type="api",
        run_by=user_sub,
        team=agent.team,
        status="running",
        started_at=now,
        sandbox_deployment_id=deployment.id,
    )
    db.add(agent_run)
    await db.flush()
    agent_run_id = str(agent_run.id)
    await db.commit()

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

    dep_result = await db.execute(select(Deployment).where(Deployment.id == parsed_dep_id))
    deployment = dep_result.scalar_one_or_none()

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

    async def _stream_and_complete() -> AsyncGenerator[str, None]:
        output_parts: list[str] = []
        async for chunk in _proxy_agent_stream(service_url, run.input_message or "", run_id):
            if chunk.startswith("data: "):
                try:
                    ev = json.loads(chunk[6:].strip())
                    if ev.get("type") == "token":
                        output_parts.append(ev.get("content", ""))
                except Exception:
                    pass
            yield chunk
        asyncio.get_event_loop().create_task(
            _complete_chat_run(run_id, "".join(output_parts), None, agent_run_id)
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

    chat_context = run.context or "playground"
    deployment = await _running_deployment(db, agent.id, context=chat_context)
    if not deployment:
        def _no_deploy():
            yield f"data: {json.dumps({'type': 'error', 'message': 'No running deployment'})}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'run_id': run_id})}\n\n"
        return StreamingResponse(_no_deploy(), media_type="text/event-stream")

    service_url = f"http://{deployment.k8s_deployment_name}.{deployment.k8s_namespace}:8080"
    decision_str = approval.status
    reviewer = approval.reviewer_id or "unknown"

    def _emit(payload: dict) -> str:
        return f"data: {json.dumps(payload)}\n\n"

    async def _proxy_resume() -> AsyncGenerator[str, None]:
        timeout = httpx.Timeout(connect=5.0, read=None, write=5.0, pool=5.0)
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
                    headers={"Accept": "text/event-stream"},
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
                                    yield _emit({"type": "token", "content": payload.get("content", "")})
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
                        _complete_chat_run(run_id, "".join(output_parts), None, None)
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
