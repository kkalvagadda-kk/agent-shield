# MCP Server — Platform Gateway for Agent Consumption

## Problem

Agents — whether running inside the platform (deployed via AgentShield) or externally (Claude Desktop, Cursor, CI pipelines) — need a standardized way to consume the full platform API. Today, the only interface is the REST API, which requires each agent to implement custom HTTP client logic, handle auth token lifecycle, and know the endpoint schema. The Model Context Protocol (MCP) provides a standard interface that any MCP-capable agent can consume immediately.

## Decision

Build an MCP server as a separate service (`services/mcp-server/`) that exposes **every platform operation** — agents, tools, skills, workflows, eval, deployments, admin — as MCP tools. The server is a stateless protocol translation layer: it authenticates callers via Keycloak JWT, checks OPA authorization policies, then proxies to registry-api. Enabling it is controlled by `mcp-server.enabled` in the Helm chart (disabled by default).

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│  Agents (Claude Desktop, Cursor, platform agents, CI, etc.)     │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTPS + Bearer JWT (Keycloak)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Envoy Gateway (HTTPRoute /mcp → JWT SecurityPolicy)            │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  MCP Server (services/mcp-server/)                              │
│                                                                 │
│  ┌──────────┐  ┌──────────┐  ┌───────────┐  ┌──────────────┐  │
│  │ Protocol │  │  Auth    │  │  Session  │  │  OPA Policy  │  │
│  │(JSON-RPC)│  │(JWT+Team)│  │  (Redis)  │  │  Enforcement │  │
│  └────┬─────┘  └──────────┘  └───────────┘  └──────┬───────┘  │
│       │                                             │           │
│       └──────────── Registry Client ────────────────┘           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP (internal, passes caller identity)
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│  Registry API (all CRUD, tool execution, deployments, eval)     │
└─────────────────────────────────────────────────────────────────┘
```

### Why a separate service (not embedded in registry-api)

- MCP holds long-lived SSE connections (up to 30 minutes for HITL approval waits) while registry-api handles short-lived CRUD requests — mixing these creates operational coupling.
- Different scaling axis: MCP scales by concurrent connections, registry-api by DB throughput.
- Smaller external-facing attack surface — the MCP server has no direct DB access.
- Stateless: no owned tables, all data flows through registry-api.

### Core principle

The MCP server owns **no business logic**. For every request it:
1. Authenticates the caller (JWT → sub, team, role)
2. Evaluates OPA policy for the operation
3. Proxies to registry-api with caller identity
4. Formats the response as MCP protocol

---

## Security Model

### Authentication: Keycloak JWT (mandatory)

Every MCP request carries a valid JWT. Validation is identical to `auth_middleware.py`:
- JWKS from `{KEYCLOAK_URL}/realms/agentshield/protocol/openid-connect/certs` (cached 5 min)
- RS256, `verify_aud: False` (same loose audience check as existing middleware)
- Extract `sub` → resolve team from `user_team_assignments` via registry-api

New Keycloak client `mcp-server` added to the realm-init job with `standardFlowEnabled: true` for browser-capable clients and `serviceAccountsEnabled: true` for M2M.

### Authentication: API Keys (non-browser agents)

For CI/CD pipelines and programmatic agents that cannot perform interactive OIDC:
- New `mcp_api_keys` table: `key_hash` (bcrypt), `user_sub`, `team`, `role`, `scopes`, `expires_at`, `revoked_at`
- Sent as `Authorization: Bearer mcpk_...` or `X-Api-Key: mcpk_...`
- Resolves to the same (sub, team, role) triple as JWT
- Managed via `POST/GET/DELETE /api/v1/admin/mcp-api-keys` in registry-api

### Authentication: Internal platform agents

Agents deployed on the platform use their provisioned Keycloak service account (client_credentials grant):
```
POST /realms/agentshield/protocol/openid-connect/token
grant_type=client_credentials&client_id=agent-{name}&client_secret=...
```
Returns JWT with `sub` = `service-account-agent-{name}`. Same validation path as external tokens.

### Authorization: OPA Policy Enforcement

Every MCP tool call evaluates OPA before proxying:

```
MCP tools/call
  → Authenticate (JWT → sub, team, role)
  → Build OPA input:
      {
        "caller": {"sub": "...", "team": "...", "role": "..."},
        "operation": "agents_create",
        "resource": {"type": "agent", "name": "..."},
        "args": {...}
      }
  → POST to OPA /v1/data/agentshield/platform/authz
  → allow → proxy to registry-api
  → deny → MCP error with deny_reason
  → require_approval → enter HITL flow
  → Log decision to /api/v1/opa-decisions (immutable audit)
