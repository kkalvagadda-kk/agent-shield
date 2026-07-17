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

| Layer | Status |
|---|---|
| `MCPServer` model + `mcp_servers` table (migration 0001) | **Built** (schema only) |
| `Tool.type='mcp_tool'` + `mcp_server_id` + `mcp_tool_name` | **Built** (schema only) |
| `MCPServerCreate` / `MCPServerResponse` schemas | **Built** |
| `auth_configs` delete-guard checks `MCPServer` refs | **Built** |
| spec.md "MCP Proxy" component + `/api/v1/mcp-servers` endpoints | **Documented, not implemented** |
| Decision 15 (Option B, three tool types incl. MCP) | **Decided** |
| `services/mcp-proxy` service (client, discovery, call proxy) | **Missing** |
| `/api/v1/mcp-servers` router (CRUD + sync) | **Missing** |
| Runner/SDK `mcp_tool` execution path | **Missing** |
| Studio: register server, browse discovered tools, tool-picker | **Missing** |
| Health / re-sync loop, `notifications/tools/list_changed` | **Missing** |

Note the existing `mcp_servers` table lacks a couple of fields these requirements need (identity mode, default transport args, health detail). Schema deltas in §7.

---

## 6. Functional requirements

Numbered `FR-MCP-##`. Each is written to be testable (a bash e2e suite and/or Playwright spec must be able to prove it — per CLAUDE.md Definition of Done).

### Server registration & lifecycle
- **FR-MCP-01** — An operator can register an MCP server with: name, description, `server_url`, `transport` (`streamable_http`|`stdio`), optional `auth_config_id`, `owner_team`, and identity mode (§4a; N/A for external). `POST /api/v1/mcp-servers`.
- **FR-MCP-02** — On registration the platform connects, runs the MCP `initialize` handshake, and calls `tools/list`. Each returned tool is persisted as a `Tool` row (`type='mcp_tool'`, `mcp_server_id`, `mcp_tool_name`, `input_schema` from the server's JSON Schema).
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
- **FR-MCP-21** — For an **internal** server with identity mode = on-behalf-of, the proxy presents the calling user's Keycloak identity (JWT forward or token exchange) so the server can enforce per-user authz. With mode = service-identity, the proxy uses a platform Keycloak service-account token.

### Health & observability
- **FR-MCP-22** — The proxy periodically health-checks each `connected` server (lightweight `ping`/`tools/list`); repeated failures flip `status` to `error` and surface in Studio.
- **FR-MCP-23** — Every MCP `tools/call` emits a Langfuse span (server name, tool name, latency, success/error) nested under the agent run trace, same as other tool calls.
- **FR-MCP-24** — Every MCP tool call writes an OPA decision record (allow/deny/require_approval) to the immutable audit log, same as native tools.

### Safety
- **FR-MCP-30** — MCP tool arguments containing PII placeholders follow the existing de-anonymization path (OPA `allow_deanonymize` gate → Safety Orchestrator `POST /scan/deanonymize`) before leaving the platform — real PII only ever goes to a tool that policy allows, never back into the LLM context.
- **FR-MCP-31** — Results from an **external** MCP server pass the Safety Orchestrator output scan before re-entering the agent's LLM context (untrusted-input boundary, §4b). Internal-server results are scanned too (defense in depth) but may be exempted per-server by an admin.

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
5. **Untrusted-input boundary proven** — a test shows an external-server result passing the Safety Orchestrator output scan before re-entering context (FR-MCP-31).
6. **Gap ledger** — anything deferred (stdio, OAuth, resources/prompts) is logged in the known-gaps list, tagged deferred-intentional vs debt.
