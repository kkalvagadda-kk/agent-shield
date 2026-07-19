  # MCP as a Tool Source — Requirements

**Status:** Requirements (not started)
**Author:** Karthik + Claude
**Last updated:** 2026-07-16
**Related:** Decision 15 (Tool & MCP Registry), spec.md "MCP Proxy" component, `docs/design/todo/tool-credential-management.md`, `docs/design/todo/mcp-server.md` (the *opposite* direction — see below)

---

## 0. Read this first — two different "MCP" features

There are two MCP features in this repo. They point in opposite directions. Don't merge them.

| | Direction | What it means | Doc |
|---|---|---|---|
| **MCP server (outbound)** | Platform **is** an MCP server | External agents (Claude Desktop, Cursor, CI) call the *platform's own API* as MCP tools | `docs/design/todo/mcp-server.md` |
| **MCP tool source (inbound)** | Platform **is** an MCP client | Our agents consume tools *hosted by other MCP servers* (GitHub, Slack, an internal DB MCP, etc.) | **this doc** |

This document is only about the second one: making tools that live on MCP servers available to agents running on the platform, under the same governance every other tool gets.

---

## 1. Problem

Agents get their tools from the Tool Registry. Today a tool is `native` (Python in the pod), `http` (a REST call), or `python` (sandboxed code). All three are things *we* define and maintain.

The MCP ecosystem is where tools actually live now — GitHub, Slack, Postgres, Sentry, filesystem, hundreds of community servers, plus whatever a team builds internally. Every one of those is a ready-made, versioned, self-describing tool surface. Right now an agent can't touch any of them without someone re-implementing each tool as an `http` tool by hand.

We want to register an MCP server once, auto-discover its tools, and let agents bind those tools — with the platform's OPA + HITL + PII governance wrapping every call, exactly like a native tool.

**The schema for this already exists and is unused.** `MCPServer` model, `mcp_servers` table (migration 0001), `Tool.type='mcp_tool'` with `mcp_server_id` + `mcp_tool_name`, and `MCPServerCreate/Response` schemas are all in the code. There is **no runtime** behind any of it: no router, no MCP client, no discovery, no proxy, no execution path, no UI. This doc specifies that runtime.

---

## 2. Goals / Non-goals

### Goals
- Register an MCP server (internal or external) through the platform and auto-discover its tools.
- Bind discovered MCP tools to agents the same way native/HTTP tools bind — many-to-many, by registry ID.
- Every MCP tool call flows through the existing governance: OPA authorize → HITL if required → execute → output scan.
- Support two deployment boundaries: **internal** servers that share our Keycloak IdP, and **external** servers behind their own auth.
- Keep credentials out of agent pods.

### Non-goals (this milestone)
- Being an MCP *server* (outbound) — that's the other doc.
- MCP OAuth 2.1 / dynamic client registration for external servers — **deferred** (see §11). MVP uses static credentials.
- MCP `resources` and `prompts` primitives — MVP consumes `tools` only. Resources/prompts are a later phase.
- A tool marketplace / catalog UI beyond the existing Tool Registry list.

---

## 3. Decisions locked for this milestone

These four were decided up front; the rest of the doc assumes them.

| # | Decision | Choice | Why |
|---|---|---|---|
| D1 | **Where the MCP client runs** | **Centralized MCP Proxy service** (`services/mcp-proxy`) | One connection pool, central health/sync, credentials never enter agent pods, one OPA/HITL hop. Resolves the spec.md-vs-Decision-15 contradiction in favor of spec.md's "MCP Proxy" component. |
| D2 | **Transports** | `streamable_http` **and** `stdio` | HTTP covers remote SaaS + internal HTTP servers. stdio unlocks the npx/uvx server ecosystem — but only run inside the proxy under the sandboxing rules in §9. |
| D3 | **External-server auth** | **Static credentials via `AuthConfig`** → K8s Secret (api_key / bearer / custom header). OAuth 2.1 deferred. | Reuses the credential plumbing already built (`tool-credential-management.md`). Ships without a consent-flow build. |
| D4 | **Governance of discovered tools** | Discovered tools become **normal `Tool` rows** (default `risk=low`), normal OPA autogen at bind time. No MCP-special-casing of risk. | Uniform tool model. **Accepted risk:** a remote server's tools default to low-risk; see §10 for the mitigation (admin can raise risk; external servers are output-scanned). |

