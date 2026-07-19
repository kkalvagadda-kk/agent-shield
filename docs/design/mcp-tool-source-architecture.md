# MCP as a Tool Source — Architecture (LOCKED)

**Status:** LOCKED — ready for team review
**Date locked:** 2026-07-19
**Author:** Karthik + Claude
**Requirements:** `docs/design/todo/mcp-tools-for-agents-requirements.md`
**Related decisions:** `docs/decisions.md` Decision 15 (Tool & MCP Registry), 27 (per-tool-call output-scan + de-anonymize gate), 28 (stdio sandboxing model), 29 (on-behalf-of impersonation exchange)
**Related, separate-scope docs surfaced during this design (not duplicated here):** `docs/design/sdk-agent-gaps.md` (SDK-runtime parity gaps — identity binding, memory absence, broken streaming resume), `docs/design/identity-propagation-architecture.md` (external dependency for FR-MCP-21, §7a)

> This is the converged design. Team review next — see `docs/spec.md`/`docs/decisions.md`-style gate: share this doc + the requirements doc for sign-off, then `/plan` for implementation. §8 is the authoritative gap ledger — anything not listed there as deferred is expected to be built.

---

## 1. Problem & Scope

Agents get tools from the Tool Registry, which today only supports `native`, `http`, and `python` types. `MCPServer`/`Tool.type='mcp_tool'` schema has existed since migration `0001`, but there is no runtime behind it: no `/api/v1/mcp-servers` router, no MCP client, no discovery, no execution path, no Studio UI. This design builds that runtime — making tools hosted on external MCP servers (GitHub, Slack, internal DB servers, etc.) available to agents under the same governance as every other tool.

This is the **inbound** direction only (platform as MCP client). Platform-as-MCP-server is a separate, unrelated doc (`docs/design/todo/mcp-server.md`).

**Locked going in (from the requirements doc, §3):**
| # | Decision | Choice |
|---|---|---|
| D1 | Where the MCP client runs | Centralized **MCP Proxy** service (`services/mcp-proxy`) |
| D2 | Transports | `streamable_http` (Phase 1) and `stdio` (Phase 3) |
| D3 | External-server auth | Static credentials via `AuthConfig` → K8s Secret. OAuth 2.1 deferred. |
| D4 | Governance of discovered tools | Normal `Tool` rows, default `risk=low`, no MCP-special-casing |

**Locked during this design round:**
| # | Decision | Choice |
|---|---|---|
| Decision 27 | Per-tool-call output-scan + de-anonymize gate | Build generically inside `governed_tool`, applied to **all** tool types (native/http/python/mcp_tool), not an MCP-only bolt-on. Details in `docs/decisions.md`. |

---

## 2. Grounding Corrections (vs. the original requirements doc and stale spec.md)

Verified against actual code, not `docs/spec.md` (confirmed stale, not used as a reference here):

1. `mcp_servers`/`tools` MCP schema already exists since migration `0001` — not new. Only 6 additive `MCPServer` fields are genuinely new (§7 of requirements doc). Latest migration is `0068`, not `0028`.
2. OPA policy generation happens at **deployment** time (`policy_generator.py`, reading `AgentVersion.tools`), not tool-bind time.
3. **`governed_tool` had no per-tool-call output scan or de-anonymize gate for any tool type** — this is what forced Decision 27. The existing free-text de-anonymize in the Safety Orchestrator (`orchestrator.py:296-308`) is unconditional, ungated by OPA, and its result is currently discarded by the SDK client (`clean_text` field-name bug in `safety_client.py`).
4. **The declarative-runner does not simply reuse the SDK's `governed_tool`** — it has its own duplicated dispatch (`workflow_executor._tool_dict_to_executor`, `node_executors.py`). Agent-owned tool calls do reach real governance; a legacy old-schema standalone-tool-node path does not (pre-existing gap, not introduced by MCP — MCP tools will not be exposed there).
5. MCP Proxy needs its **own** credential-resolution path — the existing deploy-controller flow only copies secrets into per-agent namespaces and never looks at `MCPServer.auth_config_id`.
6. `python-executor` (closest sidecar template) is stateless-per-call; MCP Proxy is fundamentally stateful (live sessions/connection pool) — a different operational shape.
7. Studio is confirmed 100% greenfield for MCP (zero existing references). Good templates: `KnowledgeBasesPage.tsx`/`KnowledgeBaseDetailPage.tsx` (list→detail shape) and `studio/e2e/knowledge.spec.ts` (register→reload→assert-persisted journey).

