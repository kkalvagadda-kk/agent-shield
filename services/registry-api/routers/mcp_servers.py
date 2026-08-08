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

from auth_middleware import get_optional_user, require_user
from rbac import get_user_global_role, get_user_team
from credential_provider import CredentialRef, get_provider
from db import get_db
from mcp_discovery import _materialize_and_discover
from mcp_secrets import delete_server_secret, materialize_server_secret
from models import Agent, AgentTool, AuthConfig, MCPOAuthGrant, MCPServer, Tool
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
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> MCPServerResponse:
    """Register an MCP server. The registering team OWNS it, and its discovered tools.

    `owner_team` is DERIVED from the caller's team assignment (Decision 46), not taken from
    the request body — the Studio Register Server modal collects only a name and a URL, so
    the body field was always absent and `MCPServer(**body.model_dump())` left it NULL.

    That was harmless while tools defaulted to `published`. Migration `0080` made them
    `private`, and `mcp_discovery.py:154` copies `server.owner_team` onto every tool it
    discovers — so a NULL server owner produced private tools owned by NOBODY, which the
    team-scoped catalog filter (`published OR owner_team == caller team`) shows to no one.
    Registering a server through Studio silently produced a catalog of invisible tools.

    Caught by `mcp-servers.spec.ts` — the tools-source filter had no chip for the server
    that had just been registered.

    Same fix and same reasoning as `create_tool` (0.2.267): a body-supplied `owner_team` is
    honoured only for a platform-admin, because seeding and admin-side registration
    legitimately assign ownership; for anyone else it would let a caller hand their server
    to a team they are not in.
    """
    # transport='stdio' and is_external+identity_mode!='none' are already rejected 422
    # by MCPServerCreate's model_validator — no re-check here.
    caller = claims["sub"]
    caller_team = await get_user_team(db, caller)
    if body.owner_team and body.owner_team != caller_team:
        if await get_user_global_role(db, caller) != "platform-admin":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=(
                    f"Cannot register an MCP server owned by team '{body.owner_team}': you are "
                    f"in '{caller_team}'. Only a platform-admin may assign ownership elsewhere."
                ),
            )
        owner_team = body.owner_team
    else:
        owner_team = caller_team

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

    # owner_team excluded from the splat: it is DERIVED above and must not be able to
    # reach the row by a path the guard does not cover.
    server = MCPServer(**body.model_dump(exclude={"owner_team"}))
    server.owner_team = owner_team
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

    # OAuth is external-only (Phase 4 WS-2). Check the MERGED external_auth_mode against
    # the merged is_external so PATCH-ing one without the other can't produce the illegal
    # oauth+internal state. oauth ⇒ external ⇒ identity_mode='none' (enforced above), so
    # no separate identity check is needed here.
    merged_external_auth = updates.get(
        "external_auth_mode", server.external_auth_mode
    )
    if merged_external_auth == "oauth" and not merged_is_external:
        raise HTTPException(
            status_code=422,
            detail=(
                "external_auth_mode='oauth' requires is_external=true "
                "(OAuth is an external-server upstream-auth concept)"
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
    # A manual sync is an explicit "re-check this server now" — clear any accumulated
    # health-loop backoff so a just-fixed server recovers promptly instead of staying
    # skip-listed for up to mcp_health_max_backoff_cycles sweeps.
    import mcp_health
    mcp_health.reset_backoff(str(server_id))

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

    # OAuth cleanup (Phase 4 WS-2): the mcp_oauth_grants rows are dropped by the
    # ON DELETE CASCADE FK when the server row goes, BUT the refresh tokens + the DCR
    # client secret behind their CredentialRefs live in the provider store (a different
    # table / AWS SM) which the FK cannot reach. Collect the refs NOW (while the rows
    # exist), then provider.delete them after the DB delete commits.
    oauth_refs: list[str] = [
        ref
        for (ref,) in (
            await db.execute(
                select(MCPOAuthGrant.credential_ref).where(
                    MCPOAuthGrant.server_id == server_id,
                    MCPOAuthGrant.credential_ref.is_not(None),
                )
            )
        ).all()
    ]
    if server.oauth_client_ref:
        oauth_refs.append(server.oauth_client_ref)

    # Unbound → delete every child Tool row (Tool.mcp_server_id FK has no cascade, so
    # the children must go BEFORE the server), then the server, then the Secret.
    child_tools = (
        await db.execute(select(Tool).where(Tool.mcp_server_id == server_id))
    ).scalars().all()
    for t in child_tools:
        await db.delete(t)
    await db.delete(server)
    await db.commit()

    # External artifacts — remove only after the DB delete committed (a rolled-back DB
    # delete must not leave them gone). 404 on the Secret is a no-op.
    await delete_server_secret(server_id)
    # provider.delete is idempotent (absent ref = no-op); a failure here must not fail the
    # (already committed) server delete — the ref rows are already gone via the cascade.
    provider = get_provider()
    for ref in oauth_refs:
        try:
            await provider.delete(CredentialRef.parse(ref))
        except Exception as exc:  # noqa: BLE001 — best-effort external cleanup
            logger.warning(
                "mcp_servers: failed to delete OAuth credential ref %s for server %s: %s",
                ref, server_id, exc,
            )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
