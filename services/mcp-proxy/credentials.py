"""
Credential + connection resolution — from the per-server K8s Secret ONLY.

registry-api materialized `agentshield-mcp-server-{server_id}` in MCP_SECRETS_NAMESPACE
at register/sync time, decrypting the Fernet credential blob once and composing
ready-to-use auth headers (research.md B3/B13). The proxy reads exactly that Secret:
no DB, no AGENTSHIELD_ENCRYPTION_KEY, no decryption here.

The Secret carries two JSON string values:
  connection   — {server_url, transport, transport_config, is_external, owner_team}
  auth_headers — {header_name: value}   (may be {} for an unauthenticated server)

A missing Secret raises the typed ServerSecretNotFound, which the endpoints turn
into a 200 error body (status='error' / is_error=true), never a 5xx.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from kubernetes.client.rest import ApiException

import config
import k8s_client

logger = logging.getLogger(__name__)


class ServerSecretNotFound(Exception):
    """The per-server credential Secret does not exist (or is malformed)."""


@dataclass
class ServerConnection:
    """Everything needed to dial an upstream MCP server — resolved from the Secret."""
    server_url: str
    transport: str
    transport_config: dict = field(default_factory=dict)
    is_external: bool = False
    owner_team: str | None = None
    auth_headers: dict[str, str] = field(default_factory=dict)
    # WS-C identity selector (Phase 2). Default "none" makes a pre-WS-C Secret (whose
    # connection JSON has neither key) behave EXACTLY as Phase 1 — identity.resolve_headers
    # returns the static auth_headers unchanged. identity_audience is the optional Keycloak
    # token audience for service_identity / on_behalf_of.
    identity_mode: str = "none"
    identity_audience: str | None = None


async def read_server_secret(server_id: str) -> ServerConnection:
    """Resolve a server_id to its ServerConnection via a single Secret read.

    Raises ServerSecretNotFound if the Secret is missing (404) or its payload is
    unparseable — both are "cannot connect" outcomes the caller reports as an
    error body, not an exception to the HTTP caller.
    """
    name = config.server_secret_name(str(server_id))
    try:
        data = await k8s_client.read_secret(name)
    except ApiException as exc:
        if exc.status == 404:
            raise ServerSecretNotFound(
                f"per-server Secret {name} not found in {config.MCP_SECRETS_NAMESPACE}"
            ) from exc
        # Any other API error (403/timeout) is not evidence of absence — surface
        # the reason but still as a typed "cannot resolve" so it fails-closed to
        # a 200 error body rather than a 5xx.
        raise ServerSecretNotFound(
            f"could not read Secret {name}: {exc.status} {exc.reason}"
        ) from exc

    try:
        connection = json.loads(data.get("connection", "{}"))
    except (ValueError, TypeError) as exc:
        raise ServerSecretNotFound(
            f"Secret {name} has a malformed 'connection' payload"
        ) from exc

    try:
        auth_headers = json.loads(data.get("auth_headers", "{}"))
    except (ValueError, TypeError):
        # A malformed auth_headers is non-fatal — treat as unauthenticated.
        logger.warning("mcp-proxy credentials: Secret %s has malformed auth_headers", name)
        auth_headers = {}

    server_url = connection.get("server_url")
    if not server_url:
        raise ServerSecretNotFound(f"Secret {name} 'connection' has no server_url")

    # Default identity_mode to "none" when the key is absent → a Secret materialized
    # before WS-C parses to Phase-1 behaviour exactly (data-model.md §3b).
    identity_mode = connection.get("identity_mode") or "none"
    identity_audience = connection.get("identity_audience")

    return ServerConnection(
        server_url=server_url,
        transport=connection.get("transport", "http"),
        transport_config=connection.get("transport_config") or {},
        is_external=bool(connection.get("is_external", False)),
        owner_team=connection.get("owner_team"),
        auth_headers=auth_headers if isinstance(auth_headers, dict) else {},
        identity_mode=identity_mode,
        identity_audience=identity_audience,
    )