```

New OPA policy package `agentshield.platform.authz` formalizes:
- Role-based access (admin can do everything, operator manages own team, viewer reads only)
- Team scoping (operators cannot modify other teams' resources unless granted)
- Operation classification (read / write / delete / deploy / admin)

This is the same authorization the platform already enforces in registry-api route handlers — but formalized into OPA so one policy engine governs both direct API and MCP access.

---

## Transport: MCP Streamable HTTP

Single endpoint: **`POST /mcp`**

All JSON-RPC messages go to this endpoint per the MCP spec:

```http
POST /mcp
Content-Type: application/json
Accept: text/event-stream
Authorization: Bearer <jwt>
Mcp-Session-Id: <uuid>

{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "agents_create", "arguments": {...}}, "id": 1}
```

**Synchronous responses** (tools/list, most CRUD): immediate JSON-RPC result in the SSE stream, then stream closes.

**Streaming responses** (chat_stream, playground_stream, HITL waits): SSE stream with progress events followed by final result.

### Session lifecycle

- Created at `initialize` — server returns capabilities, generates session ID
- Stored in Redis (or in-memory for single-replica dev)
- Contains: `session_id`, `caller_sub`, `team`, `role`, `created_at`, `last_seen_at`
- TTL: 30 minutes, refreshed on activity
- Destroyed on explicit `shutdown` message or TTL expiry

---

## MCP Tool Catalog

The MCP server exposes every registry-api operation as an MCP tool. Tools are named `{domain}_{action}` and organized by domain.

### Tool Definition Generation

Each MCP tool needs a name, a human-readable description, and a JSON Schema describing its inputs. These are **not hand-written** — they are derived automatically from what FastAPI already generates.

#### Where descriptions come from

Every registry-api endpoint already has a description via FastAPI's route decorator:

```python
@router.post("/api/v1/agents/", summary="Register a new agent with optional tool bindings")
async def create_agent(body: AgentCreate, ...):
    ...
```

FastAPI places the `summary` (and any docstring) into the OpenAPI spec it serves at `GET /openapi.json`.

#### Where input schemas come from

Every endpoint's request body is a Pydantic model with typed fields:

```python
class AgentCreate(BaseModel):
    name: str
    description: str | None = None
    tools: list[str] = []
    agent_type: str = "sdk"
```

FastAPI auto-converts this into JSON Schema in the OpenAPI spec:

```json
{
  "type": "object",
  "properties": {
    "name": {"type": "string"},
    "description": {"type": "string", "nullable": true},
    "tools": {"type": "array", "items": {"type": "string"}, "default": []},
    "agent_type": {"type": "string", "default": "sdk"}
  },
  "required": ["name"]
}
```

Path parameters and query parameters (also typed in the route signature) are likewise captured in the OpenAPI spec.

#### How the MCP server uses this

At startup, the MCP server:
1. Fetches `GET /openapi.json` from registry-api
2. For each tool in its declarative mapping, finds the matching OpenAPI operation
3. Extracts the `summary` → MCP tool `description`
4. Merges path params + query params + request body into a single `inputSchema` JSON Schema object
5. Caches the result (refreshes every 5 minutes)

#### What the declarative mapping provides

The MCP server maintains a lightweight mapping that controls **naming and routing** — not schemas:

```python
TOOL_DEFINITIONS = [
    ToolDef(name="agents_create",  method="POST", path="/api/v1/agents/"),
    ToolDef(name="agents_list",    method="GET",  path="/api/v1/agents/"),
    ToolDef(name="agents_publish", method="POST", path="/api/v1/agents/{name}/publish"),
    ...
]
```

This exists because:
- It gives tools stable, well-named identifiers (not leaked internal route names like `create_agent_api_v1_agents__post`)
- It allows annotating tools with OPA policy metadata and streaming behavior
- It allows selective exclusion of internal-only endpoints if needed

#### End-to-end flow

```
Developer writes Pydantic model + route in registry-api
    → FastAPI auto-generates OpenAPI spec (descriptions + JSON Schemas)
    → MCP server reads OpenAPI at startup
    → MCP client calls tools/list → receives tools with descriptions + schemas
