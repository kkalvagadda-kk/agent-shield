"""
Per-server credential Secret materializer (MCP-as-tool-source, data-model.md
"Per-server credential Secret").

registry-api materializes exactly one K8s Secret per registered MCP server so the
MCP Proxy can resolve connection + credentials WITHOUT a DB connection and WITHOUT
the master encryption key. registry-api decrypts the durable Fernet blob once, here,
and writes the derived Secret; the proxy only ever `get`s it.

  Secret name : agentshield-mcp-server-{server_id}
  Namespace   : agentshield-mcp  (dedicated — scopes the proxy's `get secrets` RBAC)
  data.connection   : JSON {server_url, transport, transport_config, is_external, owner_team}
  data.auth_headers : JSON {header_name: header_value, ...}  ({} when no auth_config)

Reuses the existing `k8s.upsert_secret` / `k8s.delete_secret` — registry-api's
cluster-wide secret ClusterRole already permits the cross-namespace write, so this
adds NO new RBAC.

Consumers: written by the `mcp_servers` router (T032, Phase 6) on register / `/sync` /
`PUT`-with-auth-change; deleted on server DELETE. The Secret is a derived artifact —
the durable credential source stays the Fernet blob in `AuthConfig.credentials_encrypted`.
"""
from __future__ import annotations

import json
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from credential_provider import CredentialRef, get_provider
from crypto import decrypt_json
from k8s import delete_secret, upsert_secret
from models import AuthConfig, MCPServer

logger = logging.getLogger(__name__)

MCP_SECRETS_NAMESPACE = "agentshield-mcp"


def _server_secret_name(server_id) -> str:
    return f"agentshield-mcp-server-{server_id}"


def _compose_auth_headers(auth_type: str | None, creds: dict) -> dict[str, str]:
    """Compose the outbound HTTP header map the proxy will send to the MCP server,
    from the decrypted AuthConfig credential dict, keyed by ``AuthConfig.type``.

    AuthConfig credentials are an arbitrary ``{env_var_name: value}`` map — credential
    keys must be valid env-var names, so they cannot themselves be hyphenated header
    names. We map the common conventions to real headers:

      * ``bearer``  → ``{"Authorization": "Bearer <token>"}`` — token from the
        ``token``/``access_token`` credential key, else the sole credential value.
      * ``api_key`` → ``{<header_name>: <value>}`` — header name from the
        ``header_name``/``header`` credential value (default ``"X-API-Key"``), value
        from ``api_key``/``key``/``value``/``token``, else the sole credential value.
      * ``oauth2`` / ``mtls`` / unknown → ``{}`` (best-effort — the proxy connects
        without auth; ledgered as a Phase-1 gap).

    Any type with no resolvable credential returns ``{}`` and logs a warning rather
    than raising — a missing header must never break server registration.
    """
    if not creds:
        return {}
    # Look up credential VALUES by lowercased KEY; values keep their original case.
    by_key = {k.lower(): v for k, v in creds.items() if isinstance(v, str)}

    def _sole_value() -> str | None:
        vals = [v for v in creds.values() if isinstance(v, str) and v.strip()]
        return vals[0] if len(vals) == 1 else None

    if auth_type == "bearer":
        token = by_key.get("token") or by_key.get("access_token") or _sole_value()
        if token:
            return {"Authorization": f"Bearer {token}"}
        logger.warning(
            "mcp_secrets: bearer auth_config has no resolvable token — no auth header written"
        )
        return {}

    if auth_type == "api_key":
        header_name = by_key.get("header_name") or by_key.get("header") or "X-API-Key"
        value = (
            by_key.get("api_key")
            or by_key.get("key")
            or by_key.get("value")
            or _sole_value()
        )
        if value:
            return {header_name: value}
        logger.warning(
            "mcp_secrets: api_key auth_config has no resolvable value — no auth header written"
        )
        return {}

    logger.warning(
        "mcp_secrets: auth_config type %r has no header composer "
        "(oauth2/mtls best-effort deferred) — no auth header written",
        auth_type,
    )
    return {}


async def materialize_server_secret(db: AsyncSession, server: MCPServer) -> None:
    """Write (create-or-replace) the per-server Secret for ``server``.

    Loads the server's ``AuthConfig`` via ``db`` (never relies on a lazily-loaded
    relationship in the async session), decrypts its credentials once, and composes
    the two Secret keys. Idempotent via ``k8s.upsert_secret``.
    """
    # WS-C (data-model.md §2c): the per-server Secret is the ONLY server-metadata
    # channel the proxy has (no DB). Carry the identity selector + optional audience
    # so the proxy's identity.resolve_headers can pick the credential branch. A Secret
    # that predates WS-C simply lacks these keys → the proxy defaults identity_mode to
    # "none" (Phase-1 behaviour, safe). auth_headers composition is UNCHANGED.
    transport_config = server.transport_config
    identity_audience = (
        transport_config.get("identity_audience")
        if isinstance(transport_config, dict)
        else None
    )
    connection = {
        "server_url": server.server_url,
        "transport": server.transport,
        "transport_config": transport_config,
        "is_external": server.is_external,
        "owner_team": server.owner_team,
        "identity_mode": server.identity_mode,
        "identity_audience": identity_audience,
    }

    auth_headers: dict[str, str] = {}
    if server.auth_config_id is not None:
        auth_config = (
            await db.execute(
                select(AuthConfig).where(AuthConfig.id == server.auth_config_id)
            )
        ).scalar_one_or_none()
        if auth_config is not None:
            # WS-1 (Decision 31): resolve the credential value through the provider
            # when a credential_ref is set (new + backfilled rows), else fall back to
            # the retained legacy column (a null-ref row that predates the provider).
            # EXPLICIT legacy branch — no try/except, no getattr sniff. On pg-fernet
            # both paths decrypt the same Fernet ciphertext with the same master key,
            # so the composed auth_headers are BYTE-IDENTICAL either way.
            if auth_config.credential_ref is not None:
                creds = await get_provider().get(
                    CredentialRef.parse(auth_config.credential_ref)
                )
                auth_headers = _compose_auth_headers(auth_config.type, creds)
            elif auth_config.credentials_encrypted:
                creds = decrypt_json(auth_config.credentials_encrypted)
                auth_headers = _compose_auth_headers(auth_config.type, creds)

    data = {
        "connection": json.dumps(connection),
        "auth_headers": json.dumps(auth_headers),
    }
    await upsert_secret(_server_secret_name(server.id), MCP_SECRETS_NAMESPACE, data)
    logger.info(
        "mcp_secrets: materialized secret %s/%s (auth_headers=%d)",
        MCP_SECRETS_NAMESPACE, _server_secret_name(server.id), len(auth_headers),
    )


async def delete_server_secret(server_id) -> None:
    """Delete the per-server Secret for ``server_id`` (404 is a no-op — see
    ``k8s.delete_secret``)."""
    await delete_secret(_server_secret_name(server_id), MCP_SECRETS_NAMESPACE)
    logger.info(
        "mcp_secrets: deleted secret %s/%s", MCP_SECRETS_NAMESPACE, _server_secret_name(server_id)
    )
