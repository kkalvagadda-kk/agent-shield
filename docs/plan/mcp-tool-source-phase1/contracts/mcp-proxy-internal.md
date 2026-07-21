# Contract — MCP Proxy internal endpoints

New service: `services/mcp-proxy`, in-cluster Service `agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8080`. Reached from **agent pods** (SDK `McpToolExecutor` / declarative-runner `McpToolNodeExecutor` → `/internal/tools/call`) and from **registry-api** (`/internal/discover`).

Implemented in `services/mcp-proxy/main.py`; request/response models in `services/mcp-proxy/schemas.py`. Wire shapes are the design doc §3c contract; auth is the design doc §3b contract. The proxy is a **pure MCP wire client** — it does **not** scan output (that runs in `governed_tool` after return) and does **not** de-anonymize (args arrive already de-anonymized).

---

## Authentication (design §3b) — applies to `/internal/*` only

Unlike the ungoverned in-cluster baseline (python-executor/embedding take zero auth), the proxy is the credential custodian for every registered server, so a forged `/internal/tools/call` is a credential confused-deputy. Every `/internal/*` request MUST carry `Authorization: Bearer <token>` where `<token>` is a **projected K8s ServiceAccount token with audience `agentshield-mcp-proxy`**.

- **Verification:** the proxy calls K8s `TokenReview` (`AuthenticationV1Api.create_token_review`, `spec.audiences=["agentshield-mcp-proxy"]`). It requires `status.authenticated == true` **and** `agentshield-mcp-proxy ∈ status.audiences`, then extracts `status.user.username` = `system:serviceaccount:<ns>:<sa>` as `caller_sa_subject`. Positive reviews are cached keyed by `sha256(token)` until the token's `exp` (parsed from the JWT payload) — no K8s hit per call.
- **Proxy SA privileges:** `system:auth-delegator` (TokenReview) + `get` on `secrets` in namespace `agentshield-mcp` (credential Secrets). Nothing else. Never the DB, never `AGENTSHIELD_ENCRYPTION_KEY`.
- **Failure mapping:** missing / malformed / unauthenticated / wrong-audience token → `401 Unauthorized`. A well-authenticated caller that fails the authz floor (below) → `403 Forbidden`.

`X-AgentShield-Trace-ID` is accepted and echoed (safety-orchestrator convention).

`GET /health` and `GET /ready` are **unauthenticated** (probe targets) — they never touch a server or a credential.

---

## `GET /health` · `GET /ready`

Liveness/readiness (mirror python-executor). `200 {"status": "ok"}` unconditionally once the app is up — never depends on a downstream MCP server being reachable (a server outage must not crash-loop the proxy). `/ready` may additionally report whether the in-cluster K8s client initialized.

---

## `POST /internal/discover`  — admin plane

Caller = **registry-api only**. On register and on `/sync`, registry-api first materializes the per-server credential Secret (data-model.md), then calls this endpoint presenting a Bearer token whose subject is registry-api's SA. The proxy verifies the token (above) **and** that `caller_sa_subject` equals registry-api's SA subject; any other authenticated subject → `403` (this is admin-plane, not agent-facing). No per-tool grant check applies to discover.

### Request — `McpDiscoverRequest`
```python
class McpDiscoverRequest(BaseModel):
    server_id: UUID
```
That is the whole request. The proxy resolves everything else (`server_url`, `transport`, `transport_config`, `is_external`, `owner_team`, `auth_headers`) by reading the per-server K8s Secret `agentshield-mcp-server-{server_id}` in `agentshield-mcp` (research.md B3) — no DB, no callback.

### Server-side flow
1. `credentials.read_server_secret(server_id)` → `ServerConnection`. Secret missing → this is an error result (below), not an exception to the caller.
2. Open a `streamable_http` client + `mcp.ClientSession` against `server_url` with `auth_headers`, run `initialize()` (capture the negotiated `protocolVersion` and whether `tools.listChanged` capability was advertised → `list_changed_supported`).
3. Run `list_tools()`.
4. Cache the live session in `session_cache.py` keyed by `server_id`.
5. Return the discovered tools + capability flags.

### Response — `McpDiscoverResponse`  (always HTTP `200`)
```python
class McpDiscoveredTool(BaseModel):
    name: str                       # RAW upstream tool name → registry-api namespaces it to Tool.name
    description: str | None = None
    input_schema: dict              # JSON Schema → Tool.input_schema

class McpDiscoverResponse(BaseModel):
    ok: bool                        # true iff connect+initialize+list_tools all succeeded
    status: str                     # 'connected' | 'error'  → MCPServer.status
    health_detail: str | None = None    # failure reason (string) → registry-api folds into health_detail.last_error
    protocol_version: str | None = None
    list_changed_supported: bool = False
    tools: list[McpDiscoveredTool] = []
```

Success example:
```json
{ "ok": true, "status": "connected", "health_detail": null, "protocol_version": "2025-06-18",
  "list_changed_supported": false,
  "tools": [ {"name": "search_issues", "description": "Search issues", "input_schema": {"type":"object","properties":{"query":{"type":"string"}},"required":["query"]}} ] }
```
Failure example (connection refused / bad creds / timeout / secret missing) — still HTTP `200`:
```json
{ "ok": false, "status": "error", "health_detail": "connection refused to http://...: [Errno 111]",
  "protocol_version": null, "list_changed_supported": false, "tools": [] }
```

