# Architecture Decision Log

**Project:** AgentShield — AI Agent Safety & Governance Platform  
**Date:** 2026-06-24  
**Participants:** Karthik + Claude (arch-design session)

---

## Decision 1: Inter-Service Communication Model

**Context:** How platform services communicate — affects latency, complexity, debugging.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Pure REST** | Synchronous HTTP everywhere | Simple but chatty; temporal coupling between all services |
| **B: REST + Async Events** | REST for commands/queries, Redis pub/sub + Postgres LISTEN/NOTIFY for notifications | Balanced — sync where needed, async for fire-and-forget; slightly more infra |
| **C: Full Event-Driven** | Everything async via message bus | Decoupled but hard to debug; eventual consistency everywhere |

**Choice: Option B**  
**Rationale:** REST gives predictable request/response for the critical path (safety scanning, policy checks). Async events avoid blocking on notifications (approval alerts, trace ingestion). Postgres LISTEN/NOTIFY is perfect for agent resume after HITL approval — no need for a heavyweight message broker.

---

## Decision 2: Agent Deployment Model

**Context:** How agent code runs in the cluster.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Agent-per-Pod** | One K8s Deployment per registered agent | Clear isolation, independent scaling, simple debugging; more resource overhead |
| **B: Shared Runtime Pool** | Generic worker pods that load agent code dynamically | Better resource utilization; harder to isolate failures, complex classloader/module loading |
| **C: Serverless (KNative)** | Scale-to-zero per agent | Great for low-traffic agents; cold start latency, complex platform dependency |

**Choice: Option A (Agent-per-Pod)**  
**Rationale:** Isolation is critical for a safety platform — one misbehaving agent shouldn't affect others. Resource overhead is acceptable given the security boundary. Each agent gets its own OPA sidecar, NetworkPolicy, and resource limits.

---

## Decision 3: Safety Service Architecture

**Context:** How safety scanning (injection, PII, guardrails) is organized.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Monolithic Safety Service** | One service embeds all scanners | Single deployment, shared memory, but monolithic scaling and single-language constraint |
| **B: Thin Orchestrator + Independent Scanners** | Stateless router fans out to LLM Guard, Presidio, NeMo in parallel | Independent scaling/deployment per scanner; more network hops but parallel execution masks latency |

**Choice: Option B (Orchestrator + Independent Scanners)**  
**Rationale:** Each scanner has different resource profiles (LLM Guard needs GPU/RAM for DeBERTa; Presidio is CPU-light; NeMo needs model files). Independent scaling means we don't over-provision. Parallel fan-out means total latency ≈ slowest scanner, not sum of all. Fail-closed: if any scanner errors, block the request.

---

## Decision 4: Safety Placement Relative to Portkey (LLM Cache)

**Context:** Should safety scanning happen before or after the LLM cache lookup?

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Safety Before Portkey** | Every request is scanned even if cache would hit | Never skip scanning; small latency cost on cache hits |
| **B: Safety After Portkey** | Cache hits bypass safety | Faster for repeated queries; but a cached injection payload could be served without scanning |

**Choice: Option A (Safety Before Portkey)**  
**Rationale:** The safety layer must never be skippable. A cached response that was safe when first generated might be served in a different context where it's not appropriate. Consistent enforcement > latency optimization.

---

## Decision 5: Identity Provider

**Context:** Platform needs OIDC for API auth, UI login, and CI service accounts.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: External provider (Okta, Azure AD)** | SaaS OIDC | Zero ops; but adds SaaS dependency (violates self-hosted constraint) |
| **B: Self-hosted Keycloak** | Full OIDC/SAML IdP, self-hosted | Full control, no SaaS dependency; another stateful service to operate |

**Choice: Option B (Self-hosted Keycloak)**  
**Rationale:** No existing OIDC provider in infrastructure. Requirement is 100% self-hosted, zero SaaS. Keycloak adds one Postgres database (shared cluster) and one service to maintain, but gives full control over realms, clients, roles, and federation.

---

## Decision 6: Namespace Isolation Model

**Context:** How to isolate teams and their agents at the K8s level.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Namespace per team** | Each team gets a namespace (e.g., `agents-commerce`) with NetworkPolicy default-deny | Strong isolation, clear ownership, RBAC per namespace; more namespaces to manage |
| **B: Single shared namespace** | All agents in one namespace, separated by labels | Simpler; but no network-level isolation between teams' agents |
| **C: Namespace per agent** | Every agent gets its own namespace | Maximum isolation; massive namespace sprawl, operational overhead |

**Choice: Option A (Namespace per team)**  
**Rationale:** Balances isolation with manageability. NetworkPolicy default-deny ensures agents can only talk to platform services (safety, postgres, langfuse). Teams own their namespace — they can't see other teams' secrets or pods.

---

## Decision 7: Registry UI & Deployment in Phase 1

**Context:** Originally Registry UI and Deployment Controller were in Phase 2.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Phase 2 (original)** | Build safety + observability first, add management later | Agents deployed manually via kubectl in Phase 1; developer experience is poor early |
| **B: Phase 1 (moved up)** | Include Registry UI and basic deployment workflows from the start | Better developer experience from day one; Phase 1 scope increases from 4 to 5 weeks |

**Choice: Option B (Phase 1)**  
**Rationale:** Without a deployment UI, the platform is hard to adopt. Developers won't use it if the workflow is "write YAML, kubectl apply, hope for the best." Registry + Deploy Controller in Phase 1 means the core developer loop works end-to-end from the start.

---

## Decision 8: SDK Coupling Strategy

**Context:** How tightly developers are coupled to the platform's SDK.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Full SDK required** | All agents extend `AgentGraph`, use `@tool` decorators. Platform handles safety, approvals, tracing transparently. | Consistent, less boilerplate; locks developers to Python + LangGraph |
| **B: SDK optional, contract required** | Agents just need to expose specific HTTP endpoints and call platform APIs. SDK is a convenience. | More flexibility for other frameworks (CrewAI, AutoGen); less consistency, more ways to misconfigure |

