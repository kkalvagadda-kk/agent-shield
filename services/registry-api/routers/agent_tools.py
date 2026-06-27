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

from db import get_db
from models import Agent, AgentTool, Tool
from schemas import AgentToolBind, AgentToolResponse, PaginatedResponse, ToolResponse

logger = logging.getLogger(__name__)

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
@router.get(
    "/{name}/tools",
    response_model=PaginatedResponse[ToolResponse],
    summary="List tools bound to an agent",
)
async def list_agent_tools(
    name: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ToolResponse]:
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
