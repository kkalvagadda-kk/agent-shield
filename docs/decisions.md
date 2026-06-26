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
