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

router = APIRouter(prefix="/api/v1/agents", tags=["consumer-chat"])
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request/response schemas
# ---------------------------------------------------------------------------

class AgentChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


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


async def _running_deployment(db: AsyncSession, agent_id: uuid.UUID) -> Optional[Deployment]:
    """Return the most-recently-deployed 'running' deployment for the agent, or None."""
    result = await db.execute(
        select(Deployment)
        .where(Deployment.agent_id == agent_id, Deployment.status == "running")
        .order_by(Deployment.deployed_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


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

    # -- Require a running deployment -----------------------------------------
    deployment = await _running_deployment(db, agent.id)
    if not deployment:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Agent '{name}' has no running deployment. Deploy it first.",
        )

    session_id = body.session_id or str(uuid.uuid4())

    # -- Record the run -------------------------------------------------------
    now = datetime.now(tz=timezone.utc)
    run = PlaygroundRun(
        user_id=user_sub,
        agent_name=name,
        context="production",
        sandbox=False,
        input_message=body.message,
        status="running",
        started_at=now,
    )
    db.add(run)
    await db.flush()
    run_id = str(run.id)

    # Create AgentRun for production tracking
    agent_run = AgentRun(
        agent_name=name,
        session_id=session_id,
        user_id=user_sub,
        input=body.message,
        context="production",
        trigger_type="api",
        run_by=user_sub,
        team=agent.team,
        status="running",
        started_at=now,
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
    agent_result = await db.execute(
        select(Agent).where(Agent.name == name, Agent.status == "active")
    )
    agent = agent_result.scalar_one_or_none()

    deployment: Optional[Deployment] = None
    if agent:
        deployment = await _running_deployment(db, agent.id)

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