**Choice: Option A now, evolve to Option B later**  
**Rationale:** Start with mandatory SDK for the first 3-5 agents — ensures consistency and fast onboarding. Design SDK with clean internal module boundaries (tracing, safety, approvals, policy as separable layers). When a team needs framework freedom, extract layers as standalone packages. The platform contract (endpoints, behaviors) is defined from day one; the SDK just satisfies it automatically.

---

## Decision 9: Database Architecture

**Context:** How to structure persistent storage across platform services.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Single Postgres cluster, separate databases** | One HA cluster with keycloak, agentshield, langfuse, langgraph, appsmith databases | Simple ops, one backup pipeline; single failure domain |
| **B: Separate Postgres instances** | Each service gets its own Postgres | Full isolation; 5x the operational overhead (backups, monitoring, upgrades) |
| **C: Single database, separate schemas** | One DB with schema-per-service | Simpler than multiple DBs; but Keycloak/Langfuse expect to own their DB, cross-schema access easier to misconfigure |

**Choice: Option A (Single cluster, separate databases)**  
**Rationale:** One backup strategy, one HA setup (Patroni/CloudNativePG), one monitoring target. Separate databases give logical isolation — each service has its own user with no cross-DB access. If contention appears later, langgraph (heaviest writer) is the first candidate to split out.

---

## Decision 10: LangGraph Checkpoint Storage

**Context:** Where agent conversation state persists between turns and across HITL approval pauses.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Redis** | Fast reads/writes; AOF persistence | Risk of data loss (last second of writes); not suitable for state that resumes hours later |
| **B: Postgres** | Durable, transactional; LangGraph has native `PostgresSaver` | Slightly higher latency than Redis; fully durable |
| **C: Both** | Redis for hot state, Postgres for durable | Unnecessary complexity for our scale |

**Choice: Option B (Postgres)**  
**Rationale:** Agent state must survive pod restarts and resume after HITL approval (up to 30 minutes later). Postgres guarantees durability. LangGraph's `PostgresSaver` is battle-tested. Redis latency advantage is irrelevant — checkpoint writes aren't in the hot path.

---

## Decision 11: Object Storage

**Context:** Need S3-compatible storage for backups, trace attachments, eval artifacts.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: MinIO (self-hosted)** | S3-compatible, erasure coded, runs on K8s | Full control, no SaaS; another service to operate |
| **B: Cloud provider S3** | AWS S3, GCS, Azure Blob | Zero ops; violates self-hosted constraint |

**Choice: Option A (MinIO)**  
**Rationale:** Self-hosted requirement. MinIO is the de facto self-hosted S3. Used for Postgres WAL archiving, ClickHouse backups, Langfuse media, and eval artifacts.

---

## Decision 12: Secret Management

**Context:** How agent pods access credentials (DB passwords, API keys for tools).

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Sealed Secrets** | Encrypt secrets in git, decrypt in cluster | GitOps-friendly; rotation requires re-encrypt + redeploy |
| **B: HashiCorp Vault** | Full secret management with dynamic creds, rotation, audit | Powerful; adds another complex stateful service |
| **C: Kubernetes native secrets + RBAC** | `kubectl create secret`, RBAC restricts access per namespace | Simplest; no rotation automation, no audit trail beyond K8s audit logs |

**Choice: Option C (K8s native secrets + RBAC)**  
**Rationale:** Simplest approach for MVP. With etcd encryption at rest, RBAC per namespace, and one secret per agent (no sharing), this is secure enough to start. Upgrade to Vault when compliance requires rotation audit logs or when managing >50 secrets becomes painful.

---

## Summary of Locked Decisions

| # | Area | Choice |
|---|------|--------|
| 1 | Communication | REST + Async Events (Option B) |
| 2 | Agent Deployment | Agent-per-Pod |
| 3 | Safety Architecture | Thin Orchestrator + Independent Scanners |
| 4 | Safety Placement | Before Portkey (never skip) |
| 5 | Identity | Self-hosted Keycloak |
| 6 | Namespace Model | Per team |
| 7 | Registry/Deploy Phase | Phase 1 |
| 8 | SDK Strategy | Required now, optional later |
| 9 | Database | Single Postgres, separate DBs |
| 10 | Checkpoints | Postgres (not Redis) |
| 11 | Object Storage | MinIO |
| 12 | Secrets | K8s native + RBAC |
| 13 | Frontend SDK | Option C — standard SSE contract, teams choose Vercel AI SDK or CopilotKit |
| 14 | Visual Agent Builder | Option B — standalone React + React Flow Studio app, separate from Appsmith |
| 15 | Tool & MCP Registry | Option B — first-class Tool Registry with three tool types (native, HTTP, MCP server) and decoupled auth configs |

---

## Decision 13: Frontend Agent SDK

**Context:** Agents can be user-facing (humans chatting in a UI). Need a strategy for how frontend apps consume agent responses, including streaming, tool call visualization, and approval state rendering.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: No frontend SDK (API-only)** | Teams call POST /chat, get JSON response. They build their own UI from scratch. | Maximum flexibility; but every team re-invents streaming, tool call display, loading states |
| **B: Chainlit (Python chat UI)** | Deploy Chainlit pod per agent — gives a full chat UI with zero frontend code | Simplest; but not embeddable in existing apps, less customizable, separate URL per agent |
| **C: Standard SSE contract + frontend SDK (Vercel AI SDK / CopilotKit)** | AgentShield defines a streaming event protocol (SSE). Teams use Vercel AI SDK or CopilotKit in their React apps to consume it. | Most flexible — embeds into existing apps, handles streaming/tool calls/approvals; teams must write some React code |

**Choice: Option C (SSE contract + team-chosen frontend SDK)**  
**Rationale:** Agents need to embed in existing web applications, not live in a separate chat window. A standard SSE protocol (text_delta, tool_call_start/end, approval_requested/decided, done) lets teams pick either Vercel AI SDK or CopilotKit. The backend SDK emits these events transparently — developers don't write streaming code. This keeps the platform backend-focused while giving frontend teams full control over UX.

---

## Decision 14: Visual Agent Builder (No-Code Studio)

