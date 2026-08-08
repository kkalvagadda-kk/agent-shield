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
from sqlalchemy.orm import selectinload

from auth_middleware import get_optional_user, require_user
from catalog_visibility import CallerKind, catalog_visibility_clause
from rbac import get_user_global_role, get_user_team
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


# Methods that are provably read-only. Anything else — a write method, a python /
# native / mcp_tool whose body we cannot inspect, or an HTTP tool with no method at
# all — is treated as side-effecting.
_READ_ONLY_HTTP_METHODS = frozenset({"GET", "HEAD"})


def infer_side_effecting(tool_type: str | None, http_method: str | None) -> bool:
    """Classify a tool as side-effecting (Eval v2 E-2) — the ONE rule.

    Fail-closed by construction: return False (read-only, delivered for real even
    under eval) ONLY for an HTTP tool whose method is provably read-only. Everything
    else returns True, so under `eval_mode=record` the delivery edge mocks it rather
    than invoking it — an unclassifiable tool is never allowed through.

    Callers may override the result explicitly (`ToolCreate/ToolUpdate.side_effecting`);
    this is the inference used when they don't. Migration 0063 mirrors this rule in
    SQL to backfill the existing rows (a migration is a snapshot and must not import
    app code that will drift under it) — keep the two in sync.
    """
    if (tool_type or "").lower() != "http":
        return True
    return (http_method or "").upper() not in _READ_ONLY_HTTP_METHODS


async def _get_tool(tool_id: uuid.UUID, db: AsyncSession) -> Tool:
    result = await db.execute(
        select(Tool)
        .options(selectinload(Tool.mcp_server))
        .where(Tool.id == tool_id)
    )
    tool = result.scalar_one_or_none()
    if tool is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Tool '{tool_id}' not found.",
        )
    return tool


def _to_tool_response(tool: Tool) -> ToolResponse:
    """Build a ToolResponse, denormalizing the source MCP server onto the 3
    `mcp_server_*` fields so the ToolsPicker can badge the source server and the
    SDK can decide `scan_results` without a second lookup. For a non-mcp tool
    (`mcp_server_id is None`) the relationship is never touched — the fields stay
    None. Callers rendering `mcp_tool` rows MUST eager-load `Tool.mcp_server`
    (`selectinload`) so this never triggers an async lazy load.
    """
    resp = ToolResponse.model_validate(tool)
    if tool.mcp_server_id is None:
        return resp
    server = tool.mcp_server
    if server is None:
        return resp
    return resp.model_copy(
        update={
            "mcp_server_name": server.name,
            "mcp_server_is_external": server.is_external,
            "mcp_server_scan_results": server.scan_results,
        }
    )


# ---------------------------------------------------------------------------
# POST /api/v1/tools/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=ToolResponse,
    summary="Register a new tool",
    # G-R3-6: this router had NO auth at all — POST/PUT/DELETE included. A tool's
    # risk_level drives the HITL gate and OPA's risk->action rule, so an anonymous
    # PUT that lowers it relaxes every control for every agent bound to that tool.
    # MUTATIONS are gated; the READS stay open because in-cluster machine callers
    # reach them with no Authorization header — declarative-runner
    # workflow_executor.py:247 (GET /tools/{id}) and the SDK tool_resolver
    # (GET /tools/). Closing those needs the service identity that
    # identity-propagation-architecture.md Phase 3 owns.
    dependencies=[Depends(require_user)],
)
async def create_tool(
    body: ToolCreate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> ToolResponse:
    """Register a tool. The creating team OWNS it (Decision 46).

    `owner_team` is derived from the CALLER's team assignment, not from the request
    body. Before this, `Tool(**body.model_dump(...))` took it from the body, which
    defaults to `None` — and `tool_access.team_may_use_tool` treats a null owner as
    usable by EVERY team. So creating a tool through Studio produced the most
    permissive state available: 65 of ~173 rows on the test cluster. Not drift; it is
    what the create path produced.

    A body-supplied `owner_team` is honoured ONLY for a platform-admin, because
    seeding and admin-side creation legitimately assign ownership. For anyone else a
    body field would let a caller assign their tool to a team they are not in, which
    is the same forgeable-attribution shape as the `X-User-Sub` fallback R2 deleted
    from `create_agent`.
    """
    caller = claims["sub"]
    caller_team = await get_user_team(db, caller)
    if body.owner_team and body.owner_team != caller_team:
        # Only platform-admin may assign ownership elsewhere. get_user_global_role
        # raises NoPlatformRole for a row-less sub (R0), which main.create_app maps
        # to 403 — the correct answer for a caller whose identity is corrupt.
        if await get_user_global_role(db, caller) != "platform-admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Cannot create a tool owned by team '{body.owner_team}': you are in "
                    f"'{caller_team}'. Only a platform-admin may assign ownership to another team."
                ),
            )
        owner_team = body.owner_team
    else:
        owner_team = caller_team

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

    # `side_effecting` is NOT NULL — a body that leaves it unset must not write NULL
    # over the column default, so resolve it here: an explicit value wins, otherwise
    # infer it from the method (fail-closed). Excluded from the kwargs splat so the
    # two can never both apply.
    # owner_team excluded from the splat: it is DERIVED above, never taken from the body.
    tool = Tool(**body.model_dump(exclude={"side_effecting", "owner_team"}))
    tool.owner_team = owner_team
    tool.side_effecting = (
        body.side_effecting
        if body.side_effecting is not None
        else infer_side_effecting(body.type, body.http_method)
    )
    tool.created_by = caller
    db.add(tool)
    await db.commit()
    await db.refresh(tool)
    # Only an mcp_tool row (created here only if a caller explicitly passes
    # mcp_server_id) needs the source server eager-loaded for the denorm.
    if tool.mcp_server_id is not None:
        await db.refresh(tool, ["mcp_server"])
    return _to_tool_response(tool)


