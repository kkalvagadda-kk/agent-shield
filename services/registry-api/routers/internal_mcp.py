"""
Internal MCP authz endpoint — cluster-internal, NetworkPolicy-trusted (unauthenticated).

  POST /api/v1/internal/mcp/authorize-tool-call  {caller_sa_subject, server_id, mcp_tool_name}
       → {allowed: bool}

The cross-team half of the MCP Proxy's §3b coarse authz floor. The proxy calls this
ONLY when a caller's team differs from the target server's ``owner_team`` (own-team is
answered locally in the proxy with zero hops). It returns only a boolean — never a
secret, credential, or server URL — so, like every other internal registry-api endpoint
(see ``routers/internal.py``), it runs no TokenReview: the proxy has already
TokenReview-verified the caller's identity before calling here.

Always answers ``200`` (``allowed: false`` is a normal answer, not an error). ``422``
only on a malformed body. Uses the SAME ``team_may_use_tool`` resolver as the deploy
gate — one implementation, two callers.
"""
from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db import AsyncSessionLocal
from models import Tool
from tool_access import team_may_use_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/internal/mcp", tags=["internal"])


async def _get_db():
    async with AsyncSessionLocal() as session:
        yield session


class AuthorizeToolCallRequest(BaseModel):
    # A non-empty SA subject; the team is derived from its `agents-{team}` namespace.
    caller_sa_subject: str = Field(..., min_length=1)
    # Typed as UUID so an invalid/missing id is a 422 (malformed body), not a 200.
    server_id: uuid.UUID
    mcp_tool_name: str = Field(..., min_length=1)


class AuthorizeToolCallResponse(BaseModel):
    allowed: bool


def _team_from_sa_subject(subject: str) -> str | None:
    """Derive the caller's team from a Kubernetes SA subject
    ``system:serviceaccount:agents-{team}:{sa_name}``.

    Any subject whose namespace is not of the ``agents-`` form (or an empty team) →
    ``None``, which the caller maps to ``allowed: false`` — not an error. Structural,
    not a guard bolted on: a non-agent caller simply holds no team grant.
    """
    parts = subject.split(":")
    if len(parts) < 4 or parts[0] != "system" or parts[1] != "serviceaccount":
        return None
    namespace = parts[2]
    prefix = "agents-"
    if not namespace.startswith(prefix):
        return None
    team = namespace[len(prefix):]
    return team or None


@router.post(
    "/authorize-tool-call",
    response_model=AuthorizeToolCallResponse,
    summary="Cross-team MCP tool-call grant check (cluster-internal)",
)
async def authorize_tool_call(
    body: AuthorizeToolCallRequest,
    db: AsyncSession = Depends(_get_db),
) -> AuthorizeToolCallResponse:
    caller_team = _team_from_sa_subject(body.caller_sa_subject)
    if caller_team is None:
        # Non-`agents-` subject → no team → not allowed (defensive; the proxy will
        # already have 403'd such a caller).
        logger.info(
            "authorize_tool_call: subject %r has no agents- team — allowed=false",
            body.caller_sa_subject,
        )
        return AuthorizeToolCallResponse(allowed=False)

    tool = (
        await db.execute(
            select(Tool).where(
                Tool.mcp_server_id == body.server_id,
                Tool.mcp_tool_name == body.mcp_tool_name,
            )
        )
    ).scalar_one_or_none()
    if tool is None:
        logger.info(
            "authorize_tool_call: no tool for server=%s mcp_tool_name=%r — allowed=false",
            body.server_id, body.mcp_tool_name,
        )
        return AuthorizeToolCallResponse(allowed=False)

    allowed = await team_may_use_tool(db, caller_team, tool.id)
    logger.info(
        "authorize_tool_call: team=%s server=%s tool=%s allowed=%s",
        caller_team, body.server_id, tool.id, allowed,
    )
    return AuthorizeToolCallResponse(allowed=allowed)
