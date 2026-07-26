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

# ---------------------------------------------------------------------------
# list_changed subscription (WS-B / FR-MCP-07 — contracts/mcp-proxy-internal-phase2.md §4)
# ---------------------------------------------------------------------------

# Master switch for the subscription manager. When false, ensure_subscription is a
# no-op — the proxy never holds a long-lived notifications/tools/list_changed session
# and never POSTs the re-sync callback. Lets an operator disable the push path entirely
# (the periodic health loop still keeps status fresh).
MCP_LIST_CHANGED_ENABLED: bool = os.getenv(
    "MCP_LIST_CHANGED_ENABLED", "true"
).strip().lower() in ("1", "true", "yes", "on")

# Per-server trailing-edge debounce: coalesce a burst of notifications/tools/list_changed
# arriving within this window into a SINGLE POST /internal/mcp/list-changed to registry-api
# (each notification resets the timer; the callback fires once the burst goes quiet).
MCP_LIST_CHANGED_DEBOUNCE_SECONDS: float = float(
    os.getenv("MCP_LIST_CHANGED_DEBOUNCE_SECONDS", "5")
)

# Backoff between reconnect attempts after a subscription session drops.
MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS: float = float(
    os.getenv("MCP_LIST_CHANGED_RECONNECT_BACKOFF_SECONDS", "10")
)

# After this many CONSECUTIVE failed reconnect attempts the subscriber gives up and
# tears itself down (the server was almost certainly deleted — its per-server Secret is
# gone). A successful reconnect resets the counter, so a flapping-but-live server never
# exhausts the cap. Bounded — never an infinite reconnect loop.
MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS: int = int(
    os.getenv("MCP_LIST_CHANGED_MAX_RECONNECT_ATTEMPTS", "5")
)


# ---------------------------------------------------------------------------
# WS-C identity — Keycloak client-credentials mint (contracts/mcp-proxy-internal-phase2.md §4)
# ---------------------------------------------------------------------------
#
# The proxy mints its OWN service-account token to present the platform's identity to
# internal `service_identity` servers. It uses a NARROW, FILE-MOUNTED Keycloak client
# secret — NEVER the master AGENTSHIELD_ENCRYPTION_KEY and NEVER a k8s get-secrets API
# read for this credential (design §3b least-privilege). If a change wants to read this
# credential via the k8s API, stop — that widens the proxy's blast radius.

# The platform Keycloak OpenID token endpoint the client-credentials grant POSTs to
# (…/realms/{realm}/protocol/openid-connect/token). Empty by default — it MUST be set
# for any service_identity server to mint a token; keycloak_client raises if it is unset.
KEYCLOAK_TOKEN_URL: str = os.getenv("KEYCLOAK_TOKEN_URL", "")

# The confidential Keycloak client the proxy authenticates AS (client-credentials grant).
MCP_PROXY_KEYCLOAK_CLIENT_ID: str = os.getenv(
    "MCP_PROXY_KEYCLOAK_CLIENT_ID", "agentshield-mcp-proxy"
)

# Path to the file-mounted client secret for MCP_PROXY_KEYCLOAK_CLIENT_ID. Read fresh on
# every actual mint (never cached to disk logic, never via the k8s API). A read-only
# volume mount (charts wire {release}-mcp-proxy-keycloak → this path).
MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH: str = os.getenv(
    "MCP_PROXY_KEYCLOAK_CLIENT_SECRET_PATH", "/var/run/secrets/mcp-proxy-keycloak/client-secret"
)

# Re-mint a cached service token this many seconds BEFORE its JWT `exp` — a safety skew
# so a token never expires mid-flight to an upstream server.
KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS: int = int(
    os.getenv("KEYCLOAK_TOKEN_CACHE_SKEW_SECONDS", "30")
)


# ---------------------------------------------------------------------------
# WS-2 external OAuth — access-token READ from registry-api (contracts/mcp-proxy-oauth-phase4.md §4)
# ---------------------------------------------------------------------------
#
# For an EXTERNAL MCP server registered with external_auth_mode == "oauth", the proxy
# presents a short-lived upstream ACCESS token as the upstream Authorization header. It
# holds NO refresh token, NO DB, and NO AGENTSHIELD_ENCRYPTION_KEY (still §3b) — it PULLS
# a fresh access token from registry-api (which owns the refresh token + the refresh-with-
# rotation) and caches it IN MEMORY only (oauth_tokens._access_cache). This adds one
# OUTBOUND call + one projected token; it does NOT add a DB URL, the master key, or a new
# inbound endpoint — the "no DB, no master key" invariant above still holds.

# registry-api endpoint the proxy POSTs {server_id, user_sub} to for a fresh access token.
# Derived from REGISTRY_API_URL so the host/port stays a SINGLE source of truth (the same
# in-cluster registry-api Service the proxy already reaches for the authorize-tool-call
# hop — port 8000). The chart sets this explicitly (T014); the default matches the code.
REGISTRY_API_OAUTH_TOKEN_URL: str = os.getenv(
    "REGISTRY_API_OAUTH_TOKEN_URL",
    f"{REGISTRY_API_URL}/api/v1/internal/mcp/oauth/access-token",
)

# Path to the file-mounted projected SA token (audience agentshield-registry-api) the
# proxy presents to that endpoint. Read FRESH from disk on every actual pull (projected
# tokens rotate ~hourly). A read-only volume mount (chart deployment.yaml projects it
# here). NEVER read via the k8s get-secrets API and NEVER the master key (§3b).
MCP_PROXY_REGISTRY_API_TOKEN_PATH: str = os.getenv(
    "MCP_PROXY_REGISTRY_API_TOKEN_PATH", "/var/run/secrets/registry-api/token"
)

# Serve a cached access token until this many seconds BEFORE its exp — a safety skew so a
# token never expires mid-flight to an upstream server (mirrors the Keycloak skew above).
OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS: int = int(
    os.getenv("OAUTH_ACCESS_TOKEN_CACHE_SKEW_SECONDS", "30")
)


def server_secret_name(server_id: str) -> str:
    """The K8s Secret name registry-api materialized for a given server_id."""
    return f"{SERVER_SECRET_PREFIX}{server_id}"
