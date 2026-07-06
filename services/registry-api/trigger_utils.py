"""Shared utilities for agent and workflow trigger handlers.

Extracted from routers/triggers.py to allow reuse in
routers/composite_workflows.py without circular imports.
"""
from __future__ import annotations

import hashlib
import os
import secrets

# Public base URL of the Event Gateway; used to build the webhook URL shown to
# the user. Overridable per-deploy; the path shape is /hooks/{name}/{token} for
# agents and /hooks/workflow/{name}/{token} for composite workflows.
EVENT_GATEWAY_PUBLIC_URL = os.getenv(
    "EVENT_GATEWAY_PUBLIC_URL", "https://<event-gateway-host>"
)


def _new_token() -> tuple[str, str]:
    """Generate a CSPRNG webhook token → (plaintext, sha256_hex). ≥256 bits (T-2)."""
    plaintext = secrets.token_urlsafe(32)
    return plaintext, hashlib.sha256(plaintext.encode()).hexdigest()


def _webhook_url(agent_name: str, token: str) -> str:
    return f"{EVENT_GATEWAY_PUBLIC_URL.rstrip('/')}/hooks/{agent_name}/{token}"


def workflow_webhook_url(name: str, token: str) -> str:
    return f"{EVENT_GATEWAY_PUBLIC_URL.rstrip('/')}/hooks/workflow/{name}/{token}"