Full detail on each point is in the requirements doc's "Corrections found during architecture grounding" section.

---

## 3. Architecture

### Components

```
┌──────────────┐                          ┌─────────────────────────────────┐
│  Agent Pod   │  governed_tool(mcp_tool) │        MCP Proxy service         │
│ SDK / decl-  │ ───POST /internal/call──▶│      (services/mcp-proxy)        │
│   runner     │◀─────result + scores─────│  agentshield-platform namespace  │
└──────┬───────┘                          │                                  │
       │ OPA authorize (+ allow_deanon)   │ • MCP client (streamable_http)   │
       │ HITL if required                 │ • per-replica session cache      │
       │ execute → NEW: output-scan step  │ • K8s Secret read (own SA/RBAC)  │
       ▼                                  │ • tools/list discovery           │
   OPA sidecar                            └───────────┬──────────────────────┘
                                                       │ streamable_http
                                                       ▼
                                          Internal / External MCP servers
                                          (stdio = Phase 3, not built yet)

┌─────────────┐   CRUD + sync trigger    ┌──────────────┐
│   Studio    │─────────────────────────▶│ registry-api │
│ MCP Servers │◀────discovered tools─────│ /mcp-servers │──▶ Postgres (Tool rows,
│   screen    │                          │  router (new)│     mcp_servers table)
└─────────────┘                          └──────┬───────┘
                                                 │ calls to run discovery
                                                 ▼
                                           MCP Proxy /discover
```

### Responsibilities

