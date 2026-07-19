# Plan — MCP as a Tool Source, Phase 1

**Status:** Ready for implementation
**Scope:** Phase 1 only — HTTP (`streamable_http`) discovery + governed execution (internal + external, static auth) + the generic per-tool-call output-scan/de-anonymize gate (Decision 27). Phase 2 (health/notifications/internal on-behalf-of identity — blocked externally on `docs/design/identity-propagation-architecture.md`), Phase 3 (stdio + sandboxing), and Phase 4 (OAuth 2.1/resources/prompts) are **deferred, not planned here** — see the Gap Ledger.
**Inputs:** `docs/design/mcp-tool-source-architecture.md` (LOCKED), `docs/design/todo/mcp-tools-for-agents-requirements.md`, `docs/decisions.md` Decisions 15/27/28/29, this repo's `CLAUDE.md`.
**Companion artifacts:** `research.md`, `data-model.md`, `contracts/registry-api-mcp-servers.md`, `contracts/mcp-proxy-internal.md`, `quickstart.md` (same directory).

---

## Scope Check — is this one plan or several?

The architecture doc and the assigning brief both frame Phase 1 as one vertical slice, and reading the actual code confirms it: a single MCP tool call only works end to end once **all** of the following exist simultaneously — the DB columns, the MCP Proxy service, the registry-api CRUD+discovery router, the SDK dispatch branch, the declarative-runner's separate dispatch branch, and Decision 27's generic gate inside `governed_tool` (which both runtimes share). None of these is independently shippable as a complete, user-visible increment; a Studio screen with no backend, or a backend with no dispatch branch, is not a working feature. This plan is therefore **one ordered task list**, not several plans.

One piece *was* checked for genuine independence and found **not** independent despite looking separable at first glance: `services/registry-api/policy_generator.py`'s per-agent Rego generation. Grounding (research.md #8) found this file's output is dead at runtime (superseded by `bundle_generator.py` + the static `opa_policy/agentshield.rego`) — it would be tempting to treat it as "a separate legacy-code question, not MCP's problem." It is kept in this plan (Task 6) only because the assigning brief named it explicitly as a known fact to honor, and touching it costs one small, clearly-labeled sub-step — not because it is architecturally coupled to the rest of the slice. No other piece met the bar for a split.

---

## Goal

Make tools hosted on external MCP servers (GitHub, Slack, an internal Postgres MCP, etc.) available to AgentShield agents under exactly the same governance every other tool gets (OPA authorize → HITL approve if required → execute → output scan) — with zero MCP-specific carve-outs in the governance path itself. Ship: server registration + auto-discovery (Studio + API), `mcp_tool` dispatch in both agent runtimes, and the generic per-tool-call de-anonymize/output-scan gate (Decision 27) that this feature forces into existence for **every** tool type.

---

## Architecture

```
┌──────────────┐  governed_tool(mcp_tool)   ┌─────────────────────────────┐
│  Agent Pod   │──POST /internal/tools-call▶│      MCP Proxy service       │
│ SDK / decl-  │◀────result (str)───────────│  (services/mcp-proxy, NEW)   │
│   runner     │                            │  agentshield-platform ns     │
└──────┬───────┘                            │                              │
       │ OPA authorize (+allow_deanonymize) │ • mcp_client.py (official    │
       │ HITL if required                   │   `mcp` SDK, streamable_http)│
       │ NEW: de-anonymize args              │ • session_cache.py (per-    │
       │ execute → NEW: output-scan          │   replica in-memory)        │
       ▼                                     │ • credentials.py +          │
   OPA sidecar (bundle-server-fed)           │   k8s_secrets.py (own SA,   │
       │                                     │   read-only Secret access) │
       │ NEW: record_decision (best-effort)  └──────┬───────────┬──────────┘
       ▼                                            │           │
  POST /api/v1/opa-decisions/ (registry-api)   streamable_http  GET /mcp-servers/{id}
                                                (target server)  GET /auth-configs/{id}/secret-ref
                                                     ▼                 (both → registry-api)
                                          Internal / External MCP servers

┌─────────────┐   CRUD + /sync            ┌──────────────────┐
│   Studio    │──────────────────────────▶│    registry-api    │
│ MCP Servers │◀───discovered tools────────│ routers/mcp_servers │──▶ Postgres
│  (NEW pages)│                           │  (NEW) + mcp_proxy_ │    (mcp_servers,
└─────────────┘                           │  client.py (NEW)    │     tools rows)
                                           └──────────┬──────────┘
                                                      │ POST /internal/discover {server_id}
                                                      ▼
                                                 MCP Proxy
```

