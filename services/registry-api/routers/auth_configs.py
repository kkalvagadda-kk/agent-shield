"""
AgentShield Registry API — Auth Configs router.

Endpoints
---------
  POST   /api/v1/auth-configs/              — create auth config (auto-creates K8s Secret)
  GET    /api/v1/auth-configs/              — list auth configs
  GET    /api/v1/auth-configs/{id}          — get auth config by ID
  GET    /api/v1/auth-configs/{id}/secret-ref — get k8s_secret_ref (internal only)
  PUT    /api/v1/auth-configs/{id}          — update auth config
  DELETE /api/v1/auth-configs/{id}          — delete auth config (blocked if tools reference it)
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from crypto import decrypt_json, encrypt_json
from db import get_db
from k8s import delete_secret, secret_exists, upsert_secret
from models import AuthConfig, MCPServer, Tool
from schemas import AuthConfigCreate, AuthConfigResponse, AuthConfigUpdate, PaginatedResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth-configs", tags=["auth-configs"])

_PLATFORM_NAMESPACE = "agentshield-platform"


async def _get_auth_config(config_id: uuid.UUID, db: AsyncSession) -> AuthConfig:
    result = await db.execute(select(AuthConfig).where(AuthConfig.id == config_id))
    row = result.scalar_one_or_none()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"AuthConfig '{config_id}' not found.",
        )
    return row


# ---------------------------------------------------------------------------
# POST /api/v1/auth-configs/
# ---------------------------------------------------------------------------
@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=AuthConfigResponse,
    summary="Create auth config",
)
async def create_auth_config(
    body: AuthConfigCreate,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    existing = (await db.execute(select(AuthConfig).where(AuthConfig.name == body.name))).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"AuthConfig '{body.name}' already exists.")

    dump = body.model_dump(exclude={"credentials"})
    config = AuthConfig(**dump)
    db.add(config)
    await db.flush()

    if body.credentials:
        secret_name = f"auth-config-{config.id}"
        # Durable source of truth: encrypt into the DB (captured by pg backups).
        config.credentials_encrypted = encrypt_json(body.credentials)
        # Runtime materialization: the K8s secret pods mount.
        await upsert_secret(secret_name, _PLATFORM_NAMESPACE, body.credentials)
        config.k8s_secret_ref = secret_name

    await db.commit()
    await db.refresh(config)
    return AuthConfigResponse.model_validate(config)


# ---------------------------------------------------------------------------
# GET /api/v1/auth-configs/
# ---------------------------------------------------------------------------
@router.get(
    "/",
    response_model=PaginatedResponse[AuthConfigResponse],
    summary="List auth configs",
)
async def list_auth_configs(
    type: str | None = Query(None),
    owner_team: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AuthConfigResponse]:
    q = select(AuthConfig)
    if type:
        q = q.where(AuthConfig.type == type)
    if owner_team:
        q = q.where(AuthConfig.owner_team == owner_team)

    total = len((await db.execute(q.with_only_columns(AuthConfig.id))).all())
    rows = (await db.execute(q.offset(offset).limit(limit))).scalars().all()
    return PaginatedResponse(
        items=[AuthConfigResponse.model_validate(c) for c in rows],
        total=total,
    )


# ---------------------------------------------------------------------------
# GET /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.get(
    "/{config_id}",
    response_model=AuthConfigResponse,
    summary="Get auth config by ID",
)
async def get_auth_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    return AuthConfigResponse.model_validate(await _get_auth_config(config_id, db))


# ---------------------------------------------------------------------------
# GET /api/v1/auth-configs/{id}/secret-ref  (internal — deploy-controller)
# ---------------------------------------------------------------------------
@router.get(
    "/{config_id}/secret-ref",
    summary="Get k8s_secret_ref for deploy-controller",
)
async def get_auth_config_secret_ref(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Return the K8s secret name, RE-MATERIALIZING it from the DB if it has vanished.

    The DB is the source of truth and the K8s Secret is a derived cache — that is what
    `credentials_encrypted` is FOR (see `create_auth_config`: "Durable source of truth:
    encrypt into the DB … Runtime materialization: the K8s secret pods mount"). Until
    this endpoint, nothing ever read the durable copy back: the Secret was written ONLY
    at auth-config create/update, so once it was gone it was gone, and re-entering the
    credential by hand was the only recovery. The intent was right; the loop was open.

    That cost a live demo. `serper-dev` pointed at `auth-config-<id>` which did not
    exist (an older Secret held the key under a stale id — DB-restore drift). The agent
    sent the literal `{{serper_api_key}}` to Serper and got a 403 — because FOUR layers
    each failed quietly: no re-materialize (here), a best-effort copy in
    `tool_secrets.py`, `envFrom … optional: true` (K8s silently skips a missing Secret),
    and `_substitute_vars` passing an unresolved `{{var}}` through verbatim.

    This is the right seam: deploy-controller ALREADY calls this immediately before
    copying the Secret into the agent's namespace, so the heal happens exactly when it
    is needed, on the deploy path, with **no plaintext on the wire** (only the name) and
    no change to deploy-controller. It is also where an external secret store (Vault /
    ASM) will plug in: swap the `decrypt_json` read for the store's read and every other
    layer stays put.

    Fail-LOUD, deliberately: if the row has no durable copy to heal from we 409 rather
    than return a name that resolves to nothing. A caller that gets a name assumes the
    Secret exists; handing back a phantom is how the placeholder reached Serper.
    """
    config = await _get_auth_config(config_id, db)

    if not config.k8s_secret_ref:
        return {"id": str(config.id), "k8s_secret_ref": None}

    if await secret_exists(config.k8s_secret_ref, _PLATFORM_NAMESPACE):
        return {"id": str(config.id), "k8s_secret_ref": config.k8s_secret_ref}

    if not config.credentials_encrypted:
        logger.error(
            "auth_config %s: k8s secret %s is MISSING and there is no durable copy to "
            "re-materialize from — the credential must be re-entered",
            config.id, config.k8s_secret_ref,
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"AuthConfig '{config.name}' has no stored credentials and its Kubernetes "
                f"secret '{config.k8s_secret_ref}' does not exist. Re-enter the credential "
                f"— returning the name would hand out a reference that resolves to nothing, "
                f"and the agent would send the unsubstituted placeholder to the upstream API."
            ),
        )

    logger.warning(
        "auth_config %s: k8s secret %s was MISSING — re-materializing from the DB",
        config.id, config.k8s_secret_ref,
    )
    await upsert_secret(
        config.k8s_secret_ref, _PLATFORM_NAMESPACE, decrypt_json(config.credentials_encrypted)
    )
    return {
        "id": str(config.id),
        "k8s_secret_ref": config.k8s_secret_ref,
        "rematerialized": True,
    }


