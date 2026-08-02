"""
Agent Triggers router — CRUD for schedule + webhook triggers.

POST/GET   /api/v1/agents/{name}/triggers
GET/PATCH/DELETE /api/v1/agents/{name}/triggers/{trigger_id}
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user, require_user
from db import AsyncSessionLocal
from models import Agent, AgentRun, AgentTrigger
from observability_backend import get_observability_backend
from rbac import ENFORCE_TRIGGER_MGMT, can_manage_artifact
from schemas import (
    AgentRunResponse,
    AgentTriggerCreate,
    AgentTriggerResponse,
    AgentTriggerUpdate,
    RotateTokenResponse,
)
from trigger_lifecycle import apply_trigger_update
from trigger_utils import _new_token, _webhook_url

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/agents", tags=["triggers"])


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def _get_agent(name: str, db: AsyncSession) -> Agent:
    result = await db.execute(select(Agent).where(Agent.name == name))
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return agent


@router.post(
    "/{name}/triggers",
    response_model=AgentTriggerResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_trigger(
    name: str,
    body: AgentTriggerCreate,
    x_user_sub: str | None = Header(None, alias="X-User-Sub"),
    user: dict | None = Depends(get_optional_user),
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    agent = await _get_agent(name, db)
    if not await can_manage_artifact(db, claims["sub"], agent.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], agent.id,
        )
    # The human who arms the trigger — authorizes the standing daemon run; audit
    # reads "service:X on behalf of {armed_by}" (WS-2 T007/T008 producer).
    armed_by = (user or {}).get("sub") or x_user_sub
    # Webhook triggers get a server-generated token; only its sha256 is stored.
    plaintext = None
    token_hash = None
    # WS-4: a webhook trigger is born `token` and UPGRADES to `client_signed` when its
    # FIRST client is registered (routers/webhook_clients.py::create_webhook_client).
    #
    # Why not born `client_signed`? Because `client_signed` + an empty allowlist is a
    # trigger that authenticates NOBODY — the gateway has no client row to match, so
    # every request 401s. Birthing it that way would make the unusable state the DEFAULT
    # state while this endpoint hands the caller a `token` + `webhook_url` that cannot
    # work until a second, separate API call is made. Flipping on first registration
    # instead makes the mode track reality: `client_signed` ⟺ at least one client
    # exists. The illegal state is unrepresentable rather than merely discouraged.
    #
    # This is still EXPLICIT dual-mode, not a fallthrough: the mode is a stored
    # per-trigger column and the gateway branches on it alone — it never "tries the
    # token, then falls back to signed". And it is the migration story WS-4 asks for
    # (plan §1: "dual-mode so existing token senders keep working during migration…
    # migrate senders one at a time"): the operator upgrades a trigger by registering
    # an application for it, which is precisely when per-app auth becomes possible.
    # Sanctioned by contracts/webhook-signing.md: "Creating the first client on a
    # webhook trigger sets agent_triggers.auth_mode='client_signed'".
    #
    # Schedule triggers have no inbound auth at all and keep the 'token' default.
    auth_mode = "token"
    if body.trigger_type == "webhook":
        plaintext, token_hash = _new_token()
    trigger = AgentTrigger(
        agent_id=agent.id,
        trigger_type=body.trigger_type,
        auth_mode=auth_mode,
        cron_expression=body.cron_expression,
        timezone=body.timezone,
        enabled=body.enabled,
        filter_conditions=body.filter_conditions,
        input_payload=body.input_payload,
        alert_email=body.alert_email,
        alert_on_failure=body.alert_on_failure,
        token_hash=token_hash,
        armed_by=armed_by,
        approver_role=body.approver_role,
    )
    db.add(trigger)
    await db.commit()
    await db.refresh(trigger)
    # Attach the plaintext token + full webhook URL as transient attributes so they
    # are returned ONCE in this create response (never persisted, never in list/get).
    # Both are None for schedule triggers.
    trigger.token = plaintext
    trigger.webhook_url = _webhook_url(name, plaintext) if plaintext else None
    logger.info("created %s trigger for agent '%s' (id=%s)", body.trigger_type, name, trigger.id)
    return trigger


@router.get("/{name}/triggers", response_model=list[AgentTriggerResponse])
async def list_triggers(
    name: str,
    db: AsyncSession = Depends(get_db),
) -> list[AgentTrigger]:
    agent = await _get_agent(name, db)
    result = await db.execute(
        select(AgentTrigger)
        .where(AgentTrigger.agent_id == agent.id)
        .order_by(AgentTrigger.created_at)
    )
    return list(result.scalars().all())


@router.get("/{name}/triggers/{trigger_id}", response_model=AgentTriggerResponse)
async def get_trigger(
    name: str,
    trigger_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    agent = await _get_agent(name, db)
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.agent_id == agent.id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return trigger


@router.get(
    "/{name}/triggers/{trigger_id}/runs",
    response_model=list[AgentRunResponse],
    summary="List the runs this trigger fired",
)
async def list_trigger_runs(
    name: str,
    trigger_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> list[AgentRunResponse]:
    """A schedule's own run history, keyed on `agent_runs.trigger_id`.

    WHY THIS EXISTS RATHER THAN A DEPLOYMENT-SCOPED READ
    ---------------------------------------------------
    The scheduled overview showed a red "Failing" badge above "Last Run: No runs
    yet" — two cards, two different scopes. The badge came from
    `GET /agents/{name}/health` (all runs for the agent); the list came from
    `GET /deployments/{id}/runs` (runs carrying that deployment's FK). Every
    trigger-driven run has BOTH deployment FK columns NULL, so the list was always
    empty while the badge was red. 1,328 runs, none of them visible.

    The obvious repair — stamp the deployment FK — is not available:
    `agent_runs.production_deployment_id` FKs to `production_deployments` (the
    published-artifact lifecycle) while the row a dispatch validates lives in
    `deployments` (the one with the `environment` column). Two tables, one
    confusable name; the validated id does not belong in that column.

    So the scope is corrected instead of the data patched. A schedule's runs are a
    property of the SCHEDULE, not of whichever deployment the operator happens to
    have open — `trigger_id` is already populated on every trigger-driven run,
    including the ones refused before dispatch (`_record_denied_run`). Reading by
    trigger makes the badge and the list answer from the same set by construction.
    """
    agent = await _get_agent(name, db)
    # Scope through the agent so a trigger id from another agent 404s rather than
    # leaking its runs — the id alone is not an authorization.
    trigger = (await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.agent_id == agent.id,
        )
    )).scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    rows = (await db.execute(
        select(AgentRun)
        .where(AgentRun.trigger_id == trigger_id)
        .order_by(AgentRun.started_at.desc())
        .limit(limit)
    )).scalars().all()

    obs = get_observability_backend()
    items: list[AgentRunResponse] = []
    for r in rows:
        resp = AgentRunResponse.model_validate(r)
        resp.trace_url = obs.build_trace_url(r.langfuse_trace_id)
        items.append(resp)
    return items


@router.patch("/{name}/triggers/{trigger_id}", response_model=AgentTriggerResponse)
async def update_trigger(
    name: str,
    trigger_id: uuid.UUID,
    body: AgentTriggerUpdate,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> AgentTrigger:
    agent = await _get_agent(name, db)
    if not await can_manage_artifact(db, claims["sub"], agent.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], agent.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.agent_id == agent.id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")

    apply_trigger_update(trigger, body)

    await db.commit()
    await db.refresh(trigger)
    return trigger


@router.post(
    "/{name}/triggers/{trigger_id}/rotate-token",
    response_model=RotateTokenResponse,
)
async def rotate_token(
    name: str,
    trigger_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> RotateTokenResponse:
    """Generate a new webhook token, store its sha256, and return the plaintext
    ONCE. The old hash is invalidated immediately (single active token per
    trigger — no dual-token overlap; that's a future improvement, spec §14)."""
    agent = await _get_agent(name, db)
    if not await can_manage_artifact(db, claims["sub"], agent.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], agent.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.agent_id == agent.id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    if trigger.trigger_type != "webhook":
        raise HTTPException(
            status_code=400, detail="Only webhook triggers have rotatable tokens"
        )

    plaintext, token_hash = _new_token()
    trigger.token_hash = token_hash
    trigger.updated_at = datetime.now(timezone.utc)
    await db.commit()
    logger.info("rotated webhook token for agent '%s' trigger %s", name, trigger_id)
    return RotateTokenResponse(
        trigger_id=trigger.id,
        token=plaintext,
        webhook_url=_webhook_url(name, plaintext),
    )


@router.delete("/{name}/triggers/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
async def delete_trigger(
    name: str,
    trigger_id: uuid.UUID,
    claims: dict = Depends(require_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    agent = await _get_agent(name, db)
    if not await can_manage_artifact(db, claims["sub"], agent.id):
        if ENFORCE_TRIGGER_MGMT:
            raise HTTPException(403, "agent-admin required to manage triggers on this agent")
        logger.warning(
            "trigger-mgmt: %s lacks agent-admin on agent %s — PERMITTED (ENFORCE_TRIGGER_MGMT=False)",
            claims["sub"], agent.id,
        )
    result = await db.execute(
        select(AgentTrigger).where(
            AgentTrigger.id == trigger_id,
            AgentTrigger.agent_id == agent.id,
        )
    )
    trigger = result.scalar_one_or_none()
    if not trigger:
        raise HTTPException(status_code=404, detail="Trigger not found")
    await db.delete(trigger)
    await db.commit()
    logger.info("deleted trigger %s for agent '%s'", trigger_id, name)
