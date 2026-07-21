# Plan — MCP as a Tool Source, Phase 1

**Status:** Ready for implementation (updated 2026-07-21 to reflect design §3b proxy auth, §3c wire contract, credential path (b), the STUB output-scan action, and the lifecycle locks).
**Scope:** Phase 1 only — HTTP (`streamable_http`) discovery + governed execution (internal + external, static auth) + the generic per-tool-call de-anonymize / output-scan gate (Decision 27, whose output-scan **action is a STUB** in Phase 1). Phase 2 (health / notifications / on-behalf-of identity — externally blocked), Phase 3 (stdio), Phase 4 (OAuth 2.1 / resources / prompts) are **deferred, not planned here** — see the Gap Ledger.
**Inputs:** `docs/design/mcp-tool-source-architecture.md` (LOCKED, updated 2026-07-21), `docs/design/todo/mcp-tools-for-agents-requirements.md`, `docs/decisions.md` 15/27/28/29, this repo's `CLAUDE.md`.
**Companion artifacts (same dir):** `research.md`, `data-model.md`, `contracts/registry-api-mcp-servers.md`, `contracts/registry-api-internal-mcp.md`, `contracts/mcp-proxy-internal.md`, `quickstart.md`.

> **Where the design doc and this plan differ, the design doc wins.** The one deliberate refinement is research.md B3/B12/B13: the proxy resolves server connection + credentials from a per-server K8s Secret and does its cross-team authz via an internal registry-api endpoint, rather than the direct DB read §3c words literally — justified there by §3b's own least-privilege framing, and the brief's explicit delegation of that choice to `/plan`.

---

## Scope Check — one plan or several?