# ---------------------------------------------------------------------------
# GET /api/v1/tools/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[ToolResponse],
    summary="List tools",
)
async def list_tools(
    name: str | None = Query(
        None,
        description="Exact tool name. The SDK tool resolver has always sent this "
        "filter; without it FastAPI silently ignored the param and returned "
        "arbitrary rows, so an agent asking for 'issue_refund' resolved whatever "
        "tool sorted first (observed: 'http_echo' → a critical-risk OPA fixture).",
    ),
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

    q = select(Tool).options(selectinload(Tool.mcp_server))

    # Visibility (Decision 47 / migration 0080). One producer, shared with list_skills —
    # this predicate used to be an inline `published OR created_by == caller` copied into
    # both handlers, and 0080 made it load-bearing by flipping the default to 'private'.
    #
    # A caller with no token is an in-cluster agent pod (the SDK tool_resolver, which calls
    # this exact endpoint with ?name=X at startup). It gets NO publish filter: a pod's
    # authority over a tool is its binding plus OPA Gate 3, not the catalog flag. Filtering
    # it would kill every agent bound to a tool created after 0080. See
    # catalog_visibility.py for why this is a named parameter and not a sniff.
    caller_kind = CallerKind.HUMAN if caller else CallerKind.IN_CLUSTER_MACHINE
    vis = catalog_visibility_clause(
        publish_status_col=Tool.publish_status,
        owner_team_col=Tool.owner_team,
        caller_kind=caller_kind,
        caller_team=await get_user_team(db, caller) if caller else None,
    )
    if vis is not None:
        q = q.where(vis)

    if name:
        q = q.where(Tool.name == name)
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
        items=[_to_tool_response(t) for t in rows],
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
    return _to_tool_response(await _get_tool(tool_id, db))


# ---------------------------------------------------------------------------
# PUT /api/v1/tools/{id}
# ---------------------------------------------------------------------------
@router.put(
    "/{tool_id}",
    response_model=ToolResponse,
    summary="Update tool",
    dependencies=[Depends(require_user)],  # G-R3-6 — see create_tool above
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

    # Eval v2 E-2 — the classification must not go stale under a method edit: changing
    # GET→POST without re-inferring would leave a write tool marked read-only, i.e.
    # delivered for real under eval (fail-OPEN). Rule: the same request may override
    # explicitly; otherwise a method change re-runs the inference.
    if "http_method" in updates and updates.get("side_effecting") is None:
        updates["side_effecting"] = infer_side_effecting(
            tool.type, updates["http_method"]
        )
    elif updates.get("side_effecting") is None:
        updates.pop("side_effecting", None)  # explicit null is not an override

    for field, value in updates.items():
        setattr(tool, field, value)

    await db.commit()
    await db.refresh(tool)
    # _get_tool eager-loaded mcp_server; only a change to mcp_server_id makes that
    # stale, so reload the relationship just in that case before denormalizing.
    if "mcp_server_id" in updates:
        await db.refresh(tool, ["mcp_server"])
    return _to_tool_response(tool)


# ---------------------------------------------------------------------------
# DELETE /api/v1/tools/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{tool_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Deprecate tool",
    dependencies=[Depends(require_user)],  # G-R3-6 — see create_tool above
)
async def delete_tool(
    tool_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    tool = await _get_tool(tool_id, db)
    # An mcp_tool row's lifecycle is owned solely by its MCPServer (discovery/sync/
    # delete) — it is never independently deletable. Reject BEFORE the soft-delete so
    # the row can never be deprecated out from under the server that manages it. It
    # goes away only when the server is deleted (blocked while bound); vanished-upstream
    # tools are marked status='inactive' on /sync, never row-deleted (models.py Tool
    # invariant; docs/design/mcp-tool-source-architecture.md §8).
    if tool.type == "mcp_tool":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This tool's lifecycle is owned by the MCP server that discovered it; "
                "delete it via the server (DELETE /api/v1/mcp-servers/{id}), not here."
            ),
        )
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
    dependencies=[Depends(require_user)],  # G-R3-6 — see create_tool above
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
