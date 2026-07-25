"""
AgentShield Registry API — MCP Servers router (MCP-as-tool-source, Phase 6 / T032).

Endpoints
---------
  POST   /api/v1/mcp-servers/            — register a server (synchronous discover)
  GET    /api/v1/mcp-servers/            — list servers (filterable, paginated)
  GET    /api/v1/mcp-servers/{id}        — detail + discovered tools (incl. inactive)
  PUT    /api/v1/mcp-servers/{id}        — partial update (name-immutable → 422)
  POST   /api/v1/mcp-servers/{id}/sync   — re-discover (vanished→inactive, drift-flag)
  DELETE /api/v1/mcp-servers/{id}        — guarded delete (409 while bound)

Two side-effects distinguish this from a plain CRUD router (contract §"Two
side-effects"):
  1. It **materializes the per-server credential Secret** (agentshield-mcp-server-{id}
     in agentshield-mcp) via mcp_secrets.materialize_server_secret on register / sync /
     auth-config change, and removes it on delete.
  2. It calls the **MCP Proxy /internal/discover** via mcp_proxy_client.discover_server.

Lifecycle invariants (models.py MCPServer/Tool comments; contract §"Lifecycle
invariants"), enforced HERE (never in the proxy):
  - `name` is IMMUTABLE after create → PUT rejects a differing name with 422.
  - DELETE is BLOCKED 409 while any AgentTool binds a child tool.
  - Vanished-upstream tools on /sync → status='inactive' (NEVER row-deleted).
  - A changed input_schema on /sync → auto-applied + flagged (health_detail.schema_drift).

Namespacing and ALL DB writes live here — the proxy connects + lists only.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from auth_middleware import get_optional_user
from db import get_db
from mcp_discovery import _materialize_and_discover
from mcp_secrets import delete_server_secret, materialize_server_secret
from models import Agent, AgentTool, AuthConfig, MCPServer, Tool
from schemas import (
    MCPServerCreate,
    MCPServerDetailResponse,
    MCPServerResponse,
    MCPServerSyncRequest,
    MCPServerSyncResponse,
    MCPServerUpdate,
    PaginatedResponse,
)
from routers.tools import _to_tool_response

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/mcp-servers", tags=["mcp-servers"])

# Immutable after create — a rename would orphan every child Tool.name (which is
# derived as f"{server.name}__{mcp_tool_name}"); server_url/transport/is_external are
# structural. PUT rejects a DIFFERING value for any of these with 422.
_IMMUTABLE_FIELDS = ("name", "server_url", "transport", "is_external")
# Fields that feed the per-server credential Secret's `connection` blob — a change to
# any of them (or to auth_config_id) requires re-materializing the Secret so the proxy
# reads fresh connection info on its next cache miss.
_SECRET_FEEDING_FIELDS = ("auth_config_id", "owner_team", "transport_config")


async def _get_server(server_id: uuid.UUID, db: AsyncSession) -> MCPServer:
    server = (
        await db.execute(select(MCPServer).where(MCPServer.id == server_id))
    ).scalar_one_or_none()
    if server is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"MCP server '{server_id}' not found.",
        )
    return server


# ---------------------------------------------------------------------------
# POST /api/v1/mcp-servers/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=MCPServerResponse,
    summary="Register an MCP server (synchronous discovery)",
)
async def create_mcp_server(
    body: MCPServerCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    # transport='stdio' and is_external+identity_mode!='none' are already rejected 422
    # by MCPServerCreate's model_validator — no re-check here.
    caller = (user or {}).get("sub") or x_user_sub

    existing = (
        await db.execute(select(MCPServer).where(MCPServer.name == body.name))
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"MCP server '{body.name}' already exists.",
        )

    if body.auth_config_id is not None:
        ac = (
            await db.execute(
                select(AuthConfig).where(AuthConfig.id == body.auth_config_id)
            )
        ).scalar_one_or_none()
        if ac is None:
            raise HTTPException(
                status_code=422,
                detail=f"AuthConfig '{body.auth_config_id}' not found.",
            )

    server = MCPServer(**body.model_dump())
    db.add(server)
    await db.flush()  # need the generated id for the Secret + discover

    # Register-time discovery is synchronous (FR-MCP-02) and never all-or-nothing:
    # the row is committed whether discover succeeds (status='connected') or fails
    # (status='error'). Always 201.
    await _materialize_and_discover(db, server, acknowledge_schema_drift=False)

    await db.commit()
    await db.refresh(server)
    logger.info(
        "mcp_servers: registered %s (%s) status=%s by=%s",
        server.name, server.id, server.status, caller,
    )
    return MCPServerResponse.model_validate(server)


# ---------------------------------------------------------------------------
# GET /api/v1/mcp-servers/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[MCPServerResponse],
    summary="List MCP servers",
)
async def list_mcp_servers(
    owner_team: str | None = Query(None),
    status: str | None = Query(None, description="connected|disconnected|error"),
    transport: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[MCPServerResponse]:
    # Servers are a Settings/admin concept (OQ-04) — no per-team visibility split,
    # every caller sees every server (like listAuthConfigs).
    q = select(MCPServer)
    if owner_team:
        q = q.where(MCPServer.owner_team == owner_team)
    if status:
        q = q.where(MCPServer.status == status)
    if transport:
        q = q.where(MCPServer.transport == transport)

    total = len((await db.execute(q.with_only_columns(MCPServer.id))).all())
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[MCPServerResponse.model_validate(s) for s in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/mcp-servers/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/{server_id}",
    response_model=MCPServerDetailResponse,
    summary="Get MCP server detail + discovered tools",
)
async def get_mcp_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> MCPServerDetailResponse:
    server = await _get_server(server_id, db)
    # Every child Tool row — INCLUDING status='inactive' (greyed in the detail page).
    # selectinload(mcp_server) so _to_tool_response can denorm without a lazy load.
    tools = (
        await db.execute(
            select(Tool)
            .options(selectinload(Tool.mcp_server))
            .where(Tool.mcp_server_id == server_id)
            .order_by(Tool.name.asc())
        )
    ).scalars().all()
    base = MCPServerResponse.model_validate(server)
    return MCPServerDetailResponse(
        **base.model_dump(),
        tools=[_to_tool_response(t) for t in tools],
    )


# ---------------------------------------------------------------------------
# PUT /api/v1/mcp-servers/{id}
# ---------------------------------------------------------------------------
@router.put(
    "/{server_id}",
    response_model=MCPServerResponse,
    summary="Update an MCP server (name immutable)",
)
async def update_mcp_server(
    server_id: uuid.UUID,
    body: MCPServerUpdate,
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    server = await _get_server(server_id, db)
    updates = body.model_dump(exclude_unset=True)

    # Lifecycle invariant: reject a DIFFERING value for any immutable field (422).
    # Setting it to the SAME value is a harmless no-op (accepted).
    for field in _IMMUTABLE_FIELDS:
        if field in updates and updates[field] is not None:
            if updates[field] != getattr(server, field):
                raise HTTPException(
                    status_code=422,
                    detail=(
                        f"'{field}' is immutable on an MCP server "
                        "(a rename would orphan every discovered tool's name). "
                        "Delete and re-register to change it."
                    ),
                )
            updates.pop(field)

    if "auth_config_id" in updates and updates["auth_config_id"] is not None:
        ac = (
            await db.execute(
                select(AuthConfig).where(AuthConfig.id == updates["auth_config_id"])
            )
        ).scalar_one_or_none()
        if ac is None:
            raise HTTPException(
                status_code=422,
                detail=f"AuthConfig '{updates['auth_config_id']}' not found.",
            )

    # Cross-field check on the MERGED post-update state (contract PUT).
    merged_is_external = updates.get("is_external", server.is_external)
    merged_identity = updates.get("identity_mode", server.identity_mode)
    if merged_is_external and merged_identity != "none":
        raise HTTPException(
            status_code=422,
            detail=(
                "an external server must use identity_mode='none' "
                "(identity modes are an internal-server concept)"
            ),
        )

    # Does this edit change anything the credential Secret's `connection` blob carries?
    secret_stale = any(
        field in updates and updates[field] != getattr(server, field)
        for field in _SECRET_FEEDING_FIELDS
    )

    for field, value in updates.items():
        setattr(server, field, value)
    server.updated_at = datetime.now(timezone.utc)

    # Re-materialize so the proxy reads fresh creds/connection on its next cache miss.
    # Does NOT re-run discovery (that is /sync's job).
    if secret_stale:
        await materialize_server_secret(db, server)

    await db.commit()
    await db.refresh(server)
    return MCPServerResponse.model_validate(server)


# ---------------------------------------------------------------------------
# POST /api/v1/mcp-servers/{id}/sync
# ---------------------------------------------------------------------------
@router.post(
    "/{server_id}/sync",
    response_model=MCPServerSyncResponse,
    summary="Re-discover an MCP server's tools",
)
async def sync_mcp_server(
    server_id: uuid.UUID,
    body: MCPServerSyncRequest | None = None,
    db: AsyncSession = Depends(get_db),
) -> MCPServerSyncResponse:
    server = await _get_server(server_id, db)
    acknowledge = bool(body.acknowledge_schema_drift) if body else False

    counters = await _materialize_and_discover(
        db, server, acknowledge_schema_drift=acknowledge
    )

    await db.commit()
    await db.refresh(server)
    return MCPServerSyncResponse(
        server=MCPServerResponse.model_validate(server),
        tools_added=counters["tools_added"],
        tools_updated=counters["tools_updated"],
        tools_inactivated=counters["tools_inactivated"],
        schema_drift_detected=counters["schema_drift_detected"],
    )


# ---------------------------------------------------------------------------
# DELETE /api/v1/mcp-servers/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{server_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an MCP server (blocked while its tools are bound)",
)
async def delete_mcp_server(
    server_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    server = await _get_server(server_id, db)

    # Lifecycle invariant: BLOCK 409 while any AgentTool binds a child tool, naming the
    # blocking tool(s) + agent(s) (stricter than DELETE /tools/{id} — scoped additive).
    bound = (
        await db.execute(
            select(Tool.name, Agent.name)
            .join(AgentTool, AgentTool.tool_id == Tool.id)
            .join(Agent, Agent.id == AgentTool.agent_id)
            .where(Tool.mcp_server_id == server_id)
        )
    ).all()
    if bound:
        blocking_tools = sorted({row[0] for row in bound})
        blocking_agents = sorted({row[1] for row in bound})
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": (
                    "Cannot delete — discovered tools from this server are bound to "
                    "agents."
                ),
                "blocking_tools": blocking_tools,
                "blocking_agents": blocking_agents,
            },
        )

    # Unbound → delete every child Tool row (Tool.mcp_server_id FK has no cascade, so
    # the children must go BEFORE the server), then the server, then the Secret.
    child_tools = (
        await db.execute(select(Tool).where(Tool.mcp_server_id == server_id))
    ).scalars().all()
    for t in child_tools:
        await db.delete(t)
    await db.delete(server)
    await db.commit()

    # External artifact — remove only after the DB delete committed (a rolled-back DB
    # delete must not leave the Secret gone). 404 on the Secret is a no-op.
    await delete_server_secret(server_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