# ---------------------------------------------------------------------------
# PUT /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.put(
    "/{config_id}",
    response_model=AuthConfigResponse,
    summary="Update auth config",
)
async def update_auth_config(
    config_id: uuid.UUID,
    body: AuthConfigUpdate,
    db: AsyncSession = Depends(get_db),
) -> AuthConfigResponse:
    config = await _get_auth_config(config_id, db)

    for field, value in body.model_dump(exclude={"credentials"}, exclude_unset=True).items():
        setattr(config, field, value)

    if body.credentials:
        secret_name = config.k8s_secret_ref or f"auth-config-{config.id}"
        # Durable source of truth (backed up) + runtime K8s materialization.
        config.credentials_encrypted = encrypt_json(body.credentials)
        await upsert_secret(secret_name, _PLATFORM_NAMESPACE, body.credentials)
        config.k8s_secret_ref = secret_name

    config.updated_at = func.now()
    await db.commit()
    await db.refresh(config)
    return AuthConfigResponse.model_validate(config)


# ---------------------------------------------------------------------------
# DELETE /api/v1/auth-configs/{id}
# ---------------------------------------------------------------------------
@router.delete(
    "/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete auth config",
)
async def delete_auth_config(
    config_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response:
    config = await _get_auth_config(config_id, db)

    referencing_tools = (
        await db.execute(
            select(Tool.name).where(Tool.auth_config_id == config_id)
        )
    ).scalars().all()
    referencing_mcp = (
        await db.execute(
            select(MCPServer.name).where(MCPServer.auth_config_id == config_id)
        )
    ).scalars().all()
    if referencing_tools or referencing_mcp:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Cannot delete — referenced by tools or MCP servers.",
                "tools": list(referencing_tools),
                "mcp_servers": list(referencing_mcp),
            },
        )

    if config.k8s_secret_ref:
        await delete_secret(config.k8s_secret_ref, _PLATFORM_NAMESPACE)

    await db.delete(config)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