> **Alignment check:** The platform's whole premise is that *every* tool call is governed. D1 (central proxy, one governance hop) and the §8 requirement that MCP calls reuse the exact `governed_tool` wrapper keep that invariant intact — MCP is a new tool *source*, not a governance bypass.

---

## 4. The two deployment boundaries

The user requirement calls out two topologies explicitly. They differ in trust and identity, so requirements split on this axis.

### 4a. Internal MCP server (same auth boundary, shares our Keycloak)
- Runs inside the cluster / trusted network. Registered by URL (e.g. `http://team-db-mcp.agents-platform:8080/mcp`).
- **Can share identity.** The proxy can present a Keycloak token so the MCP server enforces its *own* per-user authz. Two identity options (pick per server at registration):
  - **On-behalf-of**: forward/exchange the calling end-user's JWT (`sub`) so the server sees the real user.
  - **Service identity**: the proxy uses a Keycloak service-account token — the server sees "the platform" as one principal.
- Results are trusted-ish but still scanned (defense in depth).

### 4b. External MCP server (different auth boundary)
- Third-party SaaS or partner endpoint (e.g. `https://mcp.githubcopilot.com/mcp`).
- **No shared identity.** The platform authenticates as a single principal using static credentials (D3). Per-user authz on the remote side is not possible in MVP.
- **Treated as untrusted input.** Tool *results* from an external server are attacker-controllable content entering the agent's context — the same threat class as webhook payloads (spec.md §Components, Event Gateway) and normal tool output (spec.md data-flow step 8). They **must** pass the Safety Orchestrator output scan before re-entering the LLM context (FR-MCP-31).

---

## 5. Current state — what exists vs. what's missing

**Verified against actual code on 2026-07-18 (not spec.md, which is stale — see below).**

