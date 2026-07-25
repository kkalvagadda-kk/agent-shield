"""
Declarative runner configuration — all values read from environment variables.

WORKFLOW_JSON is required; all others have defaults that enable local dev
without external dependencies.
"""
import os

# --- Workflow definition (OPTIONAL) ---
# If set (base64-encoded or plain JSON), the runner uses this workflow graph.
# If absent, the runner enters "simple agent" mode: fetches instructions + tools
# from the Registry API using AGENT_NAME at startup.
WORKFLOW_JSON: str = os.environ.get("WORKFLOW_JSON", "")

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

# --- End-user identity (WS-C / FR-MCP-21 on-behalf-of) ---
# The `sub` of the human this run is acting on behalf of, if any. Best-effort source
# for the `x-user-sub` header McpToolNodeExecutor forwards to the MCP proxy so it can
# route on_behalf_of servers. Empty for daemon/scheduled runs (no user) — the executor
# then sends NO such header, keeping a Phase-1 request byte-identical. A SEPARATE impl
# from the SDK's config.USER_SUB (runner and SDK are distinct runtimes).
USER_SUB: str = os.getenv("AGENTSHIELD_USER_SUB", "")

# --- Composite-workflow mode (Decision 22) ---
# When set by the deploy controller, this pod runs as a workflow
# orchestrator rather than a single agent.
COMPOSITE_WORKFLOW_ID: str | None = os.getenv("COMPOSITE_WORKFLOW_ID")
WORKFLOW_CONFIG: str = os.environ.get("WORKFLOW_CONFIG", "")

# --- LLM provider ---
LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "anthropic")
LLM_MODEL: str = os.getenv("LLM_MODEL", "claude-sonnet-4-6")

# --- FastAPI port ---
PORT: int = int(os.getenv("PORT", "8080"))

# --- OPA sidecar ---
AGENTSHIELD_OPA_URL: str = os.getenv("AGENTSHIELD_OPA_URL", "http://localhost:8181")

# --- Langfuse tracing ---
# Standard Langfuse env vars — same names deploy-controller injects into the pod
# and _make_langfuse_handler already reads. Previously AGENTSHIELD_LANGFUSE_KEY/
# HOST (nothing set them) so the SDK tracer path silently no-op'd.
LANGFUSE_PUBLIC_KEY: str = os.getenv("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY: str = os.getenv("LANGFUSE_SECRET_KEY", "")
LANGFUSE_HOST: str = os.getenv(
    "LANGFUSE_HOST", "http://langfuse.agentshield-platform:3000"
)

# --- Registry API ---
# Used by the declarative runner to fetch tool and skill definitions at startup.
REGISTRY_API_URL: str = os.environ.get("REGISTRY_API_URL", "http://agentshield-registry-api.agentshield-platform:8000")

# --- Python Executor ---
# Used by PythonToolNodeExecutor to run sandboxed user-supplied Python code.
PYTHON_EXECUTOR_URL: str = os.environ.get("PYTHON_EXECUTOR_URL", "http://python-executor.agentshield-platform:8080")

# --- MCP Proxy (MCP-as-tool-source) ---
# Used by McpToolNodeExecutor — the single egress hop for a declarative/workflow
# agent's mcp_tool call. Authenticated with the projected SA token (audience
# agentshield-mcp-proxy) the deploy controller mounts at MCP_PROXY_SA_TOKEN_PATH.
MCP_PROXY_URL: str = os.environ.get("MCP_PROXY_URL", "http://agentshield-mcp-proxy.agentshield-platform:8080")
MCP_PROXY_SA_TOKEN_PATH: str = os.environ.get("MCP_PROXY_SA_TOKEN_PATH", "/var/run/secrets/mcp-proxy-token/token")

# --- Dev mode ---
# True when OPA URL is the default localhost value and not explicitly configured.
_OPA_URL_EXPLICITLY_SET: bool = bool(os.getenv("AGENTSHIELD_OPA_URL"))
DEV_MODE: bool = not _OPA_URL_EXPLICITLY_SET
