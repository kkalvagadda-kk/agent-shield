"""
SDK configuration — all values read from environment variables.
Imported by every module that needs external service URLs or settings.
"""
import os

# --- Safety Orchestrator ---
# If absent, mock_safety is used instead of a real HTTP call.
AGENTSHIELD_SAFETY_URL: str = os.getenv("AGENTSHIELD_SAFETY_URL", "")

# --- Langfuse tracing ---
# Standard Langfuse env vars — the SAME names registry-api, safety-orchestrator,
# and deploy-controller (into agent pods) all use. Previously the SDK read
# AGENTSHIELD_LANGFUSE_KEY/HOST, which nothing set — so the tracer silently
# no-op'd on every agent pod. If the keys are absent the tracer no-ops.
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST: str = os.getenv(
    "LANGFUSE_HOST", "http://langfuse.agentshield-platform:3000"
)

# --- OPA sidecar ---
# Defaults to the OPA sidecar port on localhost (injected by deploy controller).
# If absent AND in dev mode, mock_opa is used.
AGENTSHIELD_OPA_URL: str = os.getenv("AGENTSHIELD_OPA_URL", "http://localhost:8181")

# --- Registry API ---
# Used for HITL approval creation and agent registration.
AGENTSHIELD_REGISTRY_URL: str = os.getenv(
    "AGENTSHIELD_REGISTRY_URL", "http://registry-api.agentshield-platform:8080"
)

# --- Studio ---
# Deep-link base URL for approval queue links embedded in HITL payloads.
AGENTSHIELD_STUDIO_URL: str = os.getenv(
    "AGENTSHIELD_STUDIO_URL", "http://studio.agentshield-platform:3001"
)

# --- MCP Proxy (MCP-as-tool-source) ---
# The single egress hop for agent -> external MCP server tool calls. An mcp_tool
# resolves to a call against MCP_PROXY_URL + '/internal/tools/call', authenticated
# with the projected SA token (audience agentshield-mcp-proxy) that the deploy
# controller mounts at MCP_PROXY_SA_TOKEN_PATH.
AGENTSHIELD_MCP_PROXY_URL: str = os.getenv(
    "AGENTSHIELD_MCP_PROXY_URL", "http://agentshield-mcp-proxy.agentshield-platform:8080"
)
AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH: str = os.getenv(
    "AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH", "/var/run/secrets/mcp-proxy-token/token"
)

# --- Portkey / OpenAI proxy (reserved, not used in Phase 6) ---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

# --- Identity injected by deploy controller ---
AGENT_NAME: str = os.getenv("AGENT_NAME", "unknown-agent")
AGENT_ID: str = os.getenv("AGENTSHIELD_AGENT_ID", "")
AGENT_TEAM: str = os.getenv("AGENTSHIELD_AGENT_TEAM", "platform")

# --- End-user identity (WS-C / FR-MCP-21 on-behalf-of) ---
# The `sub` of the human on whose behalf this agent is acting, if any. Best-effort
# source for the `x-user-sub` header the MCP proxy reads to route on_behalf_of
# servers. Empty for daemon agents (no user) — in which case the executor sends NO
# such header, so a Phase-1 request stays byte-identical. Populated only once
# identity-propagation lands (see docs/design/identity-propagation-architecture.md).
USER_SUB: str = os.getenv("AGENTSHIELD_USER_SUB", "")

# --- LLM provider ---
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

# --- Postgres checkpointer ---
# If absent, MemorySaver is used for local dev.
DIRECT_DATABASE_URL: str = os.getenv("DIRECT_DATABASE_URL", "")

# --- Dev-mode flag ---
# True when AGENTSHIELD_OPA_URL is the default localhost value and the user has
# not explicitly set it, indicating local dev rather than a cluster deployment.
_OPA_URL_EXPLICITLY_SET: bool = bool(os.getenv("AGENTSHIELD_OPA_URL"))
DEV_MODE: bool = not _OPA_URL_EXPLICITLY_SET