| Layer | Status |
|---|---|
| `MCPServer` model + `mcp_servers` table | **Built since migration `0001`** (not a future addition) — `models.py:957-1005` |
| `Tool.type='mcp_tool'` + `mcp_server_id` + `mcp_tool_name` | **Built since migration `0001`** — `models.py:1011-1134` |
| `MCPServerCreate` / `MCPServerResponse` schemas | **Built, but dead** — `schemas.py:838-861`, zero references from any router |
| `auth_configs` delete-guard checks `MCPServer` refs | **Built** — `routers/auth_configs.py:246-265` |
| spec.md "MCP Proxy" component + `/api/v1/mcp-servers` endpoints | **Documented, not implemented.** spec.md is stale platform-wide — do not use it as a design reference for this feature. |
| Decision 15 (Option B, three tool types incl. MCP) | **Decided** |
| `services/mcp-proxy` service (client, discovery, call proxy) | **Missing** — confirmed 100% absent (no dir, no chart stub, no script ref) |
| `/api/v1/mcp-servers` router (CRUD + sync) | **Missing** — confirmed absent from `main.py` router mounts |
| Runner/SDK `mcp_tool` execution path | **Missing** — `tool_resolver._build_executor` (`sdk/agentshield_sdk/tool_resolver.py:76-111`) has no `mcp_tool`/`native` branch; anything not `"python"` falls into the HTTP executor today |
| Declarative-runner `mcp_tool` execution path | **Missing** — and **not just a mirror of the SDK path**. See "Declarative-runner has its own dispatch" below — a second place to wire, not one. |
| Studio: register server, browse discovered tools, tool-picker | **Missing** — confirmed 100% absent, zero grep hits for "mcp" anywhere in `studio/src` |
| Health / re-sync loop, `notifications/tools/list_changed` | **Missing** |
| Per-tool-call output scan (any tool type) | **Missing** — see "Output-scan/de-anonymize gate doesn't exist" below. FR-MCP-30/31 assumed reuse of an existing hook; there isn't one. |
| OPA `allow_deanonymize` decision field | **Missing** — `opa_client.Decision` (`sdk/agentshield_sdk/opa_client.py:61-62`) only carries `allow`/`require_approval`. The live enforcement Rego (`opa_policy/agentshield.rego`, fed by `bundle_generator.py` — **not** `policy_generator.py`, whose per-agent Rego is legacy/inert at runtime, see `docs/plan/mcp-tool-source-phase1/research.md` #8) only emits `action` from a flat risk→action map — no de-anonymize concept exists in policy today. |

Note the existing `mcp_servers` table lacks a few fields these requirements need (identity mode, transport config, health detail). Schema deltas in §7 are **additive** (chain after the current latest migration, `0068`, not `0028` as previously stated here).

### Corrections found during architecture grounding (2026-07-18/19)

1. **OPA policy generation happens at *deployment* time, not tool-bind time.** `policy_generator.generate_and_store` (`policy_generator.py:78-123`) is called from `routers/deployments.py` when a deployment is created, reading the `AgentVersion.tools` JSON snapshot — not from `POST /agents/{name}/tools` (`routers/agent_tools.py:60-88`), which has no immediate OPA side effect. Doesn't change the "no MCP special-casing" intent (D4) — MCP tools get Rego generated at the same trigger point as every other type — just corrects *when*. **Further correction (found during `/plan`, 2026-07-19): `policy_generator.py`'s output is not actually the live enforcement path.** Its per-agent Rego/ConfigMap write is legacy and inert at runtime (own docstring confirms the ConfigMap write is a no-op since Phase 9.1) — kept only as an audit/history artifact. The real path every tool call's OPA check goes through is `bundle_generator.py` (builds live per-agent/per-team data) + one static, checked-in `opa_policy/agentshield.rego`, served via `GET /api/v1/bundle/bundle.tar.gz` and polled by every OPA sidecar. Any new decision field (e.g. `allow_deanonymize`) must be threaded through `bundle_generator.py` + `agentshield.rego`, not `policy_generator.py`. Full trace: `docs/plan/mcp-tool-source-phase1/research.md` #8.

2. **`governed_tool` has no per-tool-call output scan and no PII de-anonymize gate — for any tool type, today.** (`sdk/agentshield_sdk/graph_builder.py:256-396`, the OPA→HITL→execute wrapper, stops at execute.) The only output scan that exists runs once per conversation *turn* on the final LLM text (`runner.py:141-145`), not per tool call. **Locked decision (2026-07-19): build this generically**, not as an MCP-only bolt-on — see Decision in `docs/decisions.md`. This means Phase 1 for this feature includes adding a per-tool-call output-scan + de-anonymize step to `governed_tool` itself, applied to native/http/python/mcp_tool alike. Concretely:
   - **Output-scan side** can reuse the existing Safety Orchestrator `scan_output` primitive (`sdk/agentshield_sdk/safety_client.py:89-134`, `POST /api/v1/scan/output`) by calling it per tool result instead of only once per turn — **but note the SDK client has a live field-name bug**: the server's `ScanOutputResponse` (`services/safety-orchestrator/schemas.py:27-32`) returns `deanonymized_message`, but the SDK client reads `data.get("clean_text", text)` (`safety_client.py:128-130`) — a key that the server never sends — so today `clean_text` is silently always the original unscanned text on any real deployment. This must be fixed as part of wiring the generic gate, not carried forward.
   - **De-anonymize side is a different shape than what exists.** The Safety Orchestrator's current de-anonymize step (`orchestrator.py:296-308`, inside `_scan_output_inner`) operates on free-text (final agent message to the human) and runs **unconditionally** whenever PII mappings exist for the session — there is no OPA gate on it today, and (per the bug above) its result is currently discarded by every caller. FR-MCP-30's actual requirement — swap PII placeholders for real values in **structured tool-call arguments**, gated per-tool by OPA, before dispatch — is a genuinely new capability, not a reuse of the existing free-text de-anonymize path. Needs its own request shape (args dict + session_id → args dict with real values) and a new OPA decision field (`allow_deanonymize: bool`, alongside `allow`/`require_approval`) threaded through `Decision` (`opa_client.py:61-62`) and the live enforcement Rego (`bundle_generator.py` + `opa_policy/agentshield.rego` — not `policy_generator.py`, which is inert at runtime; see correction above).

3. **Declarative-runner has its own dispatch, duplicated from the SDK, not a wrapper around it.** `workflow_executor._tool_dict_to_executor` (`workflow_executor.py:258-312`) and `node_executors.py` independently re-implement `"python"` vs. HTTP-executor dispatch — a second copy of `tool_resolver._build_executor`. Agent-owned tool calls (new canvas schema) do eventually route through the real `governed_tool` via `AgentNodeExecutor.build_subgraph()` (`node_executors.py:322-350` → `graph_builder.build_graph()`), so governance is intact there — but there's also a **legacy "old-schema standalone tool node" path** (`workflow_executor._build_old_schema_graph`, `workflow_executor.py:601-669`) that calls `HttpToolNodeExecutor.execute()` directly with **zero OPA/HITL** (pre-existing gap, not introduced by MCP). **Design stance: MCP tools are only wired into the governed agent-owned-tool path in both runtimes (SDK + declarative-runner's new-schema agent subgraph). They are explicitly NOT exposed on the legacy ungoverned standalone-tool-node path** — this doesn't fix that pre-existing gap, but it also doesn't extend it to a new tool source. Net: an MCP executor needs adding in **four** places — `sdk/agentshield_sdk/tool_resolver.py`, `sdk/agentshield_sdk/tool_executor.py` (new `McpToolExecutor`), `services/declarative-runner/workflow_executor.py` (`_tool_dict_to_executor`), `services/declarative-runner/node_executors.py` (new `McpToolNodeExecutor`) — not one.

