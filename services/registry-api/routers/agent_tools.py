"""
AgentShield Registry API — Agent-Tool bindings router.

Endpoints
---------
  POST   /api/v1/agents/{name}/tools              — bind a tool to an agent
  DELETE /api/v1/agents/{name}/tools/{tool_id}    — unbind a tool from an agent
  GET    /api/v1/agents/{name}/tools              — list tools bound to an agent
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_identity import AgentIdentity, get_optional_agent
from auth_middleware import get_optional_user, require_user
from db import get_db
from models import Agent, AgentTool, Tool
from schemas import AgentToolBind, AgentToolResponse, PaginatedResponse, ToolResponse

logger = logging.getLogger(__name__)

# MIXED (R1, FR-11): protection is per-endpoint here, NOT router-level, because
# deploy-controller and declarative-runner read an agent's tool bindings with no JWT.
# Protected: POST /{name}/tools, DELETE /{name}/tools/{tool_id}.
# Exempt: GET /{name}/tools (see the G-R1-5 comment below).
router = APIRouter(prefix="/api/v1/agents", tags=["agent-tools"])


async def _resolve_agent(name: str, db: AsyncSession) -> Agent:
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if agent is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Agent '{name}' not found.",
        )
    return agent


async def _resolve_tool(tool_id: uuid.UUID, db: AsyncSession) -> Tool:
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found.",
        )
    return tool


# ---------------------------------------------------------------------------
# POST /api/v1/agents/{name}/tools
# ---------------------------------------------------------------------------
@router.post(
    "/{name}/tools",
    status_code=status.HTTP_201_CREATED,
    response_model=AgentToolResponse,
    summary="Bind a tool to an agent",
    dependencies=[Depends(require_user)],
)
async def bind_tool(
    name: str,
    body: AgentToolBind,
    db: AsyncSession = Depends(get_db),
) -> AgentToolResponse:
    agent = await _resolve_agent(name, db)
    tool = await _resolve_tool(body.tool_id, db)

    existing = await db.execute(
        select(AgentTool).where(
            AgentTool.agent_id == agent.id,
            AgentTool.tool_id == tool.id,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tool '{tool.id}' is already bound to agent '{name}'.",
        )

    binding = AgentTool(
        agent_id=agent.id,
        tool_id=tool.id,
        added_by=body.added_by,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return AgentToolResponse.model_validate(binding)


# ---------------------------------------------------------------------------
# DELETE /api/v1/agents/{name}/tools/{tool_id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{name}/tools/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Unbind a tool from an agent",
    dependencies=[Depends(require_user)],
)
async def unbind_tool(
    name: str,
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    agent = await _resolve_agent(name, db)

    result = await db.execute(
        select(AgentTool).where(
            AgentTool.agent_id == agent.id,
            AgentTool.tool_id == tool_id,
        )
    )
    binding = result.scalar_one_or_none()
    if binding is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' is not bound to agent '{name}'.",
        )
    await db.delete(binding)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# GET /api/v1/agents/{name}/tools
# ---------------------------------------------------------------------------
# UNAUTHENTICATED BY NECESSITY (R1, G-R1-5). In-cluster machine callers with no user
# JWT: services/deploy-controller/tool_secrets.py:36 (resolves which tool credentials
# an agent Pod needs at deploy time) and services/declarative-runner/
# workflow_executor.py:171 (resolves the agent's tool set at run time). Closing this
# needs a service identity that docs/design/identity-propagation-architecture.md owns
# (migrations 0080-0082); doing it here would break control-plane reconciliation and
# every declarative run. Same posture as routers/internal.py: cluster-internal,
# NetworkPolicy-trusted. suite-97 T-S97-011 pins this exemption set.
@router.get(
    "/{name}/tools",
    response_model=PaginatedResponse[ToolResponse],
    summary="List tools bound to an agent",
)
async def list_agent_tools(
    name: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    agent_ident: AgentIdentity | None = Depends(get_optional_agent),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ToolResponse]:
    """The tools bound to one agent — the BINDING question, not a catalog question.

    Two legitimate caller kinds, checked explicitly rather than by priority fallthrough:

      * an AGENT POD resolving its own tools at startup (the SDK tool_resolver and
        declarative-runner). It presents a projected Kubernetes ServiceAccount token, and the
        agent named in the path MUST equal the agent named in the verified token — otherwise
        any pod could read any agent's bindings by editing the URL.
      * a HUMAN in Studio (the tools picker, the agent detail page).

    No `publish_status` filter, deliberately. A pod's authority over a tool is its binding
    plus OPA Gate 3, never the catalog flag; this is the same set Gate 3 authorizes, which is
    why the registry and the policy engine now agree by construction.
    """
    if agent_ident is not None:
        if agent_ident.agent_name != name:
            logger.warning(
                "list_agent_tools: DENY pod %s asked for agent %r",
                agent_ident.sa_subject, name,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"This ServiceAccount belongs to agent '{agent_ident.agent_name}'; "
                    f"it cannot read '{name}'."
                ),
            )
    elif user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=(
                "Requires either a user token or an agent ServiceAccount token "
                "(audience 'agentshield-registry-api')."
            ),
        )

    agent = await _resolve_agent(name, db)

    q = (
        select(Tool)
        .join(AgentTool, AgentTool.tool_id == Tool.id)
        .where(AgentTool.agent_id == agent.id)
    )
    total = len((await db.execute(q.with_only_columns(Tool.id))).all())
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[ToolResponse.model_validate(t) for t in rows],
        total=total,
    )
