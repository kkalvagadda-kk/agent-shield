"""
SDK configuration — all values read from environment variables.
Imported by every module that needs external service URLs or settings.
"""
import os

# --- Safety Orchestrator ---
# If absent, mock_safety is used instead of a real HTTP call.
AGENTSHIELD_SAFETY_URL: str = os.getenv("AGENTSHIELD_SAFETY_URL", "")

# --- Langfuse tracing ---
# If absent, tracer no-ops silently.
AGENTSHIELD_LANGFUSE_KEY: str = os.getenv("AGENTSHIELD_LANGFUSE_KEY", "")
AGENTSHIELD_LANGFUSE_HOST: str = os.getenv(
    "AGENTSHIELD_LANGFUSE_HOST", "http://langfuse.agentshield-platform:3000"
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

# --- Portkey / OpenAI proxy (reserved, not used in Phase 6) ---
OPENAI_BASE_URL: str = os.getenv("OPENAI_BASE_URL", "")

# --- Identity injected by deploy controller ---
AGENT_NAME: str = os.getenv("AGENT_NAME", "unknown-agent")

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
