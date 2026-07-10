"""
AgentShield Registry API — Tools router.

Endpoints
---------
  POST   /api/v1/tools/              — register a new tool
  GET    /api/v1/tools/              — list tools (filterable, paginated)
  GET    /api/v1/tools/{id}          — get tool by ID
  PUT    /api/v1/tools/{id}          — update tool fields
  DELETE /api/v1/tools/{id}          — deprecate tool (soft-delete)
  GET    /api/v1/tools/{id}/agents   — list agents bound to this tool
  POST   /api/v1/tools/{id}/test     — test-invoke the tool (stub)
"""

from __future__ import annotations

import logging
import time
import uuid

from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from models import Agent, AgentTool, AuthConfig, Tool
from schemas import (
    AgentResponse,
    PaginatedResponse,
    ToolCreate,
    ToolResponse,
    ToolTestRequest,
    ToolTestResponse,
    ToolUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tools", tags=["tools"])


async def _get_tool(tool_id: uuid.UUID, db: AsyncSession) -> Tool:
    result = await db.execute(select(Tool).where(Tool.id == tool_id))
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found.",
        )
    return tool


# ---------------------------------------------------------------------------
# POST /api/v1/tools/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ToolResponse,
    summary="Register a new tool",
)
async def create_tool(
    body: ToolCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    caller = (user or {}).get("sub") or x_user_sub

    existing = await db.execute(select(Tool).where(Tool.name == body.name))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Tool '{body.name}' already exists.",
        )

    if body.auth_config_id:
        ac = (await db.execute(select(AuthConfig).where(AuthConfig.id == body.auth_config_id))).scalar_one_or_none()
        if ac is None:
            raise HTTPException(status_code=422, detail=f"AuthConfig '{body.auth_config_id}' not found.")

    tool = Tool(**body.model_dump())
    tool.created_by = caller
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    return ToolResponse.model_validate(tool)


# ---------------------------------------------------------------------------
# GET /api/v1/tools/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[ToolResponse],
    summary="List tools",
)
async def list_tools(
    type: str | None = Query(None),
    risk_level: str | None = Query(None),
    status: str | None = Query(None),
    owner_team: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[ToolResponse]:
    caller = (user or {}).get("sub") or x_user_sub

    q = select(Tool)

    # Visibility: published tools visible to all; private only to creator.
    if caller:
        q = q.where(or_(Tool.publish_status == "published", Tool.created_by == caller))
    else:
        q = q.where(Tool.publish_status == "published")

    if type:
        q = q.where(Tool.type == type)
    if risk_level:
        q = q.where(Tool.risk_level == risk_level)
    if status:
        q = q.where(Tool.status == status)
    if owner_team:
        q = q.where(Tool.owner_team == owner_team)

    total_q = q.with_only_columns(Tool.id)
    total = len((await db.execute(total_q)).all())

    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[ToolResponse.model_validate(t) for t in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/tools/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/{tool_id}",
    response_model=ToolResponse,
    summary="Get tool by ID",
)
async def get_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    return ToolResponse.model_validate(await _get_tool(tool_id, db))


# ---------------------------------------------------------------------------
# PUT /api/v1/tools/{id}
# ---------------------------------------------------------------------------
@router.put(
    "/{tool_id}",
    response_model=ToolResponse,
    summary="Update tool",
)
async def update_tool(
    tool_id: uuid.UUID,
    body: ToolUpdate,
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    tool = await _get_tool(tool_id, db)

    updates = body.model_dump(exclude_unset=True)
    if "auth_config_id" in updates and updates["auth_config_id"] is not None:
        ac = (await db.execute(select(AuthConfig).where(AuthConfig.id == updates["auth_config_id"]))).scalar_one_or_none()
        if ac is None:
            raise HTTPException(status_code=422, detail=f"AuthConfig '{updates['auth_config_id']}' not found.")

    for field, value in updates.items():
        setattr(tool, field, value)

    await db.commit()
    await db.refresh(tool)
    return ToolResponse.model_validate(tool)


# ---------------------------------------------------------------------------
# DELETE /api/v1/tools/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deprecate tool",
)
async def delete_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    tool = await _get_tool(tool_id, db)
    tool.status = "deprecated"
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# GET /api/v1/tools/{id}/agents
# ---------------------------------------------------------------------------
@router.get(
    "/{tool_id}/agents",
    response_model=PaginatedResponse[AgentResponse],
    summary="List agents bound to this tool",
)
async def list_agents_for_tool(
    tool_id: uuid.UUID,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AgentResponse]:
    await _get_tool(tool_id, db)

    q = (
        select(Agent)
        .join(AgentTool, AgentTool.agent_id == Agent.id)
        .where(AgentTool.tool_id == tool_id)
    )
    total = len((await db.execute(q.with_only_columns(Agent.id))).all())
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[AgentResponse.model_validate(a) for a in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# POST /api/v1/tools/{id}/test
# ---------------------------------------------------------------------------
@router.post(
    "/{tool_id}/test",
    response_model=ToolTestResponse,
    summary="Test-invoke a tool",
)
async def test_tool(
    tool_id: uuid.UUID,
    body: ToolTestRequest,
    db: AsyncSession = Depends(get_db),
) -> ToolTestResponse:
    tool = await _get_tool(tool_id, db)
    start = time.monotonic()

    if tool.type == "http" and tool.http_url:
        # Real HTTP invocation deferred to Phase 9 (when full tool execution is built).
        # Return a stub success so the endpoint is callable and schema-correct.
        duration_ms = int((time.monotonic() - start) * 1000)
        return ToolTestResponse(
            success=True,
            output={"stub": True, "tool": tool.name, "input": body.input},
            duration_ms=duration_ms,
        )

    duration_ms = int((time.monotonic() - start) * 1000)
    return ToolTestResponse(
        success=True,
        output={"stub": True, "tool": tool.name, "input": body.input},
        duration_ms=duration_ms,
    )