**Context:** Non-developers (product teams, ops) need to create agent workflows without writing Python. The platform should support a visual drag-and-drop builder similar to OpenAI's agent builder, Dify, or Langflow.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: React Flow embedded in Appsmith** | Use Appsmith's custom widget to embed a React Flow canvas inside the existing admin UI | Keeps one UI; but Appsmith widget API limits canvas performance and developer experience |
| **B: Standalone Studio app (React + React Flow)** | Dedicated "AgentShield Studio" React app with React Flow for the visual canvas. Separate from Appsmith. | Clean separation (Studio = creation, Appsmith = operations). Two apps to deploy, but each focused. |
| **C: Fork Langflow** | Fork the open-source Langflow project and customize it for AgentShield (wire in safety, OPA, HITL) | Gets 70% fast; but Langflow is opinionated about LangChain internals, heavy fork maintenance burden |

**Choice: Option B (Standalone Studio — React + React Flow)**  
**Rationale:** Appsmith is the right tool for admin/ops workflows (registry tables, approval queues, dashboards) but not for a visual graph editor. A dedicated Studio app built with React Flow gives full control over the canvas UX — drag-drop nodes, edge conditions, inline property editing. Separation of concerns: Studio handles *creation* (build, test, version), Appsmith handles *operations* (deploy, approve, monitor). Both share the same Registry API and Deploy Controller backend.

**Agent creation modes (three tiers):**

| Method | Audience | Definition | Deploys as |
|--------|----------|------------|-----------|
| Studio (visual) | Product/ops teams | Drag-drop graph → JSON workflow definition | Declarative runner pod (generic image) |
| SDK declarative | Developers (simple agents) | `Agent(instructions, tools)` — OpenAI-style | Custom container image |
| SDK graph | Engineers (complex flows) | Explicit `StateGraph` + `AgentGraph` | Custom container image |

All three produce agents governed by the same safety, OPA, HITL, and tracing pipeline. The visual builder serializes to a JSON workflow definition stored in the Registry; the Deploy Controller instantiates a "declarative agent runner" pod that interprets the definition at runtime.

**Studio UX components:**
- **Node palette** — Agent, Tool (HTTP/DB/Code), Approval Gate, Router, Handoff, End
- **Canvas** — React Flow graph editor with edge conditions
- **Properties panel** — Configure selected node (instructions, model, tools, risk tier)
- **Tool configurator** — No-code HTTP/DB tool setup with test button
- **Version history** — Git-like diffs of workflow changes
- **Test/Preview** — Run the workflow in sandbox mode before deploying

---

## Decision 15: Tool & MCP Registry

**Context:** Tools are currently embedded inside agent definitions — each agent owns its tool code or HTTP config. This means no sharing across agents, no central auth management, and MCP servers require per-agent wiring. As agent count grows, the same "send Slack message" tool gets redefined a dozen times with a dozen different auth setups.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Tools embedded in agents (current)** | Tools defined inside agent source (Python `@tool`) or workflow JSON node. No sharing. | Simple start; breaks down at scale — same tool duplicated across agents, auth spread everywhere, no impact analysis before changing a tool |
| **B: First-class Tool Registry with three tool types and decoupled auth** | Tools are independent entities in the registry (native Python, HTTP, MCP server). Agents reference tools by ID. Auth configs managed separately. MCP servers registered once, tools discovered dynamically via `tools/list`. | One tool definition, many agents. Auth rotation in one place. Impact analysis before deleting. Studio populates tool picker from registry. More up-front schema, pays off at 5+ agents. |
| **C: MCP-only (no native/HTTP types)** | Require every tool to be an MCP server. Uniform protocol. | Forces every team to build an MCP server for simple HTTP tools; overhead too high for MVP. MCP is better suited as one of the three tool types, not the only one. |

**Choice: Option B (First-class Tool Registry — three types, decoupled auth)**

**Rationale:** Market research shows this is the convergent pattern across Composio (auth separate from tool definitions), OpenAI (function tools + hosted tools + remote MCP tools as distinct types), and MCP itself (server-scoped tool discovery via `tools/list`). The critical insight from Composio: separating tool *definition*, tool *authentication*, and tool *access* eliminates per-agent credential sprawl. MCP servers registered once expose their tools to all agents that need them — matching the MCP protocol's own "build once, integrate everywhere" philosophy.

**Three tool types:**

| Type | Definition | Use Case | Runtime |
|------|-----------|----------|---------|
| **Native** | Python `@tool` function, packaged with SDK | Custom business logic, DB queries | Runs in agent pod |
| **HTTP** | Method + URL + headers + body template + JSON Schema | External REST APIs, no code needed | HTTP call at runtime, auth injected |
| **MCP Server** | MCP server URL + transport + auth headers | Any MCP-compatible service (GitHub, Slack, Postgres MCP, etc.) | MCP client per agent, tools discovered via `tools/list` |

**Auth decoupled from tools (Composio pattern):**
- `AuthConfig` entity stores credential type (api_key, oauth2, bearer, mtls) and a reference to a K8s Secret — never the credential itself
- Tool definitions reference an `auth_config_id`; same tool definition, different auth per team

**MCP server lifecycle:**
- Register server → AgentShield calls `tools/list` and stores discovered tools as child records
- Subscribe to `notifications/tools/list_changed` → re-discover when server's tool list changes
- Agents reference individual MCP tools or the entire server

**Agent ↔ Tool relationship:** Many-to-many. Agents reference tools by registry ID. OPA policy auto-generates from tool risk levels at bind time.

**Phase 1 scope:** Tool CRUD API + HTTP tool type + agent-tool binding + OPA policy generation. Studio Tool Picker populates from registry in Week 7.  
**Phase 2 scope:** MCP server registration + auto-discovery + auth configs + `notifications/tools/list_changed` subscription.  
**Phase 3 scope:** Tool versioning (semver), deprecation workflow, impact analysis UI, tool catalog/marketplace.

---

## Decision 16: Agent Machine Identity Mechanism