4. **MCP Proxy needs its own credential-resolution path — the existing one doesn't reach it.** Today's `AuthConfig` credential flow is two-phase and **agent-pod-scoped**: create-time encryption + K8s Secret write (`routers/auth_configs.py:56-79`), then *deploy-time* resolution by `deploy-controller`'s `resolve_and_copy_tool_secrets` (`services/deploy-controller/tool_secrets.py:22-65`), which walks `GET /agents/{name}/tools` → `Tool.auth_config_id` → copies the secret into that **agent's own namespace**. It never looks at `MCPServer.auth_config_id`, and — since MCP Proxy (D1) is a standalone centralized service, not a per-agent pod — this namespace-copy mechanism doesn't apply to it anyway. The proxy needs a direct resolution path (e.g. calling `GET /api/v1/auth-configs/{id}/secret-ref` itself and mounting/reading from its own namespace) — new plumbing, not a reuse of `tool-credential-management.md`'s existing flow as-is.

5. **`python-executor` (closest existing sidecar template) is stateless-per-call; MCP Proxy is fundamentally stateful.** `services/python-executor/main.py` spawns a subprocess per `/execute` call with no session state carried between requests. MCP Proxy (D1) needs to hold live `streamable_http` sessions and `stdio` subprocess connections in memory across calls (that's the point of centralizing — one connection pool, not one per call). This means the Helm chart/deployment shape is a genuinely different exercise, not a clone of `python-executor`'s chart: multi-replica session affinity (or accept that a session lives on exactly one replica and route accordingly) and a reconnect-on-restart strategy both need explicit design — see Round 1 architecture below.

6. **Studio is confirmed 100% greenfield** (matches the original doc's claim) — zero grep hits for "mcp" anywhere in `studio/src`. Good templates exist to build from: `studio/src/pages/KnowledgeBasesPage.tsx` + `KnowledgeBaseDetailPage.tsx` (list → detail-with-tabs shape) and `studio/e2e/knowledge.spec.ts` (register → reload → assert-persisted journey) map closely to FR-MCP-40/41/43. `ToolsPage.tsx`'s tool-type selector is a hardcoded 2-way enum (`http`/`python`, `ToolsPage.tsx:29`) with no native/mcp option — recommend MCP-sourced tools appear **read-only** in the Tools list (discovered, not manually creatable there); creation only happens via the new MCP Servers screen's register+sync flow.

---

## 6. Functional requirements

Numbered `FR-MCP-##`. Each is written to be testable (a bash e2e suite and/or Playwright spec must be able to prove it — per CLAUDE.md Definition of Done).

### Server registration & lifecycle
- **FR-MCP-01** — An operator can register an MCP server with: name, description, `server_url`, `transport` (`streamable_http`|`stdio`), optional `auth_config_id`, `owner_team`, and identity mode (§4a; N/A for external). `POST /api/v1/mcp-servers`.
- **FR-MCP-02** — On registration the platform connects, runs the MCP `initialize` handshake, and calls `tools/list`. Each returned tool is persisted as a `Tool` row (`type='mcp_tool'`, `mcp_server_id`, `mcp_tool_name`, `input_schema` from the server's JSON Schema, **`owner_team` set from `MCPServer.owner_team`**).
  - **Team-scoping (confirmed 2026-07-19): reuse the existing tool grant model as-is, no new mechanism.** `Tool.publish_status` (`routers/tools.py:158-160`) gates cross-team visibility; `AssetGrant` (`asset_type='tool'`, `models.py:1242-1263`, checked at deploy time in `routers/deployments.py:390-536`) gates cross-team deployment. Neither branches on `Tool.type` — as long as `owner_team` is populated correctly at discovery, MCP tools inherit both automatically.
- **FR-MCP-03** — `discovered_tool_count` and `last_synced_at` are updated after every discovery. Server `status` is one of `connected` / `disconnected` / `error`, reflecting the last connection attempt.
- **FR-MCP-04** — `POST /api/v1/mcp-servers/{id}/sync` re-runs discovery. New tools are inserted; tools that vanished from the server are marked `status='deprecated'` (never hard-deleted while bound to an agent — impact analysis, same as native tools).
- **FR-MCP-05** — `GET /api/v1/mcp-servers` lists servers with discovered-tool counts; `GET /api/v1/mcp-servers/{id}` returns server detail + its discovered tools.
- **FR-MCP-06** — `DELETE /api/v1/mcp-servers/{id}` soft-deletes the server. Blocked (409) if any discovered tool is bound to an agent, listing the blocking agents.
- **FR-MCP-07** — If the server supports `notifications/tools/list_changed`, the proxy subscribes and triggers a re-sync automatically. (Fallback: periodic re-sync on a configurable interval.)

### Binding & execution
- **FR-MCP-10** — A discovered MCP tool binds to an agent identically to any other tool (`POST /agents/{name}/tools` by `tool_id`). No MCP-specific binding path.
- **FR-MCP-11** — At runtime an `mcp_tool` call is dispatched to the MCP Proxy, which invokes `tools/call` on the owning server and returns the result to the agent.
- **FR-MCP-12** — The MCP tool callable is wrapped by the **same** `governed_tool` wrapper as native/HTTP tools (`sdk/agentshield_sdk/graph_builder.py`). OPA authorize → HITL if required → execute → return. No parallel governance code path.
- **FR-MCP-13** — MCP tool input schemas surface to the LLM exactly like native tool schemas, so the model calls them by name with typed args.
- **FR-MCP-14** — A `tools/call` failure (server down, protocol error, timeout) returns a structured tool error to the agent (not a crash), and increments the server's error signal for health (FR-MCP-22).

### Auth & identity
- **FR-MCP-20** — For an **external** server, the proxy injects static credentials from the linked `AuthConfig` (api_key / bearer / custom header) on connect. Credentials come from the K8s Secret; they are never stored in Postgres or returned by the API (reuses `tool-credential-management.md`).
- **FR-MCP-21** — For an **internal** server with identity mode = on-behalf-of, the proxy presents the calling user's Keycloak identity so the server can enforce per-user authz. With mode = service-identity, the proxy uses a platform Keycloak service-account token.
  - **Mechanism (confirmed 2026-07-19, see Decision 29 in `docs/decisions.md`): impersonation-based Keycloak token exchange, not JWT forward, not classic RFC 8693 subject_token exchange.** Verified in code that no raw Keycloak JWT survives past `services/registry-api/auth_middleware.py` today — every internal hop only ever carries a derived `user_id`/`user_team` string (`x-user-sub`/`x-agent-team` headers). `docs/design/identity-propagation-architecture.md` (Proposed, unimplemented) will replace that ad-hoc header pattern with a durable `RunContext.user_sub` string, propagated via an HMAC-signed internal token — but by design it **still never carries a re-presentable access token**, only the verified subject string. So on-behalf-of cannot use Classic Token Exchange (which requires an actual `subject_token`); it must be impersonation-based exchange: MCP Proxy holds a confidential Keycloak client with an impersonation grant, and mints a token *for* `RunContext.user_sub` without ever possessing that user's original token.
  - **Hard dependency:** FR-MCP-21 is blocked on `docs/design/identity-propagation-architecture.md` Phase 0–2 landing (shared `RunContext` infra + SDK pod runtime reading it) — without it, `RunContext.user_sub` never reaches `governed_tool` for `sdk`-type agents at all (see `docs/design/sdk-agent-gaps.md` Gap 1, same root cause). This is an external dependency, not something MCP Phase 2 builds standalone.
  - **MCP-specific incremental work, once that dependency lands:** (1) new Keycloak confidential client for MCP Proxy with an impersonation grant scoped appropriately; (2) `governed_tool`'s `mcp_tool` dispatch branch reads `RunContext.user_sub` and includes it in the `/tools-call` request to MCP Proxy, only when `MCPServer.identity_mode == on_behalf_of`; (3) MCP Proxy performs the impersonation exchange (client credentials + impersonation permission + `requested_subject=user_sub`) to mint a token scoped/audienced for that internal server, then uses it for `tools/call`; (4) fail-closed if `user_sub` is empty for an on-behalf-of-configured server (deny, do not silently fall back to service-identity); (5) no token caching in the first cut — mint fresh per call, revisit only if latency becomes a real issue.

### Health & observability
- **FR-MCP-22** — The proxy periodically health-checks each `connected` server (lightweight `ping`/`tools/list`); repeated failures flip `status` to `error` and surface in Studio.
- **FR-MCP-23** — Every MCP `tools/call` emits a Langfuse span (server name, tool name, latency, success/error) nested under the agent run trace, same as other tool calls.
- **FR-MCP-24** — Every MCP tool call writes an OPA decision record (allow/deny/require_approval) to the immutable audit log, same as native tools.

### Safety

**Generic governance gate (Decision 27, `docs/decisions.md`) — applies to ALL tool types, not MCP-specific. Built once, in `governed_tool`, consumed by every tool type including MCP.**
- **FR-MCP-50** — `governed_tool` (`sdk/agentshield_sdk/graph_builder.py:256-396`) gains a per-tool-call output-scan step, applied after execute and before the result re-enters the LLM context, for native/http/python/mcp_tool alike. Reuses the Safety Orchestrator's `scan_output` primitive, called per tool call instead of only once per turn. Includes fixing the live field-name bug where the SDK client reads `clean_text` (`safety_client.py:128-130`) but the server only ever sends `deanonymized_message` (`services/safety-orchestrator/schemas.py:27-32`) — today `clean_text` is silently always the original unscanned text on any real deployment.
- **FR-MCP-51** — New OPA decision field `allow_deanonymize: bool`, added to `Decision` (`opa_client.py:61-62`) and the live enforcement Rego (`bundle_generator.py` + `opa_policy/agentshield.rego` — the actual sidecar-served path; `policy_generator.py` gets the field too, for audit-trail parity only, not enforcement), alongside the existing `allow`/`require_approval`. Per-tool, same risk-driven generation path as everything else — no MCP-specific policy branch.
- **FR-MCP-52** — New structured de-anonymize primitive: tool-call args (dict) + session_id → args dict with real PII values substituted, gated by FR-MCP-51's `allow_deanonymize`. Distinct from the existing free-text de-anonymize path inside the Safety Orchestrator (`orchestrator.py:296-308`), which stays untouched for its original purpose (final-message de-anonymization for human review) — this is a new, separate capability, not a reuse.
- **FR-MCP-30** — MCP tool arguments containing PII placeholders consume FR-MCP-52 (structured de-anonymize, gated by FR-MCP-51's `allow_deanonymize`) before leaving the platform — real PII only ever goes to a tool that policy allows, never back into the LLM context. No MCP-specific implementation; MCP is one consumer of the generic gate.
- **FR-MCP-31** — Results from an **external** MCP server consume FR-MCP-50 (generic output scan) before re-entering the agent's LLM context (untrusted-input boundary, §4b) and cannot have this step skipped. Internal-server results are scanned too (defense in depth) but may be exempted per-server via `scan_results` (an admin-configurable override).

### Studio (UX)
- **FR-MCP-40** — A "MCP Servers" screen under Settings: list, register (form: name, URL, transport, auth config, identity mode), see status + discovered-tool count, sync, delete.
- **FR-MCP-41** — Register → the UI shows discovery results (the tools that were found) before the user leaves the screen — proving the round-trip, not just a 201.
- **FR-MCP-42** — The agent-builder Tool Picker shows MCP tools alongside native/HTTP tools, tagged with their source server (spec.md roadmap milestone 11: "Tool Picker shows MCP server tools").
- **FR-MCP-43** — **Save → reload → assert:** register a server, reload from backend, confirm the server and its discovered tools persisted (CLAUDE.md DoD #2).

---

## 7. Data model deltas

The `mcp_servers` table exists but is missing fields these requirements need. Add via a new numbered migration (idempotent, guarded):

| Field | Type | Purpose |
|---|---|---|
| `identity_mode` | `varchar` `('on_behalf_of'\|'service_identity'\|'none')` | §4a. `none` for external servers. |
| `is_external` | `boolean` | Drives the untrusted-input output scan (FR-MCP-31) and identity handling. |
| `transport_config` | `jsonb` | stdio command/args/env, or HTTP extra config. |
| `health_detail` | `jsonb` | Last error, last successful ping, consecutive failures. |
| `list_changed_supported` | `boolean` | Whether to subscribe (FR-MCP-07). |
| `scan_results` | `boolean` default `true` | Per-server override for FR-MCP-31 output scan. |

`Tool` already carries `mcp_server_id` + `mcp_tool_name`; no change needed there. Discovered tools reuse `risk_level` (default `low` per D4) and `side_effecting` (infer conservatively — see §10).

---

## 8. Architecture (target)

```
┌──────────────┐   governed_tool(mcp)    ┌───────────────────────────┐
│  Agent Pod   │ ───────────────────────▶│      MCP Proxy service     │
│ (runner/SDK) │   POST /proxy/tools-call│  (services/mcp-proxy)      │
│              │◀────────────────────────│                            │
└──────┬───────┘        result           │  • MCP client (http+stdio) │
       │ OPA + HITL (unchanged)          │  • session/conn pool       │
       ▼                                 │  • credential injection    │
   OPA sidecar                           │  • tools/list discovery    │
                                         │  • health / list_changed   │
                                         └──────────┬──────────┬──────┘
                                                    │          │
                                    streamable_http │          │ stdio (sandboxed)
                                                    ▼          ▼
                                          Internal / External   Local subprocess
                                            MCP servers          MCP servers
```

- **registry-api** owns CRUD (`/api/v1/mcp-servers`), the `Tool` rows, and OPA policy generation at bind time. It calls the proxy to run discovery on register/sync.
- **MCP Proxy** owns all live MCP connections and the `tools/call` execution. Stateless w.r.t. business data; holds sessions + credential material in memory only.
- **Agent pod** dispatches `mcp_tool` calls to the proxy through the unchanged `governed_tool` wrapper.

New service, so: image tag var `MCP_PROXY_TAG` in `deploy-cpe2e.sh` + `charts/agentshield/values.yaml`, Helm sub-chart gated on `mcp-proxy.enabled` (default false).

---

## 9. Security requirements (esp. stdio)

- **SR-01 (stdio supply chain)** — A `stdio` server runs third-party code (npx/uvx) inside the proxy's trust boundary. stdio servers MUST run in a locked-down sandbox: no host network unless explicitly granted, read-only rootfs, dropped capabilities, CPU/mem limits, and an allowlist of runnable commands. A registered stdio command is **admin-approved**, not free-text from any operator.
- **SR-02 (credential isolation)** — Credentials for MCP servers live in K8s Secrets and are read only by the proxy. They are never mounted into agent pods and never returned by any API (write-only, per `tool-credential-management.md`).
- **SR-03 (external = untrusted)** — External-server results are output-scanned (FR-MCP-31). An external MCP server is assumed hostile-capable; nothing it returns reaches the LLM unscanned.
- **SR-04 (egress control)** — The proxy is the only component that opens outbound connections to external MCP servers. Egress is centralized there so it can be network-policied and audited, rather than every agent pod dialing the internet.
- **SR-05 (SSRF on register)** — `server_url` for HTTP transport is validated on registration to prevent pointing the proxy at internal metadata endpoints / link-local addresses when the intent was "external."

---

## 10. Accepted risks & open questions

- **AR-01 (D4 — discovered tools default low-risk).** We chose to treat discovered tools as normal `Tool` rows with `risk=low`. That's permissive for a remote server. **Mitigations:** (a) admins can raise `risk_level` per tool through the existing Tool edit path; (b) external-server results are output-scanned regardless of risk; (c) `side_effecting` should be inferred conservatively for MCP tools (default `true` unless the tool is provably read-only), so batch eval mocks them and HITL can gate them. Revisit if a "fail-closed by default" posture is wanted later (that was the rejected option).
- **OQ-01 (OAuth 2.1).** External servers that *require* MCP OAuth 2.1 (consent, DCR, refresh) are out of scope for MVP (D3). Which real target servers need it, and when? Drives the Phase-3 build in §11.
- **OQ-02 (resources & prompts).** MVP consumes `tools` only. Do any target servers expose must-have `resources`/`prompts`? If yes, sequence a follow-up.
- **OQ-03 (per-user identity to external servers).** MVP is one static principal per external server. If a partner needs per-end-user identity externally, that needs OAuth OBO — folds into OQ-01.
- **OQ-04 (stdio in prod).** stdio unlocks the npx/uvx ecosystem but is the heaviest to secure (SR-01). Confirm which stdio servers are actually needed vs. deferring stdio to internal-only / dev.

---

## 11. Phasing

**Phase 1 — HTTP discovery + governed execution (internal + external, static auth).**
`services/mcp-proxy` skeleton, `streamable_http` client, `initialize` + `tools/list`, persist discovered tools, `/api/v1/mcp-servers` CRUD + sync, `mcp_tool` dispatch through `governed_tool`, external-result output scan, Langfuse spans, OPA audit. Studio: register + browse + tool-picker. Helm sub-chart (disabled by default). → FR-MCP-01..06, 10..14, 20, 23, 24, 30, 31, 40..43.

**Phase 2 — Health, change notifications, internal identity.**
`notifications/tools/list_changed` subscription, periodic health-check + status surfacing, internal-server on-behalf-of / service identity (FR-MCP-07, 21, 22).

**Phase 3 — stdio + hardening.**
Sandboxed stdio transport (SR-01), egress policy, SSRF validation, admin command allowlist (D2, SR-04, SR-05).

**Phase 4 (deferred) — MCP OAuth 2.1** for external servers that require it (OQ-01), and `resources`/`prompts` (OQ-02) if needed.

---

## 12. Acceptance criteria (Definition of Done mapping)

Per CLAUDE.md, "backend works" ≠ done. For this feature, done means:

1. **Real user journey proven** — a Playwright spec registers an MCP server in Studio, sees discovered tools, binds one to an agent, and the tool appears in the picker (FR-MCP-40..43). Not just a green endpoint.
2. **Save → reload → assert** — register a server, reload from the backend, discovered tools still there (FR-MCP-43).
3. **No orphan code** — every new symbol (proxy client methods, `/mcp-servers` router, SSE/health signals) has a live caller in the same change; grep before claiming done.
4. **Governance proven end-to-end** — a bash e2e suite (`scripts/e2e/suite-NN-mcp-tools.sh`) registers a stub MCP server, binds a tool, runs it through OPA + HITL, and asserts the audit record + Langfuse span (FR-MCP-12, 23, 24).
5. **Untrusted-input boundary proven** — a test shows an external-server result passing the generic output scan (FR-MCP-50) before re-entering context (FR-MCP-31).
6. **Generic gate proven for every tool type, not just MCP** — since FR-MCP-50/51/52 change `governed_tool` itself, the impacted-blast-radius regression sweep (CLAUDE.md's mandatory rule) must re-run existing native/http/python tool-call e2e coverage, not only the new MCP suite — a green MCP suite with a broken native-tool output scan is a shipped regression.
7. **Team-scoping proven** — a test confirms a foreign-team's agent cannot deploy with an unpublished/ungranted MCP tool bound (`AssetGrant`/`publish_status`, same mechanism as any other tool, no new code path).
8. **Gap ledger** — see the consolidated Known Gaps list in `docs/design/mcp-tool-source-architecture.md` §8; anything deferred (stdio, OAuth, resources/prompts, latency budget, pagination, backpressure) is tagged deferred-intentional vs debt.