One vertical slice. A single MCP tool call only works end-to-end once **all** of these exist together: the DB columns, the MCP Proxy service (with its auth + credential resolution), the registry-api CRUD+discovery router (with per-server Secret materialization), the SDK dispatch branch, the declarative-runner's separate dispatch branch, and Decision 27's generic gate inside `governed_tool` (shared by both runtimes). None is independently shippable as a user-visible increment. This is **one ordered task list**, not several plans. Tasks are ordered so the register→discover→bind→appears-in-picker slice is provable early (Tasks 1–6 back it; Task 15's suite proves it end-to-end) before the Decision-27 governance polish and the last UI tasks.

`policy_generator.py` was checked for genuine independence and found dead at runtime (research.md #8): it is touched (Task 9) only as a clearly-labeled audit-parity sub-step, never as the enforcement path.

---

## Goal

Make tools hosted on external/internal MCP servers available to AgentShield agents under exactly the same governance every other tool gets (OPA authorize → HITL approve if required → de-anonymize → execute → output-scan), with zero MCP-specific carve-outs in the governance path. Ship: server registration + auto-discovery (Studio + API) with a proxy that authenticates its callers and holds credentials off the master key; `mcp_tool` dispatch in both agent runtimes; and the generic per-tool-call de-anonymize/output-scan gate (Decision 27) — with the output-scan **action stubbed** and honestly ledgered.

---

## Architecture

```
┌──────────────┐  governed_tool(mcp_tool)      ┌──────────────────────────────────┐
│  Agent Pod   │  Bearer <mcp-proxy-token>     │        MCP Proxy service          │
│ SDK / decl-  │──POST /internal/tools/call───▶│      (services/mcp-proxy, NEW)     │
│   runner     │◀────result (str), 200─────────│  agentshield-platform ns          │
└──────┬───────┘                               │  • authn.py  TokenReview + cache  │
       │ OPA authorize (+allow_deanonymize)    │  • authz.py  team-from-ns + floor │
       │ HITL if required                      │  • credentials.py  per-server K8s │
       │ NEW: de-anonymize args (fail-open)     │    Secret read (ns agentshield-mcp)│
       │ execute → NEW: output-scan (STUB act.) │  • mcp_client.py (`mcp` SDK)      │
       ▼                                        │  • session_cache.py               │
   OPA sidecar (bundle-server-fed)              └───┬──────────────┬────────────────┘
       │ NEW: record_decision (best-effort)        │ cross-team    │ streamable_http
       ▼                                            │ authz only    ▼
  POST /api/v1/opa-decisions/ (registry-api)        │        Internal / External MCP servers
                                                    ▼
                              POST /api/v1/internal/mcp/authorize-tool-call
                                                    │  (registry-api, team_may_use_tool)
┌─────────────┐   CRUD + /sync            ┌─────────┴──────────┐
│   Studio    │──────────────────────────▶│     registry-api    │  materialize per-server
│ MCP Servers │◀───discovered tools───────│ routers/mcp_servers  │──▶ K8s Secret (ns
│  (NEW pages)│                           │ + mcp_secrets.py     │    agentshield-mcp)
└─────────────┘                           │ + mcp_proxy_client   │──▶ Postgres (mcp_servers,
                                          └──────────┬───────────┘    tools rows)
                                            Bearer <mcp-proxy-token>
                                                     ▼  POST /internal/discover {server_id}
                                                MCP Proxy
```

**One governance seam, two dispatch points.** `governed_tool` (Decision 27's gate) lives only in `sdk/agentshield_sdk/graph_builder.py`. The declarative-runner's `AgentNodeExecutor.build_subgraph()` already imports `agentshield_sdk.graph_builder.build_graph()`, so every Task-11 change to `governed_tool` is inherited by declarative-runner agent-owned tool calls when the runner image is rebuilt against the updated SDK. What needs **two** implementations (grounding correction #4) is only the **dispatch** — turning a `type='mcp_tool'` dict into a callable — because the two runtimes have always had duplicated dispatch for every other type (`HttpToolExecutor` vs `HttpToolNodeExecutor`). MCP follows that existing pattern.

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 async ORM, Alembic, `httpx` (async), PostgreSQL, Kubernetes Python client (`kubernetes`, in-cluster config; `AuthenticationV1Api.create_token_review` for AuthN, `CoreV1Api.read_namespaced_secret` for creds).
- **MCP protocol:** official `mcp` Python SDK (`mcp.client.streamable_http`, `mcp.ClientSession`, `mcp.server.fastmcp.FastMCP` for the fixture) — research.md B1, pin `mcp>=1.2,<2.0`.
- **Frontend:** React + TypeScript + Vite + TailwindCSS, TanStack Query, react-hook-form + zod, Vitest + RTL, Playwright.
- **Infra:** Helm (new `charts/agentshield/charts/mcp-proxy` sub-chart mirroring `python-executor`, plus its own ServiceAccount/RBAC mirroring `registry-api`), `scripts/deploy-cpe2e.sh`, `infra/network-policies/`.
- **Test harness:** bash + `kubectl exec` + inline Python/httpx (backend e2e), Vitest, Playwright.

---

## Constitution Check (against this repo's `CLAUDE.md`)

| # | Principle | Status | How this plan satisfies it |
|---|---|---|---|
| 1 | Real user journey proven (Playwright, not just an endpoint) | **PASS (planned)** | Task 16's `studio/e2e/mcp-servers.spec.ts` drives register → see discovered tools → bind to an agent → tool appears in `ToolsPicker` with a source-server badge → reload agent, still bound — real clicks + `page.waitForResponse`. |
| 2 | Save → reload → assert survived | **PASS (planned)** | Task 16 reloads the Server Detail route and re-asserts the discovered-tools table from a fresh `GET`; reloads the agent builder and re-asserts the MCP tool still bound. Task 12's Vitest covers the list-refetch-after-create round trip at the component level. |
| 3 | No orphan code | **PASS (planned)** | Every new exported symbol in Key Interfaces has a named caller in the same or an immediately-dependent task; File Structure lists every file used and every task lists exactly the files it touches. |
| 4 | Vertical slices, not horizontal layers | **PASS** | Order: migration (1) → models/schemas (2) → shared authz + secret + internal endpoint (3) → deploy-controller token (4, [P]) → proxy (5) → mcp-servers router (6, closes register→discover) → SDK/runner dispatch (7,8) → Decision-27 gate (9,10,11) → UI (12,13,14) → e2e (15) → Playwright (16) → regression (17). The thinnest real call is proven (Task 15) before the last UI polish. |
| 5 | Honest gap ledger | **PASS** | Gap Ledger below tags every deferred/incomplete item; the output-scan **STUB** is called out both there and inline in Task 11. |
| 6 | Reason from the running product | **PASS** | research.md Part A: the real OPA enforcement path (#8), the unwritten audit log (#9), the sandbox/prod snapshot asymmetry (#10), the moved baseline — migration head 0071, not 0068 (#11), registry-api's existing secret CRUD (#12), governed_tool having no scan today (#13), the two-sided safety_client bug (#14), team-in-namespace (#15). Each moved where a task's code lands. |
| 7 | Bug fixes reproduce first | **PASS (planned)** | The `safety_client` field bug (Task 10) is treated as a bug: a test that **fails against the current code** (`scan_output` against a mocked `{"deanonymized_message": ...}` returns the original text because the request 422s / the read key is wrong) is added and confirmed red before the fix. |
| 8 | Document every bug + debugging session | **PASS (planned)** | Task 10 writes `docs/bugs/safety-client-scan-field-mismatch.md` per the mandatory format, cross-linking the regression test. |

**Deliberate, justified scope note (Complexity Tracking):** the `opa_decisions` audit write (research.md #9) and the deploy-gate `team_may_use_tool` extraction (Task 3) are slightly beyond a minimal MCP slice, but each fixes the class of problem at a seam already being edited (a native-only audit fix, or a forked grant rule, would be the exact special-casing the constitution rejects). No other deviations.

---

## File Structure

Every file any task creates or modifies. "New" = does not exist today (verified). "Modify" cites the pre-existing anchor where relevant.

### New — MCP Proxy service (`services/mcp-proxy/`)

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `services/mcp-proxy/main.py` | Create | 5 | FastAPI app: `/health`, `/ready`, `/internal/discover`, `/internal/tools/call`; wires authn → authz → credentials → mcp_client. |
| `services/mcp-proxy/config.py` | Create | 5 | Env: `REGISTRY_API_URL`, `PORT`, `MCP_PROXY_AUDIENCE` (`agentshield-mcp-proxy`), `REGISTRY_API_SA_SUBJECT`, `MCP_SECRETS_NAMESPACE` (`agentshield-mcp`), cache TTLs. |
| `services/mcp-proxy/schemas.py` | Create | 5 | Pydantic models — §3c contracts (`McpDiscoverRequest/Response`, `McpToolCallRequest/Response`, `McpDiscoveredTool`). |
| `services/mcp-proxy/mcp_client.py` | Create | 5 | `mcp` SDK wrapper: `connect_and_initialize()`, `.list_tools()`, `.call_tool()`, `.close()`. |
| `services/mcp-proxy/session_cache.py` | Create | 5 | Per-replica `{server_id: CachedSession}` (live session + parsed `ServerConnection`); get-or-create + evict-on-error. |
| `services/mcp-proxy/k8s_client.py` | Create | 5 | In-cluster kubernetes client: `read_namespaced_secret` (in `MCP_SECRETS_NAMESPACE`) + `create_token_review`. Read-only. |
| `services/mcp-proxy/authn.py` | Create | 5 | `verify_bearer_token(token) -> str \| None` via TokenReview (audience gate) + positive-review cache keyed by `sha256(token)` until token `exp`. |
| `services/mcp-proxy/authz.py` | Create | 5 | `team_from_sa_subject()`; `authorize_tool_call(caller_sa_subject, server_id, mcp_tool_name, owner_team) -> bool` (own-team fast path; registry-api callback for cross-team). |
| `services/mcp-proxy/credentials.py` | Create | 5 | `read_server_secret(server_id) -> ServerConnection` from the per-server K8s Secret (metadata + auth headers). |
| `services/mcp-proxy/Dockerfile` | Create | 5 | `python:3.12-slim`, mirrors `python-executor/Dockerfile`; `COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/` (test fixture, inert unless exec'd). |
| `services/mcp-proxy/requirements.txt` | Create | 5 | `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `kubernetes`, `mcp>=1.2,<2.0`. |

### New — Helm sub-chart (`charts/agentshield/charts/mcp-proxy/`)

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `charts/agentshield/charts/mcp-proxy/Chart.yaml` | Create | 5 | Sub-chart descriptor (mirrors `python-executor/Chart.yaml`). |
| `charts/agentshield/charts/mcp-proxy/values.yaml` | Create | 5 | `replicaCount`, `image.{repository,tag,pullPolicy}`, `service.port` (8080), `resources`, `secretsNamespace: agentshield-mcp`. |
| `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml` | Create | 5 | Deployment (mirrors `python-executor`), SA `{release}-mcp-proxy`, env from values, `/health` probes. |
| `charts/agentshield/charts/mcp-proxy/templates/service.yaml` | Create | 5 | ClusterIP `agentshield-mcp-proxy:8080` (mirrors `python-executor`). |
| `charts/agentshield/charts/mcp-proxy/templates/serviceaccount.yaml` | Create | 5 | SA (mirrors `registry-api/templates/serviceaccount.yaml`). |
| `charts/agentshield/charts/mcp-proxy/templates/rbac.yaml` | Create | 5 | ClusterRole+binding for `system:auth-delegator` (TokenReview); Role+binding in `agentshield-mcp` for `get` on `secrets`. |
| `charts/agentshield/charts/mcp-proxy/templates/namespace.yaml` | Create | 5 | Creates namespace `agentshield-mcp` (dedicated per-server-secret ns; guarded so a re-apply is safe). |

### New — registry-api

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `services/registry-api/alembic/versions/0072_mcp_server_fields.py` | Create | 1 | The migration (data-model.md), `down_revision="0071"`. |
| `services/registry-api/tool_access.py` | Create | 3 | `team_may_use_tool(db, team, tool_id) -> bool` — the single grant resolver. |
| `services/registry-api/mcp_secrets.py` | Create | 3 | `materialize_server_secret(db, server)` / `delete_server_secret(server_id)` (per-server K8s Secret, reuses `crypto.decrypt_json` + `k8s.upsert_secret`/`delete_secret`). |
| `services/registry-api/routers/internal_mcp.py` | Create | 3 | `POST /api/v1/internal/mcp/authorize-tool-call` (contracts/registry-api-internal-mcp.md). |
| `services/registry-api/routers/mcp_servers.py` | Create | 6 | CRUD + `/sync` (contracts/registry-api-mcp-servers.md). |
| `services/registry-api/mcp_proxy_client.py` | Create | 6 | `discover_server(server_id)` — POST `/internal/discover` with an audience-`agentshield-mcp-proxy` SA token. |

### New — e2e / Studio / docs

| File | C/M | Task | Responsibility |
|---|---|---|---|
| `scripts/e2e/fixtures/stub_mcp_server.py` | Create | 15 | `mcp.server.fastmcp.FastMCP` fixture (`echo`, `add`) for suite-84 (research.md B10). |
| `scripts/e2e/suite-84-mcp-tools.sh` | Create | 15 | Backend e2e suite (T-S84-*). |
| `studio/src/api/mcpServersApi.ts` | Create | 12 | `McpServer` type + CRUD + sync client (rides shared `http` from `registryApi.ts`). |
| `studio/src/pages/McpServersPage.tsx` | Create | 12 | List + register form. |
| `studio/src/pages/McpServerDetailPage.tsx` | Create | 12 | Detail + discovered-tools table + Sync/Delete. |
| `studio/src/pages/McpServersPage.test.tsx` | Create | 12 | Vitest. |
| `studio/src/pages/McpServerDetailPage.test.tsx` | Create | 12 | Vitest. |
| `studio/src/pages/ToolsPage.test.tsx` | Create | 13 | Vitest (page had zero coverage; covers existing http/python + new mcp_tool read-only). |
| `studio/src/components/agent/ToolsPicker.test.tsx` | Create | 14 | Vitest (component had zero coverage; covers filter/selection + new badge). |
| `studio/e2e/mcp-servers.spec.ts` | Create | 16 | The Definition-of-Done journey spec. |
| `docs/bugs/safety-client-scan-field-mismatch.md` | Create | 10 | Mandatory bug postmortem for the safety_client request+response field fix. |

### Modified

| File | Task(s) | Change |
|---|---|---|
| `services/registry-api/models.py` | 2 | `MCPServer` +6 mapped columns; `Tool` +`pii_deanonymize_allowed` (invariant comments already present). |
| `services/registry-api/schemas.py` | 2, 6 | Extend `MCPServerCreate`/`MCPServerResponse` (+6 fields, `health_detail` shape); add `MCPServerUpdate`, `MCPServerDetailResponse`, `MCPServerSyncRequest`, `MCPServerSyncResponse`; `ToolCreate`/`ToolUpdate` +`pii_deanonymize_allowed`; `ToolResponse` +`pii_deanonymize_allowed`/`mcp_server_name`/`mcp_server_is_external`/`mcp_server_scan_results`. |
| `services/registry-api/routers/tools.py` | 2 | `_to_tool_response()` denormalization helper (+ `selectinload(Tool.mcp_server)`); every `ToolResponse`-returning route uses it; **reject `DELETE` on a `type='mcp_tool'` row → `409`**. |
| `services/registry-api/routers/deployments.py` | 3, 9 | (3) Deploy gate's per-tool grant loop (~L591-613) refactored to call `team_may_use_tool` (behavior-neutral). (9) Tools-snapshot dict (~L505) gains `pii_deanonymize_allowed`. |
| `services/registry-api/routers/versions.py` | 9 | Tools-snapshot dict (~L93) gains `pii_deanonymize_allowed`. |
| `services/registry-api/main.py` | 3, 6 | `include_router(internal_mcp_router)` (3); `include_router(mcp_servers_router)` (6). |
| `services/registry-api/bundle_generator.py` | 9 | `agents[sa_subject].tools` list and the `grants[team]` join/list both gain `pii_deanonymize_allowed`. |
| `services/registry-api/opa_policy/agentshield.rego` | 9 | New `allow_deanonymize` default + rule + `_deanon_of()` extractor. |
| `services/registry-api/opa_policy/agentshield_test.rego` | 9 | New `allow_deanonymize` test cases. |
| `services/registry-api/policy_generator.py` | 9 | Per-agent audit Rego/`risk_map` gains the field (audit-parity only — research.md #8; **not** enforcement). |
| `services/deploy-controller/manifest_builder.py` | 4 | Project a **second** SA token into agent pods (audience `agentshield-mcp-proxy`, path `mcp-proxy-token`, TTL 3600) + volumeMount + env `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH` (extends the existing OPA-token projection at ~L383-398 + mount ~L318-323 + env ~L176-179). |
| `sdk/agentshield_sdk/config.py` | 7 | `AGENTSHIELD_MCP_PROXY_URL`, `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH`. |
| `sdk/agentshield_sdk/tool_resolver.py` | 7 | `_build_executor`: new `elif tool_type == "mcp_tool":` branch. |
| `sdk/agentshield_sdk/tool_executor.py` | 7 | New `McpToolExecutor` class (reads the mcp-proxy token, sends Bearer). |
| `sdk/agentshield_sdk/opa_client.py` | 9 | `OPADecision` +`allow_deanonymize: bool = False`; `check_tool()` parses it; new `record_decision()`. |
| `sdk/agentshield_sdk/mock_opa.py` | 9 | Mock decision +`"allow_deanonymize": True`. |
| `sdk/agentshield_sdk/safety_client.py` | 10 | Fix `scan_input`/`scan_output` **request** keys (`text→message`, `trace_id→thread_id`) and **response** reads (`sanitized_text→anonymized_message`, `clean_text→deanonymized_message`); new `deanonymize_args()`. |
| `sdk/agentshield_sdk/mock_safety.py` | 10 | New `deanonymize_args()` mock (pass-through); keep `scan_*` mock keys as the SDK dataclass fields (`sanitized_text`/`clean_text`) since mock bypasses the wire. |
| `sdk/agentshield_sdk/graph_builder.py` | 11 | `governed_tool`: hoist `thread_id`; `record_decision` (best-effort); de-anonymize step (research.md B11 placement); output-scan step with **STUB action** (research.md B14). |
| `sdk/agentshield_sdk/__init__.py` | 7, 10, 11 | `__version__` bumps (0.2.0 → 0.2.1 → 0.2.2 → 0.2.3). |
| `services/declarative-runner/config.py` | 8 | `MCP_PROXY_URL`, `MCP_PROXY_SA_TOKEN_PATH`. |
| `services/declarative-runner/workflow_executor.py` | 8 | `_tool_dict_to_executor`: new `mcp_tool` branch. |
| `services/declarative-runner/node_executors.py` | 8 | New `McpToolNodeExecutor` class. |
| `services/safety-orchestrator/schemas.py` | 10 | New `DeanonymizeArgsRequest`/`DeanonymizeArgsResponse`. |
| `services/safety-orchestrator/orchestrator.py` | 10 | New `deanonymize_args()` (local substitution — research.md B5); STUB breadcrumb comment in `scan_output`. |
| `services/safety-orchestrator/main.py` | 10 | New `POST /api/v1/deanonymize/args` route. |
| `studio/src/api/registryApi.ts` | 13 | `RegistryTool` +`mcp_server_id`/`mcp_tool_name`/`mcp_server_name`/`mcp_server_is_external`/`mcp_server_scan_results`/`pii_deanonymize_allowed`; `CreateToolPayload` +`pii_deanonymize_allowed`. |
| `studio/src/pages/ToolsPage.tsx` | 13 | `mcp_tool` rows read-only (no Edit/Delete; "view source server" link); `pii_deanonymize_allowed` checkbox (all types). |
| `studio/src/components/agent/ToolsPicker.tsx` | 14 | Source-server badge on any `tool.mcp_server_name`-carrying row. |
| `studio/src/components/Sidebar.tsx` | 12 | `SETTINGS_ITEMS` +`{label:"MCP Servers", to:"/mcp-servers", icon:Server}`; `detectSections` recognizes `/mcp-servers`. |
| `studio/src/App.tsx` | 12 | Routes `/mcp-servers`, `/mcp-servers/:id`. |
| `infra/network-policies/agents-allow-egress.yaml` | 5 | New egress block: `role: agent` pods → `mcp-proxy:8080`. |
| `infra/network-policies/platform-allow-ingress.yaml` | 5 | Allow agent-namespace + registry-api ingress to the proxy; note the proxy is the only platform component permitted egress to external MCP hosts (external-egress allowance on the proxy pod). |
| `charts/agentshield/Chart.yaml` | 5 | New `mcp-proxy` dependency entry (`condition: mcp-proxy.enabled`, mirrors `python-executor`). |
| `charts/agentshield/values.yaml` | 5,6,7,8,9,10,11 | `mcp-proxy: {enabled: true, image: {tag: ...}}` (5); per-task tag bumps mirrored in lockstep. |
| `charts/agentshield/charts/registry-api/templates/deployment.yaml` | 6 | Projected SA token volume (audience `agentshield-mcp-proxy`, path `mcp-proxy-token`) + mount + env `MCP_PROXY_SA_TOKEN_PATH` (so registry-api can call `/internal/discover`). |
| `scripts/deploy-cpe2e.sh` | 4,5,6,7,8,9,10,11 | New `MCP_PROXY_TAG` var + `docker build services/mcp-proxy/` line + rollout wait (5); per-task tag bumps. **All build/deploy actions here are marked deferred (not executed this run).** |
| `scripts/e2e/run-all.sh` | 15 | Register `suite-84-mcp-tools.sh`. |
| `scripts/e2e/suite-18-opa-governance.sh` | 17 | One new regression assertion: a native `http` tool call now also produces an `opa_decisions` row. |

Every file above appears in exactly one task's Files list, and every file in a task's Files list appears here.

---

## Key Interfaces

Exact signatures every task must match.

```python
# sdk/agentshield_sdk/opa_client.py — Task 9
@dataclass
class OPADecision:
    allow: bool
    require_approval: bool
    reason: str
    deny_reason: str = ""
    allow_deanonymize: bool = False          # NEW — parsed from result.get("allow_deanonymize", False)

async def check_tool(agent_name: str, tool_name: str, args: dict,
                     user_context: Optional[UserContext] = None) -> OPADecision: ...
                     # unchanged signature; parses the new field from the OPA result

async def record_decision(agent_name: str, tool_name: str, decision: "OPADecision",
                          args: dict, thread_id: str = "") -> None:
    """NEW. Best-effort POST to {AGENTSHIELD_REGISTRY_URL}/api/v1/opa-decisions/. Never
    raises — an audit-write failure must never block or alter tool execution (research.md #9)."""
```

```python
# sdk/agentshield_sdk/safety_client.py — Task 10
@dataclass
class ScanOutputResult:
    clean_text: str          # UNCHANGED SDK-side field name (public contract to runner/graph_builder)
    scores: dict

async def scan_output(text: str, agent_name: str, session_id: str | None = None,
                      trace_id: str | None = None) -> ScanOutputResult:
    """UNCHANGED signature. FIX: request payload now sends {"message": text, "agent_name",
    "session_id", "thread_id": trace_id or session_id}; response reads
    data.get("deanonymized_message", text). Still fail-closed on unreachable (raises
    SafetyBlockedError) — research.md #14, B6."""

async def deanonymize_args(args: dict, agent_name: str, session_id: str | None) -> dict:
    """NEW. POSTs {session_id, agent_name, args} to /api/v1/deanonymize/args. On ANY
    failure (unreachable, non-200) logs a warning and returns `args` UNCHANGED —
    fail-open-with-degradation (research.md B6), NOT fail-closed."""
```
(`scan_input` gets the symmetric request/response fix: `message`/`thread_id` request keys, `data.get("anonymized_message", text)` response read; SDK-side field stays `sanitized_text`.)

```python
# sdk/agentshield_sdk/tool_executor.py — Task 7
class McpToolExecutor:
    def __init__(self, name: str, risk: str, mcp_server_id: str, mcp_tool_name: str,
                 input_schema: dict | None, description: str | None = None,
                 side_effecting: bool | None = None, scan_results: bool = True,
                 timeout_ms: int = 15_000) -> None: ...
    def as_tool_callable(self) -> Any:
        """Async callable identical in shape to HttpToolExecutor's: .risk, .tool_name,
        .side_effecting, .scan_results (NEW attr read by governed_tool's output-scan
        exemption), __signature__ derived from input_schema via _params_from_input_schema.
        POSTs to AGENTSHIELD_MCP_PROXY_URL + '/internal/tools/call' with
        McpToolCallRequest{server_id: mcp_server_id, mcp_tool_name, arguments: kwargs,
        session_id: <thread_id ContextVar, best-effort>, agent_name: <best-effort>} and
        header Authorization: Bearer <read AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH>. Returns
        response.result (str). On is_error=True returns result as-is (not raised); on an
        HTTP/transport failure to the proxy, catches and returns a JSON error string
        (not raised) — FR-MCP-14. In DEV_MODE (no proxy URL) returns a mock string."""
```

```python
# sdk/agentshield_sdk/tool_resolver.py — Task 7, _build_executor new branch
elif tool_type == "mcp_tool":
    is_external = tool_def.get("mcp_server_is_external")
    scan_results = True if is_external else bool(tool_def.get("mcp_server_scan_results", True))
    executor = McpToolExecutor(
        name=name, risk=risk,
        mcp_server_id=str(tool_def.get("mcp_server_id")),
        mcp_tool_name=tool_def.get("mcp_tool_name", ""),
        input_schema=tool_def.get("input_schema"),
        description=tool_def.get("description"),
        side_effecting=side_effecting, scan_results=scan_results)
```

```python
# services/declarative-runner/node_executors.py — Task 8 (SEPARATE impl, not a shared import)
class McpToolNodeExecutor:
    def __init__(self, node_config: dict) -> None:
        """node_config keys: name, mcp_server_id, mcp_tool_name, risk, description,
        side_effecting, scan_results, input_schema — same {name,...} dict shape
        HttpToolNodeExecutor/PythonToolNodeExecutor already take."""
    def as_tool_callable(self) -> Any:
        """Mirrors sdk McpToolExecutor.as_tool_callable() in wire contract (same request/
        response against the proxy, same Bearer token from MCP_PROXY_SA_TOKEN_PATH) — a
        SEPARATE implementation (grounding #4), not a shared import; declarative-runner
        never imports sdk.tool_executor, same non-sharing precedent as Http/Python today."""
```

```python
# services/registry-api/tool_access.py — Task 3
async def team_may_use_tool(db: AsyncSession, team: str, tool_id: uuid.UUID) -> bool:
    """Single grant resolver (contracts/registry-api-internal-mcp.md). True iff the tool
    is own-team/team-less OR an active AssetGrant(asset_type='tool', asset_id=tool_id,
    grantee_team=team, revoked_at IS NULL) exists. Called by the deploy gate AND the
    internal MCP authz endpoint — one implementation."""
```

```python
# services/registry-api/mcp_secrets.py — Task 3
async def materialize_server_secret(db: AsyncSession, server: MCPServer) -> None:
    """Write K8s Secret 'agentshield-mcp-server-{server.id}' in 'agentshield-mcp' with
    data {'connection': json(url,transport,transport_config,is_external,owner_team),
    'auth_headers': json(headers)}. headers composed from crypto.decrypt_json(
    auth_config.credentials_encrypted) by AuthConfig.type; {} if no auth_config_id.
    Reuses k8s.upsert_secret (registry-api's existing cluster-wide secret RBAC)."""

async def delete_server_secret(server_id: uuid.UUID) -> None:
    """k8s.delete_secret('agentshield-mcp-server-{server_id}', 'agentshield-mcp')."""
```

```python
# services/registry-api/mcp_proxy_client.py — Task 6
MCP_PROXY_URL = os.getenv("MCP_PROXY_URL",
    "http://agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8080")
MCP_PROXY_SA_TOKEN_PATH = os.getenv("MCP_PROXY_SA_TOKEN_PATH", "/var/run/secrets/mcp-proxy-token/token")

async def discover_server(server_id: uuid.UUID) -> dict:
    """POST {MCP_PROXY_URL}/internal/discover {"server_id": str(server_id)} with header
    Authorization: Bearer <read MCP_PROXY_SA_TOKEN_PATH>. Returns the parsed
    McpDiscoverResponse body regardless of its 'ok'/'status'. Raises RuntimeError only on
    a genuine transport failure (proxy unreachable) or a 401/403/5xx — the caller
    (mcp_servers router) catches it and treats it identically to an ok=false body (both
    become server.status='error'; never a 4xx to the Studio caller)."""
```

```python
# services/mcp-proxy/authn.py & authz.py & credentials.py — Task 5
async def verify_bearer_token(token: str) -> str | None:
    """TokenReview (audience agentshield-mcp-proxy). Returns 'system:serviceaccount:<ns>:<sa>'
    if authenticated with the right audience, else None. Positive results cached by
    sha256(token) until the token's exp."""

def team_from_sa_subject(sa_subject: str) -> str | None:
    """'system:serviceaccount:agents-{team}:{sa}' -> '{team}'. None if the namespace is
    not of the agents- form."""

async def authorize_tool_call(caller_sa_subject: str, server_id: str,
                              mcp_tool_name: str, owner_team: str | None) -> bool:
    """caller_team = team_from_sa_subject(...); None -> False. If caller_team == owner_team
    -> True (fast path, no hop). Else POST {REGISTRY_API_URL}/api/v1/internal/mcp/
    authorize-tool-call {caller_sa_subject, server_id, mcp_tool_name} -> {allowed}; cache
    per (caller_sa_subject, server_id, mcp_tool_name), short TTL."""

@dataclass
class ServerConnection:
    server_url: str; transport: str; transport_config: dict | None
    is_external: bool; owner_team: str | None; auth_headers: dict[str, str]

async def read_server_secret(server_id: str) -> ServerConnection:
    """read_namespaced_secret('agentshield-mcp-server-{server_id}', MCP_SECRETS_NAMESPACE);
    parse 'connection' + 'auth_headers' JSON. Raises a typed error if the Secret is missing
    (surfaces as a 200 is_error/status='error' body, never a 5xx)."""
```

```python
# services/mcp-proxy/mcp_client.py — Task 5
async def connect_and_initialize(server_url: str, headers: dict[str, str]) -> "McpSession":
    """streamablehttp_client + mcp.ClientSession, run .initialize(). Returns a wrapper
    exposing .protocol_version, .list_changed_supported, .list_tools() -> list[DiscoveredTool],
    .call_tool(name, arguments) -> CallResult(result:str, is_error:bool, structured:dict|None),
    and .close()."""
```

```typescript
// studio/src/api/mcpServersApi.ts — Task 12
export interface McpServer {
  id: string; name: string; description: string | null; server_url: string;
  transport: 'streamable_http' | 'stdio'; auth_config_id: string | null;
  owner_team: string | null; identity_mode: 'on_behalf_of' | 'service_identity' | 'none';
  is_external: boolean; scan_results: boolean; transport_config: object | null;
  status: 'connected' | 'disconnected' | 'error';
  health_detail: { last_error: string | null; last_success_at: string | null;
                   consecutive_failures: number; schema_drift?: {tool_name: string; detected_at: string}[] };
  list_changed_supported: boolean; last_synced_at: string | null; discovered_tool_count: number;
  created_at: string; updated_at: string;
}
export const listMcpServers: (limit?: number, offset?: number) => Promise<Paginated<McpServer>>;
export const getMcpServer: (id: string) => Promise<McpServer & { tools: RegistryTool[] }>;
export const createMcpServer: (payload: CreateMcpServerPayload) => Promise<McpServer>;
export const updateMcpServer: (id: string, payload: Partial<CreateMcpServerPayload>) => Promise<McpServer>;
export const syncMcpServer: (id: string, acknowledgeSchemaDrift?: boolean) => Promise<McpServerSyncResult>;
export const deleteMcpServer: (id: string) => Promise<void>;
```

---

## Tasks

Baseline tags observed **this session** (verify + bump from current per quickstart.md — never reuse a claimed tag): `REGISTRY_API_TAG=0.2.224`, `STUDIO_TAG=0.1.160`, `DECLARATIVE_RUNNER_TAG=0.1.59`, `SAFETY_ORCHESTRATOR_TAG=0.1.3`, `DEPLOY_CONTROLLER_TAG=0.1.40`, `PYTHON_EXECUTOR_TAG=0.1.0`, `sdk.__version__=0.2.0`, new `MCP_PROXY_TAG=0.1.0`. **All `deploy-cpe2e.sh` / `helm` / `kubectl` build+deploy commands below are DEFERRED — not executed this run; they are recorded so a later implementer runs them.**

**Tag-bump convention (applies to every image-building task — 4,5,6,7,8,9,10,11 — not repeated in each Files line):** each such task bumps its service's tag in **both** `scripts/deploy-cpe2e.sh` and its home in `charts/agentshield/values.yaml` (for `mcp-proxy`, also the sub-chart `values.yaml`), in the same change, deferred. These two files are listed once in the File Structure with their full task set; a task's own Files list names only the *code* it changes.

### Task 1 — Migration `0072`
**Files:** `services/registry-api/alembic/versions/0072_mcp_server_fields.py`.
**Interface contract:** data-model.md's skeleton — 6 `MCPServer` cols + 1 `Tool` col, `revision="0072"`, `down_revision="0071"`, idempotent (`_existing_columns()` guards).
**Dependencies:** none.
**Acceptance:** `alembic upgrade head` from `0071` applies cleanly on a fresh DB and on a partially-migrated DB (idempotent re-run is a no-op); `alembic downgrade -1` reverses (drops the CHECK before its column); no row needs a manual backfill (every column defaulted).
**Test cases:** `T-S84-001` (folded into suite-84 setup): apply migration twice, second is a no-op. Manual: `\d mcp_servers`/`\d tools` show all 7 columns with the exact types/defaults/constraints.
**Verification (DEFERRED):** `kubectl cp` the file into the registry-api pod, `alembic upgrade head` twice; confirm `0072` is head via `alembic current`.

### Task 2 — registry-api models + schemas + `tools.py`
**Files:** `services/registry-api/models.py`, `services/registry-api/schemas.py`, `services/registry-api/routers/tools.py`.
**Interface contract:**
- `models.MCPServer` +6 mapped columns (types/defaults per data-model.md); `models.Tool` +`pii_deanonymize_allowed: Mapped[bool]`.
- `schemas.MCPServerCreate`/`MCPServerResponse` +6 fields; new `MCPServerUpdate`, `MCPServerDetailResponse(MCPServerResponse)` with `tools: list[ToolResponse]`, `MCPServerSyncRequest`, `MCPServerSyncResponse`. `MCPServerCreate`/`Update` `model_validator` reject `is_external=true` + `identity_mode != 'none'` and `transport='stdio'`.
- `schemas.ToolCreate`/`ToolUpdate` +`pii_deanonymize_allowed: bool = False`; `ToolResponse` +`pii_deanonymize_allowed: bool = False`, `mcp_server_name: str | None = None`, `mcp_server_is_external: bool | None = None`, `mcp_server_scan_results: bool | None = None`.
- `routers/tools.py`: `_to_tool_response(tool)` populates the 3 denormalized fields from `tool.mcp_server` (via `.model_copy(update=...)`); every `ToolResponse`-returning route calls it; `list_tools`/`get_tool` add `.options(selectinload(Tool.mcp_server))`. `delete_tool` **rejects a `type=='mcp_tool'` row** with `409` (message: lifecycle owned by the MCP server) before the soft-delete.
**Dependencies:** Task 1.
**Acceptance:** `ast.parse` clean for all three files; `sqlalchemy.orm.configure_mappers()` succeeds after importing `models`; a `type='http'` tool's `ToolResponse` returns `mcp_server_name: null`, `pii_deanonymize_allowed: false`; `DELETE /api/v1/tools/{id}` on an `mcp_tool` row → `409`, on an `http` row → still `204` (unchanged).
**Test cases:** `T-S84-002` (suite-84 setup): create an `http` tool via ORM, `GET /tools/{id}` → `pii_deanonymize_allowed:false`, `mcp_server_name:null`; `DELETE` an `mcp_tool` row → `409`.
**Verification (partial, non-deploy):** `kubectl exec ... python3 -c "from routers import tools; import models; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('OK')"`. Full HTTP verification lands with Task 6's redeploy.

### Task 3 — Shared grant resolver + per-server Secret materializer + internal authz endpoint
**Files:** `services/registry-api/tool_access.py`, `services/registry-api/mcp_secrets.py`, `services/registry-api/routers/internal_mcp.py`, `services/registry-api/routers/deployments.py`, `services/registry-api/main.py`.
**Interface contract:** Key Interfaces (`team_may_use_tool`, `materialize_server_secret`/`delete_server_secret`) + contracts/registry-api-internal-mcp.md (`POST /api/v1/internal/mcp/authorize-tool-call`). `deployments.py`'s deploy-gate loop (~L591-613) is refactored to call `team_may_use_tool` per foreign tool — behavior-neutral. `main.py` mounts `internal_mcp_router`.
**Dependencies:** Task 2.
**Acceptance:**
- `team_may_use_tool(db, team, tool_id)` returns True for an own-team tool, True for a foreign tool with an active grant, False for a foreign tool with no/revoked grant.
- The deploy gate's `422 tool_grants_missing` behavior is unchanged (proven by `suite-18`/`suite-81` in Task 17).
- `POST /internal/mcp/authorize-tool-call` returns `{allowed:true}` for an own-team caller and for a cross-team caller with a grant; `{allowed:false}` otherwise; `200` always for a valid body; `422` for a malformed one.
- `materialize_server_secret` writes `agentshield-mcp-server-{id}` in `agentshield-mcp` with valid `connection`+`auth_headers` JSON; `delete_server_secret` removes it (idempotent on a missing Secret).
**Test cases:** `T-S84-003` (authorize-tool-call own-team → allowed), `T-S84-004` (cross-team no grant → not allowed; add a grant → allowed), `T-S84-005` (materialize then read back the Secret's `connection.server_url`).
**Verification (DEFERRED for the Secret path — needs the cluster):** `python3 -c "from routers import internal_mcp, deployments; from tool_access import team_may_use_tool; import mcp_secrets; print('OK')"` for the import/refactor; Secret round-trip verified in-cluster during Task 6.

### Task 4 — deploy-controller: project the second SA token  `[P]` (parallel with Tasks 3/5/9/10)
**Files:** `services/deploy-controller/manifest_builder.py`.
**Interface contract:** extend the agent pod spec: add a second projected SA token (audience `agentshield-mcp-proxy`, `expiration_seconds=3600`, `path="token"`) as a new projected volume `mcp-proxy-token` (mirroring the OPA `sa-token` volume at ~L383-398), a `read_only` volumeMount at `/var/run/secrets/mcp-proxy-token` (mirroring ~L318-323), and env `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH=/var/run/secrets/mcp-proxy-token/token` on the agent container (mirroring the `AGENTSHIELD_SA_TOKEN_PATH` env at ~L176-179).
**Dependencies:** none (structurally independent — but its output is exercised only once Tasks 5/7/8 land). Bump `DEPLOY_CONTROLLER_TAG`.
**Acceptance:** a freshly deployed agent pod has BOTH `/var/run/secrets/sa-token/token` (audience agentshield-opa) AND `/var/run/secrets/mcp-proxy-token/token` (audience agentshield-mcp-proxy); the second token's audience verifies via `kubectl create tokenreview` (or is accepted by the proxy in Task 15). No regression to the existing OPA token (suite-18 stays green).
**Test cases:** `T-S84-006` — exec into a deployed fixture agent pod, confirm the second token file exists and its decoded `aud` claim is `agentshield-mcp-proxy`.
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (rebuilds deploy-controller), redeploy a fixture agent, `kubectl exec ... cat /var/run/secrets/mcp-proxy-token/token | cut -d. -f2 | base64 -d`.

### Task 5 — MCP Proxy service + Helm sub-chart + RBAC + NetworkPolicy  (paired with Task 6 for the register→discover slice)
**Files:** all `services/mcp-proxy/*` (Create), all `charts/agentshield/charts/mcp-proxy/*` (Create), `charts/agentshield/Chart.yaml`, `charts/agentshield/values.yaml`, `infra/network-policies/agents-allow-egress.yaml`, `infra/network-policies/platform-allow-ingress.yaml`, `scripts/deploy-cpe2e.sh`.
**Interface contract:** contracts/mcp-proxy-internal.md in full (both endpoints + §3b AuthN/AuthZ); Key Interfaces (`verify_bearer_token`, `team_from_sa_subject`, `authorize_tool_call`, `read_server_secret`, `connect_and_initialize`). The proxy holds **only** `system:auth-delegator` + `get secrets` in `agentshield-mcp` (rbac.yaml) — no DB, no encryption key.
**Dependencies:** Task 3 (the authz endpoint + the per-server Secret shape it reads).
**Acceptance:**
- `GET /health` → `200` even with no reachable MCP server.
- `/internal/discover` with a valid registry-api-SA token against the Task 15 fixture (used ad hoc during this task's manual verification) → `ok:true`, `status:"connected"`, non-empty `tools`. Missing token → `401`; an agent-SA token (not registry-api's) → `403`.
- `/internal/tools/call` with a valid agent-SA token whose team == the server's owner_team → executes; a cross-team token with no grant → `403`; a missing token → `401`; a tool/transport failure → `200` + `is_error:true` (never 5xx).
- Proxy SA RBAC: `kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-mcp` → yes; `... get secrets -n agentshield-platform` → **no**; `... create tokenreviews.authentication.k8s.io` → yes; `... get pods ...` → no.
- `MCP_PROXY_TAG` present in both `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`; `Chart.yaml` lists the `mcp-proxy` dependency with `condition: mcp-proxy.enabled`; namespace `agentshield-mcp` created by the sub-chart.
**Test cases (become T-S84-007..012, run against the Task 15 fixture):** discover happy path, discover missing-token 401, discover wrong-subject 403, tools/call own-team happy path, tools/call transport-error 200-is_error, RBAC `can-i` matrix.
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (builds `mcp-proxy:0.1.0`); `kubectl rollout status deployment/agentshield-mcp-proxy -n agentshield-platform --timeout=3m`; `kubectl get ns agentshield-mcp`.

### Task 6 — registry-api `mcp_servers` router + proxy client + registry-api token  (closes register→discover)
**Files:** `services/registry-api/routers/mcp_servers.py`, `services/registry-api/mcp_proxy_client.py`, `services/registry-api/main.py`, `services/registry-api/schemas.py` (sync/detail models if not already added in Task 2), `charts/agentshield/charts/registry-api/templates/deployment.yaml`, `charts/agentshield/values.yaml`, `scripts/deploy-cpe2e.sh`.
**Interface contract:** contracts/registry-api-mcp-servers.md in full; `mcp_proxy_client.discover_server` (Key Interfaces). Register/sync flow: validate → `materialize_server_secret` → `discover_server` → upsert `Tool` rows (namespacing, `owner_team`, data-model.md re-sync semantics incl. vanished→`inactive` + schema-drift) → set status/counts/health_detail. PUT enforces name-immutability (`422`) and re-materializes the Secret on auth-config change. DELETE `409`-guards on bound tools, else deletes tools+server+`delete_server_secret`. registry-api's Deployment gets the projected `mcp-proxy-token`.
**Dependencies:** Task 3 (materializer + models), Task 5 (proxy `/internal/discover`).
**Acceptance:**
- `POST /mcp-servers/` against the fixture → `201`, `status:"connected"`, `discovered_tool_count>=1`; against an unreachable URL → `201`, `status:"error"`, `health_detail.last_error` populated (registration never all-or-nothing).
- `GET /mcp-servers/{id}` returns the server + its tools; each `Tool.owner_team == server.owner_team`, each `Tool.name == f"{server_name}__{mcp_tool_name}"`.
- `POST /mcp-servers/{id}/sync` twice with no upstream change → second reports `tools_added=0, tools_updated=0, tools_inactivated=0`; a tool removed upstream → `tools_inactivated>=1` and that `Tool.status=='inactive'` (not deleted).
- `PUT` changing `name` → `422`; changing `auth_config_id` re-materializes the Secret.
- `DELETE` on a server with a bound tool → `409` naming the blocking agent; on an unbound server → `204` and the per-server Secret is gone (`kubectl get secret agentshield-mcp-server-{id} -n agentshield-mcp` → NotFound).
**Test cases:** `T-S84-013`..`T-S84-020` (register connected, register error, owner_team propagation, namespacing, sync no-op, vanished→inactive, delete-guard 409, delete-cleanup incl. Secret).
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (registry-api tag bump — verify current then increment); `curl` the register happy-path + error-path; `kubectl get secret -n agentshield-mcp`.

### Task 7 — SDK: `McpToolExecutor` + `tool_resolver` dispatch + token
**Files:** `sdk/agentshield_sdk/config.py`, `sdk/agentshield_sdk/tool_resolver.py`, `sdk/agentshield_sdk/tool_executor.py`, `sdk/agentshield_sdk/__init__.py`.
**Interface contract:** Key Interfaces (`McpToolExecutor`, the `_build_executor` branch). `config.py` adds `AGENTSHIELD_MCP_PROXY_URL` + `AGENTSHIELD_MCP_PROXY_SA_TOKEN_PATH`. `__version__` → 0.2.1.
**Dependencies:** Task 5, Task 6 (a real `/internal/tools/call` + a discovered tool to resolve).
**Acceptance:** `resolve_tools(["github-mcp__search_issues"])` returns a callable whose `.tool_name`/`.risk`/`.side_effecting`/`.scan_results` match the registry row/server; calling it against the fixture returns the fixture's real string; calling it against an unreachable proxy returns a JSON error **string** (no raised exception — FR-MCP-14); the request carries `Authorization: Bearer <mcp-proxy-token>`.
**Test cases:** `T-S84-021` (resolve+invoke round trip, asserting attrs + return value), `T-S84-022` (unreachable proxy → error string, no exception).
**Verification (DEFERRED):** rebuild SDK into a fixture agent image via `deploy-cpe2e.sh`; exec a resolve+invoke snippet (quickstart.md).

### Task 8 — declarative-runner: `McpToolNodeExecutor` + `workflow_executor` dispatch
**Files:** `services/declarative-runner/config.py`, `services/declarative-runner/workflow_executor.py`, `services/declarative-runner/node_executors.py`.
**Interface contract:** Key Interfaces (`McpToolNodeExecutor`, the `_tool_dict_to_executor` branch); `config.py` adds `MCP_PROXY_URL` + `MCP_PROXY_SA_TOKEN_PATH`.
**Dependencies:** Task 5, Task 6.
**Acceptance:** identical to Task 7's, exercised through `AgentNodeExecutor.build_subgraph()` (a workflow agent node with an `mcp_tool`-bound tool) rather than the SDK's `resolve_agent_tools`. Bump `DECLARATIVE_RUNNER_TAG`.
**Test cases:** `T-S84-023` — a composite-workflow agent node with one `mcp_tool` in `tool_ids` resolves via `_prefetch_agent_tools` → `_tool_dict_to_executor` → `McpToolNodeExecutor`; a live subgraph run returns the fixture's real response.
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (declarative-runner tag bump); redeploy a fixture workflow agent and confirm its pod picks up the new runner image.

### Task 9 — Decision 27: OPA `allow_deanonymize` plumbing  `[P]` (parallel with Task 10)
**Files:** `sdk/agentshield_sdk/opa_client.py`, `sdk/agentshield_sdk/mock_opa.py`, `services/registry-api/bundle_generator.py`, `services/registry-api/opa_policy/agentshield.rego`, `services/registry-api/opa_policy/agentshield_test.rego`, `services/registry-api/policy_generator.py`, `services/registry-api/routers/versions.py`, `services/registry-api/routers/deployments.py`.
**Interface contract:** `OPADecision.allow_deanonymize` + `check_tool()` parse (Key Interfaces). `bundle_generator.generate_bundle_data()`: the `agents[sa_subject].tools` dicts (both sandbox + production legs, ~L136-143) and the `grants[team]` SELECT+dicts (~L158-186, add `t.pii_deanonymize_allowed` to the join and the emitted dict) gain `pii_deanonymize_allowed: bool` (fail-closed default False for a bare-string/missing entry). `versions.py`(~L93) and `deployments.py`(~L505) tools-snapshot dicts gain `"pii_deanonymize_allowed": bool(t.pii_deanonymize_allowed)`. `agentshield.rego`:
```rego
default allow_deanonymize := false
_deanon_of(entry) := true  if { is_object(entry); entry.pii_deanonymize_allowed == true }
_deanon_of(entry) := false if { is_object(entry); not entry.pii_deanonymize_allowed == true }
_deanon_of(entry) := false if is_string(entry)
_matching_deanon contains true if { some t in agent.tools; _name_of(t) == input.tool_name; _deanon_of(t) }
_matching_deanon contains true if { some t in data.grants[agent.team]; _name_of(t) == input.tool_name; _deanon_of(t) }
allow_deanonymize if { allow; count(_matching_deanon) > 0 }
```
`policy_generator.py` gets the field in its `risk_map`/audit Rego for parity only (research.md #8 — **not** enforcement; a comment must say so).
**Dependencies:** Task 1 (column), Task 2 (schema exposure).
**Acceptance:** `opa test services/registry-api/opa_policy/` passes incl. new cases; `GET /api/v1/bundle/data.json` shows `pii_deanonymize_allowed` on every tool entry in both `agents[...].tools` and `grants[...]`; `versions.py`/`deployments.py` each grep to one `pii_deanonymize_allowed` hit; `check_tool()` returns `allow_deanonymize=True` for a flagged, otherwise-allowed tool; `mock_opa` returns `allow_deanonymize: True` (DEV_MODE unaffected).
**Test cases:** `T-S84-024` (flagged tool → bundle carries it), `T-S84-025` (its `check_tool` → `allow_deanonymize=True`), `T-S84-026` (unflagged/denied → `False`); rego `test_allow_deanonymize_true_when_flagged_and_allowed`, `_false_when_not_flagged`, `_false_when_denied`.
**Verification:** `opa test services/registry-api/opa_policy/ -v` (runs locally, not deferred); bundle check DEFERRED to a registry-api redeploy.

### Task 10 — Decision 27: Safety Orchestrator `deanonymize_args` + the two-sided `safety_client` field-bug fix  `[P]`
**Files:** `services/safety-orchestrator/schemas.py`, `services/safety-orchestrator/orchestrator.py`, `services/safety-orchestrator/main.py`, `sdk/agentshield_sdk/safety_client.py`, `sdk/agentshield_sdk/mock_safety.py`, `sdk/agentshield_sdk/__init__.py`, `docs/bugs/safety-client-scan-field-mismatch.md`.
**Interface contract:** Key Interfaces (`scan_output`/`scan_input` request+response fix, `deanonymize_args`). New orchestrator schemas `DeanonymizeArgsRequest{session_id, agent_name, args: dict}` / `DeanonymizeArgsResponse{args: dict}`; `Orchestrator.deanonymize_args(req)` fetches `pii_store.get_mappings(session_id, agent_name)` and recursively substitutes `anonymized_text → original_text` in every string leaf (research.md B5); no mappings → `args` unchanged. `main.py` adds `POST /api/v1/deanonymize/args`. `__version__` → 0.2.2. Bump `SAFETY_ORCHESTRATOR_TAG`.
**Dependencies:** none (parallel with Tasks 3–9).
**Acceptance:**
- **Regression-test-first:** a test calling `safety_client.scan_output(...)` against a mocked server `{"blocked": false, "deanonymized_message": "Jane Doe", "scores": {}}` — with the request also asserted to carry `message`/`thread_id` (not `text`/`trace_id`) — **fails against the current code** (today it 422s / reads the wrong key, returning the original text), then passes after the fix. Added, confirmed red, then fixed.
- `deanonymize_args({"recipient":"<PERSON_0>"}, ...)` with a stored `PiiMapping(anonymized_text="<PERSON_0>", original_text="Jane Doe")` → `{"recipient":"Jane Doe"}`; with no mappings → input unchanged; SDK-side unreachable orchestrator → returns input `args`, logs a warning, never raises.
- `docs/bugs/safety-client-scan-field-mismatch.md` has Found/Fixed, Symptom, Root cause, Fix, and cross-links the regression test.
**Test cases:** `T-S84-027` (scan field regression red→green), `T-S84-028` (deanonymize substitution), `T-S84-029` (no-mappings no-op), `T-S84-030` (SDK fail-open).
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (safety-orchestrator tag bump); `kubectl exec ... -m pytest -k deanonymize`.

### Task 11 — Decision 27: wire the gate into `governed_tool` (with the STUB output-scan action)
**Files:** `sdk/agentshield_sdk/graph_builder.py`, `sdk/agentshield_sdk/__init__.py`; `services/declarative-runner` inherits via SDK rebuild (no file change, but `DECLARATIVE_RUNNER_TAG` re-bumps to pick up the new SDK).
**Interface contract:** `governed_tool` (currently ends at "3. Deliver" with NO scan — research.md #13) gains, in this exact order:
1. Hoist `thread_id` resolution to the top (before the OPA call) — today it is computed only inside the `needs_approval` branch (graph_builder.py ~L308-314).
2. Immediately after `decision = await opa_client.check_tool(...)`: `await opa_client.record_decision(agent_name, fn.tool_name, decision, kwargs, thread_id)` (best-effort, never raises — research.md #9).
3. Unchanged deny path (`if not decision.allow: return ...`) and unchanged HITL block.
4. Unchanged eval-mode short-circuit (`if _should_record(fn): ... return ...`).
5. **New**, after the short-circuit, immediately before the real call (research.md B11): `if decision.allow_deanonymize: try: kwargs = await safety_client.deanonymize_args(kwargs, agent_name=agent_name, session_id=thread_id); except Exception: log + proceed with original kwargs` (fail-open).
6. Unchanged dispatch: `result = await fn(**kwargs)` (or sync).
7. **New — output-scan, ACTION STUBBED (research.md B14):**
   ```python
   if getattr(fn, "scan_results", True):
       try:
           scan = await safety_client.scan_output(str(result), agent_name=agent_name, session_id=thread_id)
           logger.info("output-scan verdict=clean tool=%s scores=%s (action not enforced — Phase 1 STUB)", fn.tool_name, scan.scores)
       except SafetyBlockedError as exc:
           # Phase 1 STUB: verdict computed + logged, block/redact NOT enforced. Deferred to the
           # safety-orchestration build. See docs/design/mcp-tool-source-architecture.md §3/§8, research.md B14.
           logger.warning("output-scan verdict=blocked tool=%s reason=%s (STUB — not enforced)", fn.tool_name, exc.reason)
       # result is returned UNCHANGED — scan.clean_text intentionally discarded in Phase 1.
   ```
8. `return result`.
`__version__` → 0.2.3.
**Dependencies:** Task 7, 8 (so `.scan_results`/mcp dispatch exists to exercise the exemption), Task 9 (`OPADecision.allow_deanonymize`), Task 10 (`deanonymize_args`, the fixed `scan_output`).
**Acceptance:**
- A **native/http/python** call (no `.scan_results`, defaults True) now also *calls* the per-tool output scan (verdict logged) — the reason Task 17's regression sweep is mandatory.
- An **internal** MCP tool whose server has `scan_results=false` skips the scan *call*; an **external** MCP tool never skips it, regardless of the flag.
- The output-scan **action is not enforced**: a tool result that the scanner would block still reaches the LLM unchanged (STUB), and the verdict is logged. (Ledgered; must not be reported done as "enforced.")
- A call under `eval_mode=record` never has its `kwargs` de-anonymized (recorded args stay in placeholder form — research.md B11).
- An `opa_decisions` row is created for every tool call, every type.
- The `thread_id` hoist is behavior-neutral for HITL (suite-4 stays green unmodified — Task 17).
**Test cases:** `T-S84-031` (MCP internal scan_results=true → opa_decisions row, decision=allow), `T-S84-032` (pii_deanonymize_allowed=true + a stored mapping → the `echo` fixture receives the REAL value, proving de-anon), `T-S84-033` (internal server scan_results=false → scan call skipped; assert via a counter/marker), `T-S84-034` (external server scan_results=false ignored → scan call still made), `T-S84-035` (eval_mode=record native tool → recorded args stay anonymized).
**Verification (DEFERRED):** `bash scripts/deploy-cpe2e.sh` (sdk 0.2.3 into declarative-runner + fixture agent images); `bash scripts/e2e/suite-84-mcp-tools.sh`.

### Task 12 — Studio: MCP Servers screen
**Files:** `studio/src/api/mcpServersApi.ts`, `studio/src/pages/McpServersPage.tsx`, `studio/src/pages/McpServerDetailPage.tsx`, `studio/src/pages/McpServersPage.test.tsx`, `studio/src/pages/McpServerDetailPage.test.tsx`, `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`.
**Interface contract:** `mcpServersApi.ts` per Key Interfaces. `McpServersPage.tsx` mirrors `KnowledgeBasesPage.tsx` (list table + "Register Server" form, `useQuery(['mcp-servers'], ...)`, create mutation invalidates `['mcp-servers']`; the register form has the Internal/External toggle: External → auth-config picker; Internal → identity-mode dropdown; `stdio` transport shown disabled). `McpServerDetailPage.tsx` mirrors `KnowledgeBaseDetailPage.tsx` (Discovered Tools tab = the FR-MCP-41 proof table incl. `inactive` rows greyed; Settings tab with edit PUT, Sync button → `syncMcpServer`, Delete → `deleteMcpServer` surfacing the `409` blocking-agents message; a `status="error"` server shows the red banner + Sync/Retry). `Sidebar.tsx` `SETTINGS_ITEMS` +`{label:"MCP Servers", to:"/mcp-servers", icon: Server}` (`lucide-react`); `detectSections` adds `/mcp-servers` to `"settings"`. `App.tsx` +2 routes.
**Dependencies:** Task 6.
**Acceptance:** **save→reload→assert** — registering a server, then reloading `/mcp-servers` and `/mcp-servers/{id}`, shows the server + discovered tools from a fresh `GET`, not client state.
**Test cases (Vitest, `vi.mock` + `renderWithProviders` like `CredentialsPage.test.tsx`):** list renders from mocked `listMcpServers`; register submits the right `createMcpServer` payload + invalidates; a `409` surfaces a toast; detail renders tools from mocked `getMcpServer`; a `status="error"` server shows the banner + Sync/Retry; Delete surfaces a mocked `409`'s blocking-agents message.
**Verification:** `cd studio && npm run test -- McpServersPage McpServerDetailPage && npm run typecheck`.

### Task 13 — Studio: `ToolsPage` read-only `mcp_tool` rows + `pii_deanonymize_allowed` checkbox
**Files:** `studio/src/api/registryApi.ts`, `studio/src/pages/ToolsPage.tsx`, `studio/src/pages/ToolsPage.test.tsx`.
**Interface contract:** `RegistryTool` +6 optional fields; `CreateToolPayload` +`pii_deanonymize_allowed`. `ToolsPage.tsx`: `type==='mcp_tool'` rows hide Edit/Delete and show a "View source server →" link to `/mcp-servers/{tool.mcp_server_id}` + an `MCP` type badge; the create/edit form gains a `pii_deanonymize_allowed` checkbox ("Allow this tool to receive real PII values") for **every** type, wired into both mutation payloads.
**Dependencies:** Task 2 (fields), Task 9 (field is meaningful once OPA reads it).
**Acceptance:** an `http`/`python` create/edit form shows + persists the checkbox like `risk_level`; an `mcp_tool` row shows no Edit/Delete; the existing http/python create flow is unchanged and now covered by the new test file (page had zero coverage — cover the pre-existing behavior too).
**Test cases:** create `http`/`python` submit the right payload; edit pre-fills; `mcp_tool` row renders no Edit/Delete + a working link; the checkbox toggles and is in both payloads.
**Verification:** `cd studio && npm run test -- ToolsPage && npm run typecheck`.

### Task 14 — Studio: `ToolsPicker` source-server badge
**Files:** `studio/src/components/agent/ToolsPicker.tsx`, `studio/src/components/agent/ToolsPicker.test.tsx`.
**Interface contract:** when `tool.mcp_server_name` is set, render a small badge (`text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500`) with the server name before the risk badge. No change to the `KNOWLEDGE_SEARCH_TOOL` filter or selection behavior.
**Dependencies:** Task 6 (needs `mcp_server_name` populated), Task 2 (schema).
**Acceptance:** an MCP-sourced tool shows the badge; native/http/python show none; `knowledge_search` still filtered.
**Test cases:** existing-behavior coverage (new — no prior file): `knowledge_search` filtered, toggle calls `onToggle`, empty-state renders; new: `mcp_server_name:"github-mcp"` → badge with that text; no `mcp_server_name` → no badge.
**Verification:** `cd studio && npm run test -- ToolsPicker && npm run typecheck`.

### Task 15 — Backend e2e: `suite-84-mcp-tools.sh` + fixture
**Files:** `scripts/e2e/fixtures/stub_mcp_server.py`, `scripts/e2e/suite-84-mcp-tools.sh`, `scripts/e2e/run-all.sh`.
**Interface contract:** the fixture (research.md B10) is a `mcp.server.fastmcp.FastMCP` exposing `echo(text: str) -> str` (verbatim — used by the de-anon proof) and `add(a: int, b: int) -> int`, run `transport="streamable-http"` on `127.0.0.1:9999`, copied into the `mcp-proxy` image and started only via `kubectl exec` inside the running proxy pod (never auto-started). The suite mirrors `suite-81`'s template (`kubectl exec` into registry-api, inline `python3` + `httpx`/ORM, `RESULT <id> PASS/FAIL`, trailing `FAILS`, exit-code keyed). Register in `run-all.sh`.
**Dependencies:** Tasks 3, 4, 5, 6, 7, 8, 11 (the full path: register → discover → SDK/runner dispatch → governed gate).
**Acceptance:** every `T-S84-0XX` referenced above is a real executable assertion in this one suite, run in dependency order against one fixture instance per run.
**Test cases:** `T-S84-001` … `T-S84-035` compiled from Tasks 1–11.
**Verification (DEFERRED):** `bash scripts/e2e/suite-84-mcp-tools.sh`; then `bash scripts/e2e/run-all.sh`.

### Task 16 — Playwright: `mcp-servers.spec.ts`
**Files:** `studio/e2e/mcp-servers.spec.ts`.
**Interface contract:** mirrors `knowledge.spec.ts` — real Keycloak login (`global-setup.ts`, unchanged), REST-fixture setup with platform-admin identity, then the browser journey:
1. `/mcp-servers` → "Register Server" → fill name/URL → submit → `page.waitForResponse(/\/api\/v1\/mcp-servers\//)`. (If the stub isn't reachable from the Playwright target, REST-create the `MCPServer` + discovered `Tool` rows and drive only the browser verification of the already-discovered state — assert this choice in the spec header, mirroring `knowledge.spec.ts`.)
2. Assert redirect to the Server Detail page + the discovered-tools table renders (FR-MCP-41 proof).
3. **Save → reload → assert:** reload the detail route; the same tools are still listed.
4. Open an agent builder / Tools Picker; assert the discovered tool appears with its source-server badge (FR-MCP-42); bind it; save the agent.
5. Reload the agent; the tool is still bound (a second persistence round trip through `POST /agents/{name}/tools`).
**Dependencies:** Tasks 12, 13, 14.
**Acceptance:** the single spec CLAUDE.md DoD #1 names as the real-journey proof — must fail if any of Tasks 12/13/14's wiring breaks.
**Test cases:** the 5 numbered steps, each with its own `expect`/`waitForResponse`.
**Verification:** `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts`.

### Task 17 — Regression sweep
**Files:** `scripts/e2e/suite-18-opa-governance.sh` (one new assertion). Otherwise a verification report; a real regression uncovered here is fixed as a follow-up with its own failing-then-passing test (CLAUDE.md rule 7).
**Blast radius (mandatory mapping):** Task 11 changes `governed_tool` — the ONE path every native/http/python/mcp_tool call crosses, in the SDK runtime and (via shared import) the declarative-runner's agent-owned subgraph. Impacted suites:
- `suite-3-safety.sh` — once-per-turn scan path (unchanged code) still behaves identically; confirms Task 10's `safety_client` request-field fix didn't break the existing scan.
- `suite-4-hitl.sh` — HITL approval; confirms the `thread_id` hoist (Task 11 item 1) didn't change approval behavior.
- `suite-18-opa-governance.sh` — allow/deny/require_approval; confirms `record_decision` + `allow_deanonymize` don't alter existing non-MCP outcomes; **+ new assertion:** a native `http` tool call now produces an `opa_decisions` row (research.md #9's fix is generic).
- `suite-74-eval-v2-side-effects.sh` — the `_should_record`/eval-mode path; confirms de-anon does NOT run before the short-circuit (B11).
- `suite-81-deploy-tool-autograt.sh` — the deploy-time auto-grant + the `team_may_use_tool` extraction (Task 3); confirms the deploy gate's 422 semantics are unchanged.
**Dependencies:** Tasks 3, 11, 15, 16.
**Acceptance:** all five suites pass **after** Task 11 lands, plus the new `suite-18` assertion.
**Test cases:** the five suites' existing IDs (unchanged bar) + `T-S18-0XX` ("native http tool call produces an opa_decisions audit row").
**Verification (DEFERRED):** run the five suites + `suite-84` + `cd studio && npm run test` + `bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts`.

---

## Complexity Tracking

| Item | Why it's here (not a shortcut) |
|---|---|
| `opa_decisions` audit write in `governed_tool` (Task 11 / research.md #9) | The endpoint exists with zero writers for *every* tool type today. Adding the write at the seam already being edited, for all four types, turns a false platform claim true — a native-only fix would be the special-casing Decision 27 rejects. Best-effort (never blocks a call). |
| `team_may_use_tool` extraction + internal authz endpoint (Task 3 / research.md B12) | §3b's floor must reuse the deploy-gate rule, not fork it. Extracting one function and adding a thin endpoint is the only way to get "one implementation" while keeping the proxy off the DB (least-privilege) — a shared cross-service library would force ORM duplication or a raw-SQL fork (two implementations). |
| Per-server Secret + dedicated `agentshield-mcp` namespace (Task 3/5 / research.md B13) | Path (b) is locked. A dedicated namespace is what makes the proxy's `get secrets` RBAC genuinely narrow (K8s can't prefix/label-scope) so it cannot read the master key — without it, path (b) would be *worse* than path (a). Reuses existing `crypto`/`k8s` helpers, so the registry-api side is thin. |
| Output-scan **STUB** (Task 11 / research.md B14) | The design explicitly defers the block/redact *action* to a future safety build. Wiring the *call* + verdict now proves the seam and gives the field-bug fix (Task 10) a live path; the stubbed action is ledgered and gated ("must not be reported done"). |
| deploy-controller second SA token (Task 4) | Not optional: without a second audience-scoped token in agent pods, the proxy's §3b AuthN has nothing to verify. It's a minimal extension of the existing OPA-token projection, not new machinery. |

No other deviations. The plan adds no runtime `if getattr(...)`-style type-sniffing to the governance path (dispatch is by explicit `tool_type` branch, mirroring the existing http/python pattern).

---

## Execution Notes

- **Deploy/build is DEFERRED this run.** Every `bash scripts/deploy-cpe2e.sh`, `helm`, and `kubectl rollout` line is a recorded step for a later implementer, not executed while producing these artifacts. Verify-then-bump each tag from the live value (quickstart.md) — never reuse a claimed tag; mirror each bump in **both** `scripts/deploy-cpe2e.sh` and the tag's home in `charts/agentshield/values.yaml` (note: `python-executor`'s tag lives only in its sub-chart values.yaml; the new `mcp-proxy` follows suit but this plan **also** adds a parent `mcp-proxy.image.tag` override so the CLAUDE.md "mirror in parent values.yaml" rule holds — bump both).
- **Migration & baseline drift:** the head is `0071` (no `0069`); this migration is `0072`/`down_revision "0071"`. Confirm the head hasn't moved again before creating the file (quickstart.md).
- **Suite number:** `suite-84` (81/82/83 are taken). If 84 is claimed by the time you build, take the next free number and rename the `T-S84-*` IDs to match.
- **DEV_MODE parity:** `mock_opa.check_tool` must return `allow_deanonymize: True` and `mock_safety.deanonymize_args` must pass through, or local (no-OPA/no-safety-URL) dev regresses.
- **Two same-named `OPADecision` classes** (SDK dataclass vs `models.OPADecision` ORM) are unrelated — do not conflate (research.md #9).
- **The proxy never holds the DB or `AGENTSHIELD_ENCRYPTION_KEY`** — if a task finds itself adding either to `services/mcp-proxy`, stop: that violates §3b/B13 and means the per-server-Secret path wasn't followed.

---

## Gap Ledger

Per CLAUDE.md DoD #5 and the design doc §8. This table adds **Phase-1 implementation gaps** on top of the architecture doc's §8 ledger (which already covers `tools/list` pagination, latency budget, inner Langfuse span, rate limiting, stdio/OAuth/resources/prompts, FR-MCP-21's external dependency, the `sdk`-agent identity gap) — not repeated here.

| Gap | Tag | Note |
|---|---|---|
| Output-scan **block/redact action** on a flagged tool result | **not-yet-wired (debt)** — STUB | Task 11 wires the scan *call* + logs the verdict, but does NOT enforce block/redact (design §3/§8, research.md B14). Breadcrumbs in `graph_builder.py` + `orchestrator.py`. **Must not be reported done as "enforced."** Deferred to the safety-orchestration build. |
| `pii_deanonymize_allowed`/`risk_level` may be stale in a **production** OPA bundle | not-yet-wired (debt), pre-existing, not deepened | research.md #10 — the production leg reads `PublishedVersion.config_snapshot['tools']` (a client-authored mirror), not a live `Tool` join. Already true for `risk_level`; tracked in `docs/design/sandbox-production-parity-architecture.md`. Sandbox is accurate (live join). |
| Free-text de-anonymize inside the reused per-tool `scan_output` could substitute PII into a tool result that echoes its own placeholder | not-yet-wired (debt), low-probability, pre-existing-shaped | research.md B7. De-risked in Phase 1 because the scan's transformed text is discarded (STUB returns the raw result), but revisit when the action is made real. |
| Proxy authz floor descope risk | decision recorded | If the §3b team floor were dropped, a `governed_tool`-bypassing pod could reach an arbitrary server's credentials. This plan keeps the floor (Task 5); named per the honest-ledger rule. |
| `x-user-sub` forgeable in Phase 1 | not-yet-wired (debt), blocked externally | Phase 1 gates credentials on the SA token (unforgeable); `x-user-sub` drives nothing until on-behalf-of lands on the RCT dependency (design §7a). |
| Production agent namespace not of `agents-{team}` form | open detail | `team_from_sa_subject` assumes `agents-{team}`. Confirm production-deployment namespace naming at build; a non-matching namespace is `403`'d (fail-closed), so this is safe-by-default but may need a second parse rule for production servers. |
| `oauth2`/`mtls` `AuthConfig` → `auth_headers` composition | not-yet-wired (debt) | Phase 1 composes headers cleanly for `bearer`/`api_key`; `oauth2`/`mtls` are best-effort (an `mtls` client cert isn't a header). Real target servers in Phase 1 are expected to use `bearer`/`api_key`; richer auth is Phase 4 (OAuth 2.1). |
| Health-check loop / `list_changed` subscription | deferred (intentional) | FR-MCP-22 / FR-MCP-07 — Phase 2. `health_detail`/`list_changed_supported` are recorded at discovery in Phase 1 but nothing keeps them fresh or subscribes. |
| Proxy session cache has no cross-replica affinity | deferred (intentional) | Matches design §3 — a cache miss re-initializes, cheap for `streamable_http`. |
| `tools/list` pagination | deferred (intentional) | Design §8 — Phase 1 assumes a server's full tool list fits one response. |