**Context:** The requirements mandate that OPA policy be keyed on a cryptographic agent identity, not an agent name string (REQ-RT-2). A rogue pod that knows an agent's name must not be able to pass policy. Three options were evaluated for how agents acquire machine identity.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: K8s Projected SA Tokens** | Each agent gets a dedicated ServiceAccount. OPA sidecar validates projected SA tokens via TokenReview API. | Fast to ship, no new infrastructure. SDK must send token explicitly — a compromised SDK can skip it. |
| **B: Istio Waypoint + OPA ext_authz** | Istio Ambient Mesh with Waypoint Proxies intercepts all outbound traffic; Waypoint calls centralized OPA via ext_authz gRPC. Machine identity = SPIFFE SVID (automatic from Istio). | SDK bypass is structurally impossible. Istio is a significant new operational dependency; adds ~1ms per tool call. |
| **C: Istio Ambient L4 + OPA Sidecar (Hybrid)** | Istio Ambient (ztunnel only, no Waypoints) provides SPIFFE SVIDs via L4 mTLS. OPA sidecar stays but policy is keyed on SPIFFE URI read from pod cert mount by SDK. OPA bundle server replaces per-agent ConfigMaps. | SPIFFE identity without Waypoint complexity. SDK still calls OPA voluntarily, but identity is cryptographic. Migration path to Option B is clean (SPIFFE already in place). |

**Choice: Option C (Istio Ambient L4 + OPA Sidecar) for v1 — migrate to Option B as a future improvement.**

**Rationale:** Option C ships a meaningful security improvement (cryptographic SPIFFE identity replaces name strings) without adding Waypoint operational complexity in v1. The key insight: because v1 keys everything on SPIFFE URIs, migrating to Option B later requires zero policy changes — it's a pure infrastructure swap (add Waypoints, point ext_authz at centralized OPA, remove sidecars from pods, remove SDK OPA call). Option A was rejected because K8s SA tokens still trust the SDK to send them; Option B was deferred (not rejected) due to operational risk of shipping Istio + Waypoints + ext_authz in one increment.

---

## Decision 17: OPA Policy Distribution

**Context:** Today, OPA policies are generated dynamically and stored as per-agent Kubernetes ConfigMaps (`{agent_name}-policy`). This does not scale past ~100 agents (ConfigMap proliferation, no versioning, no audit trail for policy changes).

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Per-agent ConfigMaps (current)** | `policy_generator.py` creates a ConfigMap per deploy. OPA sidecar loads from `/policies/` mount. | Zero new infra. No central view. No policy versioning. SPIFFE-keyed policy requires one ConfigMap per SPIFFE URI. |
| **B: OPA Bundle Server** | Single OPA bundle containing all agents in `data.json` (keyed by SPIFFE URI) + one `policy.rego`. Sidecars pull from bundle server on startup + poll every 30s. | One policy file to audit. Versioned via git. Scales to 1000+ agents. Adds one HTTP service (bundle server). |

**Choice: Option B (OPA Bundle Server)**

**Rationale:** The SPIFFE-keyed policy model (Decision 16) requires a shared data structure across all agents (`data.registered_agents[spiffe_uri]`), which per-agent ConfigMaps cannot cleanly express. A bundle server also enables git-backed policy history and lets a security team audit the single Rego file rather than hunting across 50+ ConfigMaps. The bundle server is operationally simple — it's nginx serving a git-synced directory, or OPA in bundle-server mode.

---

## Decision 18: Publish / Grant Workflow Placement

**Context:** The requirements introduce a publish lifecycle (private → pending_review → published) and an explicit team grant model. These could live in registry-api or a new dedicated authorization service.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Extend registry-api** | Add `publish_requests`, `asset_grants`, `grant_audit` tables to existing Postgres schema. New endpoints in registry-api. | No new service. Consistent with existing agent/tool/deployment CRUD. Slightly grows registry-api's surface area. |
| **B: New authorization service** | Separate service owns grant/publish data. Registry-api calls it for visibility checks. | Cleaner separation of concerns. Adds network hop on every list/bind request. Two services to deploy and operate. |

**Choice: Option A (extend registry-api)**

**Rationale:** The grant model is tightly coupled to asset visibility, which registry-api already owns. Adding a service boundary introduces a network dependency on every list and bind operation without meaningful benefit at the current scale. The control plane load (admin approvals, grant checks) is low-frequency compared to the data plane (tool calls). Revisit when the auth model becomes complex enough to warrant isolation.

---

## Decision 19: Authorization Migration Phasing

**Context:** Option C (Istio Ambient + OPA sidecar) was chosen as the v1 implementation with Option B (Istio Waypoint + centralized OPA ext_authz) as the future target. The question was whether to phase Option B rollout: first deploy ztunnel-only (no Waypoints) as a risk-reduction step, then add Waypoints in a second deployment.

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: ztunnel first, Waypoints second** | Install Istio Ambient (ztunnel) as a standalone phase to validate Istio in the cluster before adding Waypoints. Authorization is unchanged during Phase 1. | Lower operational risk; two separate deployment events; authorization gap persists through Phase 1. |
| **B: Skip intermediate phase — go directly to Waypoints** | When migrating from Option C to Option B, deploy Waypoints directly alongside ztunnel in a single operation. Shadow mode (log-only Waypoint + running sidecars) provides the safety net instead of a separate phase. | One deployment event; shorter migration window; shadow mode de-risks the cutover instead of a separate install phase. |

**Choice: Option B — deploy Waypoints directly, no ztunnel-only intermediate phase.**

**Rationale:** The ztunnel-only intermediate phase provides operational comfort but zero authorization improvement — the same gap exists during it as exists today. By the time we migrate to Option B, Istio Ambient (ztunnel + istiod) is already running as part of Option C Phase 1. The Waypoint add-on is incremental to a cluster that already has Istio. The shadow mode step (run Waypoint in log-only mode alongside existing sidecars, compare decisions for 24–48h, then switch enforcement on) provides stronger safety guarantees than a separate phase because it directly validates decision parity before cutover, rather than relying on the absence of Istio-related failures.

---

## Decision 20: Agent Lifecycle & Eval-Gate Placement

