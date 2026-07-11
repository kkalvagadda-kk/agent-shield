"""
Human-In-The-Loop (HITL) module.

When a high-risk tool requires approval:
1. POST an approval record to the Registry API so the Studio UI can show it.
2. Call LangGraph's ``interrupt()`` to checkpoint the graph state and pause.
   LangGraph raises ``GraphInterrupt``, which is caught by the runtime and
   surfaced as an ``approval_requested`` SSE event.
3. When the reviewer acts (approve/reject), ``POST /resume/{thread_id}`` is
   called by the Studio, the graph resumes, and ``interrupt()`` returns the
   reviewer's decision dict.

The caller (graph_builder._wrap_tool_with_governance) inspects the returned
dict to decide whether to proceed or abort the tool call.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from langgraph.types import interrupt  # type: ignore[import]

from . import config

logger = logging.getLogger(__name__)

# Approvals expire after 30 minutes by default.
APPROVAL_TTL_MINUTES: int = 30


async def require_approval(
    agent_name: str,
    tool_name: str,
    tool_args: dict,
    thread_id: str,
    risk: str = "high",
    reasoning: str = "",
    conversation_history: list[Any] | None = None,
) -> dict:
    """Pause execution and request human approval for a tool invocation.

    Steps:
    1. POST to Registry API ``/api/v1/approvals`` to create the record.
    2. Call ``langgraph.types.interrupt()`` with the approval metadata — this
       raises ``GraphInterrupt``, checkpoints the state, and pauses the graph.
    3. When the graph is resumed (via POST /resume/{thread_id}), ``interrupt()``
       returns the value passed in the resume payload — the reviewer's decision.

    Args:
        agent_name:           Agent that triggered this tool call.
        tool_name:            Name of the tool awaiting approval.
        tool_args:            Arguments the tool was called with.
        thread_id:            LangGraph thread ID for the current conversation.
        risk:                 Risk level (always "high" when HITL is needed).
        reasoning:            The LLM's stated reason for the call (best-effort; may
                              be empty). Surfaced to the reviewer as the "why".
        conversation_history: Last N messages for reviewer context.

    Returns:
        The dict passed in the resume payload (``{"decision": "approved"|"rejected", ...}``).
    """
    expires_at = (
        datetime.now(tz=timezone.utc) + timedelta(minutes=APPROVAL_TTL_MINUTES)
    ).isoformat()

    approval_id: str | None = None
    queue_url: str = f"{config.AGENTSHIELD_STUDIO_URL}/approvals"

    # Determine context: playground sessions use lightweight approval flow.
    is_playground = os.getenv("AGENTSHIELD_PLAYGROUND", "false").lower() == "true"
    context = "playground" if is_playground else "production"

    # 1. Create approval record in Registry API.
    request_body = {
        "agent_id": config.AGENT_ID,
        "agent_name": agent_name,
        "team": config.AGENT_TEAM,
        "tool_name": tool_name,
        "tool_args": tool_args,
        "thread_id": thread_id,
        "risk_level": risk,
        "context": context,
        "reasoning": reasoning or None,
    }
    created = False
    try:
        async with httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.post(
                f"{config.AGENTSHIELD_REGISTRY_URL}/api/v1/approvals/",
                json=request_body,
            )
            resp.raise_for_status()
            body = resp.json()
            approval_id = body.get("id") or body.get("approval_id")
            created = approval_id is not None
            if approval_id:
                queue_url = f"{config.AGENTSHIELD_STUDIO_URL}/approvals/{approval_id}"
            logger.info(
                "HITL approval record created id=%s tool=%s thread=%s context=%s risk=%s",
                approval_id, tool_name, thread_id, context, risk,
            )
    except httpx.HTTPStatusError as exc:
        # Log the FULL server response (status + validation body) — the response body
        # names the exact field that failed (e.g. agent_id/risk_level), which the bare
        # exception string hides. Also log the request so the mismatch is obvious.
        logger.error(
            "HITL approval record creation FAILED: status=%s body=%s | request agent_id=%r "
            "team=%r risk_level=%r context=%r tool=%s thread=%s",
            exc.response.status_code, exc.response.text[:1000],
            config.AGENT_ID, config.AGENT_TEAM, risk, context, tool_name, thread_id,
        )
    except Exception as exc:
        logger.error(
            "HITL approval record creation FAILED (transport): %r | tool=%s thread=%s "
            "registry=%s", exc, tool_name, thread_id, config.AGENTSHIELD_REGISTRY_URL,
        )

    # Fail CLOSED: if no approval record exists, nobody can approve it — interrupting
    # here would hang the conversation forever on an un-actionable pause. Deny the tool
    # instead, with a clear reason the user (and the logs) can see.
    if not created:
        logger.error(
            "HITL denying tool=%s (no approval record could be created) — fail-closed",
            tool_name,
        )
        return {
            "decision": "rejected",
            "reason": "approval could not be recorded (see agent pod logs) — denied for safety",
        }

    # 2. Interrupt the graph.  LangGraph checkpoints state and pauses here.
    #    The value passed to interrupt() is emitted as the approval_requested SSE payload.
    interrupt_payload: dict = {
        "approval_id": approval_id,
        "thread_id": thread_id,
        "tool": tool_name,
        "args": tool_args,
        "risk": risk,
        "reasoning": reasoning or None,
        "expires_at": expires_at,
        "queue_url": queue_url,
    }
    # interrupt() raises GraphInterrupt internally; when the graph is resumed
    # (POST /resume/{thread_id}), it returns the resume payload.
    decision: dict = interrupt(interrupt_payload)  # type: ignore[assignment]
    return decision


def _serialise_message(msg: Any) -> dict:
    """Convert a LangChain message object (or plain dict) to a JSON-safe dict."""
    if isinstance(msg, dict):
        return msg
    # LangChain BaseMessage objects have .type and .content
    return {
        "role": getattr(msg, "type", "unknown"),
        "content": getattr(msg, "content", str(msg)),
    }
