"""
Declarative runner configuration — all values read from environment variables.

WORKFLOW_JSON is required; all others have defaults that enable local dev
without external dependencies.
"""
import os

# --- Workflow definition (REQUIRED) ---
# Must be a base64-encoded or plain JSON string injected by the deploy controller.
# Raise at import time so the pod CrashLoops with a clear error instead of
# accepting requests with no workflow loaded.
WORKFLOW_JSON: str = os.environ.get("WORKFLOW_JSON", "")
if not WORKFLOW_JSON:
    raise ValueError(
        "WORKFLOW_JSON environment variable is required but not set. "
        "The deploy controller should inject this as a base64-encoded "
        "workflow definition JSON string."
    )

# --- Safety Orchestrator ---
# If absent, mock_safety is used (local dev / unit tests).
AGENTSHIELD_SAFETY_URL: str = os.getenv("AGENTSHIELD_SAFETY_URL", "")

# --- Postgres ---
# DATABASE_URL  — general connection string (informational; not used directly by runner)
# DIRECT_DATABASE_URL — bypasses PgBouncer; used by LangGraph AsyncPostgresSaver
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
DIRECT_DATABASE_URL: str = os.getenv("DIRECT_DATABASE_URL", "")

# --- Identity injected by deploy controller ---
AGENT_NAME: str = os.getenv("AGENT_NAME", "declarative-agent")

# --- LLM provider ---
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

# --- FastAPI port ---
PORT: int = int(os.getenv("PORT", "8080"))

# --- OPA sidecar ---
AGENTSHIELD_OPA_URL: str = os.getenv("AGENTSHIELD_OPA_URL", "http://localhost:8181")

# --- Langfuse tracing ---
AGENTSHIELD_LANGFUSE_KEY: str = os.getenv("AGENTSHIELD_LANGFUSE_KEY", "")
AGENTSHIELD_LANGFUSE_HOST: str = os.getenv(
    "AGENTSHIELD_LANGFUSE_HOST", "http://langfuse.agentshield-platform:3000"
)

# --- Dev mode ---
# True when OPA URL is the default localhost value and not explicitly configured.
_OPA_URL_EXPLICITLY_SET: bool = bool(os.getenv("AGENTSHIELD_OPA_URL"))
DEV_MODE: bool = not _OPA_URL_EXPLICITLY_SET
