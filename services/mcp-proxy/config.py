"""
MCP Proxy configuration — environment-driven, no defaults that reach the DB.

The proxy is a pure MCP wire client and the credential custodian for every
registered server. It holds exactly two K8s privileges (design §3b):
  - system:auth-delegator (TokenReview) for AuthN
  - get on secrets in MCP_SECRETS_NAMESPACE (per-server credential Secrets)

There is intentionally NO database URL and NO AGENTSHIELD_ENCRYPTION_KEY here —
server connection info + ready-to-use auth headers arrive only via the per-server
K8s Secret that registry-api materialized (research.md B3/B13). If a change wants
either of those, it violates least-privilege — stop.
"""
from __future__ import annotations

import os

# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

# Port the FastAPI app listens on (Service targets 8080 — contracts/mcp-proxy-internal.md).
PORT: int = int(os.getenv("PORT", "8080"))

# ---------------------------------------------------------------------------
# AuthN / AuthZ (design §3b)
# ---------------------------------------------------------------------------

# The audience every inbound projected SA token MUST carry. TokenReview is run
# with spec.audiences=[MCP_PROXY_AUDIENCE] and the review's status.audiences must
# contain it — a token minted for a different audience (e.g. agentshield-opa) fails.
MCP_PROXY_AUDIENCE: str = os.getenv("MCP_PROXY_AUDIENCE", "agentshield-mcp-proxy")

# Admin-plane caller allow-list for POST /internal/discover: the verified caller
# subject MUST equal this, else 403. Default is registry-api's in-cluster SA subject.
REGISTRY_API_SA_SUBJECT: str = os.getenv(
    "REGISTRY_API_SA_SUBJECT",
    "system:serviceaccount:agentshield-platform:agentshield-registry-api",
)

# registry-api base URL — used ONLY for the cross-team authz callback
# (POST /api/v1/internal/mcp/authorize-tool-call). Never for credentials.
REGISTRY_API_URL: str = os.getenv(
    "REGISTRY_API_URL",
    "http://agentshield-registry-api.agentshield-platform:8000",
).rstrip("/")

# ---------------------------------------------------------------------------
# Credential Secrets (design §3b / research.md B13)
# ---------------------------------------------------------------------------

# The dedicated namespace holding per-server credential Secrets. A dedicated
# namespace is what makes the proxy's read-only RBAC provably unable to reach
# the master encryption key (which lives in agentshield-platform). Do NOT point
# this at agentshield-platform.
MCP_SECRETS_NAMESPACE: str = os.getenv("MCP_SECRETS_NAMESPACE", "agentshield-mcp")

# Per-server Secret name pattern: agentshield-mcp-server-{server_id}.
SERVER_SECRET_PREFIX: str = os.getenv("SERVER_SECRET_PREFIX", "agentshield-mcp-server-")

# ---------------------------------------------------------------------------
# Caches (per-replica, in-memory)
# ---------------------------------------------------------------------------

# Positive TokenReview cache safety cap (seconds). Entries expire at min(token exp,
# now + this). A token with no parseable exp falls back to this cap.
TOKEN_REVIEW_CACHE_MAX_TTL_SECONDS: int = int(
    os.getenv("MCP_TOKEN_REVIEW_CACHE_MAX_TTL_SECONDS", "3600")
)

# Cross-team authz decision cache TTL (seconds). Own-team calls never hit this
# (zero-hop fast path); only cross-team registry-api callbacks are cached.
AUTHZ_CACHE_TTL_SECONDS: int = int(os.getenv("MCP_AUTHZ_CACHE_TTL_SECONDS", "60"))

# ---------------------------------------------------------------------------
# Timeouts
# ---------------------------------------------------------------------------

# httpx timeout for the registry-api cross-team authz callback.
REGISTRY_API_TIMEOUT_SECONDS: float = float(
    os.getenv("MCP_REGISTRY_API_TIMEOUT_SECONDS", "5.0")
)

# MCP connect / initialize timeout when dialling an upstream server.
MCP_CONNECT_TIMEOUT_SECONDS: float = float(
    os.getenv("MCP_CONNECT_TIMEOUT_SECONDS", "30.0")
)


def server_secret_name(server_id: str) -> str:
    """The K8s Secret name registry-api materialized for a given server_id."""
    return f"{SERVER_SECRET_PREFIX}{server_id}"