```

No duplication. Any new endpoint added to registry-api (with a mapping entry) automatically becomes an MCP tool with accurate schema.

### Complete tool listing by domain

**Agents**: `agents_list`, `agents_get`, `agents_create`, `agents_update`, `agents_delete`, `agents_quarantine`, `agents_unquarantine`, `agents_publish`, `agents_list_identities`

**Agent Versions**: `versions_create`, `versions_list`, `versions_patch`, `versions_get`

**Deployments**: `deployments_deploy`, `deployments_rollback`, `deployments_list_for_agent`, `deployments_list_all`, `deployments_patch`

**Tools**: `tools_list`, `tools_get`, `tools_create`, `tools_update`, `tools_delete`, `tools_execute`, `tools_list_agents`

**Agent-Tool Bindings**: `agent_tools_bind`, `agent_tools_unbind`, `agent_tools_list`

**Skills**: `skills_list`, `skills_get`, `skills_create`, `skills_update`, `skills_delete`

**Workflows**: `workflows_list`, `workflows_get`, `workflows_create`, `workflows_update`, `workflows_deploy`, `workflows_list_versions`, `workflows_restore_version`

**Approvals / HITL**: `approvals_list`, `approvals_get`, `approvals_decide`, `approvals_reopen`

**Playground (Execution)**: `playground_run`, `playground_list_runs`, `playground_get_run`, `playground_stream`, `playground_get_trace`, `playground_save_to_dataset`, `playground_feedback`

**Consumer Chat (Production Execution)**: `chat_start`, `chat_stream`

**Datasets**: `datasets_list`, `datasets_create`, `datasets_get`, `datasets_update`, `datasets_delete`

**Eval**: `eval_create_run`, `eval_list_runs`, `eval_get_run`, `eval_get_results`

**Teams**: `teams_list`, `teams_get`, `teams_create`, `teams_update`, `teams_list_agents`

**Admin**: `admin_regenerate_bundle`, `admin_list_publish_requests`, `admin_approve_publish`, `admin_reject_publish`, `admin_create_grant`, `admin_list_grants`, `admin_revoke_grant`, `admin_get_grant_audit`, `admin_list_approval_authority`, `admin_create_approval_authority`, `admin_revoke_approval_authority`

**Admin Users**: `admin_users_list`, `admin_users_create`, `admin_users_get`, `admin_users_update`, `admin_users_delete`, `admin_users_reset_password`, `admin_teams_summary`

**LLM Providers**: `llm_providers_list`, `llm_providers_get`, `llm_providers_create`, `llm_providers_update`, `llm_providers_delete`

**OPA Audit**: `opa_decisions_list`

**Identity**: `me`

---

## HITL Approval Flow in MCP

When OPA returns `require_approval` for an operation (e.g., production deployment, high-risk tool execution):

1. MCP server creates approval record via `POST /api/v1/approvals`
2. Returns SSE stream with progress event:
   ```
   event: message
   data: {"jsonrpc":"2.0","method":"notifications/progress","params":{"type":"approval_requested","approval_id":"...","expires_at":"..."}}
   ```
3. Polls `GET /api/v1/approvals/{id}` every 5 seconds
4. SSE comment `: keepalive` every 30 seconds
5. On `approved` → executes the original operation, returns tool result, closes stream
6. On `rejected` / `timed_out` → returns MCP error, closes stream
7. On client disconnect → cancels polling (no orphaned loops)
8. Hard timeout: 30 minutes maximum

---

## MCP Resources

Read-only browsable data (no execution):

| URI | Description |
|-----|-------------|
| `agentshield://agents` | Published + team-owned agent catalog |
| `agentshield://agents/{name}` | Agent detail with versions |
| `agentshield://tools` | Active tools accessible to caller |
| `agentshield://tools/{id}` | Tool detail: schema, risk level, type |
| `agentshield://workflows` | Team workflows |
| `agentshield://workflows/{id}` | Workflow definition + version history |
| `agentshield://runs` | Recent agent runs (team-scoped) |
| `agentshield://approvals/pending` | Pending approvals (authority-scoped) |
| `agentshield://datasets` | Eval datasets |
| `agentshield://eval-runs` | Eval history + scores |

All resources serve JSON content. Access control follows the same team/role/grant checks as tool calls.

---

## Service Structure