**One governance seam, two dispatch points — not two governance implementations.** `governed_tool` (Decision 27's gate) lives in exactly one place: `sdk/agentshield_sdk/graph_builder.py`. The declarative-runner does **not** get its own copy of the gate — its `AgentNodeExecutor.build_subgraph()` already imports and calls `agentshield_sdk.graph_builder.build_graph()` (confirmed in `services/declarative-runner/node_executors.py:330-344`), so every change Task 8 makes to `governed_tool` is inherited automatically by declarative-runner agent-owned tool calls the moment the runner's image is rebuilt against the updated SDK. What genuinely needs **two** separate implementations (per the architecture doc's grounding correction #4) is only the **dispatch** — turning a `Tool` dict of `type='mcp_tool'` into a callable — because the two runtimes have always had duplicated dispatch code for every other type too (`HttpToolExecutor`/`PythonToolExecutor` in the SDK vs. `HttpToolNodeExecutor`/`PythonToolNodeExecutor` in the runner). MCP follows the existing pattern; it does not introduce a new one.

---

## Tech Stack

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2.0 async ORM, Alembic, `httpx` (async), PostgreSQL (existing cluster), Kubernetes Python client (`kubernetes` package, in-cluster config).
- **MCP protocol:** official `mcp` Python SDK (`mcp.client.streamable_http`, `mcp.ClientSession`, `mcp.server.fastmcp.FastMCP` for the test fixture) — research.md B1.
- **Frontend:** unchanged stack — React + TypeScript + Vite + TailwindCSS, TanStack Query, react-hook-form + zod, Vitest + React Testing Library, Playwright.
- **Infra:** Helm (new `charts/agentshield/charts/mcp-proxy` sub-chart, mirroring the existing `python-executor` sub-chart shape), `scripts/deploy-cpe2e.sh`.
- **Test harness:** bash + `kubectl exec` + inline Python/httpx assertions (backend e2e, `scripts/e2e/suite-*.sh`), Vitest (`studio/src/**/*.test.tsx`), Playwright (`studio/e2e/*.spec.ts`).

---

## Constitution Check (against this repo's `CLAUDE.md`)

| # | Principle | Status | How this plan satisfies it |
|---|---|---|---|
| 1 | Real user journey proven (Playwright, not just an endpoint) | **PASS (planned)** | Task 13's `studio/e2e/mcp-servers.spec.ts` drives register → see discovered tools → bind to an agent → tool appears in `ToolsPicker` with a source-server badge, through real clicks and `page.waitForResponse`. |
| 2 | Save → reload → assert survived | **PASS (planned)** | Task 13's spec reloads the Server Detail route after registration and re-asserts the discovered-tools table from a fresh `GET`, mirroring `knowledge.spec.ts`'s exact pattern. Task 9's Vitest also covers the list-refetch-after-create round trip at the component level. |
| 3 | No orphan code | **PASS (planned)** | Every new exported symbol below (Key Interfaces) has a named caller in the same or an immediately-dependent task; File Structure lists every file used, and every task lists exactly the files it needs — no task introduces a file absent from that table. |
| 4 | Vertical slices, not horizontal layers | **PASS** | Task order is register→discover (3) → dispatch (4,5) → governance gate (6,7,8) → UI (9,10,11) → tests (12,13,14), i.e. the thinnest path to a real call is proven (Task 12's suite) before the last UI polish tasks — not "all backend, then all UI." |
| 5 | Honest gap ledger | **PASS** | See Gap Ledger below — every deferred/incomplete item tagged deferred (intentional) vs not-yet-wired (debt). |
| 6 | Reason from the running product, not the design doc | **PASS** | research.md's Part A documents three grounding corrections found by reading actual code that the architecture doc did not anticipate (the real OPA enforcement path, the unpopulated audit log, the sandbox/production snapshot asymmetry) — each changes where a task's code actually lands. |
| 7 | Bug fixes reproduce first | **N/A this plan** | This plan is new-feature work, not a bug fix, except for the `clean_text`/`deanonymized_message` field-name bug (Task 7) — which **is** treated as a bug: Task 7's test cases include one that fails against the current buggy `safety_client.py` before the fix (asserting `scan_output(...).clean_text` actually differs from the input when the (mocked) server returns `deanonymized_message`), then passes after. |
| 8 | Document every bug + debugging session | **PASS (planned)** | Task 7 includes writing `docs/bugs/safety-client-deanonymized-message-field-mismatch.md` per the mandatory bug-doc format, cross-linking the regression test that proves it. |

No Complexity Tracking deviations beyond what's stated: the one deliberately-out-of-strict-scope addition (the `opa_decisions` audit write, research.md #9) is justified there as fixing the class of problem at a seam already being edited, not a shortcut.

---

## File Structure

Every file any task creates or modifies. "New" files do not exist in the repo today (verified during research); "Modified" files are cited with their pre-existing line/anchor where relevant.

### New services / infra

| File | Task | Purpose |
|---|---|---|
| `services/mcp-proxy/main.py` | 3 | FastAPI app: `/health`, `/internal/discover`, `/internal/tools-call`. |
| `services/mcp-proxy/config.py` | 3 | Env vars (`REGISTRY_API_URL`, `PORT`, session cache TTL). |
| `services/mcp-proxy/schemas.py` | 3 | Pydantic request/response models (contracts/mcp-proxy-internal.md). |
| `services/mcp-proxy/mcp_client.py` | 3 | Wraps `mcp.client.streamable_http` + `mcp.ClientSession`: `initialize()`, `list_tools()`, `call_tool()`. |
| `services/mcp-proxy/session_cache.py` | 3 | Per-replica in-memory `{server_id: CachedSession}`; get-or-create + evict-on-error. |
| `services/mcp-proxy/credentials.py` | 3 | Resolves a server's connection details + auth headers by calling registry-api. |
| `services/mcp-proxy/k8s_secrets.py` | 3 | Read-only K8s Secret client (mirrors `registry-api/k8s.py`'s `_init_k8s` pattern). |
| `services/mcp-proxy/Dockerfile` | 3 | `python:3.12-slim` base, mirrors `python-executor/Dockerfile`. |
| `services/mcp-proxy/requirements.txt` | 3 | `fastapi`, `uvicorn[standard]`, `pydantic`, `httpx`, `kubernetes`, `mcp`. |
| `charts/agentshield/charts/mcp-proxy/Chart.yaml` | 3 | New Helm sub-chart descriptor. |
| `charts/agentshield/charts/mcp-proxy/values.yaml` | 3 | `replicaCount`, `image`, `service.port`, `resources`. |
| `charts/agentshield/charts/mcp-proxy/templates/deployment.yaml` | 3 | Mirrors `python-executor`'s Deployment template. |
| `charts/agentshield/charts/mcp-proxy/templates/service.yaml` | 3 | Mirrors `python-executor`'s Service template. |
| `charts/agentshield/charts/mcp-proxy/templates/rbac.yaml` | 3 | ServiceAccount + Role (`get` on `secrets` in `agentshield-platform` only) + RoleBinding. |

### New registry-api files

| File | Task | Purpose |
|---|---|---|
| `services/registry-api/alembic/versions/0069_mcp_server_fields.py` | 1 | The migration (data-model.md). |
| `services/registry-api/routers/mcp_servers.py` | 3 | CRUD + `/sync` (contracts/registry-api-mcp-servers.md). |
| `services/registry-api/mcp_proxy_client.py` | 3 | `discover_server(server_id)` HTTP client, mirrors `embedding_client.py`'s shape. |

### New SDK / declarative-runner files

None — every SDK/declarative-runner change is additions inside existing files (see below).

### New e2e / Studio files

| File | Task | Purpose |
|---|---|---|
| `scripts/e2e/fixtures/stub_mcp_server.py` | 12 | Real `mcp.server.fastmcp.FastMCP` fixture server for suite-82 (research.md B10). |
| `scripts/e2e/suite-82-mcp-tools.sh` | 12 | The new backend e2e suite. |
| `studio/src/api/mcpServersApi.ts` | 9 | `McpServer` type + CRUD + sync client functions. |
| `studio/src/pages/McpServersPage.tsx` | 9 | List + register form. |
| `studio/src/pages/McpServerDetailPage.tsx` | 9 | Detail + discovered-tools table + sync/delete. |
| `studio/src/pages/McpServersPage.test.tsx` | 9 | Vitest. |
| `studio/src/pages/McpServerDetailPage.test.tsx` | 9 | Vitest. |
| `studio/src/pages/ToolsPage.test.tsx` | 10 | New — `ToolsPage.tsx` has zero test coverage today; covers existing http/python behavior plus the new mcp_tool read-only row. |
| `studio/src/components/agent/ToolsPicker.test.tsx` | 11 | New — `ToolsPicker.tsx` has zero test coverage today; covers the new source-server badge plus existing filter/selection behavior. |
| `studio/e2e/mcp-servers.spec.ts` | 13 | The Definition-of-Done journey spec. |
| `docs/bugs/safety-client-deanonymized-message-field-mismatch.md` | 7 | Mandatory bug postmortem for the `clean_text`/`deanonymized_message` fix (CLAUDE.md bug-doc rule). |

### Modified files

| File | Task(s) | Change |
|---|---|---|
| `services/registry-api/models.py` | 2 | `MCPServer` +6 columns; `Tool` +`pii_deanonymize_allowed`. |
| `services/registry-api/schemas.py` | 2, 3 | Extend `MCPServerCreate`/`MCPServerResponse`; add `MCPServerUpdate`, `MCPServerDetailResponse`, `MCPServerSyncRequest`, `MCPServerSyncResponse`; extend `ToolCreate`/`ToolUpdate`/`ToolResponse` with `pii_deanonymize_allowed` + 3 denormalized `mcp_server_*` fields. |
| `services/registry-api/routers/tools.py` | 2 | Eager-load `Tool.mcp_server`; add `_to_tool_response()` helper populating the 3 denormalized fields; every route that returns a `ToolResponse` uses it. |
| `services/registry-api/main.py` | 3 | `app.include_router(mcp_servers_router)`. |
| `services/registry-api/routers/versions.py` | 6 | Line ~93's tools-snapshot dict gains `pii_deanonymize_allowed`. |
| `services/registry-api/routers/deployments.py` | 6 | Line ~505's tools-snapshot dict gains `pii_deanonymize_allowed`. |
| `services/registry-api/bundle_generator.py` | 6 | `agents[sa_subject].tools` list and the `grants[team]` query/list both gain `pii_deanonymize_allowed`. |
| `services/registry-api/opa_policy/agentshield.rego` | 6 | New `allow_deanonymize` default + rule + `_deanon_of()` extractor. |
| `services/registry-api/opa_policy/agentshield_test.rego` | 6 | New test cases for `allow_deanonymize`. |
| `services/registry-api/policy_generator.py` | 6 | Per-agent audit Rego/`risk_map` gains the field too (audit-trail parity only — research.md #8; **not** the enforcement path). |
| `scripts/deploy-cpe2e.sh` | 3, 5, 6, 7, 8, 9, 10, 11 | New `MCP_PROXY_TAG` var + `docker build` line + rollout wait + port-forward echo (Task 3); per-task tag bumps (see each task). |
| `charts/agentshield/Chart.yaml` | 3 | New `mcp-proxy` dependency entry (mirrors the `python-executor` entry exactly). |
| `charts/agentshield/values.yaml` | 3, 5, 6, 7, 8, 9, 10, 11 | `mcp-proxy: {enabled: true, image: {tag: "0.1.0"}}` (Task 3); per-task tag bumps mirrored here in lockstep, same commit as the `deploy-cpe2e.sh` change. |
| `sdk/agentshield_sdk/config.py` | 4 | `AGENTSHIELD_MCP_PROXY_URL`. |
| `sdk/agentshield_sdk/tool_resolver.py` | 4 | `_build_executor`: new `elif tool_type == "mcp_tool":` branch. |
| `sdk/agentshield_sdk/tool_executor.py` | 4 | New `McpToolExecutor` class. |
| `sdk/agentshield_sdk/opa_client.py` | 6 | `OPADecision` +`allow_deanonymize: bool = False`; `check_tool()` parses it from the bundle response; new `record_decision()` function. |
| `sdk/agentshield_sdk/mock_opa.py` | 6 | Mock response +`"allow_deanonymize": True`. |
| `sdk/agentshield_sdk/safety_client.py` | 7 | Fix `scan_output()`'s `clean_text` bug (read `deanonymized_message`, not `clean_text`, from the server response); new `deanonymize_args()` function. |
| `sdk/agentshield_sdk/mock_safety.py` | 7 | New `deanonymize_args()` mock (pass-through). |
| `sdk/agentshield_sdk/graph_builder.py` | 8 | `_wrap_tool_with_governance`/`governed_tool`: hoist `thread_id` resolution; add `record_decision` call; add de-anonymize step (placed per research.md B11); add output-scan step honoring `fn.scan_results`. |
| `sdk/agentshield_sdk/__init__.py` | 4, 7, 8 | `__version__` bumps (0.2.0→0.2.1→0.2.2→0.2.3). |
| `services/declarative-runner/config.py` | 5 | `AGENTSHIELD_MCP_PROXY_URL`. |
| `services/declarative-runner/workflow_executor.py` | 5 | `_tool_dict_to_executor`: new `mcp_tool` branch. |
| `services/declarative-runner/node_executors.py` | 5 | New `McpToolNodeExecutor` class. |
| `services/safety-orchestrator/schemas.py` | 7 | New `DeanonymizeArgsRequest`/`DeanonymizeArgsResponse`. |
| `services/safety-orchestrator/orchestrator.py` | 7 | New `Orchestrator.deanonymize_args()` method (research.md B5 — local substitution, no Presidio HTTP call). |
| `services/safety-orchestrator/main.py` | 7 | New `POST /api/v1/deanonymize/args` route. |
| `studio/src/api/registryApi.ts` | 2 | `RegistryTool` +`mcp_server_id`/`mcp_tool_name`/`mcp_server_name`/`mcp_server_is_external`/`mcp_server_scan_results`/`pii_deanonymize_allowed`; `CreateToolPayload` +`pii_deanonymize_allowed`. |
| `studio/src/pages/ToolsPage.tsx` | 10 | `mcp_tool` rows read-only (no Edit/Delete; "discovered from {server}" link); `pii_deanonymize_allowed` checkbox on the create/edit form (all types). |
| `studio/src/components/agent/ToolsPicker.tsx` | 11 | Source-server badge next to any `tool.mcp_server_name`-carrying row. |
| `studio/src/components/Sidebar.tsx` | 9 | `SETTINGS_ITEMS` +`{"MCP Servers", "/mcp-servers"}`; `detectSections` recognizes `/mcp-servers`. |
| `studio/src/App.tsx` | 9 | Routes `/mcp-servers`, `/mcp-servers/:id`. |
| `scripts/e2e/run-all.sh` | 12 | Register `suite-82-mcp-tools.sh`. |
| `scripts/e2e/suite-18-opa-governance.sh` | 14 | One new regression assertion: a native `http` tool call now also produces an `opa_decisions` audit row (proves research.md #9's fix is generic, not MCP-only). |

---

## Key Interfaces

Exact signatures every task must match — a cold implementer should not need to guess a parameter name.

```python
# sdk/agentshield_sdk/opa_client.py
@dataclass
class OPADecision:
    allow: bool
    require_approval: bool
    reason: str
    deny_reason: str = ""
    allow_deanonymize: bool = False          # NEW

async def check_tool(
    agent_name: str, tool_name: str, args: dict,
    user_context: Optional[UserContext] = None,
) -> OPADecision: ...                        # unchanged signature; parses the new field

async def record_decision(                   # NEW
    agent_name: str, tool_name: str, decision: "OPADecision",
    args: dict, thread_id: str = "",
) -> None:
    """Best-effort POST to /api/v1/opa-decisions/. Never raises — a failure here
    must never block or alter tool execution; it is audit-observability only."""
```

```python
# sdk/agentshield_sdk/safety_client.py
@dataclass
class ScanOutputResult:
    clean_text: str          # UNCHANGED field name (public contract preserved)
    scores: dict

async def scan_output(
    text: str, agent_name: str,
    session_id: str | None = None, trace_id: str | None = None,
) -> ScanOutputResult:
    """UNCHANGED signature. FIX: clean_text now reads data.get("deanonymized_message")
    (falling back to the original text), not the never-sent "clean_text" key."""

async def deanonymize_args(                  # NEW
    args: dict, agent_name: str, session_id: str | None,
) -> dict:
    """POSTs {session_id, agent_name, args} to /api/v1/deanonymize/args. On any
    failure (unreachable, non-200), logs a warning and returns `args` UNCHANGED —
    fail-open-with-degradation (research.md B6), not fail-closed."""
```

```python
# sdk/agentshield_sdk/tool_executor.py
class McpToolExecutor:
    def __init__(
        self, name: str, risk: str, mcp_server_id: str, mcp_tool_name: str,
        input_schema: dict | None, description: str | None = None,
        side_effecting: bool | None = None, scan_results: bool = True,
        timeout_ms: int = 15_000,
    ) -> None: ...
    def as_tool_callable(self) -> Any:
        """Returns an async callable identical in shape to HttpToolExecutor's:
        .risk, .tool_name, .side_effecting, .scan_results (NEW attribute — read by
        governed_tool's output-scan exemption, see graph_builder.py), __signature__
        derived from input_schema (reuses _params_from_input_schema), POSTs to
        AGENTSHIELD_MCP_PROXY_URL + "/internal/tools-call" with
        {server_id: mcp_server_id, mcp_tool_name, args: kwargs}. On is_error=True
        in the response, returns the result string as-is (not raised) — FR-MCP-14.
        On an HTTP-level failure calling the proxy itself, catches the exception
        and returns a structured JSON error string (also not raised) — same FR."""
```

```python
# sdk/agentshield_sdk/tool_resolver.py — _build_executor, new branch
elif tool_type == "mcp_tool":
    is_external = tool_def.get("mcp_server_is_external")
    scan_results = True if is_external else bool(tool_def.get("mcp_server_scan_results", True))
    executor = McpToolExecutor(
        name=name, risk=risk,
        mcp_server_id=str(tool_def.get("mcp_server_id")),
        mcp_tool_name=tool_def.get("mcp_tool_name", ""),
        input_schema=tool_def.get("input_schema"),
        description=tool_def.get("description"),
        side_effecting=side_effecting,
        scan_results=scan_results,
    )
```

```python
# services/declarative-runner/node_executors.py
class McpToolNodeExecutor:
    def __init__(self, node_config: dict) -> None:
        """node_config keys: name, mcp_server_id, mcp_tool_name, risk, description,
        side_effecting, scan_results, input_schema — same {name,...} dict shape
        HttpToolNodeExecutor/PythonToolNodeExecutor already take."""
    def as_tool_callable(self) -> Any:
        """Mirrors sdk McpToolExecutor.as_tool_callable() byte-for-byte in
        contract (same request/response shape against MCP Proxy) — a SEPARATE
        implementation per the architecture doc's grounding correction #4, not a
        shared import (declarative-runner's node_executors.py never imports
        sdk.tool_executor — same non-sharing precedent as Http/PythonToolExecutor
        vs Http/PythonToolNodeExecutor today)."""
```

```python
# services/declarative-runner/workflow_executor.py — _tool_dict_to_executor, new branch
if tool_type == "mcp_tool":
    is_external = tool.get("mcp_server_is_external")
    scan_results = True if is_external else bool(tool.get("mcp_server_scan_results", True))
    config = {
        "name": tool.get("name", "mcp_tool"),
        "mcp_server_id": tool.get("mcp_server_id"),
        "mcp_tool_name": tool.get("mcp_tool_name", ""),
        "risk": tool.get("risk_level", "low"),
        "description": tool.get("description"),
        "side_effecting": tool.get("side_effecting"),
        "scan_results": scan_results,
        "input_schema": tool.get("input_schema"),
    }
    return McpToolNodeExecutor(config)
```

```python
# services/registry-api/mcp_proxy_client.py
MCP_PROXY_URL: str = os.getenv(
    "MCP_PROXY_URL",
    "http://agentshield-mcp-proxy.agentshield-platform.svc.cluster.local:8000",
)

async def discover_server(server_id: uuid.UUID) -> dict:
    """POST {MCP_PROXY_URL}/internal/discover {"server_id": str(server_id)}.
    Returns the parsed JSON body (contracts/mcp-proxy-internal.md's discover
    response shape) regardless of its "status" field. Raises RuntimeError only on
    a genuine transport failure (proxy unreachable) or a non-200 the proxy itself
    should never send for a well-formed request — the caller (mcp_servers router)
    catches RuntimeError and treats it identically to a {"status": "error", ...}
    body (both become server.status="error", never a 4xx to the Studio caller)."""
```

```python
# services/mcp-proxy/mcp_client.py
async def connect_and_initialize(server_url: str, headers: dict[str, str]) -> "McpSession":
    """Opens a streamable_http client + mcp.ClientSession, runs .initialize().
    Returns a wrapper exposing .list_tools() -> list[DiscoveredTool] and
    .call_tool(name: str, args: dict) -> CallResult, plus .close()."""
```

```typescript
// studio/src/api/mcpServersApi.ts
export interface McpServer {
  id: string; name: string; description: string | null; server_url: string;
  transport: 'streamable_http' | 'stdio'; auth_config_id: string | null;
  owner_team: string | null; identity_mode: 'on_behalf_of' | 'service_identity' | 'none';
  is_external: boolean; scan_results: boolean;
  status: 'connected' | 'disconnected' | 'error';
  health_detail: { last_error: string | null; last_success_at: string | null; consecutive_failures: number };
  list_changed_supported: boolean;
  last_synced_at: string | null; discovered_tool_count: number;
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

Numbers below assume the observed baseline tags: `REGISTRY_API_TAG="0.2.210"`, `STUDIO_TAG="0.1.158"`, `DECLARATIVE_RUNNER_TAG="0.1.59"`, `SAFETY_ORCHESTRATOR_TAG="0.1.3"`, `sdk.__version__ == "0.2.0"`. If other work has landed since, re-read the current values first (quickstart.md) — never reuse a claimed tag.

### Task 1 — Database migration `0069`

**Files:** `services/registry-api/alembic/versions/0069_mcp_server_fields.py` (new).

**Interface contract:** exact column set in data-model.md's migration skeleton — 6 `MCPServer` columns, 1 `Tool` column, both idempotent (`_existing_columns()` guard).

**Dependencies:** none.

**Acceptance criteria:**
- `alembic upgrade head` from `0068` applies cleanly against a fresh dev DB and against a DB that already has some-but-not-all of the 7 new columns (idempotency).
- `alembic downgrade -1` cleanly reverses (drops the CHECK constraint before the column it guards).
- No existing row requires a manual backfill (every new column has a server-side default).

**Test cases:**
- `T-S82-001` (folded into the new suite-82, run against a scratch/test schema before the rest of suite-82's live-server tests): apply the migration twice in a row against the same DB — second run is a no-op, no error.
- Manual: `\d mcp_servers` and `\d tools` in `psql` show all 7 new columns with the exact types/defaults/constraints from data-model.md.

**Verification command:**
```bash
kubectl cp services/registry-api/alembic/versions/0069_mcp_server_fields.py \
  agentshield-platform/$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}'):/app/alembic/versions/
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- alembic upgrade head
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- alembic upgrade head   # idempotency re-run
```

---

### Task 2 — registry-api models + schemas + `tools.py` denormalization

**Files:** `services/registry-api/models.py`, `services/registry-api/schemas.py`, `services/registry-api/routers/tools.py`.

**Interface contract:**
- `models.MCPServer` gains the 6 mapped columns (types/defaults per data-model.md); `models.Tool` gains `pii_deanonymize_allowed: Mapped[bool]`.
- `schemas.MCPServerCreate`/`MCPServerResponse` extended with the 6 fields (contracts/registry-api-mcp-servers.md's exact JSON shape); new `MCPServerUpdate`, `MCPServerDetailResponse(MCPServerResponse)` with `tools: list[ToolResponse]`, `MCPServerSyncRequest`, `MCPServerSyncResponse`.
- `schemas.ToolCreate`/`ToolUpdate` gain `pii_deanonymize_allowed: bool = False`; `schemas.ToolResponse` gains `pii_deanonymize_allowed: bool = False`, `mcp_server_name: str | None = None`, `mcp_server_is_external: bool | None = None`, `mcp_server_scan_results: bool | None = None`.
- `routers/tools.py`: new helper
  ```python
  def _to_tool_response(tool: Tool) -> ToolResponse:
      data = ToolResponse.model_validate(tool)
      server = tool.mcp_server
      return data.model_copy(update={
          "mcp_server_name": server.name if server else None,
          "mcp_server_is_external": server.is_external if server else None,
          "mcp_server_scan_results": server.scan_results if server else None,
      })
  ```
  Every route returning `ToolResponse` (`create_tool`, `get_tool`, `list_tools`, `update_tool`) calls this instead of a bare `ToolResponse.model_validate(...)`. `list_tools`'/`get_tool`'s query gains `.options(selectinload(Tool.mcp_server))` (avoids a `MissingGreenlet` lazy-load error under async SQLAlchemy).

**Acceptance criteria:**
- `python3 -c "import ast; ast.parse(open('services/registry-api/models.py').read())"` and same for `schemas.py`/`routers/tools.py` — no syntax errors.
- `sqlalchemy.orm.configure_mappers()` succeeds after importing `models` (no FK/relationship break from the new `Tool` column).
- `GET /api/v1/tools/?type=http` for an existing http tool still returns `mcp_server_name: null` (no regression for non-MCP rows).
- `Tool.pii_deanonymize_allowed` defaults to `false` for every pre-existing row (verified by the migration's server_default, not a Python-side backfill).

**Dependencies:** Task 1.

**Test cases:**
- Unit-level (run in-pod via `kubectl exec ... python3 -c "..."`, no suite number yet — folded into suite-82's setup phase as `T-S82-002`): create an `http` tool via ORM directly, fetch via `GET /api/v1/tools/{id}`, assert response JSON has `pii_deanonymize_allowed: false` and `mcp_server_name: null`.

**Verification command:**
```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- \
  python3 -c "from routers import tools; import models; from sqlalchemy.orm import configure_mappers; configure_mappers(); print('OK')"
```
(Full HTTP-level verification happens once Task 3 redeploys the image — this task's own gate is the static/import check plus the migration from Task 1 being present.)

---

### Task 3 — MCP Proxy service (new) + registry-api `mcp_servers` router (paired vertical slice: register → discover)

Built and verified together (see "Scope Check" above — neither half is meaningfully testable alone).

**Files:** all "New services / infra" files above, plus `services/registry-api/routers/mcp_servers.py`, `services/registry-api/mcp_proxy_client.py`, `services/registry-api/main.py` (router mount), `charts/agentshield/Chart.yaml`, `charts/agentshield/values.yaml`, `scripts/deploy-cpe2e.sh`.

**Interface contract:** contracts/registry-api-mcp-servers.md (registry-api side) + contracts/mcp-proxy-internal.md (proxy side) in full — this task implements both documents completely.

**Dependencies:** Task 2 (needs the extended `MCPServerResponse`/`ToolResponse` shapes).

**Acceptance criteria:**
- `POST /api/v1/mcp-servers/` against a real MCP server (the Task 12 fixture, reachable during this task's own manual verification too) returns `201` with `status: "connected"` and a non-empty `discovered_tool_count`.
- The same call against an unreachable URL still returns `201` with `status: "error"` and a populated `health_detail.last_error` (registration is never all-or-nothing).
- `GET /api/v1/mcp-servers/{id}` returns the server plus its discovered `Tool` rows, each `Tool.owner_team == MCPServer.owner_team`, each `Tool.name == f"{server_name}__{mcp_tool_name}"`.
- `POST /api/v1/mcp-servers/{id}/sync` run twice with no server-side tool changes reports `tools_added=0, tools_updated=0, tools_deprecated=0` the second time.
- `DELETE /api/v1/mcp-servers/{id}` on a server with a bound discovered tool returns `409` naming the blocking agent; on a server with no bindings, returns `204` and removes both the server row and its (unbound) tool rows.
- MCP Proxy's own `kubernetes` ServiceAccount can `get` a `Secret` by name in `agentshield-platform` and cannot do anything else (verified via `kubectl auth can-i --as=system:serviceaccount:agentshield-platform:agentshield-mcp-proxy get secrets -n agentshield-platform` → yes; `... list secrets ...` → no; `... get pods ...` → no).
- `MCP_PROXY_TAG="0.1.0"` present in both `scripts/deploy-cpe2e.sh` and `charts/agentshield/values.yaml`; `charts/agentshield/Chart.yaml` lists the new `mcp-proxy` dependency with `condition: mcp-proxy.enabled`.

**Test cases (become `T-S82-003`..`T-S82-010`, run against the Task 12 fixture once it exists — the manual verification below stands in for them until Task 12 lands):**
- `T-S82-003` — register against the fixture stub server → `201`, `status=connected`, `discovered_tool_count >= 1`.
- `T-S82-004` — discovered `Tool.owner_team` equals the request's `owner_team`.
- `T-S82-005` — discovered `Tool.name` follows the `{server}__{tool}` namespace.
- `T-S82-006` — register against `http://127.0.0.1:1` (nothing listening) → `201`, `status=error`, `health_detail.last_error` non-empty.
- `T-S82-007` — `/sync` re-run with no upstream change → all three counts `0`.
- `T-S82-008` — `DELETE` blocked by a bound tool → `409` with the blocking agent name.
- `T-S82-009` — MCP Proxy RBAC — `kubectl auth can-i` checks above.

**Verification command:**
```bash
bash scripts/deploy-cpe2e.sh   # builds mcp-proxy:0.1.0 + registry-api:0.2.212 (see Task 6 for the second bump landing here too if done together; if sequenced strictly, this task alone lands 0.2.212 covering Tasks 2+3's registry-api changes — see note below)
kubectl rollout status deployment/agentshield-mcp-proxy -n agentshield-platform --timeout=3m
curl -s -X POST http://localhost:8000/api/v1/mcp-servers/ -H 'Content-Type: application/json' \
  -d '{"name":"test-mcp","server_url":"http://127.0.0.1:1","transport":"streamable_http","owner_team":"platform"}' | jq .status
```
**Note on tag sequencing:** Task 2 made no independently-deployed change (its own gate was static-only); this task is therefore the first real registry-api redeploy carrying Tasks 1+2+3's combined registry-api changes — bump `REGISTRY_API_TAG` **once**, to `0.2.211`, here (not `0.2.212` — that number is reserved for Task 6's later, separate redeploy).

---

### Task 4 — SDK: `McpToolExecutor` + `tool_resolver` dispatch

**Files:** `sdk/agentshield_sdk/config.py`, `sdk/agentshield_sdk/tool_resolver.py`, `sdk/agentshield_sdk/tool_executor.py`, `sdk/agentshield_sdk/__init__.py`.

**Interface contract:** Key Interfaces section above (`McpToolExecutor`, the `_build_executor` branch).

**Dependencies:** Task 3 (needs a real `/internal/tools-call` to call against).

**Acceptance criteria:**
- `resolve_tools(["github-mcp__search_issues"])` against a registry seeded with that discovered tool returns a callable whose `.tool_name`, `.risk`, `.side_effecting`, `.scan_results` all match the registry row / owning server.
- Calling that callable with valid kwargs against the Task 3/12 fixture returns the fixture's real response text.
- Calling it with an intentionally-wrong `mcp_tool_name` (simulate by pointing at a nonexistent server_id) returns a structured error **string**, not a raised exception (FR-MCP-14).

**Test cases:**
- `T-S82-010` — SDK-level resolve + invoke round trip against the fixture, asserting the callable's `.risk`/`.side_effecting`/`.scan_results` attributes and its real string return value.
- `T-S82-011` — invoke against an unreachable proxy/server → return value is a JSON-parseable error string, no exception propagates.

**Verification command:**
```bash
kubectl exec -n agentshield-platform deploy/agentshield-registry-api -- true  # (no-op placeholder for "backend already up")
kubectl exec -n <agent-fixture-pod> -- python3 -c "
import asyncio
from agentshield_sdk.tool_resolver import resolve_tools
async def main():
    tools = await resolve_tools(['github-mcp__search_issues'])
    print(tools[0].risk, tools[0].side_effecting, tools[0].scan_results)
    print(await tools[0](query='is:open'))
asyncio.run(main())
"
```
(Run inside any pod with the rebuilt SDK installed — a fixture agent pod, or a throwaway pod built from the same base image, per this task's own iteration; the full in-governed-path proof comes from Task 12's suite once Task 8 also lands.)

---

### Task 5 — declarative-runner: `McpToolNodeExecutor` + `workflow_executor` dispatch

**Files:** `services/declarative-runner/config.py`, `services/declarative-runner/workflow_executor.py`, `services/declarative-runner/node_executors.py`.

**Interface contract:** Key Interfaces section above (`McpToolNodeExecutor`, the `_tool_dict_to_executor` branch).

**Dependencies:** Task 3.

**Acceptance criteria:** identical to Task 4's, but exercised through `AgentNodeExecutor.build_subgraph()` (a workflow agent node with an `mcp_tool`-type bound tool) rather than the SDK's `resolve_agent_tools`.

**Test cases:**
- `T-S82-012` — a composite-workflow agent node with one `mcp_tool` in `tool_ids` resolves via `_prefetch_agent_tools` → `_tool_dict_to_executor` → `McpToolNodeExecutor`, and a live run of that node's subgraph produces the fixture's real response.

**Verification command:**
```bash
bash scripts/deploy-cpe2e.sh   # rebuilds declarative-runner:0.1.60
kubectl rollout status deployment/agentshield-declarative-runner -n agentshield-platform --timeout=3m 2>/dev/null || true
```
(`declarative-runner` is deployed per-agent by `deploy-controller`, not as a standalone Deployment — the rollout-status line is a template; the actual check is redeploying a fixture workflow agent and confirming its pod picks up `declarative-runner:0.1.60`.)

---

### Task 6 — Decision 27: OPA `allow_deanonymize` plumbing

**Files:** `sdk/agentshield_sdk/opa_client.py`, `sdk/agentshield_sdk/mock_opa.py`, `services/registry-api/bundle_generator.py`, `services/registry-api/opa_policy/agentshield.rego`, `services/registry-api/opa_policy/agentshield_test.rego`, `services/registry-api/policy_generator.py`, `services/registry-api/routers/versions.py`, `services/registry-api/routers/deployments.py`.

**Interface contract:** `OPADecision.allow_deanonymize` (Key Interfaces); `bundle_generator.generate_bundle_data()`'s per-tool dicts gain `"pii_deanonymize_allowed": bool`; `agentshield.rego` gains:
```rego
default allow_deanonymize := false

_deanon_of(entry) := true if { is_object(entry); entry.pii_deanonymize_allowed == true }
_deanon_of(entry) := false if { is_object(entry); not entry.pii_deanonymize_allowed == true }
_deanon_of(entry) := false if is_string(entry)

_matching_deanon contains true if { some t in agent.tools; _name_of(t) == input.tool_name; _deanon_of(t) }
_matching_deanon contains true if { some t in data.grants[agent.team]; _name_of(t) == input.tool_name; _deanon_of(t) }

allow_deanonymize if { allow; count(_matching_deanon) > 0 }
```

**Dependencies:** Task 1 (column), Task 2 (schema exposure).

**Acceptance criteria:**
- `opa test services/registry-api/opa_policy/` passes, including new `allow_deanonymize` cases (a tool with the flag set → `true` when otherwise allowed; a tool without it → `false`; a `deny`d call → `allow_deanonymize=false` regardless of the flag).
- `bundle_generator.generate_bundle_data()` output (`GET /api/v1/bundle/data.json`) includes `pii_deanonymize_allowed` on every tool entry in both `agents[...].tools` and `grants[...]`.
- `routers/versions.py`'s and `routers/deployments.py`'s tools-snapshot construction both include the field (grep both files for `pii_deanonymize_allowed` — one hit each).
- `sdk/agentshield_sdk/opa_client.check_tool()` against the live bundle returns `OPADecision.allow_deanonymize == True` for a tool with the flag set and an otherwise-allowed call.
- DEV_MODE (`mock_opa.py`) returns `allow_deanonymize: True` — local dev is unaffected.

**Test cases:**
- `T-S82-013` — mark a fixture tool `pii_deanonymize_allowed=true`, redeploy the agent (new `AgentVersion`), confirm the served bundle's `data.agents[sa_subject].tools` entry carries it.
- `T-S82-014` — the SAME fixture tool's OPA decision (`check_tool`) returns `allow_deanonymize=True`.
- `T-S82-015` — a second fixture tool with the flag unset (or the agent denied outright) returns `allow_deanonymize=False`.
- `opa test` cases (in `agentshield_test.rego`): `test_allow_deanonymize_true_when_flagged_and_allowed`, `test_allow_deanonymize_false_when_not_flagged`, `test_allow_deanonymize_false_when_denied`.

**Verification command:**
```bash
opa test services/registry-api/opa_policy/ -v
bash scripts/deploy-cpe2e.sh   # rebuilds registry-api:0.2.212
curl -s http://localhost:8000/api/v1/bundle/data.json | jq '.agents | to_entries[0].value.tools'
```

---

### Task 7 — Decision 27: Safety Orchestrator structured de-anonymize + the `clean_text` bug fix

**Files:** `services/safety-orchestrator/schemas.py`, `services/safety-orchestrator/orchestrator.py`, `services/safety-orchestrator/main.py`, `sdk/agentshield_sdk/safety_client.py`, `sdk/agentshield_sdk/mock_safety.py`, `sdk/agentshield_sdk/__init__.py`, plus **`docs/bugs/safety-client-deanonymized-message-field-mismatch.md`** (new — mandatory per CLAUDE.md's bug-doc rule).

**Interface contract:** Key Interfaces section above (`scan_output` fix, `deanonymize_args`); new safety-orchestrator schemas:
```python
class DeanonymizeArgsRequest(BaseModel):
    session_id: str
    agent_name: str
    args: dict[str, Any]

class DeanonymizeArgsResponse(BaseModel):
    args: dict[str, Any]
```
`Orchestrator.deanonymize_args(req: DeanonymizeArgsRequest) -> DeanonymizeArgsResponse`: fetches `pii_store.get_mappings(session_id, agent_name)`, recursively substitutes every occurrence of each mapping's `anonymized_text` with `original_text` inside every string leaf of `args` (dicts/lists recursed, other types passed through), returns the substituted dict. No mappings → `args` returned unchanged.

**Dependencies:** none (independent of the MCP-specific tasks; can run in parallel with Tasks 3–6).

**Acceptance criteria:**
- **Regression-test-first (CLAUDE.md rule 7):** a test that calls `safety_client.scan_output(...)` against a mocked server response `{"deanonymized_message": "real name here", ...}` **fails against the pre-fix code** (asserting `result.clean_text == "real name here"` — today's code returns the original input text instead, since it reads the wrong key). This test is added, confirmed red, then the fix lands and it goes green.
- `deanonymize_args({"recipient": "<PERSON_0>"}, ...)` against a session with a stored `PiiMapping(anonymized_text="<PERSON_0>", original_text="Jane Doe")` returns `{"recipient": "Jane Doe"}`.
- `deanonymize_args(...)` against a session with **no** mappings returns the input dict unchanged (same object shape, not an error).
- A safety-orchestrator-unreachable `deanonymize_args` call (SDK side) logs a warning and returns the original `args` — never raises to its caller.
- `docs/bugs/safety-client-deanonymized-message-field-mismatch.md` exists with the mandatory sections (Found/Fixed, Symptom, Root cause, Fix) and cross-links the regression test above.

**Test cases:**
- `T-S82-016` — the `clean_text` regression test (red → green), as described.
- `T-S82-017` — `deanonymize_args` substitution round trip.
- `T-S82-018` — `deanonymize_args` no-mappings no-op.
- `T-S82-019` — `deanonymize_args` SDK-side fail-open on an unreachable orchestrator.

**Verification command:**
```bash
bash scripts/deploy-cpe2e.sh   # rebuilds safety-orchestrator:0.1.4
kubectl exec -n agentshield-platform deploy/agentshield-safety-orchestrator -- \
  python3 -m pytest -k deanonymize -v   # or the repo's existing test invocation for this service
```

---

### Task 8 — Decision 27: wire the gate into `governed_tool`

**Files:** `sdk/agentshield_sdk/graph_builder.py`, `sdk/agentshield_sdk/__init__.py`, `services/declarative-runner` (no file changes — inherits via SDK rebuild, per the Architecture section above; `DECLARATIVE_RUNNER_TAG` still bumps to pick up the new SDK).

**Interface contract:** `_wrap_tool_with_governance`'s `governed_tool` closure gains, in this exact order (research.md B11 for why de-anonymize sits where it does):
1. `thread_id` resolution hoisted to the top of the function (before the OPA call) — was previously computed only inside the `needs_approval` branch.
2. Immediately after `decision = await opa_client.check_tool(...)`: `await opa_client.record_decision(agent_name, fn.tool_name, decision, kwargs, thread_id)` (best-effort, never raises, logged on failure).
3. Unchanged: `if not decision.allow: return ...` deny path; unchanged HITL block.
4. Unchanged: `if _should_record(fn): ... return ...` eval-mode short-circuit.
5. **New**, after the eval-mode short-circuit, before the real call: `if decision.allow_deanonymize: kwargs = await safety_client.deanonymize_args(kwargs, agent_name=agent_name, session_id=thread_id)` wrapped in try/except (log + proceed with original `kwargs` on failure).
6. Unchanged dispatch: `result = await fn(**kwargs)` (or sync).
7. **New:** `if getattr(fn, "scan_results", True): scan = await safety_client.scan_output(str(result), agent_name=agent_name, session_id=thread_id); result = scan.clean_text` — `scan_output`'s existing fail-closed behavior (raises `SafetyBlockedError`) is **unchanged**; a caught `SafetyBlockedError` here returns `f"Tool '{fn.tool_name}' result blocked by safety scan: {exc.reason}"` instead of `result`.
8. `return result`.

**Dependencies:** Task 4, 5 (so `.scan_results`/`mcp` dispatch actually exists to exercise the exemption logic meaningfully), Task 6 (`OPADecision.allow_deanonymize`), Task 7 (`safety_client.deanonymize_args`, the `scan_output` fix).

**Acceptance criteria:**
- A **native/http/python** tool call (no `.scan_results` attribute set, defaults `True`) now also goes through the per-tool-call output scan — this is the change that makes the mandatory regression sweep (Task 14) necessary.
- An **internal** MCP tool whose owning server has `scan_results=false` skips the output-scan step (verified: its raw, unscanned result reaches the LLM).
- An **external** MCP tool skips nothing, regardless of its owning server's `scan_results` value.
- A tool call under `eval_mode=record` never has its `kwargs` de-anonymized (the recorded entry's args stay in placeholder form — research.md B11's whole point).
- An `opa_decisions` row is created for every tool call, of every type, with the correct 3-way `decision` value.
- `thread_id`-resolution hoist is behavior-neutral for HITL (verified by the existing `suite-4-hitl.sh` staying green unmodified).

**Test cases:**
- `T-S82-020` — MCP tool call (internal, `scan_results=true`) → `opa_decisions` row present, `decision="allow"`.
- `T-S82-021` — same, but the tool's `pii_deanonymize_allowed=true` and the calling session has a stored PII mapping matching a placeholder in the call args → the fixture MCP server (which just echoes its input) receives the REAL value, not the placeholder.
- `T-S82-022` — internal MCP server with `scan_results=false` → the fixture's raw unscanned output reaches the returned string unmodified (assert no scan call was made — a spy/counter on the safety-orchestrator mock, or assert the returned text contains a marker the scan would have altered).
- `T-S82-023` — external MCP server with `scan_results=false` set anyway → the output-scan step still ran (the flag is ignored for external servers).
- Regression, folded into Task 14 but written here: a **native** tool call under `eval_mode=record` — confirm `_record_side_effect`'s captured args are still the ORIGINAL (anonymized) ones, not de-anonymized.

**Verification command:**
```bash
bash scripts/deploy-cpe2e.sh   # sdk 0.2.3 baked into declarative-runner:0.1.61 + any rebuilt fixture agent images
bash scripts/e2e/suite-82-mcp-tools.sh   # once Task 12 exists; until then, run the T-S82-020..023 assertions ad hoc via the pattern in quickstart.md
```

---

### Task 9 — Studio: MCP Servers screen

**Files:** `studio/src/api/mcpServersApi.ts`, `studio/src/pages/McpServersPage.tsx`, `studio/src/pages/McpServerDetailPage.tsx`, `studio/src/pages/McpServersPage.test.tsx`, `studio/src/pages/McpServerDetailPage.test.tsx`, `studio/src/components/Sidebar.tsx`, `studio/src/App.tsx`.

**Interface contract:** `mcpServersApi.ts` per Key Interfaces. `McpServersPage.tsx` follows `KnowledgeBasesPage.tsx`'s exact shape (list table + "Register Server" modal/inline form, `useQuery(['mcp-servers'], listMcpServers)`, create mutation invalidates `['mcp-servers']`). `McpServerDetailPage.tsx` follows `KnowledgeBaseDetailPage.tsx`'s list→detail-with-tabs shape: a Discovered Tools tab (the table proving FR-MCP-41), a Settings tab (edit `PUT`, Sync button calling `syncMcpServer`, Delete button calling `deleteMcpServer` with the 409-blocking-agents message surfaced on failure). `Sidebar.tsx`'s `SETTINGS_ITEMS` gains `{ label: "MCP Servers", to: "/mcp-servers", icon: Server }` (import `Server` from `lucide-react`); `detectSections` adds `pathname.startsWith("/mcp-servers")` to the `"settings"` branch. `App.tsx` adds `<Route path="/mcp-servers" element={<McpServersPage />} />` and `<Route path="/mcp-servers/:id" element={<McpServerDetailPage />} />`.

**Dependencies:** Task 3 (the backend API this screen calls).

**Acceptance criteria:** save→reload→assert (CLAUDE.md DoD #2) — registering a server, then reloading `/mcp-servers` and `/mcp-servers/{id}`, still shows the server and its discovered tools from a fresh `GET`, not client-side state.

**Test cases (Vitest, mirroring `CredentialsPage.test.tsx`'s `vi.mock` + `renderWithProviders` pattern):**
- `McpServersPage.test.tsx`: renders the list from a mocked `listMcpServers`; submitting the register form calls `createMcpServer` with the right payload and invalidates the query; a `409` (duplicate name) surfaces a toast error.
- `McpServerDetailPage.test.tsx`: renders discovered tools from a mocked `getMcpServer`; a `status="error"` server shows the red banner + Sync/Retry button; clicking Sync calls `syncMcpServer`; clicking Delete on a server with `discovered_tool_count > 0` still attempts the call (the 409 guard is server-side) and surfaces the blocking-agents message from a mocked `409` response.

**Verification command:**
```bash
cd studio && npm run test -- McpServersPage McpServerDetailPage
cd studio && npm run typecheck
```

---

### Task 10 — Studio: `ToolsPage.tsx` read-only `mcp_tool` rows + `pii_deanonymize_allowed` checkbox

**Files:** `studio/src/api/registryApi.ts`, `studio/src/pages/ToolsPage.tsx`, `studio/src/pages/ToolsPage.test.tsx` (new).

**Interface contract:** `RegistryTool` gains the 6 new optional fields (Key Interfaces / File Structure). `ToolsPage.tsx`'s table row rendering: when `tool.type === 'mcp_tool'`, hide the Edit/Delete action buttons and instead render a "View source server →" link to `/mcp-servers/{tool.mcp_server_id}` (Link, `react-router-dom`); the type badge shows `MCP` with a distinct icon. The create/edit form gains a `pii_deanonymize_allowed` checkbox (labelled "Allow this tool to receive real PII values" with helper text referencing the de-anonymize gate) for **every** tool type (not gated on `tool_type === 'mcp_tool'` — Decision 27 is generic), wired into both the create and update mutation payloads.

**Dependencies:** Task 2 (schema/field), Task 6 (the field is meaningful once OPA reads it).

**Acceptance criteria:** an `http` or `python` tool's create/edit form shows and persists the checkbox exactly like `risk_level`; an `mcp_tool` row never shows Edit/Delete buttons; `ToolsPage.tsx`'s existing `http`/`python` create flow is unchanged and covered by the new test file (this page had zero test coverage before this task — the new file covers the pre-existing behavior too, not only the diff, per CLAUDE.md's "a green new test with a broken neighbor" caution applied to test *creation*, not just test *changes*).

**Test cases:**
- Existing-behavior coverage (new, since none existed): creating an `http` tool submits the right payload; creating a `python` tool submits the right payload; editing an existing tool pre-fills the form.
- New-behavior coverage: an `mcp_tool` row renders no Edit/Delete controls and a working "View source server" link; the `pii_deanonymize_allowed` checkbox toggles and is included in both create and update payloads.

**Verification command:**
```bash
cd studio && npm run test -- ToolsPage
cd studio && npm run typecheck
```

---

### Task 11 — Studio: `ToolsPicker.tsx` source-server badge

**Files:** `studio/src/components/agent/ToolsPicker.tsx`, `studio/src/components/agent/ToolsPicker.test.tsx` (new).

**Interface contract:** when `tool.mcp_server_name` is set, render a small badge (`<span className="text-xs px-1.5 py-0.5 rounded bg-slate-100 text-slate-500">{tool.mcp_server_name}</span>`) next to the tool's display name, before the risk badge. No change to the existing `KNOWLEDGE_SEARCH_TOOL` filter or selection/checkbox behavior.

**Dependencies:** Task 3 (needs `mcp_server_name` populated, from Task 2's schema + Task 3's real discovered tools existing to test against).

**Acceptance criteria:** an MCP-sourced tool in the picker shows its source-server badge; a native/http/python tool shows no badge (unchanged appearance); `KNOWLEDGE_SEARCH_TOOL` is still filtered out regardless of source.

**Test cases:**
- Existing-behavior coverage (new — no prior test file): `knowledge_search` is filtered from the pickable list; selecting/deselecting a tool calls `onToggle` with the right name; the empty-state text renders when `tools` is empty.
- New-behavior coverage: a tool with `mcp_server_name: "github-mcp"` renders the badge with that text; a tool with no `mcp_server_name` renders no badge.

**Verification command:**
```bash
cd studio && npm run test -- ToolsPicker
cd studio && npm run typecheck
```

---

### Task 12 — Backend e2e: `suite-82-mcp-tools.sh` + fixture

**Files:** `scripts/e2e/fixtures/stub_mcp_server.py`, `scripts/e2e/suite-82-mcp-tools.sh`, `scripts/e2e/run-all.sh`.

**Interface contract:** the fixture (research.md B10) is a `mcp.server.fastmcp.FastMCP` instance exposing at minimum two tools: `echo(text: str) -> str` (returns `text` verbatim — used by `T-S82-021`'s de-anonymize proof) and `add(a: int, b: int) -> int`. Run with `mcp.run(transport="streamable-http")` bound to `127.0.0.1:9999` inside the `mcp-proxy` pod (copied into that image's filesystem at build time, `COPY scripts/e2e/fixtures/stub_mcp_server.py /app/fixtures/`, started only when the suite explicitly execs it — never auto-started by `main.py`, so it is inert in a normal deployment). The suite itself follows `suite-81-deploy-tool-autograt.sh`'s exact template: `kubectl exec` into the registry-api pod, inline `python3` using `AsyncSessionLocal`/ORM directly for setup/teardown plus real `httpx` calls against the live services for the actual assertions, `RESULT <id> PASS/FAIL <msg>` lines, a trailing `FAILS` line, exit code keyed off it.

**Dependencies:** Tasks 3, 4, 5, 8 (needs the full path: register → discover → SDK dispatch → runner dispatch → governance gate, all real).

**Acceptance criteria:** every `T-S82-00X` id referenced in Tasks 1–8 above is a real, executable assertion inside this one suite file (they were designed alongside those tasks but this is where they're actually written and run together, end to end, against one registered fixture server per suite run).

**Test cases:** the full `T-S82-001` through `T-S82-023` list compiled from every task above, run in one script against one instance of the fixture server, in dependency order (register → discover → bind → SDK call → runner call → governed-gate assertions → delete-guard → cleanup).

**Verification command:**
```bash
bash scripts/e2e/suite-82-mcp-tools.sh
bash scripts/e2e/run-all.sh   # confirms suite-82 is now part of the full run
```

---

### Task 13 — Playwright: `mcp-servers.spec.ts`

**Files:** `studio/e2e/mcp-servers.spec.ts`.

**Interface contract:** follows `knowledge.spec.ts`'s structure — real Keycloak login (`global-setup.ts`, unchanged), a REST-fixture setup phase using the platform-admin identity headers, then a browser-driven journey:
1. Navigate to `/mcp-servers` → "Register Server" → fill name/URL (pointing at a REST-registered fixture MCP server — reuse the Task 12 stub, reachable from wherever Playwright's target cluster's `mcp-proxy` pod can dial; if the stub isn't independently reachable from outside the `mcp-proxy` pod, this spec instead REST-creates the `MCPServer` + its discovered `Tool` rows directly via `POST`/ORM-equivalent fixture setup and only drives the **browser verification** of the already-discovered state — assert this choice explicitly in the spec's header comment, mirroring `knowledge.spec.ts`'s own "REST fixture setup, browser-driven verification" split) → submit → `page.waitForResponse(/\/api\/v1\/mcp-servers\//)`.
2. Assert redirect to the Server Detail page; assert the discovered-tools table renders the fixture's tools (FR-MCP-41's proof point).
3. **Save → reload → assert:** reload the detail route; re-fetch; the same tools are still listed (DoD #2).
4. Navigate to an agent's builder / Tools Picker; assert the discovered tool appears with its source-server badge (FR-MCP-42); bind it; save the agent.
5. Reload the agent and confirm the tool is still bound (a second persistence round trip, this time through the existing `POST /agents/{name}/tools` path — unchanged, but proven end-to-end through the browser for an MCP-sourced tool specifically, which is the actual Definition-of-Done requirement here).

**Dependencies:** Tasks 9, 10, 11 (every Studio surface this spec drives).

**Acceptance criteria:** this is the single spec CLAUDE.md's Definition of Done point 1 names as the proof of a real user journey — it must fail if any of Tasks 9/10/11's wiring is broken (e.g., if the badge never renders, if the bind button doesn't actually POST, if the reload shows stale/empty state).

**Test cases:** the 5 numbered steps above, each with its own `expect(...)` / `waitForResponse` assertion — not one giant assertion at the end.

**Verification command:**
```bash
bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts
```

---

### Task 14 — Regression sweep

**Files:** none new/modified — this task's output is a verification report, not code (unless it uncovers a real regression, in which case the fix lands as a follow-up to whichever Task 6/8 introduced it, with its own new failing-then-passing test per CLAUDE.md rule 7).

**Interface contract:** N/A.

**Dependencies:** Tasks 8, 12, 13 (this task's own verification runs `suite-82` (Task 12) and `mcp-servers.spec.ts` (Task 13) alongside the pre-existing suites, so both must already exist; the *reason* the sweep is needed is Task 8 changing the shared `governed_tool` seam).

**Blast radius (per CLAUDE.md's mandatory mapping):** Task 8 changes `governed_tool`, the ONE code path every native/http/python/mcp_tool call goes through, in both the SDK agent runtime and (via shared import) the declarative-runner's agent-owned-tool subgraph. Every existing suite that exercises a real tool call through that path is impacted:
- `scripts/e2e/suite-3-safety.sh` — safety scanning; must confirm the once-per-turn scan (unchanged code path) still behaves identically.
- `scripts/e2e/suite-4-hitl.sh` — HITL approval flow; confirms the `thread_id`-resolution hoist (Task 8, item 1) didn't change approval behavior.
- `scripts/e2e/suite-18-opa-governance.sh` — OPA allow/deny/require_approval; confirms the new `record_decision` call and `allow_deanonymize` field don't alter existing allow/deny outcomes for non-MCP tools.
- `scripts/e2e/suite-74-eval-v2-side-effects.sh` — the `_should_record`/eval-mode mock path; confirms de-anonymize correctly does NOT run before this short-circuit (research.md B11) by checking a recorded entry's args are unchanged from what a real call would have de-anonymized.
- `scripts/e2e/suite-81-deploy-tool-autograt.sh` — the deploy-time auto-grant this design's team-scoping precondition depends on; confirms it's untouched.

**Acceptance criteria:** all five suites above pass **after** Task 8 lands, not just `suite-82`. Additionally, one **new** assertion is added to `suite-18-opa-governance.sh` (not a new suite — extending the existing one, since this is a regression check, not new MCP-specific functionality): after a native `http` tool call, an `opa_decisions` row now exists for it (proving research.md #9's fix is generic, not MCP-only) — a green `suite-82` with this assertion absent or red is exactly the "shipped regression" CLAUDE.md warns about.

**Test cases:** the 5 suites' existing test IDs (unchanged pass/fail bar) plus one new one, `T-S18-0XX` (next free number in that suite) — "native http tool call produces an opa_decisions audit row."

**Verification command:**
```bash
bash scripts/e2e/suite-3-safety.sh
bash scripts/e2e/suite-4-hitl.sh
bash scripts/e2e/suite-18-opa-governance.sh
bash scripts/e2e/suite-74-eval-v2-side-effects.sh
bash scripts/e2e/suite-81-deploy-tool-autograt.sh
bash scripts/e2e/suite-82-mcp-tools.sh
cd studio && npm run test
bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts
```

---

## Gap Ledger

Per CLAUDE.md's Definition of Done point 5 and the architecture doc's own §8 (this table adds Phase-1-specific implementation gaps on top of that existing ledger — it does not repeat entries already listed there, e.g. `tools/list` pagination, the latency budget, the inner Langfuse span, stdio, OAuth 2.1, resources/prompts, FR-MCP-21's external dependency — see `docs/design/mcp-tool-source-architecture.md` §8 for those).

| Gap | Tag | Note |
|---|---|---|
| `pii_deanonymize_allowed` / `risk_level` may be stale in a **production** deployment's OPA bundle | not-yet-wired (debt), pre-existing, not deepened by this design | research.md #10 — the production leg reads `PublishedVersion.config_snapshot['tools']`, sourced from `Agent.metadata['tools']` (a client-authored mirror), not a live `Tool` join. Already true for `risk_level` today; this plan does not extend or fix it, only documents it so `allow_deanonymize` isn't assumed reliable in production without checking. Tracked separately in `docs/design/sandbox-production-parity-architecture.md`. |
| Free-text de-anonymize inside the reused per-tool-call `scan_output` could, in a narrow edge case, substitute real PII into a tool result that echoes back one of its own input placeholders | not-yet-wired (debt), low-probability, pre-existing-shaped | research.md B7. Same risk class already exists for the once-per-turn scan; not deepened qualitatively by calling `scan_output` more often, but the exposure surface (tool's own output, potentially LLM-context-bound) differs from the final-human-facing-message case it was designed for. Revisit only if an incident surfaces it as real. |
| Health-check loop keeping `MCPServer.health_detail`/`status` fresh between explicit `/sync` calls | deferred (intentional) | FR-MCP-22 — Phase 2, per the architecture doc's own phasing table. Phase 1's `status` can go stale if a server dies between syncs. |
| `notifications/tools/list_changed` subscription | deferred (intentional) | FR-MCP-07 — Phase 2. `list_changed_supported` is recorded at discovery time in Phase 1 but nothing subscribes yet. |
| Internal on-behalf-of / service-identity token exchange | deferred (intentional), blocked externally | FR-MCP-21 — unchanged from the architecture doc §7a/§8; `identity_mode` column exists and is settable in Phase 1's schema/UI but has zero runtime effect until Phase 2's external dependency lands. |
| MCP Proxy rate limiting / backpressure toward external servers | not-yet-wired (debt) | Unchanged from the architecture doc §8 — noted here because it is the first Phase where MCP Proxy actually exists to need it. |
| `services/mcp-proxy`'s own session cache has no cross-replica affinity | deferred (intentional) | Matches the architecture doc's Key Decision (§3, "no sticky routing... acceptable at Phase 1 scale") — a cache miss just re-initializes, which is cheap for `streamable_http`. |