**Ownership split:** the **proxy** connects + lists; **registry-api** owns every DB write — the `{server_name}__{mcp_tool_name}` namespacing, `Tool` upserts, and `discovered_tool_count`/`last_synced_at`/`status`/`health_detail`. Namespacing never happens in the proxy.

### Errors (real HTTP status)
- `401` — missing/invalid/wrong-audience token.
- `403` — authenticated, but `caller_sa_subject` is not registry-api's SA (discover is admin-plane).
- `422` — body fails schema validation (missing/invalid `server_id`).
- A connect/`initialize` failure or a missing Secret is **not** an HTTP error — it is `200` + `status:"error"` + a `health_detail` reason (mirrors registry-api treating a failed connect as a successful API call about an unhealthy server).

---

## `POST /internal/tools/call`  — data plane

Caller = **agent pod** (SDK `McpToolExecutor` / runner `McpToolNodeExecutor`), **after** `governed_tool`'s OPA-authorize + HITL-approve + de-anonymize steps have run. This endpoint is the "execute" step only; it has no governance logic of its own beyond the §3b coarse floor.

Headers: `Authorization: Bearer <SA token>` (audience `agentshield-mcp-proxy`, **required**); `X-AgentShield-Trace-ID` (optional); `x-user-sub` (optional, Phase 2 on-behalf-of — **ignored for any credential/authz decision in Phase 1**).

### Request — `McpToolCallRequest`
```python
class McpToolCallRequest(BaseModel):
    server_id: UUID          # route target (Tool.mcp_server_id)
    mcp_tool_name: str       # RAW upstream name (Tool.mcp_tool_name), NOT the namespaced Tool.name
    arguments: dict          # already OPA-authorized + de-anonymized by governed_tool
    session_id: str          # == thread_id == run_id; trace correlation only (best-effort)
    agent_name: str          # audit / trace only (best-effort)
```
The caller uses `Tool.mcp_tool_name` (raw), never the namespaced `Tool.name` — the proxy does not un-namespace. `session_id`/`agent_name` are trace metadata and drive **no** credential or authz decision in Phase 1 (the SA token is the identity of record).

### Server-side flow
1. **AuthN:** verify the Bearer token (§3b) → `caller_sa_subject`. Fail → `401`.
2. **AuthZ floor (design §3b):** `caller_team = authz.team_from_sa_subject(caller_sa_subject)` (parse the `agents-{team}` namespace; a non-`agents-` namespace → `403`). Read the per-server Secret's `connection.owner_team`. **Fast path:** `caller_team == owner_team` → allow. **Cross-team:** call registry-api `POST /api/v1/internal/mcp/authorize-tool-call {caller_sa_subject, server_id, mcp_tool_name}` → `{allowed}`; `allowed == false` → `403`. Cache the cross-team decision per `(caller_sa_subject, server_id, mcp_tool_name)` (short TTL).
3. Look up (or lazily create, on cache miss) the live session for `server_id` from `session_cache.py` (same read-secret-then-connect flow as discover, skipped on a cache hit).
4. `call_tool(mcp_tool_name, arguments)` via `mcp_client.py`.
5. On a transport/auth error (dropped connection, upstream 401), evict the cached session and retry **once** with a fresh connection before giving up.

### Response — `McpToolCallResponse`  (always HTTP `200` for tool/transport outcomes)
```python
class McpToolCallResponse(BaseModel):
    result: str | None = None                # MCP content flattened to a string (like http/python tools)
    is_error: bool = False                   # from MCP tools/call `isError`
    error: str | None = None                 # transport / protocol / tool error text (fail-closed body)
    structured_content: dict | None = None   # optional MCP structured result passthrough
```
`result` is a **string** — the same contract every other executor returns to `governed_tool` (whose output-scan step operates on `str`). If `CallToolResult.content` has multiple text blocks, concatenate them; non-text blocks are stringified as a placeholder (`"[non-text content: image]"`) in Phase 1 (richer multimodal is out of scope). A tool that ran but reported an error sets `is_error=true` with the message in `result`/`error` — still HTTP `200` (FR-MCP-14: a structured tool error to the agent, not a crash). A **transport/credential failure that could not complete the call** (server unreachable after the one retry, secret missing, upstream auth failed) is **also** `200` with `is_error=true` + a populated `error` — design §3c fail-closed body — so the caller hands the error back to the LLM. There is **no** 5xx for a downstream MCP problem; only real auth failures use non-200.

### Errors (real HTTP status)
- `401` — missing/invalid/wrong-audience token.
- `403` — authenticated but fails the §3b team floor (bad namespace, or cross-team with no grant).
- `422` — malformed request body.
- (No `404`/`502` for a missing tool or an unreachable server — those become `200` + `is_error=true`, so the SDK/runner never raise; FR-MCP-14.)

---

## What the proxy does NOT do
- **No output scanning** — Decision 27's `scan_output` runs in `governed_tool` after this returns (design §3 step 6). The proxy returns the raw upstream result.
- **No de-anonymization** — `arguments` arrive already de-anonymized (design §3 step 4).
- **No DB access** and **no `AGENTSHIELD_ENCRYPTION_KEY`** — server connection + credentials come only from the per-server K8s Secret (research.md B3/B13).
- **No namespacing / no `Tool` writes** — registry-api owns all DB writes.