**Context:** The playground evaluates an agent by streaming from its live pod, so evaluation *requires* a running deployment. But the deploy pre-flight gate (`deployments.py`) requires `version.eval_passed=True`. That is a chicken-and-egg: you cannot evaluate before deploying, yet deploy is gated on eval. In practice the gate is a rubber stamp — Studio's `DeployAgentPage` hardcodes `eval_passed: true` at version creation. Question: where should the eval gate sit in the `create → deploy → evaluate → iterate → publish` loop?

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Keep eval gate on deploy (status quo)** | Require `eval_passed` before any deploy. | Forces the rubber stamp — you can't evaluate until deployed, so `eval_passed` gets set before there's anything to base it on. Gate enforces nothing. |
| **B: Move eval gate to publish; deploy to an ungated sandbox** | Deploy-to-sandbox needs only tool grants. Evaluate against that sandbox pod. `eval_passed` (auto-set from a passing `EvalRun`) gates **publish to catalog**, alongside the existing critical-risk + admin-approval checks. | One coherent loop; the gate sits after the thing it measures; requires introducing a sandbox environment and re-homing the gate. |

**Choice: Option B — eval gate moves from deploy to publish; deploy-to-sandbox is ungated.**

**Rationale:** A gate must sit *after* the thing it measures. AgentShield's governance goal is to keep unevaluated agents away from **users** — and the catalog (publish) is what reaches users, not an isolated sandbox pod. Gating publish on `eval_passed` protects the thing that matters and dissolves the chicken-and-egg. `eval_passed` should be **auto-set from a passing `EvalRun`**, never a manual or hardcoded flag. Canonical loop: `create → deploy (sandbox, ungated) → evaluate in playground → iterate → eval_passed flips automatically → publish (eval-gated)`.

**Implications:**
- Remove the `eval_passed` pre-flight check in `deployments.py` (or scope it to `environment=production` only); keep sandbox/staging ungated.
- Add `eval_passed` (+ adversarial) check to `publish_agent` in `agents.py`.
- Auto-set `version.eval_passed` when an `EvalRun` passes (the missing wire).
- Studio: stop hardcoding `eval_passed: true` in `DeployAgentPage`; relabel "Deploy to Production" (it's a sandbox test deploy, not production).

---

## Decision 21: Execution Models & Memory — Design Revisions

**Context:** `docs/design/execution-models-and-memory.md` (DRAFT, unimplemented) defines how deployed agents behave **in production** — scheduled/cron, event/webhook, durable multi-step with HITL, and cross-session memory. Review found two structural flaws and one governance gap, plus an oversized single-phase scope. These revisions are folded into the spec (now "DRAFT v2").

**Corrections:**

1. **Split `execution_model` into two orthogonal fields.** The spec itself admits trigger and execution shape are independent ("scheduled runs are otherwise identical to reactive or long-running"). Replace the four-value enum with `execution_shape` (`reactive` | `durable`) + composable **triggers** (`manual`/`api`/`schedule`/`webhook`, many per agent). The old four "models" become points in a shape × trigger grid.

2. **Merge `agent_runs`, don't duplicate it.** A table named `agent_runs` already exists as an observability/cost log. The spec's central primitive shares the name with a different schema. Reconcile by `ALTER`-ing the existing table to add orchestration fields (`trigger_type`, `trigger_payload`, `thread_id`, `parent_run_id`, `run_by`, `team`, `error_message`; widen `status`) — one run spine, not two tables.

3. **Constrain memory to preserve session-scoped PII.** AgentShield's PII de-anonymization is session-scoped; cross-session `fact`/`knowledge` memory would leak it. Rule: memory writes pass through the safety proxy; only session `message_history` may hold de-anon PII (TTL'd); cross-session facts store the anonymized/tokenized form. Prerequisite for the memory build phase. (Spec §5.8.)