| Component | Owns | Depends on |
|---|---|---|
| **registry-api** (`routers/mcp_servers.py`, new) | CRUD for `MCPServer`, `Tool` rows for discovered tools | Postgres, MCP Proxy (`/discover`) |
| **MCP Proxy** (new service) | MCP wire protocol, per-replica session cache, credential resolution, `tools/list`/`tools/call` | MCP servers (remote), K8s Secrets (read-only) |
| **SDK `tool_resolver.py`/`tool_executor.py`** | New `McpToolExecutor` | MCP Proxy `/tools-call` |
| **declarative-runner `workflow_executor.py`/`node_executors.py`** | New `McpToolNodeExecutor` (separate from SDK's, per grounding finding #4) | MCP Proxy `/tools-call` |
| **`governed_tool`** (both runtimes) | New de-anonymize-args + output-scan-result steps, applied to every tool type | OPA sidecar, Safety Orchestrator |
| **Studio "MCP Servers" screen** (new, under Settings) | Register/list/sync/delete UI, discovery-result display | registry-api |

### Data Flow — Register + Discover
1. Operator → Studio → `POST /api/v1/mcp-servers` (name, url, transport, auth_config_id, owner_team, identity_mode).
2. registry-api persists `MCPServer`, calls MCP Proxy `POST /internal/discover {server_id}`.
3. Proxy resolves credentials (own K8s Secret read), opens a `streamable_http` session, runs `initialize` + `tools/list`, returns tool list + JSON schemas.
4. registry-api upserts one `Tool` row per discovered tool (`type='mcp_tool'`, `mcp_server_id`, `mcp_tool_name`, `input_schema`); updates `discovered_tool_count`/`last_synced_at`/`status`.
5. Studio shows discovery results inline before the operator leaves the screen (FR-MCP-41).

### Data Flow — Governed Tool Call (Decision 27's new gate, applied to all types)
1. LLM emits a tool call → `governed_tool` (same code path for all 4 types).
2. `opa_client.check_tool()` → OPA sidecar → `Decision{allow, require_approval, allow_deanonymize}` (new field).
3. If `require_approval` → HITL pause/resume (unchanged mechanism).
4. **New:** if `allow_deanonymize` and args contain a PII placeholder → structured de-anonymize primitive (args dict + session_id → args dict with real values). Distinct from the existing free-text de-anonymize, which is untouched.
5. Dispatch by type: `native`/`http`/`python` unchanged; `mcp_tool` → MCP Proxy `/tools-call`.
6. **New:** result → Safety Orchestrator `scan_output` (with the `clean_text`/`deanonymized_message` field bug fixed) before returning to the LLM. External-server results (`is_external=true`) cannot skip this; internal-server results can be exempted per-server (`scan_results` flag).
7. Langfuse span: automatic via existing OpenInference auto-instrumentation — no new wiring for the outer span (an inner span specifically for the proxy call itself is still an open item, see §6).

### Team-Scoping / Multi-Tenancy — Resolved via Existing Mechanisms, No New Ones
MCP servers and their discovered tools use the platform's existing publish/grant model as-is (no MCP-specific mechanism):
- **`Tool.publish_status`** (`routers/tools.py:158-160`) gates cross-team *visibility* — published tools are listed for everyone; drafts only for their creator. Same default behavior new MCP-discovered tools get as any other newly-created tool.
- **`AssetGrant`** (`asset_type='tool'`, `models.py:1242-1263`) gates cross-team *deployment* — `routers/deployments.py:390-536` blocks deploying an agent with a foreign-owned tool bound unless an active grant exists for the deploying team (own-team tools are implicitly exempt, `deployments.py:527`). Neither mechanism branches on `Tool.type` anywhere.
- **Only precondition:** at discovery time, registry-api must set the new `Tool.owner_team = MCPServer.owner_team` (and apply whatever `publish_status` default any newly-created tool gets today). With that one field populated correctly, both mechanisms apply to MCP tools automatically.

### Key Decisions Proposed This Round
- **Credential resolution**: MCP Proxy reads `AuthConfig` secrets directly via its own K8s ServiceAccount/RBAC (read-only, scoped to Secrets in `agentshield-platform`) — no copy-to-namespace step, since the proxy already lives where the secret is written (unlike agent pods).
- **HTTP session management (Phase 1 scope only)**: plain K8s `Deployment`, N replicas, no sticky routing. Each replica keeps its own in-memory session cache; a cache miss just re-runs `initialize` (cheap for HTTP).
- **Gate ordering**: authorize → approve → de-anonymize → execute → scan, always in that order, for every tool type.

---

## 4. What This Sacrifices

- No connection-affinity for HTTP sessions across replicas — acceptable at Phase 1 scale, revisit if external servers rate-limit by connection count.
- Decision 27's generic gate is bigger than "just MCP" — touches core SDK governance files, not only MCP-specific code. Slower to ship, but avoids leaving native/http tools with an equivalent unaddressed risk.
- stdio is deliberately not designed yet (Phase 3) — see open question in §7.

---

## 5. Self-Critique / Risks

- **Failure mode**: MCP Proxy pod restart drops every in-flight session. Cheap to recover for Phase 1 (HTTP lazy-reconnect); much more expensive once stdio (Phase 3) means killing live subprocesses with multi-second cold-start costs.
- **Scale**: the new gate adds up to 2 network hops per tool call (de-anonymize + output-scan) on top of the existing 2 (OPA + HITL-check). No latency budget has been set — see §7.
- **Change**: a future 5th tool type touches the same 4 dispatch points MCP is establishing (`tool_resolver.py`, `tool_executor.py`, `workflow_executor.py`, `node_executors.py`) — inherent to the declarative-runner's duplicated dispatch, not fixable within this design's scope.
- **Ops**: debugging a stuck/slow MCP tool call spans agent pod → OPA → MCP Proxy → external server. `health_detail` (per-server) helps; a dedicated Langfuse span for the proxy call itself is not yet specified.

---

## 6. Non-Goals / Deferred (unchanged from requirements doc)
- MCP OAuth 2.1 / dynamic client registration (Phase 4).
- MCP `resources`/`prompts` primitives (Phase 4, if needed).
- stdio transport (Phase 3) — sandboxing model still an open question, see §7.
- Tool marketplace/catalog UI beyond the existing Tool Registry list.

---

## 6a. Phasing — What's Actually In Each Phase

The requirements doc's original §11 phasing predates Decision 27 (the generic gate came out of *this* design round). Updated here so Decision 27's work has an explicit home, and so each phase is self-contained rather than a scattered reference.

### Phase 1 — HTTP discovery + governed execution + the generic gate (this design's primary scope)
| Area | Work |
|---|---|
| Data model | New migration (chains after `0068`): `identity_mode`, `is_external`, `transport_config`, `health_detail`, `list_changed_supported`, `scan_results` on `MCPServer` |
| registry-api | New `routers/mcp_servers.py`: CRUD + `/sync`; calls MCP Proxy `/discover`; upserts `Tool` rows |
| MCP Proxy | New service skeleton: `streamable_http` client, `initialize` + `tools/list`, own K8s Secret read (own SA/RBAC, no copy-to-namespace), `/tools-call` execution endpoint |
| SDK | New `McpToolExecutor` in `tool_resolver.py`/`tool_executor.py` |
| declarative-runner | New `McpToolNodeExecutor` in `workflow_executor.py`/`node_executors.py` — **agent-owned tool path only**, not the legacy ungoverned standalone-node path |
| Decision 27 (generic gate) | OPA `allow_deanonymize` field + Rego gen update (`policy_generator.py`); new structured de-anonymize primitive; per-tool-call output-scan wired into `governed_tool` for **all four** tool types; fix the `clean_text`/`deanonymized_message` field bug in `safety_client.py` |
| Safety | External-server results (`is_external=true`) always scanned; internal servers scanned by default with per-server `scan_results` opt-out |
| Observability | Langfuse spans on the outer Tool call — automatic, no new work. Inner proxy-call span — not yet decided (see Q2/ops note in §5) |
| Studio | New "MCP Servers" screen (register/list/sync/delete) under Settings; discovery-result shown inline on register; Tool Picker tags MCP tools by source server; `ToolsPage` shows `mcp_tool` rows **read-only** (not creatable there) |
| Tests | Bash e2e suite proving OPA+HITL+audit end-to-end for an MCP tool call; Playwright spec for register→discover→bind→appears-in-picker; Vitest for the new Studio screen |
| Maps to | FR-MCP-01..06, 10..14, 20, 23, 24, 30, 31, 40..43, **plus** Decision 27's SDK/OPA/Safety-Orchestrator work (not covered by an existing FR number — worth assigning new FR-MCP-5x numbers when this moves to a final spec) |

### Phase 2 — Health, change notifications, internal identity
| Area | Work |
|---|---|
| MCP Proxy | Subscribe to `notifications/tools/list_changed` where supported; periodic health-check loop; `health_detail` surfaced to Studio |
| Identity | Internal-server `on-behalf-of` and `service-identity` (Keycloak service-account token) modes — see §7a for on-behalf-of's scoped work and external dependency |
| Observability | Inner Langfuse span for the proxy call itself, if deferred from Phase 1 |
| Maps to | FR-MCP-07, 21, 22 |

### 7a. FR-MCP-21 (on-behalf-of) — Scoped Work and External Dependency

**Confirmed mechanism (Decision 29, `docs/decisions.md`): impersonation-based Keycloak token exchange — not JWT forward, not Classic RFC 8693 subject_token exchange.** Verified in code (see the JWT-plumbing investigation earlier in this design's history) that no raw Keycloak JWT survives past `auth_middleware.py` today — every internal hop carries only a derived `user_id`/`user_team` string. `docs/design/identity-propagation-architecture.md` (Proposed, unimplemented) will replace the current ad-hoc `x-user-sub`/`x-agent-team` header pattern with a durable `RunContext.user_sub` string propagated via an HMAC-signed internal token (RCT) — but **by design it still never carries a re-presentable access token**, only the verified subject string, specifically because (a) verifying a full JWT at every hop needs JWKS infra that doesn't exist outside registry-api, and (b) identity has to survive HITL pauses of up to 24 hours, by which point any original token would be long expired anyway. Both reasons independently rule out Classic Exchange and confirm impersonation-based exchange is the only mechanism that fits the platform's actual constraints — this holds even after `identity-propagation-architecture.md` ships in full, not just today.

**This is a hard external dependency, not standalone MCP work.** FR-MCP-21 is blocked on `identity-propagation-architecture.md` Phase 0–2 (shared `RunContext` infra + SDK pod runtime reading it) landing first. Without it, `RunContext.user_sub` never reaches `governed_tool` for `sdk`-type agents at all — the same root cause as Gap 1 in `docs/design/sdk-agent-gaps.md` (a broader SDK-runtime parity gap set, tracked there, not duplicated here).

**MCP-specific incremental work, once that dependency lands:**
1. New Keycloak confidential client for MCP Proxy, granted an impersonation permission scoped appropriately (which users/realm — an open scoping detail, but should track whatever team boundary already gates which agents/users can reach a given internal MCP server).
2. `governed_tool`'s `mcp_tool` dispatch branch reads `RunContext.user_sub` and includes it in the `/tools-call` request to MCP Proxy, only when `MCPServer.identity_mode == on_behalf_of`.
3. MCP Proxy performs the impersonation exchange (its own client credentials + impersonation grant + `requested_subject=user_sub`) to mint a token scoped/audienced for that specific internal server, then uses it for `tools/call`.
4. **Fail-closed, not silent fallback:** if `user_sub` is empty at call time for an on-behalf-of-configured server (e.g. a daemon/service-triggered agent unexpectedly reaching such a server), deny the call with a structured error — do not silently drop to `service-identity` semantics, which would defeat the reason the server was configured as on-behalf-of in the first place.
5. No token caching in this first cut — mint fresh per call; only revisit if latency becomes a measured problem.

### Phase 3 — stdio + hardening
| Area | Work |
|---|---|
| Transport | Sandboxed `stdio` — **blocked on Open Question 1** (per-server dedicated pod vs. subprocess-in-shared-proxy) |
| Security | Egress policy centralization (SR-04), SSRF validation on `server_url` registration (SR-05), admin-approved command allowlist (SR-01) — ties to **Open Question 4** (who approves) |
| Maps to | D2 (stdio half), SR-01, SR-04, SR-05 |

### Phase 4 (deferred, no work planned yet)
| Area | Work |
|---|---|
| Auth | MCP OAuth 2.1 / dynamic client registration for external servers that require it (OQ-01) |
| Protocol | `resources`/`prompts` primitives, if a target server needs them (OQ-02) |

### Which open question (§7) blocks which phase
| Q | Blocks | Note |
|---|---|---|
| 1. stdio sandboxing model | Phase 3 build; **but the Phase 1 `/tools-call` contract shape needs an answer now** | Only structural fork requiring a decision before Phase 1 code is written |
| 2. Latency budget | Phase 1 | Gate ships in Phase 1 |
| 3. Tool name collisions | Phase 1 | Discovery + `Tool` row creation ships in Phase 1 |
| 4. Who can register a server | Phase 1 (registration); stdio-approval half is Phase 3 | |
| 5. Schema drift on re-sync | Phase 1 | `/sync` ships in Phase 1 |
| 6. `tools/list` pagination | Phase 1 (can be logged as a gap instead of blocking) | Lowest priority |

---

## 7. Open Questions — Status

### Resolved (2026-07-19)

3. **Tool name collisions — RESOLVED.** Discovered MCP tool names are auto-namespaced at discovery time as `{server_name}__{mcp_tool_name}` (this becomes `Tool.name`; `Tool.mcp_tool_name` keeps the raw upstream name for `tools/call` dispatch). Guarantees uniqueness across servers and against existing native/http tools without any manual rename/collision-fail path.

4. **Who can register an MCP server — RESOLVED.** Follows whatever pattern current Tool creation already uses (no new restriction invented for MCP specifically). Proper artifact-scoped RBAC for MCP servers (mirroring Decision 25's model) is deferred to whenever RBAC is extended to this artifact type generally — not a gap specific to this feature, just inherits today's tool-creation permission model as-is.

5. **Schema drift on re-sync — RESOLVED.** On `/sync`, a changed `input_schema` for an already-discovered tool is **auto-applied** (the agent gets the new contract immediately, no manual approval gate blocking sync) **and flagged** for review — i.e. the tool row / server gets a visible "schema changed since last sync" marker so an admin can go audit whether anything bound to it broke, but the sync itself doesn't stall waiting for that review.

6. **`tools/list` pagination — DEFERRED, logged as a gap.** Phase 1 assumes a server's full tool list fits in one `tools/list` response (no cursor handling). Tagged **deferred (intentional)** in the gap ledger — revisit if a real target server needs it.

1. **stdio sandboxing model — RESOLVED (Decision 28, `docs/decisions.md`).** Dedicated K8s pod per registered stdio server (Option B), with MCP Proxy acting as router/entry-point rather than holding subprocesses itself (Option C's caller-facing framing). Mirrors the existing Agent-per-Pod pattern (Decision 2). Confirmed no impact on the Phase 1 `/tools-call` contract — this is purely a Phase 3 build item.

2. **Latency budget for the new gate — DEFERRED (2026-07-19), logged in §8.** No numeric target set for Phase 1; best-effort, revisit only if the added hops (de-anonymize + output-scan) show up as a measured problem in practice rather than a theoretical one.

---

### Elaborating on Open Question 1 — stdio sandboxing model

**Why this is a real fork, not a detail:** `stdio` transport means the proxy runs a third-party command (`npx some-mcp-server`, `uvx another-one`) as a live child process with a stdin/stdout pipe. That process can do anything its own process permissions allow — read files, make network calls, spawn more processes — for as long as it's alive. SR-01 lists exactly the controls you'd reach for to contain that: no host network unless granted, read-only rootfs, dropped capabilities, CPU/mem limits. Those four things are almost verbatim the fields of a Kubernetes `PodSecurityContext` + resource `limits` block — which is telling, because D2's original phrasing ("stdio... only run inside the proxy under the sandboxing rules in §9") assumes those controls apply *inside* one shared proxy process, where they don't natively exist.

**Option A — subprocess-in-shared-proxy (D2's literal wording).**
The MCP Proxy pod spawns and holds N stdio subprocesses itself (one per registered stdio server), all inside its own container.
- To actually get "no host network / read-only rootfs / dropped capabilities" *per subprocess* (not per pod), the proxy process itself would need elevated privileges to construct namespaces/seccomp profiles around its children (`unshare`, mount namespaces, etc.) — which means granting the proxy container capabilities it wouldn't otherwise need, widening its own attack surface.
- Blast radius: if any one subprocess escapes its manual sandboxing, it's now running inside the same pod identity that holds the credentials for **every** registered server (internal and external) — all in one process's memory.
- Ops: one Deployment, no new controller needed — simplest to stand up.

**Option B — dedicated pod per registered stdio server.**
Registering a stdio server provisions its own K8s Pod (new reconciler, or an extension of `deploy-controller`, analogous to how it already reconciles one Deployment per agent). MCP Proxy becomes a *router*: it looks up which pod owns a given `server_id` and forwards the call there instead of holding the subprocess itself.
- Isolation is native and enforced by the kubelet, not hand-rolled: `PodSecurityContext` (`runAsNonRoot`, `readOnlyRootFilesystem`, `capabilities.drop: [ALL]`, seccomp profile), a per-pod `NetworkPolicy` (deny-by-default egress, allow only if explicitly granted), and resource `limits` — this is SR-01's checklist, implemented the way K8s already implements it for every other pod on the platform.
- Blast radius is capped **per server** — that pod's own `ServiceAccount` can be scoped to read only *that* server's `AuthConfig` secret, not every registered server's credentials.
- Cost: this is real new infrastructure — a controller to create/destroy pods (and a Service per pod, or an equivalent routing table) as servers are registered/deleted, plus cold-start latency whenever a stdio pod needs to (re)start (`npx`/`uvx` startup can be multi-second, which lands directly in tool-call latency the first time or after a restart).

**A middle ground worth naming:** Option B, but frame MCP Proxy purely as "the brain" (session bookkeeping, discovery orchestration, retry/health logic) while each per-server pod is a thin shim exposing the stdio subprocess over a small internal-only interface that only the Proxy's `NetworkPolicy` allows it to reach. This keeps a single logical entry point for the SDK/runner (they still just call "the proxy") while getting Option B's isolation — it's Option B's infrastructure with Option A's simplicity from the caller's point of view.

**My recommendation:** Option B (or the middle-ground framing of it). SR-01's own language already assumes pod-level guarantees; Option A can only approximate them by making the proxy process itself more privileged, which cuts against the platform's own posture (least-privilege, narrow blast radius) elsewhere. Option B also isn't a new operational muscle — it's the same pattern `deploy-controller` already runs for agents (Decision 2), just applied to stdio-registered MCP servers.

**On whether Phase 1 needs to change because of this:** No extra work needed now. The Phase 1 contract as designed (agent pod → `POST http://mcp-proxy.../tools-call`, one stable service name) is already abstract enough — nothing in the SDK/runner needs to know or assume where a session physically lives. Whatever sits behind that endpoint (a single process today, a router-plus-per-server-pods in Phase 3) can change without touching the caller side, **as long as Phase 1 doesn't leak any assumption that the proxy holds all sessions locally in a way other code reaches into directly** (it doesn't, in the current design — the SDK/runner only ever talk to the proxy's HTTP endpoint). So this is genuinely a Phase 3 decision with no Phase 1 consequence — you can defer picking A vs. B until Phase 3 planning if you'd rather not decide it now.

---

## 8. Known Gaps (Ledger)

Per CLAUDE.md's Definition of Done — anything deferred or knowingly incomplete, tagged **deferred (intentional)** vs **not-yet-wired (debt)**.

| Gap | Tag | Note |
|---|---|---|
| `tools/list` pagination | deferred (intentional) | Phase 1 assumes a server's full tool list fits in one response, no cursor handling. Revisit if a real target server needs it. |
| Latency budget for the generic gate (FR-MCP-50/51/52) | deferred (intentional) | No numeric target set. Best-effort for Phase 1; revisit if de-anonymize/output-scan hops show up as a measured problem. |
| Inner Langfuse span for the MCP Proxy call itself | not-yet-wired (debt) | The outer Tool-call span is automatic (existing OpenInference instrumentation); a dedicated span for the proxy→server hop (useful for "why did this MCP call time out" debugging) is not yet designed. |
| Rate limiting / backpressure on external MCP server calls | not-yet-wired (debt) | Phase 1 has no protection against a chatty agent hammering an external server through the proxy. Not addressed by any FR in this design. |
| stdio transport, egress policy, SSRF validation, admin command allowlist | deferred (intentional) | Phase 3 (SR-01, SR-04, SR-05, Decision 28). |
| MCP OAuth 2.1 / dynamic client registration | deferred (intentional) | Phase 4 (OQ-01). |
| MCP `resources`/`prompts` primitives | deferred (intentional) | Phase 4, if a target server needs them (OQ-02). |
| FR-MCP-21 (on-behalf-of internal identity) | not-yet-wired (debt), blocked externally | Hard dependency on `docs/design/identity-propagation-architecture.md` Phase 0–2 landing first (§7a). Not something this design's Phase 2 can build standalone. |
| `sdk`-type agents' MCP tool calls inherit the pre-existing identity gap | not a gap introduced by this design — inherited, logged for visibility | Per `docs/design/sdk-agent-gaps.md` Gap 1, any `sdk`-type, `user_delegated`-class agent gets every governed tool call hard-denied (`missing_user_identity`) today, regardless of tool type. MCP tools are not specially broken here — they fail exactly like native/http/python tools already do for this agent class until that gap is fixed (tracked separately, not by this design). |
| Impersonation Keycloak client's grant scope (which users/realm) | open implementation detail, not blocking | Phase 2, once `identity-propagation-architecture.md` lands. Default: scope to whatever team boundary already gates agent/server access. |