```
services/mcp-server/
├── Dockerfile
├── requirements.txt
├── main.py                  # FastAPI app, lifespan, /health, /ready, POST /mcp
├── config.py                # pydantic_settings
├── auth.py                  # JWT verification + API key resolution
├── session.py               # McpSession, memory + Redis backends
├── protocol.py              # JSON-RPC parsing, method routing, SSE formatting
├── tool_registry.py         # Declarative tool-name → route mapping
├── handlers/
│   ├── __init__.py
│   ├── initialize.py        # initialize, ping, shutdown
│   ├── tools.py             # tools/list, tools/call
│   ├── resources.py         # resources/list, resources/read
│   └── prompts.py           # prompts/list, prompts/get
├── governance.py            # OPA evaluation, HITL poll loop, audit logging
├── registry_client.py       # httpx AsyncClient, circuit breaker
├── streaming.py             # SSE proxy for chat/playground streams
└── rate_limit.py            # Per-caller token bucket
```

---

## Helm Chart

### Sub-chart: `charts/agentshield/charts/mcp-server/`

```
charts/agentshield/charts/mcp-server/
├── Chart.yaml               # condition: mcp-server.enabled
├── values.yaml
└── templates/
    ├── _helpers.tpl
    ├── deployment.yaml
    ├── service.yaml
    ├── serviceaccount.yaml
    └── httproute.yaml       # Envoy route: /mcp → mcp-server:8080
```

### Parent Chart.yaml dependency:
```yaml
- name: mcp-server
  version: "0.1.0"
  repository: "file://charts/mcp-server"
  condition: mcp-server.enabled
```

### Key Helm values:
```yaml
mcp-server:
  enabled: false              # deployer controls this

  replicaCount: 2
  image:
    repository: registry.internal/agentshield/mcp-server
    tag: "0.1.0"
    pullPolicy: IfNotPresent
  service:
    type: ClusterIP
    port: 8080

  env:
    REGISTRY_API_URL: "http://agentshield-registry-api:8000"
    OPA_URL: "http://agentshield-opa:8181"
    KEYCLOAK_URL: ""
    KEYCLOAK_REALM: "agentshield"
    SESSION_BACKEND: "memory"     # "memory" | "redis"
    REDIS_URL: ""
    RATE_LIMIT_RPM: "60"
    LOG_LEVEL: "INFO"

  auth:
    requireJwt: true
    allowApiKeys: true

  resources:
    requests: {cpu: 100m, memory: 256Mi}
    limits: {cpu: 500m, memory: 512Mi}
```

### Deploy script (`scripts/deploy-cpe2e.sh`):
```bash
MCP_SERVER_TAG="0.1.0"
# In helm upgrade:
--set "mcp-server.enabled=${MCP_SERVER_ENABLED:-false}"
--set "mcp-server.image.tag=${MCP_SERVER_TAG}"
```

---

## Reliability

| Pattern | Implementation |
|---------|---------------|
| Circuit breaker | `registry_client.py`: CLOSED → OPEN (5 failures/30s) → HALF_OPEN (60s cooldown) |
| Rate limiting | Per-caller token bucket, configurable via `RATE_LIMIT_RPM` |
| Health probes | `/health` (liveness), `/ready` (registry-api + session store reachable) |
| Graceful shutdown | SIGTERM → stop new sessions → drain streams (30s) → exit |
| Tool catalog cache | 5-min TTL; stale cache served when registry-api is down |
| SSE keepalive | Comment line every 30s |
| Reconnection | `Last-Event-ID` → replay buffered events (60s ring buffer) |

---

## Prerequisites in Registry-API

| Change | Purpose |
|--------|---------|
| Complete `POST /api/v1/tools/{id}/execute` | Real tool execution (currently a test stub) |
| New OPA package `agentshield.platform.authz` | Platform-operation authorization policy |
| New `mcp_api_keys` table + admin endpoints | API key auth for non-browser agents |
| Realm-init: `mcp-server` Keycloak client | OAuth 2.1 for MCP connections |

---

## Phasing

**Phase 1 — Full CRUD via MCP**: Service skeleton, JWT auth, OPA checks, declarative tool registry exposing all endpoints, audit logging, Helm chart. Agents can create/manage agents, tools, skills, workflows, run evals, deploy — everything except streaming.

**Phase 2 — Streaming + HITL**: SSE streaming for chat and playground execution. HITL approval flow. Redis sessions for multi-replica. Rate limiting. API key auth.

**Phase 3 — Hardening**: Client_credentials for internal agents. Circuit breaking. Reconnection support. Langfuse traces. Load testing.