4. **Resequence the build by risk, event-driven last.** Order: reactive + run spine → durable + Approvals Inbox (reuses existing HITL) → memory (after rule #3) → scheduled → event-driven last. The public webhook gateway is the biggest new attack surface and needs a threat model (rate limiting, replay protection, payload sanitization) before build.

**Rationale:** A gate/model must match reality: trigger and shape are orthogonal, so they get separate fields; a second `agent_runs` would fork the run history; memory that ignores the PII model is a covert channel that defeats the platform's core guarantee; and the highest-risk surface (public ingress) ships last, after the threat model.

**Scope note:** Phases 3a–3e are currently 0% implemented — these are design corrections to the DRAFT before Phase 3 starts, not changes to shipped code.

---

## Decision 22: Workflow Redefinition & the Executable Abstraction

**Context:** The execution-modes design (Decisions 20–21, and the three design docs) was agent-centric, but a **collection of agents working together** — a "workflow" in the user's mental model — is equally something that must be triggered, scheduled, run durably with HITL, evaluated in the playground, and operated in production. The docs missed it. Separately, "workflow" is overloaded: today it means *a single declarative agent's canvas graph* (`workflows` table, backing `agent_versions.workflow_id`).

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Separate multi-agent doc + stack** | Design workflows in their own doc, implement as their own feature. | Reinforces "two features → two implementations"; risks duplicating the run spine, triggers, memory, playground, and production surfaces — messy rework. |
| **B: One executable abstraction; Workflow = composite kind** | `executable = Agent \| Workflow`. Both carry the same modes / triggers / memory / runs / playground / production / integrations; the **only** difference is orchestration (a Workflow produces a run tree). Weave into the existing docs; the new orchestration design gets one backend-spec section. | Shared substrate implemented once; orchestration engine is the only branch point. Requires redefining "Workflow" and renaming the old canvas-graph concept. |

**Choice: Option B — one executable abstraction. Agent (atomic) and Workflow (composite = collection of agents) are two kinds of one executable.**

**Rationale:** ~90% of the surface is shared (triggers, execution shape, memory, the `agent_runs` spine, playground evaluation, production operation, publish gate, integrations). The single genuine difference is orchestration, which surfaces as a **run tree** — and `agent_runs.parent_run_id` + the shared `StepTracker` already model exactly that. A separate doc/stack would fight the abstraction and invite the duplication/rework we want to avoid. Documents shape implementation: keeping it in the shared docs says "one run spine, one trigger system, one memory, one playground, one production surface; orchestration is the only place code branches on kind."

**Naming:** "Workflow" is **redefined** to mean the composite executable — matching Microsoft Agent Framework and Anthropic, which both use *Agents + Workflows* as their two top-level categories (validated by industry survey). The current canvas-graph `workflows` / `workflow_versions` tables are renamed **`agent_graphs` / `agent_graph_versions`** (the authoring definition of one declarative agent; `agent_versions.workflow_id → agent_graph_id`).

**Where captured:** backend spec §2.6 (abstraction) + §4.5 (composite design, orchestration patterns, run tree, rename); thin "run-tree granularity" deltas in the playground and production experience docs. No separate doc.

**Confirmed 2026-07-03:** canvas rename name **"Agent Graph"** accepted; **reactive workflows allowed** (not always durable; default `durable`); trigger-targeting model (nullable `workflow_id` vs polymorphic `executable_id`) **deferred** — revisit before Phase 6 (tracked in `todo-workflow-executable`).

## Decision 23: Failure-Alert Transport (Phase 8)

**Context:** Phase 8 adds failure alerting for triggered (scheduled/event-driven) agent runs. The platform already ships a `send_email` capability as an **agent-facing tool** (registered, `risk=high` → HITL-gated in OPA) and a separate control-plane `notify_slack` path for approval notifications. Question raised: should failure alerts reuse the `send_email` tool instead of registry-api implementing SMTP directly?

| Option | Description | Trade-off |
|--------|-------------|-----------|
| **A: Control-plane SMTP (chosen)** | `alerting.py` in registry-api sends email directly from the run-completion path. | Fires even when the agent is dead/crashed (only needs registry-api + a mail relay); not governance-gated so alerts actually send; platform is the correct sender. Cost: registry-api owns SMTP creds — a second email integration alongside `send_email`. |
| **B: Reuse the `send_email` agent tool** | Route failure alerts through the risk=high governed tool. | ❌ HITL-gated (every alert waits for human approval); ❌ depends on the agent tool-execution stack (python-executor/OPA sidecar) — correlated failure if the run failed *because* that subsystem is down; ❌ attributes a platform alert to the agent's SA (audit/governance mismatch). |
| **C: Shared internal notification service** | One internal notifier owns transport + channels (email/Slack/webhook/PagerDuty) + templates + dedup; control-plane alerters call it directly, `send_email` fronts it as the governed agent door. | The DRY win without B's problems, but more upfront work. Deferred until a 2nd channel/source justifies it. |

**Choice: Option A.** Keep the inlined SMTP transport (`SMTP_HOST/PORT/FROM`, log-only fallback; chart-wired via `registry-api.smtp.*`).

**Rationale:** A failure alert is a control-plane signal that must fire *precisely when the agent is broken* and must not be human-gated — the exact two properties Option B destroys. `send_email` (risk=high, HITL) is correctly reserved for the different use case where an agent's *job* is to send mail. The duplication concern is real but small today; when a second channel or alert source appears, evolve toward Option C (fold `alerting.py` + `notify_slack` onto one notifier) rather than routing operator alerts through the agent tool.

---

## Decision 24: Builder Unification & Full Workflow Orchestration

**Context:** UX review found two overlapping graph builders ("Agent Graphs" = inline-defined agents compiled to one declarative agent; "Workflows" = composite of existing agents → run tree) and three real gaps in the composite builder: it couldn't create new agents inline, its edges were never persisted (`serializeCompositeWorkflow` written but never called; `store.setEdges([])` wiped them on load), it lacked a per-node config / edge-condition panel, and only `sequential` orchestration ran (supervisor/handoff 422-rejected). Also the create-agent wizard couldn't express scheduled/event-driven agents and `createTrigger` was wired to no UI.

**Choice (extends Decision 22):**
- **Hide "Agent Graphs" from the nav** (routes remain, reachable by URL); the composite **Workflow builder is the single graph builder**, absorbing the Agent Graph canvas capabilities (per-node config panel + edge-condition editor) and adding "**existing agent OR new inline agent**" (inline = a real, shareable agent via `POST /agents`).
- **Edges are first-class**: new `workflow_edges` table (migration 0029; `source_agent_id → target_agent_id`, `condition`) — chosen over `workflow_members.routing` JSONB because an edge is a cross-member construct. Edges persist on save and reload (closing the wipe-on-load bug).
- **All four orchestration modes run** (`workflow_orchestrator.orchestrate()`): sequential (edge chain), conditional (edge conditions evaluated by the reused `filter_engine` predicate DSL — no `eval`), supervisor (`role=supervisor` member routes; `max_iterations` cap), handoff (agents signal the next hop). Orchestrator stays in registry-api (the declarative-runner path is inert).
- **Create-agent wizard = 4-way adaptive type picker** (Reactive/Durable/Scheduled/Event-driven) → shape + optional trigger; shape defaults reactive for scheduled/event (flip in Settings). Trigger creation also added to **Settings**. `AgentTriggerResponse` returns `webhook_url` once on create.

**Deferred (honest ledger):** per-node **tool/skill** re-editing on the workflow canvas (edit on the agent's page — `AgentUpdate` has no tool-rebind field); **workflow-level triggers** (the `agent_triggers.workflow_id` FK exists, no UI yet); node-position persistence. Node-config editing is gated by `is_inline` (existing-agent nodes are read-only to avoid mutating a shared agent from a workflow).

**Addendum (type-aware instructions + per-schedule input, 2026-07-06):** the create-agent wizard now swaps the **instructions template by agent type** — reactive/durable stay conversational; **scheduled** = an autonomous parameterized-worker template (input is a JSON job spec, no user, deliver via tools, be idempotent); **event-driven** = a parse-the-event-JSON template (untrusted payload, at-least-once idempotency). This fixes the chat-only template that made headless runs stall. Separately, a schedule trigger now carries an optional **`input_payload` JSONB** (migration 0030) — the per-job parameters — because an agent is a *reusable capability* while a schedule is a *concrete job*, and one agent can have many schedule triggers (each with different params). `internal.py` resolves a scheduled run's input from the trigger's `input_payload` by `trigger_id` (single source of truth; the scheduler still sends only `trigger_id`). Payload is free-form JSON (agent parses it; no schema enforcement — documented gap). Suite 32 (backend) + wizard Vitest/Playwright cover it. registry-api 0.2.62 / studio 0.1.46.

**Addendum (composable member filter + workflow-level triggers + production HITL resume, 2026-07-06):** three changes shipped in implementation pass #3.

**Composable member filter.** `GET /api/v1/agents/?composable=true` returns only agents that have no enabled schedule or webhook trigger. The workflow builder's Add-Agent modal uses this filter so workflow members are pure capabilities invoked by the orchestrator and won't double-fire. The Create-New tab in that modal restricts inline agent creation to reactive/durable shapes — no new agent type was introduced; members are ordinary agents.

**Workflow-level triggers (G-4 resolved).** Composite workflows are now triggerable like agents. New CRUD at `/api/v1/workflows/{id}/triggers` (schedule + webhook) stores rows with `agent_triggers.workflow_id` set and `agent_id NULL`. The scheduler UNION-queries agent and workflow trigger rows and dispatches both via `POST /internal/runs/start` with `workflow_id`. The event-gateway exposes `POST /hooks/workflow/{name}/{token}` for workflow webhooks. `_start_workflow_run` resolves the run input from `trigger.input_payload` when no explicit payload is sent — mirroring the agent path. Migration 0031 adds nullable `agent_events.workflow_id`. Studio: **Triggers** button + `WorkflowTriggersPanel` in the workflow builder; `execution_shape` (reactive/durable) selector in the workflow Save modal.

**Production single-agent HITL resume (bug fix).** `PATCH /api/v1/approvals/{id}` (the production decide path) now best-effort fire-and-forget POSTs to the agent pod `/resume/{thread_id}` after recording the decision. Previously only the playground path did this, leaving a production approval with the LangGraph thread suspended. Errors are swallowed so a failed resume never fails the decision. Mirrors `approval_timeout_worker.py`.

**Deferred (honest ledger):** the pausable workflow-HITL orchestrator — a member agent's approval pausing/resuming the whole workflow run tree — is not yet implemented. The orchestrator still dispatches member agents one-shot via `/chat`, and the Safety Orchestrator (`require_approval` via OPA) is disabled in this deployment, so end-to-end workflow-level HITL cannot be verified; it gets its own pass. Also: adding a schedule/webhook trigger to an agent already in a workflow is not blocked at the data layer — only the add-time composable filter guards it.

---

## Decision 25: Platform RBAC — Global Roles + Artifact-Scoped Roles

**Date:** 2026-07-06

**Context:** The platform had three Keycloak realm roles (`admin`, `operator`, `viewer`) stored in `user_team_assignments` but with no backend enforcement. HITL routing showed everything to everyone on a team. Production agent management had no delegation — only platform-admin could act. The role model needed to support both platform-wide capabilities and per-artifact authority, with grants targeting individual users or entire teams.

**Decision:** Two-tier RBAC: **global roles** (platform-wide capability) + **artifact-scoped roles** (per-agent/workflow authority).

### Global Roles (mutually exclusive per user, stored in `user_team_assignments.role`)

| Role | What it grants |
|------|---------------|
| **platform-admin** | Full platform access — manage users/teams, approve publish requests, configure approval authority, deploy to production, assign artifact-scoped roles |
| **contributor** | Create agents/workflows, develop in sandbox (playground, test runs), submit for publish, manage tools/skills. Cannot manage users or deploy to production (unless also agent-admin on the artifact). |
| **viewer** | Read-only — browse catalog, view run history. Cannot use playground, create, or modify anything. |

### Artifact-Scoped Roles (many-per-user, stored in new `artifact_role_grants` table)

| Role | Scope | What it grants |
|------|-------|---------------|
| **agent-admin** | Per agent or workflow | Suspend, resume, scale replicas, upgrade version, rollback, edit runtime config (env vars, LLM keys), delete deployment. Can grant `agent-admin` and `approver` to other users/teams within their artifact scope. |
| **approver** | Per agent or workflow | Receives HITL approval requests for that agent/workflow. Approves all HITL regardless of which tool triggered it (tool-level granularity is a future improvement). |

### Grant Rules

- **Grantee is polymorphic**: a grant targets either a `user_sub` or a `team_name` (column: `grantee_type` = `user` | `team`, `grantee_id`).
- **Users/teams can hold multiple roles**: a user can be a global `contributor` + `agent-admin` on Agent X + `approver` on Agent Y.
- **Creator auto-grant**: when a contributor creates an agent or workflow, they automatically receive `agent-admin` on it.
- **Production deploy**: only `platform-admin` or a user with `agent-admin` on the artifact can deploy to production. Contributors deploy to sandbox only.
- **Delegation**: `agent-admin` can grant both `agent-admin` and `approver` within their artifact scope. `platform-admin` can grant any scoped role on any artifact.

### Data Model

**Existing table — `user_team_assignments`** (rename `role` values: `admin` → `platform-admin`, `operator` → `contributor`):
```
user_sub, team_name, role (platform-admin | contributor | viewer), assigned_by, assigned_at
```

**New table — `artifact_role_grants`** (migration 0030):
```
id              UUID PK
grantee_type    VARCHAR(16)  -- 'user' or 'team'
grantee_id      VARCHAR(255) -- user_sub or team_name
artifact_type   VARCHAR(32)  -- 'agent' or 'workflow'
artifact_id     UUID         -- FK to agents.id or workflows.id
role            VARCHAR(32)  -- 'agent-admin' or 'approver'
granted_by      VARCHAR(255) -- user_sub of granter
granted_at      TIMESTAMP
revoked_at      TIMESTAMP NULL
```

**Relationship to `asset_grants`**: `asset_grants` controls **visibility** (can a team see/bind a published asset). `artifact_role_grants` controls **authority** (can a user/team manage or approve for a specific artifact). They are independent — having visibility does not imply authority, and vice versa.

### Permission Check Logic (pseudocode)

```
def has_artifact_role(user, artifact_type, artifact_id, role):
    # Direct user grant
    if artifact_role_grants.exists(grantee_type='user', grantee_id=user.sub,
                                    artifact_type, artifact_id, role, revoked_at=NULL):
        return True
    # Team grant
    if artifact_role_grants.exists(grantee_type='team', grantee_id=user.team,
                                    artifact_type, artifact_id, role, revoked_at=NULL):
        return True
    return False

def can_deploy_to_production(user, artifact):
    return user.role == 'platform-admin' or has_artifact_role(user, artifact, 'agent-admin')

def can_approve_hitl(user, approval):
    return (user.role == 'platform-admin'
            or has_artifact_role(user, approval.artifact_type, approval.artifact_id, 'approver'))
```

### HITL Routing Change

Production HITL queue filtering changes from "show all pending to the team" to "show pending approvals where the user has `approver` on that agent/workflow (direct or via team grant), or user is `platform-admin`."

### Resolved Design Gaps

| Gap | Resolution |
|-----|-----------|
| Who can deploy to production? | `platform-admin` or `agent-admin` on the artifact. Contributors deploy to sandbox only. |
| Creator ownership | Creator auto-receives `agent-admin` at creation time. |
| Agent-admin delegation scope | `agent-admin` can grant both `agent-admin` and `approver` within their artifact scope. |
| Approver granularity | Scoped per agent/workflow — approves all HITL for that agent regardless of tool. |
| Agent-admin operations | Suspend, resume, scale, upgrade, rollback, edit runtime config, delete deployment. |
| Viewer + Playground | Playground access requires `contributor` globally. Artifact-scoped roles alone don't grant playground access. |
| Deploy gate (mandatory?) | Advisory for now. Creator already has `agent-admin` auto-grant. |
| Revocation cascading | Orphan-keep — revoking User A's `agent-admin` does not cascade to grants User A made. |
| Relationship to asset_grants | Separate tables, separate concerns (visibility vs. authority). |

### Future Improvements (Deferred)

- **Tool-level approver granularity**: scope `approver` to a specific `(agent, tool)` pair, not just the agent.
- **Mandatory deploy gate**: block production deploy until at least one `agent-admin` is assigned (beyond the creator).
- **Revocation cascading**: option to cascade-revoke all grants made by a revoked `agent-admin`.
- **Role audit log**: track who granted/revoked what, when (currently `granted_by` + `granted_at` only; no history on revoke-and-re-grant).

---

## Summary of Locked Decisions

| # | Area | Choice |
|---|------|--------|
| 1 | Inter-service communication | REST + async events (Postgres LISTEN/NOTIFY) |
| 2 | Agent deployment model | Agent-per-Pod (K8s Deployment per agent) |
| 3 | Safety service architecture | Thin orchestrator + independent scanners |
| 4 | Safety placement | Before Portkey (never skip scanning on cache hits) |
| 5 | Identity provider | Self-hosted Keycloak |
| 6 | Namespace isolation | Single namespace, NetworkPolicy per agent pod |
| 7 | Registry UI | Appsmith (Phase 1), Studio (Phase 2+) |
| 8 | SDK coupling | Required now, contract-based later |
| 9 | Database | Single Postgres + PgBouncer |
| 10 | Checkpoint storage | Postgres (not Redis) |
| 11 | Object storage | MinIO |
| 12 | Secret management | K8s Secrets + RBAC |
| 13 | Frontend agent SDK | TypeScript SDK |
| 14 | Visual agent builder | Studio (React + ReactFlow) |
| 15 | Tool registry | First-class Tool Registry (3 types: native, HTTP, MCP) |
| 16 | Agent machine identity | Istio Ambient L4 (SPIFFE SVIDs) + OPA sidecar; migrate to Waypoint ext_authz in future |
| 17 | OPA policy distribution | OPA Bundle Server (nginx + git-sync); replace per-agent ConfigMaps |
| 18 | Publish/grant workflow | Extend registry-api (no new service) |
| 19 | Authorization migration phasing | Option C now; migrate directly to full Option B (Waypoints + centralized OPA). No ztunnel-only intermediate phase. |
| 20 | Agent lifecycle & eval-gate placement | Deploy-to-sandbox is ungated; eval gate moves to **publish**; `eval_passed` auto-set from a passing EvalRun (not manual/hardcoded). |
| 21 | Execution models & memory design revisions | Split `execution_model` → `execution_shape` + composable triggers; merge (not duplicate) `agent_runs`; memory constrained to preserve session-scoped PII; build resequenced with event-driven last. |
| 22 | Workflow redefinition & executable abstraction | `executable = Agent \| Workflow`; Workflow = composite (collection of agents) on the shared substrate; only orchestration differs (run tree); old canvas "workflow" → "agent graph". Woven into existing docs, no separate doc. |
| 23 | Failure-alert transport | Control-plane SMTP in registry-api (`alerting.py`), NOT the risk=high HITL-gated `send_email` agent tool; must fire when the agent is dead and must not be gated. Shared notification service (Option C) deferred until a 2nd channel/source appears. |
| 24 | Builder unification & full orchestration | Hide "Agent Graphs" nav; composite Workflow builder is the single builder (per-node config + edges + inline/existing agents); `workflow_edges` table (0029); all four orchestration modes run (sequential/conditional/supervisor/handoff); 4-way create-agent wizard + trigger-create UI in Settings. Deferred: canvas tool-editing, workflow-level triggers. |
| 25 | Platform RBAC | Two-tier: global roles (`platform-admin`, `contributor`, `viewer`) + artifact-scoped roles (`agent-admin`, `approver`) in `artifact_role_grants` table. Grants target users or teams. Creator auto-gets `agent-admin`. Production deploy requires `platform-admin` or `agent-admin`. HITL routed to scoped `approver` holders. Deferred: tool-level approver granularity, mandatory deploy gate, revocation cascading. |
