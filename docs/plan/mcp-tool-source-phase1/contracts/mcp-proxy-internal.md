# Contract — MCP Proxy internal endpoints

New service: `services/mcp-proxy`. Internal-only (no public ingress; reached from `registry-api` and from agent pods' SDK/declarative-runner over the in-cluster Service `agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8000`). No end-user auth on these endpoints — trust boundary is NetworkPolicy, matching every other internal platform service (`python-executor`, `embedding-sidecar`).

Implemented in `services/mcp-proxy/main.py`, request/response models in `services/mcp-proxy/schemas.py`.

---

## `GET /health`

Liveness/readiness probe target (mirrors `python-executor`'s `/health` exactly). `200 {"status": "ok"}` unconditionally once the FastAPI app is up — does not depend on any MCP server being reachable (a downstream server outage must not crash-loop the proxy pod).

---

## `POST /internal/discover`

Called by `registry-api` on server register and on `/sync`.

### Request
```json
{ "server_id": "b3e5b6b0-...-uuid" }
```
That's the whole request — per the architecture doc §3's Data Flow ("registry-api... calls MCP Proxy `POST /internal/discover {server_id}`") and research.md B3, MCP Proxy resolves everything else (`server_url`, `transport`, `auth_config_id`, credentials) itself by calling back into `registry-api`.

### Server-side flow
1. `GET {REGISTRY_API_URL}/api/v1/mcp-servers/{server_id}` → `server_url`, `transport`, `transport_config`, `auth_config_id`, `is_external`. `404` from registry-api here is an unexpected-state error (registry-api just inserted this row) — treated as a discover failure, not retried.
2. If `auth_config_id` is set: resolve credentials (see `credentials.py`, research.md B3) → a headers dict (e.g. `{"Authorization": "Bearer <token>"}` or `{"X-API-Key": "<key>"}`, shape driven by the `AuthConfig.type`).
3. Open a `streamable_http` client session (`mcp_client.py`) against `server_url` with those headers, run `initialize()`.
4. Run `list_tools()`.
5. Cache the live session in `session_cache.py` keyed by `server_id` (subsequent `/tools-call`s for the same server on this replica reuse it).
6. Return the discovered tool list + capability flags.

### Response — success
```json
{
  "status": "connected",
  "tools": [
    {
      "mcp_tool_name": "search_issues",
      "description": "Search issues in a GitHub repository",
      "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
    }
  ],
  "list_changed_supported": false,
  "health_detail": {"last_error": null, "consecutive_failures": 0}
}
```

### Response — failure (still HTTP `200` — see note below)
```json
{
  "status": "error",
  "tools": [],
  "list_changed_supported": false,
  "health_detail": {"last_error": "connection refused to https://...: [Errno 111]", "consecutive_failures": 1}
}
```

**Note on status codes:** `/internal/discover` returns HTTP `200` for both outcomes — the *distinction* is carried in the `status` field, not the HTTP status line. This mirrors how `registry-api`'s own `POST /mcp-servers` treats a failed connect as a successful *API call* about an unhealthy server (data-model.md / the registry-api contract). Reserve HTTP-level errors (`4xx`/`5xx`) for genuinely malformed requests (e.g. `server_id` missing from the body → `422`) or the registry-api lookup itself failing unexpectedly (`502`, since that's an internal-plumbing failure, not "the target MCP server is down").

### Errors
- `422` — request body fails schema validation (missing `server_id`, not a valid UUID).
- `502 Bad Gateway` — the callback to `registry-api` (`GET /api/v1/mcp-servers/{server_id}`) itself failed (registry-api unreachable, or returned a non-404 5xx) — distinct from "the *target MCP server* is unreachable," which is a `200` + `status: "error"` as above.

---

## `POST /internal/tools-call`

Called by the SDK's `McpToolExecutor` and the declarative-runner's `McpToolNodeExecutor` — **after** `governed_tool`'s OPA-authorize + HITL-approve + de-anonymize-args steps have already run (Decision 27's gate ordering: authorize → approve → de-anonymize → **execute** → scan). This endpoint is the "execute" step only; it has no governance logic of its own.

### Request
```json
{
  "server_id": "b3e5b6b0-...-uuid",
  "mcp_tool_name": "search_issues",
  "args": {"query": "is:open label:bug"}
}
```
`args` is whatever `governed_tool` is about to pass the tool — already de-anonymized if `allow_deanonymize` was true and a substitution occurred. `mcp_tool_name` is the raw upstream name (`Tool.mcp_tool_name`), **not** the namespaced `Tool.name` — the caller (SDK/runner) is responsible for using the right one; the proxy does not un-namespace anything.

### Server-side flow
1. Look up (or lazily create, on cache miss) the live session for `server_id` from `session_cache.py` — same resolve-server-then-resolve-credentials-then-connect flow as `/internal/discover`, but skipped entirely on a cache hit.
2. `call_tool(mcp_tool_name, args)` via `mcp_client.py`.
3. On a transport/auth error (connection dropped, 401 from the server), evict the cached session and retry **once** with a fresh connection (covers the common case of a stale HTTP session on the remote server) before giving up.

### Response — success
```json
{
  "is_error": false,
  "result": "{\"issues\": [{\"number\": 42, \"title\": \"...\"}]}",
  "latency_ms": 340
}
```
`result` is a **string** (mirrors the existing contract every other tool executor already returns to `governed_tool` — `HttpToolExecutor`/`PythonToolExecutor` both return `str`, and `governed_tool`'s output-scan step operates on `text: str`). If the MCP tool's `CallToolResult.content` contains multiple content blocks, concatenate their text (mirrors `_join_message_text`'s existing pattern in `graph_builder.py` for LLM message content) — non-text content blocks (e.g. embedded images) are stringified as a placeholder (`"[non-text content: image]"`) in Phase 1; a richer multimodal tool-result path is out of scope.

### Response — tool-level error (the target tool ran but reported an error — MCP's own `isError` flag)
```json
{
  "is_error": true,
  "result": "Tool error: repository not found",
  "latency_ms": 210
}
```
Still HTTP `200` — an `isError` result is a normal, structured outcome the calling `McpToolExecutor` returns to the LLM as tool output (FR-MCP-14: "returns a structured tool error to the agent, not a crash"), not an HTTP-level failure.

### Errors
- `422` — malformed request body.
- `404` — `mcp_tool_name` not found on the target server's current tool list (only detectable after connecting — if the session is cached from a discovery that's since gone stale, this surfaces as a tool-level error from the server itself instead, which is fine — same FR-MCP-14 outcome either way, just via a different response shape upstream).
- `502` — could not establish/re-establish a session with the target server at all (connection refused, timeout, TLS failure) after the one retry in step 3. The caller (`McpToolExecutor`) turns this into the same "structured tool error, not a crash" string `governed_tool` returns to the LLM — the 502 never propagates as an unhandled exception up to the agent runtime.
- `502` — credential resolution itself failed (the registry-api `secret-ref` call errored, or the K8s Secret read failed) — same handling as above from the caller's perspective.

---

## Auth / trust boundary

Neither endpoint validates a caller identity beyond "reachable over the cluster network" — this mirrors `python-executor`'s `/execute` and `embedding-sidecar`'s `/embed`, both internal-only services with no per-caller auth today. MCP Proxy's own outbound calls (to `registry-api` and to target MCP servers) carry whatever credentials research.md B3 describes; nothing about *inbound* calls to MCP Proxy is authenticated in Phase 1. This is consistent with the existing platform posture for internal services (NetworkPolicy is the trust boundary, not a service JWT) and is not a new gap this design introduces.
