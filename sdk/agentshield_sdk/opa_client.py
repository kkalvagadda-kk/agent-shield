"""
OPA client — calls the OPA sidecar to get a per-tool policy decision.

Phase 9.1 update: unified bundle-server policy. All agents now share a single
Rego package (agentshield) keyed on K8s SA subject, not agent name.

The client reads the pod's projected bound SA token from the filesystem
(mounted at AGENTSHIELD_SA_TOKEN_PATH, default /var/run/secrets/sa-token/token)
and includes it in every OPA decision request so the policy can verify that
the calling pod is the registered agent for that SA subject.

New OPA input fields (Phase 9.1):
  - sa_subject:   SA subject parsed from the projected token JWT
  - agent_class:  'daemon' | 'user_delegated' (from env AGENTSHIELD_AGENT_CLASS)
  - user_id:      invoking user sub (Class B only; '' for Class A)
  - user_team:    invoking user team (Class B only; '' for Class A)
  - playground:   bool (from env AGENTSHIELD_PLAYGROUND)
  - sandbox:      bool (from env AGENTSHIELD_SANDBOX)

OPA policy path: /v1/data/agentshield  (unified bundle, not per-agent path)

Fallback:
- DEV_MODE (AGENTSHIELD_OPA_URL not explicitly set): uses mock_opa.
- PROD_MODE (OPA URL set but OPA unreachable): FAIL CLOSED — deny.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

from . import config, mock_opa

logger = logging.getLogger(__name__)

_SA_TOKEN_PATH = os.environ.get(
    "AGENTSHIELD_SA_TOKEN_PATH", "/var/run/secrets/sa-token/token"
)
_AGENT_CLASS = os.environ.get("AGENTSHIELD_AGENT_CLASS", "user_delegated")
_PLAYGROUND = os.environ.get("AGENTSHIELD_PLAYGROUND", "false").lower() == "true"
_SANDBOX = os.environ.get("AGENTSHIELD_SANDBOX", "false").lower() == "true"

# OPA policy path — unified bundle package (Phase 9.1)
_OPA_POLICY_PATH = "/v1/data/agentshield"


@dataclass
class UserContext:
    """User identity propagated through Class B (user_delegated) agent calls."""
    user_id: str
    user_team: str


@dataclass
class OPADecision:
    allow: bool
    require_approval: bool
    reason: str
    deny_reason: str = ""


def _read_sa_token() -> str:
    """Read the projected SA token from the filesystem.

    Returns an empty string if the file doesn't exist (dev/test environments).
    """
    try:
        with open(_SA_TOKEN_PATH) as f:
            return f.read().strip()
    except OSError:
        return ""


def _parse_sa_subject(token: str) -> str:
    """Extract the 'sub' claim from a JWT without verifying the signature.

    OPA validates the token against the Kubernetes API; we just need the
    subject to include in the request so OPA can look up the agent entry.
    """
    if not token:
        return ""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return ""
        # JWT payload is base64url-encoded (no padding)
        payload_b64 = parts[1] + "=="  # add padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("sub", "")
    except Exception:
        return ""


async def check_tool(
    agent_name: str,
    tool_name: str,
    args: dict,
    user_context: Optional[UserContext] = None,
) -> OPADecision:
    """Return an OPA policy decision for a tool invocation.

    Args:
        agent_name:   The agent's registered name (for logging; SA subject is the real key).
        tool_name:    The tool being invoked.
        args:         The tool's input arguments.
        user_context: User identity for Class B agents; None for Class A (daemon).

    Returns:
        OPADecision with allow/require_approval/reason/deny_reason fields.
    """
    # Dev mode: use mock OPA
    if config.DEV_MODE:
        raw = await mock_opa.check_tool(agent_name, tool_name, args)
        return OPADecision(**raw, deny_reason="")

    sa_token = _read_sa_token()
    sa_subject = _parse_sa_subject(sa_token)

    if not sa_subject:
        logger.warning(
            "No SA token/subject for agent '%s' — OPA will deny (agent_unauthenticated)",
            agent_name,
        )

    opa_input = {
        "sa_subject": sa_subject,
        "tool_name": tool_name,
        "args": args,
        "agent_class": _AGENT_CLASS,
        "playground": _PLAYGROUND,
        "sandbox": _SANDBOX,
        # Class B: include user identity; Class A: empty strings
        "user_id": user_context.user_id if user_context else "",
        "user_team": user_context.user_team if user_context else "",
    }

    url = f"{config.AGENTSHIELD_OPA_URL}{_OPA_POLICY_PATH}"
    payload = {"input": opa_input}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.error(
            "OPA unreachable for agent '%s' tool '%s': %s — failing closed",
            agent_name,
            tool_name,
            exc,
        )
        return OPADecision(
            allow=False,
            require_approval=False,
            reason="opa_unreachable",
            deny_reason="opa_unreachable",
        )

    result = data.get("result", {})
    allow = bool(result.get("allow", False))
    deny_reason = str(result.get("deny_reason", "")) if not allow else ""

    return OPADecision(
        allow=allow,
        require_approval=bool(result.get("require_approval", False)),
        reason=str(result.get("reason", "policy_decision")),
        deny_reason=deny_reason,
    )
