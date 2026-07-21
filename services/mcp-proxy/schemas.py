"""
MCP Proxy wire models — the design §3c / contracts/mcp-proxy-internal.md contract.

Field names here are load-bearing: registry-api (discover) and the SDK/runner
(tools/call) serialize/deserialize against these exact names. Do not rename a
field without updating both contract docs and every caller.

Convention (mirrors python-executor + safety-orchestrator): tool/transport
failures come back as HTTP 200 with an error body (is_error / status='error'),
NOT a 5xx — so the caller can hand the error to the LLM instead of crashing.
Only real auth/body failures use 401/403/422.
"""
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# POST /internal/discover  — admin plane (caller = registry-api)
# ---------------------------------------------------------------------------

class McpDiscoverRequest(BaseModel):
    server_id: UUID


class McpDiscoveredTool(BaseModel):
    name: str                       # RAW upstream tool name — registry-api namespaces it to Tool.name
    description: str | None = None
    input_schema: dict              # JSON Schema → Tool.input_schema


class McpDiscoverResponse(BaseModel):
    # Always returned with HTTP 200 — a connect/initialize failure or a missing
    # Secret is ok:false / status:'error', never an HTTP error.
    ok: bool                        # true iff connect + initialize + list_tools all succeeded
    status: str                     # 'connected' | 'error'  → MCPServer.status
    health_detail: str | None = None    # failure reason → registry-api folds into health_detail.last_error
    protocol_version: str | None = None
    list_changed_supported: bool = False
    tools: list[McpDiscoveredTool] = []


# ---------------------------------------------------------------------------
# POST /internal/tools/call  — data plane (caller = agent pod)
# ---------------------------------------------------------------------------

class McpToolCallRequest(BaseModel):
    server_id: UUID          # route target (Tool.mcp_server_id)
    mcp_tool_name: str       # RAW upstream name (Tool.mcp_tool_name), NOT the namespaced Tool.name
    arguments: dict          # already OPA-authorized + de-anonymized by governed_tool
    session_id: str          # == thread_id == run_id; trace correlation only (best-effort)
    agent_name: str          # audit / trace only (best-effort)


class McpToolCallResponse(BaseModel):
    # Always returned with HTTP 200 for tool/transport outcomes (fail-closed body).
    result: str | None = None                # MCP content flattened to a string (like http/python tools)
    is_error: bool = False                   # from MCP tools/call `isError`
    error: str | None = None                 # transport / protocol / tool error text
    structured_content: dict | None = None   # optional MCP structured result passthrough
