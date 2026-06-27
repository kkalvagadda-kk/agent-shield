"""
Multi-agent handoff — delegates a conversation turn to another agent via the
Envoy ingress (not direct K8s DNS).

Routing through Envoy means:
- Safety Orchestrator scans the incoming message on the receiving agent's side.
- JWT validation is enforced at the gateway.
- The session ID is propagated via X-AgentShield-Session-Id so cross-agent
  Langfuse traces can be stitched (Phase 10 will complete this wiring).

Usage:
    result = await handoff("billing-agent", message="Process refund #123", session_id="sess-abc")
"""
from __future__ import annotations

import logging

import httpx

from . import config

logger = logging.getLogger(__name__)

# Envoy gateway base URL — in cluster this is the Envoy service, not the agent's
# ClusterIP directly.  Configured via AGENTSHIELD_REGISTRY_URL path convention
# or a dedicated AGENTSHIELD_GATEWAY_URL env var (falls back to registry URL).
import os
_GATEWAY_URL: str = os.getenv(
    "AGENTSHIELD_GATEWAY_URL",
    os.getenv("AGENTSHIELD_REGISTRY_URL", "http://envoy.agentshield-platform:8080"),
)


async def handoff(
    target_agent: str,
    message: str,
    session_id: str,
    metadata: dict | None = None,
) -> dict:
    """Send a message to another agent via Envoy and return its response.

    Args:
        target_agent: The registered name of the target agent.
        message:      The message to forward.
        session_id:   Current session ID (propagated via header for trace stitching).
        metadata:     Optional metadata dict passed in the request body.

    Returns:
        The target agent's response dict (``{"response": str, "thread_id": str}``).

    Raises:
        httpx.HTTPStatusError: If the target agent returns a non-2xx response.
        httpx.TimeoutException: If the call times out (30s limit).
    """
    url = f"{_GATEWAY_URL}/agents/{target_agent}/chat"
    headers = {
        "X-AgentShield-Session-Id": session_id,
        "X-AgentShield-Source-Agent": config.AGENT_NAME,
        "Content-Type": "application/json",
    }
    payload: dict = {"message": message, "thread_id": session_id}
    if metadata:
        payload["metadata"] = metadata

    logger.info(
        "Handoff: %s → %s (session=%s)", config.AGENT_NAME, target_agent, session_id
    )

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()
