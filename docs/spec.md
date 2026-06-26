# AgentShield — Architecture Specification

**Status**: PROPOSED — Pending team review  
**Date**: 2026-06-24  
**Author**: Karthik + Claude  
**Version**: 1.1.0

## Problem Statement

Engineering teams deploying AI agents have no consistent way to enforce safety guardrails, govern high-risk actions, or observe what agents are doing. Each team rolls their own (or skips it entirely). AgentShield provides a self-hosted platform on Kubernetes that standardizes safety scanning, policy enforcement, human approval gates, observability, and agent deployment — with zero SaaS dependencies.

---

## User Scenarios & Testing

### User Story 1 — Developer Deploys a New Agent via SDK (Priority: P1)

A developer writes an agent using the SDK (declarative `Agent()` or full `StateGraph`), pushes code, CI validates it, and deploys through the Registry UI.

**Why this priority**: Without a working deploy path, nothing else matters — no safety scanning, no approvals, no observability.

**Independent Test**: Deploy a single "echo" agent that accepts input and returns it. Verify it's reachable and traced.

**Acceptance Scenarios**:

1. **Given** agent code passes CI (lint, tests, eval), **When** developer clicks Deploy in Appsmith, **Then** agent pod is running and reachable within 60s.
2. **Given** agent eval fails in CI, **When** developer attempts to deploy that version, **Then** deploy button is disabled (version not marked eval-passed).
3. **Given** an agent is live, **When** developer clicks Rollback, **Then** previous version is serving within 60s.

---

### User Story 1b — Product Team Creates Agent via Visual Builder (Priority: P2)

A product/ops team member creates an agent workflow using drag-and-drop in AgentShield Studio — no Python code required.

**Why this priority**: Unlocks agent creation for non-developers; critical for scaling adoption beyond engineering.

**Independent Test**: Build a two-node agent (lookup → respond) entirely in the Studio UI, deploy it, send a request, verify response.

**Acceptance Scenarios**:

1. **Given** a user opens Studio, **When** they drag an Agent node + HTTP Tool node, connect them, fill in instructions/endpoint, and click Deploy, **Then** agent is live within 90s.
2. **Given** a visual workflow exists, **When** user edits instructions and redeploys, **Then** new version serves within 60s (no container build needed).
3. **Given** a visual agent is deployed, **When** a high-risk tool is called, **Then** the same HITL approval flow fires as for SDK-built agents.

---

### User Story 2 — Safety Scanning Blocks Injection (Priority: P1)

A user sends a prompt injection attack. The platform detects and blocks it before the agent processes it.

**Why this priority**: Core safety guarantee — without this, the platform has no reason to exist.

**Independent Test**: Send a known injection payload (e.g., "ignore previous instructions and..."). Verify it's blocked and logged.

**Acceptance Scenarios**:

1. **Given** a request with injection payload, **When** it hits the Safety Orchestrator, **Then** it's blocked (HTTP 422), logged to Langfuse, and never reaches the agent.
2. **Given** a request with PII (SSN, credit card), **When** scanned by Presidio, **Then** PII is redacted before agent sees it, mapping stored for de-anonymization.
3. **Given** Safety Orchestrator's LLM Guard pod is down, **When** a request arrives, **Then** request is blocked (fail-closed) and platform team is alerted.

---

### User Story 3 — High-Risk Action Routes to Approval (Priority: P1)

An agent attempts to cancel an order (high-risk tool). The platform pauses the agent and routes the action to a human reviewer.

**Why this priority**: Governance requires human oversight before irreversible actions.

**Independent Test**: Trigger a high-risk tool call, verify agent pauses, approve via Appsmith, verify agent resumes.

**Acceptance Scenarios**:

1. **Given** agent calls `cancel_order` (risk=high), **When** OPA classifies it, **Then** agent pauses (interrupt), approval record created in Postgres, reviewer notified via Slack.
2. **Given** reviewer approves, **When** decision is written to DB, **Then** agent resumes and executes the tool within 5s.
3. **Given** no reviewer responds within 30 minutes, **When** timeout fires, **Then** approval auto-rejects, agent resumes with denial.

---

### User Story 4 — Platform Engineer Installs AgentShield (Priority: P1)

A platform engineer deploys the entire platform from scratch using Helm on an existing Kubernetes cluster.

**Why this priority**: Platform must be installable by one person in a day, not a multi-week project.

**Independent Test**: `helm install agentshield ./charts` on a fresh cluster, verify all pods healthy.

**Acceptance Scenarios**:

1. **Given** a K8s cluster (1.27+), **When** `helm install` runs with default values, **Then** all platform pods are Running within 10 minutes.
2. **Given** a component pod crashes, **When** Kubernetes restarts it, **Then** service recovers without manual intervention.
3. **Given** the platform is running, **When** engineer runs `helm upgrade` with new chart version, **Then** rolling update completes with zero downtime.

---

### User Story 5 — Security Auditor Reviews Compliance (Priority: P2)

A security auditor needs to prove that all high-risk actions were approved by a human, and that policies are enforced consistently.

**Why this priority**: Compliance is a must-have for production use, but can follow initial deployment.

**Independent Test**: Query the approvals table for the last 30 days, verify every high-risk action has a decision record.

**Acceptance Scenarios**:

1. **Given** 100 high-risk actions occurred this month, **When** auditor queries approvals table, **Then** each has a record with reviewer, decision, timestamp, and notes.
2. **Given** OPA denied a tool call, **When** auditor checks decision logs, **Then** deny reason includes the specific policy rule that fired.

---

### Edge Cases

- What happens when an agent calls a tool not in its allowlist? → OPA denies, event logged, agent receives deny response.
- What happens when two reviewers try to approve the same action? → First write wins (optimistic lock on status column), second gets a conflict error.
- What happens when Postgres is unreachable? → Agent cannot checkpoint or create approvals. Health probe fails, pod marked NotReady, traffic stops routing to it.
- What happens when an agent's container image doesn't exist? → Deploy Controller validates image exists before creating Deployment. Deploy fails with clear error in UI.

---

## Requirements

### Functional Requirements

| ID | Priority | Requirement | Acceptance Criteria |
|----|----------|-------------|-------------------|
| FR-001 | P0 | Register agent with name, team, tools, risk levels | Agent visible in registry within 30s |
| FR-002 | P0 | Deploy agent version (full rollout) | K8s Deployment live within 60s |
| FR-003 | P0 | Rollback to previous version | Previous version serving within 60s |
| FR-004 | P0 | Scan all inputs for injection + PII | Every request passes Safety Orchestrator |
| FR-005 | P0 | Scan all outputs for PII leakage | Output passes through scanners before user sees it |
| FR-005a | P0 | De-anonymize PII placeholders in tool call args, gated by OPA allow_deanonymize policy per tool | Tool receives real PII value; LLM context retains placeholder; OPA deny blocks substitution |
| FR-006 | P0 | Evaluate OPA policy before every tool call | Allow/deny within 5ms |
| FR-007 | P0 | Route high-risk actions to approval queue | Agent pauses, record created, reviewer notified |
| FR-008 | P0 | One-click approve/reject in Appsmith | Decision stored, agent resumes within 5s |
| FR-009 | P0 | Auto-reject on timeout (30min default) | Agent resumes with denial, event logged |
| FR-010 | P0 | Full trace capture for every request | Trace in Langfuse within 10s of completion |
| FR-011 | P0 | Run eval suite in CI on every PR | PR blocked if assertions fail |
| FR-012 | P0 | Registry UI: list agents, versions, deploy buttons | Functional Appsmith dashboard |
| FR-013 | P1 | Canary deployment with traffic percentage | Envoy routes configured split |
| FR-014 | P1 | Slack notification on pending approval | Webhook within 10s |
| FR-015 | P1 | Cost tracking per agent/team/model | Visible in Langfuse dashboard |
| FR-016 | P1 | Weekly Garak vulnerability scan | Scheduled CI, alert on findings |
| FR-017 | P1 | Chunked scanning for inputs >512 tokens | Each chunk scanned independently |
| FR-018 | P2 | LLM-as-Judge automated scoring | Async evaluator on all traces |
| FR-019 | P2 | Time-based policy constraints | Rego rules reference current time |

### Non-Functional Requirements

| Attribute | Target | How Achieved |
|-----------|--------|-------------|
| Gateway latency | <5ms p99 overhead | Envoy (compiled C++, in-process JWT validation) |
| Safety scan latency | <200ms p99 | Parallel fan-out to 3 scanners, slowest wins |
| OPA evaluation | <5ms p99 | In-pod sidecar, pre-compiled Rego to Wasm |
| Platform availability | 99.9% | HA Postgres (sync replica), multi-replica stateless services |
| Concurrent agents | 50+ | Agent-per-Pod, independent HPA |
| Throughput | 5,000 req/s aggregate | Horizontal scaling of gateway + safety pods |
| Trace retention | 90 days | ClickHouse columnar storage (~1GB per 1M traces) |
| Approval retention | Indefinite | Append-only Postgres table, no DELETE policy |
| Deployment rollback | <60s | Image tag swap + RollingUpdate (maxUnavailable=0) |
| Zero data loss on restart | Guaranteed | LangGraph checkpoints in Postgres, sync replication |
| Postgres direct connections | Max 1 per agent pod (for LISTEN/NOTIFY) | Postgres max_connections must account for: PgBouncer pool (100) + agent pods × 1 direct connection |

### Integration Points

| System | Direction | Protocol | Purpose |
|--------|-----------|----------|---------|
| LLM Providers (OpenAI, Anthropic, etc.) | Outbound | HTTPS | Agent inference calls (via Portkey) |
| External Tools (Order API, Email, etc.) | Outbound | HTTPS/gRPC | Agent tool execution |
| Slack | Outbound | Webhook | Approval notifications |
| Container Registry | Inbound/Outbound | HTTPS | Image storage for agent builds |
| Git (CI system) | Inbound | Webhook | Version registration, eval results |

### Key Entities

| Entity | Description | Key Attributes | Relationships |
|--------|-------------|---------------|---------------|
| Agent | A registered AI agent | name, team, description, status | Has many versions, deployments, approvals; references many tools |
| AgentVersion | A specific build of an agent | image_tag, tools[], eval_passed | Belongs to agent, deployed as deployment |
| Deployment | An active deployment of a version | status, replicas, canary_percent | Links agent to version in an environment |
| Approval | A human decision on a high-risk action | action, risk_level, status, reviewer, session_id, opa_decision_id | Belongs to agent; links to trace; session_id references pii_mappings for reviewer de-anonymization; opa_decision_id links to OPADecision that triggered it |
| OPADecision | Audit log entry for every OPA policy evaluation | id, agent_name, tool_name, decision (allow/deny/require_approval), policy_version, deny_reason, thread_id, trace_id | Written on every tool-call authorization; 1:0..1 with Approval (every require_approval decision has a linked Approval row) |
| OPAPolicy | Auto-generated policy for an agent | tool_allowlist, risk_classification, allow_deanonymize_tools (list) | 1:1 with agent; derived from agent-tool bindings |
| Tool | A reusable, independently-managed tool definition | name, type (native/http/mcp), input_schema, risk_level, auth_config_id | Many-to-many with agents; belongs to optional auth config |
| AuthConfig | Credential configuration decoupled from tool definition | type (api_key/oauth2/bearer/mtls), k8s_secret_ref, owner_team | Referenced by many tools |
| MCPServer | A registered MCP server whose tools are auto-discovered | server_url, transport, auth_config_id, status | Has many tools (discovered); referenced by agents |

---

## Architecture

### System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────────┐
│ namespace: agentshield-platform                                                   │
│                                                                                   │
│   ┌────────────┐  ┌────────────────┐  ┌─────────────┐  ┌───────────────────┐   │
│   │   Envoy    │  │Safety Orchest. │  │Portkey OSS  │  │    Keycloak       │   │
│   │  Gateway   │  │  (fan-out)     │  │(LLM routing)│  │   (OIDC IdP)      │   │
│   └─────┬──────┘  └───────┬────────┘  └──────┬──────┘  └───────────────────┘   │
│         │                  │                   │                                  │
│   ┌─────┴──────────────────┴───────────────────┴──────────────────────────┐     │
│   │  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────────┐  │     │
│   │  │LLM Guard │  │ Presidio │  │   NeMo   │  │        OPA           │  │     │
│   │  │(injection)│  │  (PII)   │  │  (YARA)  │  │  (sidecar/pod)      │  │     │
│   │  └──────────┘  └──────────┘  └──────────┘  └──────────────────────┘  │     │
│   └───────────────────────────────────────────────────────────────────────┘     │
│                                                                                   │
│   ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────────────────┐      │
│   │ Appsmith │  │ Registry │  │  Deploy  │  │       Langfuse            │      │
│   │   (UI)   │  │   API    │  │Controller│  │  (web + worker + CH)      │      │
│   └──────────┘  └──────────┘  └──────────┘  └───────────────────────────┘      │
│                                                                                   │
│   ┌──────────────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────┐       │
│   │ Postgres 16 (HA)     │  │  Redis 7 │  │ClickHouse│  │    MinIO     │       │
│   │ + PgBouncer          │  │          │  │          │  │  (S3-compat) │       │
│   └──────────────────────┘  └──────────┘  └──────────┘  └──────────────┘       │
└─────────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────────┐
│ namespace: agents-{team}  (one per team, NetworkPolicy: default-deny)            │
│                                                                                   │
│   ┌─────────────────────────────────────────────────────────────────────────┐   │
│   │  Agent Pod                                                               │   │
│   │  ┌──────────────────────┐  ┌──────────────────┐                        │   │
│   │  │  Agent Container      │  │  OPA Sidecar     │                        │   │
│   │  │  (SDK + LangGraph)    │  │  (policy bundle) │                        │   │
│   │  └──────────────────────┘  └──────────────────┘                        │   │
│   └─────────────────────────────────────────────────────────────────────────┘   │
│                                                                                   │
│   Allowed egress only:                                                            │
│   → safety-orchestrator:8080                                                      │
│   → pgbouncer:5432 (queries via connection pool)                                  │
│   → postgresql-primary:5432 (LISTEN/NOTIFY direct — one conn per pod)            │
│   → langfuse-web:3000 (traces)                                                   │
│   → internal tool APIs (10.0.0.0/8:443)                                          │
│   → kube-dns:53                                                                   │
└─────────────────────────────────────────────────────────────────────────────────┘
```

### Components

| Component | Responsibility | Owns | Depends On |
|-----------|---------------|------|------------|
| Envoy Gateway | TLS, auth (JWT validation via Keycloak), rate limiting; routes all agent chat requests to Safety Orchestrator (not directly to agent pods); routes /api/v1/* to registry-api | Ingress rules, rate limit config | Keycloak (public key) |
| Safety Orchestrator | Acts as input proxy — receives requests from Envoy, scans (LLM Guard + Presidio + NeMo in parallel), proxies sanitized request to agent pod; also called by agent SDK for output scanning and PII de-anonymization at tool-call time. Fail-closed: blocked or error → 422, never reaches agent. Must include a PodDisruptionBudget with `minAvailable: 1` — a rolling update that takes all scanner pods down simultaneously causes a complete platform traffic blackout. | Scan orchestration logic, PII session mappings (scoped to request lifetime) | LLM Guard, Presidio, NeMo |
| LLM Guard | Prompt injection detection (DeBERTa), toxicity, secrets. Must include a PodDisruptionBudget with `minAvailable: 1`. | ML models, thresholds | None (stateless) |
| Presidio | PII detection and anonymization. Must include a PodDisruptionBudget with `minAvailable: 1`. | Entity recognizers, PII mapping | Postgres (mapping store) |
| NeMo Guardrails | YARA injection rules, AlignScore fact-checking. Must include a PodDisruptionBudget with `minAvailable: 1`. | Rule definitions | None (stateless) |
| Portkey OSS | LLM provider routing, retries, fallbacks, load balancing; called by agent pods for LLM inference — agents set OPENAI_BASE_URL=http://portkey:8787/v1 | Provider configs, routing rules | Redis (cache), LLM providers |
| OPA | Policy enforcement per tool call | Rego policies, decision logs | Policy bundles from git |
| Keycloak | OIDC identity provider — users, roles, service accounts | Realms, clients, sessions | Postgres (keycloak DB) |
| Registry API | CRUD for agents/versions/deployments, webhook receiver | Agent metadata, version state | Postgres (agentshield DB) |
| Deploy Controller | Reconcile K8s state with desired state from Registry | K8s manifests (generated) | Registry API, K8s API |
| Appsmith | UI for approval queue, agent registry, dashboards, ops | Dashboard config | Registry API, Postgres |
| AgentShield Studio | Visual drag-and-drop agent builder (React + React Flow) | Workflow definitions (JSON) | Registry API, Tool Registry |
| Declarative Runner | Generic pod that interprets visual workflow JSON at runtime | Runtime execution of no-code agents | Postgres, Safety Orchestrator, Langfuse, Tool Registry |
| Tool Registry | First-class CRUD for tools (native, HTTP, MCP server) and auth configs | Tool definitions, auth configs, agent-tool bindings | Postgres, K8s Secrets |
| MCP Proxy | Manages connections to registered MCP servers, discovers tools, proxies calls | MCP server sessions, tool cache | MCP servers (remote), Tool Registry |
| Langfuse | Tracing, cost tracking, eval scoring, dashboards | Traces, scores, datasets | Postgres, ClickHouse, Redis, MinIO |
| Postgres | Relational storage (5 databases) | All persistent state | PgBouncer (pooling) |
| Redis | Cache (Portkey), pub/sub (events), sessions (Keycloak) | Ephemeral data | None |
| ClickHouse | Trace analytics (Langfuse backend) | Observation/span data | MinIO (backups) |
| MinIO | Object storage for backups and media | Backup archives, attachments | Local disks |

### Data Flow

**Request Lifecycle (Low-Risk)**:

1. User → Envoy (TLS terminate, validate JWT via Keycloak, rate limit)
2. Envoy → Safety Orchestrator (fans out to LLM Guard + Presidio + NeMo in parallel)
3. Safety Orchestrator: if blocked → return HTTP 422 to user; if clean → proxy sanitized request to Agent Pod. Safety scan response includes `session_id` referencing the PII mapping stored in Postgres. Placeholders (e.g. `<EMAIL_0>`) replace real values in the sanitized text.
4. Agent Pod receives sanitized input (never sees raw injections or PII)
5. Agent → Portkey → LLM Provider (agent initiates LLM inference via Portkey proxy)
6. Agent → OPA sidecar (authorize tool call; risk=low → auto-execute). If tool args contain PII placeholders, SDK checks OPA `allow_deanonymize` flag for this tool. If allowed, SDK calls Safety Orchestrator `POST /scan/deanonymize` to substitute real values in tool args only — never back into LLM context.
7. Agent → External Tool (execute, get result)
8. Agent → Safety Orchestrator (output scan: PII leakage, toxicity check)
9. Agent → Langfuse (emit trace async)
10. Agent → User (final response)

**Multi-Agent Handoff Path**:

1. Source agent SDK calls target agent via Envoy ingress URL (not K8s internal DNS)
2. Envoy validates JWT (service account token for source agent)
3. Safety Orchestrator scans handoff payload — same fail-closed rules apply
4. `session_id` from original user scan is forwarded in `X-AgentShield-Session-Id` header
5. Target agent receives sanitized payload with `session_id` for de-anonymization
6. Target agent's OPA sidecar evaluates tool calls independently

Handoff requests also carry `X-AgentShield-Source-Agent` so OPA can apply source-specific policies. Agent pods must never call peer agents via K8s internal DNS directly; the SDK enforces the Envoy ingress URL at the point of handoff construction.

**Request Lifecycle (High-Risk)** — steps 1-5 same, then:

6. Agent → OPA sidecar (risk=high → require approval)
7. Agent → Postgres (INSERT approval record, status=pending)
8. Agent → `interrupt()` (LangGraph pauses, checkpoint written to Postgres)
9. Registry API → Redis pub/sub → Slack webhook (notify reviewer)
10. Reviewer → Appsmith → Postgres (UPDATE status=approved)
11. Postgres NOTIFY → Agent resumes from checkpoint
12. Agent → External Tool (execute approved action)
13. Agent → Safety Orchestrator (output scan)
14. Agent → Langfuse (trace)
15. Agent → User (response)

### Key Decisions

| Decision | Choice | Rationale | Alternatives Rejected |
|----------|--------|-----------|----------------------|
| Communication | REST + async events | Predictable for critical path, async for notifications | Pure REST (chatty), full event-driven (hard to debug) |
| Agent isolation | Agent-per-Pod | Security boundary, independent scaling | Shared runtime (noisy neighbor risk), serverless (cold starts) |
| Safety architecture | Orchestrator + independent scanners | Different resource profiles, parallel execution | Monolith (can't scale scanners independently) |
| Safety placement | Before Portkey | Never skip scanning, even on cache hits | After Portkey (cache bypass risk) |
| Identity | Self-hosted Keycloak | Zero SaaS, full control | External IdP (violates self-hosted constraint) |
| Namespace model | Per team | Balance of isolation vs management overhead | Per agent (sprawl), shared (no isolation) |
| SDK strategy | Required now, contract-based later | Consistency for first agents, flexibility when needed | Optional from start (inconsistency risk) |
| Database | Single Postgres, separate DBs | One backup pipeline, one HA setup | Multiple instances (5x ops overhead) |
| Checkpoints | Postgres (not Redis) | Durability for state that resumes hours later | Redis (risk of data loss on restart) |
| Object storage | MinIO | Self-hosted S3-compatible | Cloud S3 (violates self-hosted) |
| Secrets | K8s native + RBAC | Simplest for MVP, secure with etcd encryption | Vault (overkill for <50 secrets), Sealed Secrets (rotation pain) |
| Deployment | Helm umbrella + ArgoCD | Repeatable, GitOps, component-level upgrades | Manual kubectl (not repeatable), Kustomize (less packaging) |
| Frontend SDK | SSE contract + Vercel AI SDK / CopilotKit | Embeds in existing apps, standard streaming protocol | Chainlit (not embeddable), API-only (teams reinvent streaming) |
| Visual builder | Standalone React + React Flow Studio | Clean separation from Appsmith ops UI, full canvas control | Embedded in Appsmith (limited), fork Langflow (maintenance burden) |
| SDK API style | OpenAI-style `Agent()` + escape to `StateGraph` | Simple for 80% of agents, full power when needed | Graph-only (steep learning curve), YAML-only (no complex flows) |

### API Contracts

#### Registry API (FastAPI)

```
POST   /api/v1/agents                    — Register new agent
GET    /api/v1/agents                    — List all agents
GET    /api/v1/agents/{name}             — Get agent detail
PUT    /api/v1/agents/{name}             — Update agent config
DELETE /api/v1/agents/{name}             — Decommission agent

POST   /api/v1/agents/{name}/versions    — Register new version
GET    /api/v1/agents/{name}/versions    — List versions
PATCH  /api/v1/agents/{name}/versions/{v} — Update (mark eval-passed)

POST   /api/v1/agents/{name}/deploy      — Deploy version
POST   /api/v1/agents/{name}/rollback    — Rollback to previous
POST   /api/v1/agents/{name}/promote     — Promote canary to full
GET    /api/v1/agents/{name}/deployments — Deployment history

POST   /api/v1/agents/{name}/quarantine
  — Immediately applies a dynamic NetworkPolicy blocking all egress from the agent pod
  — Does NOT scale the deployment to 0 (preserves forensic state and LangGraph checkpoints)
  — Sets agent status="quarantined" in registry
  — Emits a Prometheus alert: agentshield_agent_quarantined{agent="name"}
  — To release: DELETE /api/v1/agents/{name}/quarantine

POST   /api/v1/agents/{name}/quarantine/release
  — Removes the dynamic NetworkPolicy
  — Sets agent status="live"
```

#### Safety Orchestrator API

```
POST   /api/v1/scan/input     — Scan input text (returns: blocked, scores, sanitized, session_id)
POST   /api/v1/scan/output    — Scan output text (returns: blocked, scores, clean)
POST   /api/v1/scan/deanonymize — Substitute PII placeholders with real values
                                   Body: {session_id, placeholders: ["<EMAIL_0>"]}
                                   Returns: {substitutions: {"<EMAIL_0>": "john@example.com"}}
                                   Requires: OPA allow_deanonymize=true for calling tool (SDK path)
                                   OR: reviewer RBAC role (Appsmith approval card path) — reviewers
                                   may de-anonymize PII from approvals.session_id for decision
                                   context; Langfuse traces always retain placeholders
GET    /health                 — Liveness
GET    /ready                  — Readiness (all scanners reachable)
```

#### Agent Contract (SDK provides automatically)

```
POST   /chat                   — Receive user message (JSON response)
POST   /chat/stream            — Receive user message (SSE streaming response)
GET    /health                 — Liveness probe
GET    /ready                  — Readiness (connected to safety, PG, Langfuse)
GET    /metrics                — Prometheus metrics (optional)
```

#### SSE Streaming Protocol (Agent → Frontend)

```
event: text_delta
data: {"content": "I'll look up your order..."}

event: tool_call_start
data: {"tool": "lookup_order", "args": {"order_id": "12345"}}

event: tool_call_end
data: {"tool": "lookup_order", "result": {"status": "delivered"}}

event: approval_requested
data: {"tool": "issue_refund", "args": {"amount": 50.0}, "approval_id": "apr_abc"}

event: approval_decided
data: {"approval_id": "apr_abc", "decision": "approved", "reviewer": "jane@co.com"}

event: done
data: {"usage": {"input_tokens": 340, "output_tokens": 128}}
```

#### Studio Workflow API (Registry API extensions)

```
POST   /api/v1/workflows               — Save visual workflow definition (JSON)
GET    /api/v1/workflows                — List workflows for team
GET    /api/v1/workflows/{id}           — Get workflow definition
PUT    /api/v1/workflows/{id}           — Update workflow
                                          On every PUT that changes `definition`, auto-creates a
                                          `workflow_versions` row with the PREVIOUS definition
                                          before overwriting. This ensures no version is ever
                                          lost silently.
POST   /api/v1/workflows/{id}/deploy    — Deploy as declarative runner pod
POST   /api/v1/workflows/{id}/test      — Run in sandbox mode (no side effects)
GET    /api/v1/workflows/{id}/versions  — Version history
POST   /api/v1/workflows/{id}/versions/{version}/restore
                                          — Copies the specified version's definition into
                                            workflows.definition
                                          — Increments workflows.version counter
                                          — Creates a new workflow_versions row (so restore is
                                            itself versioned)
                                          — Does NOT auto-deploy — caller must POST /{id}/deploy
                                            separately
                                          — Returns: WorkflowResponse with updated version number
```

**Workflow JSON node config — `output_mapping` field:**

Each Agent node config supports an `output_mapping` object that tells the runner how to extract named values from the LLM's unstructured output:

```json
{
  "id": "lookup_node",
  "type": "agent",
  "config": {
    "instructions": "Look up the order and return the order_id and amount as JSON.",
    "model": "claude-sonnet-4-20250514",
    "tools": ["lookup_order"],
    "output_mapping": {
      "order_id": "$.order_id",
      "amount":   "$.amount"
    }
  }
}
```

- Keys in `output_mapping` become named slots available to downstream nodes via `{{order_id}}` or condition expressions
- Values are JSONPath expressions (applied against structured LLM output) or regex patterns (applied against free text)
- Extracted values are stored in `state["outputs"][node_id]` and addressable by subsequent nodes

#### Tool Registry API

```
# Tool CRUD
POST   /api/v1/tools                    — Create tool definition (type: native|http|mcp_tool)
GET    /api/v1/tools                    — List tools (filter: team, type, category, risk_level)
GET    /api/v1/tools/{id}              — Get tool + schema + usage stats
PUT    /api/v1/tools/{id}              — Update tool (creates new semver version)
DELETE /api/v1/tools/{id}              — Deprecate tool (soft delete, with impact warning)
GET    /api/v1/tools/{id}/agents       — Which agents currently reference this tool?
POST   /api/v1/tools/{id}/test         — Execute tool in sandbox (validates schema + auth)

# MCP Server management
POST   /api/v1/mcp-servers              — Register MCP server (triggers tools/list discovery)
GET    /api/v1/mcp-servers              — List registered MCP servers + discovered tool counts
GET    /api/v1/mcp-servers/{id}        — Server detail + all discovered tools
POST   /api/v1/mcp-servers/{id}/sync   — Force re-discover tools (refresh tools/list)
DELETE /api/v1/mcp-servers/{id}        — Unregister server (soft delete)

# Auth configs (credentials decoupled from tool definitions)
POST   /api/v1/auth-configs             — Create auth config (references K8s Secret)
GET    /api/v1/auth-configs             — List auth configs (no credentials returned)
PUT    /api/v1/auth-configs/{id}       — Update (rotate K8s Secret reference)
DELETE /api/v1/auth-configs/{id}       — Remove (blocked if in use by tools)

# Agent-Tool bindings
POST   /api/v1/agents/{name}/tools      — Attach tool(s) to agent (auto-regenerates OPA policy)
DELETE /api/v1/agents/{name}/tools/{id} — Detach tool (auto-regenerates OPA policy)
GET    /api/v1/agents/{name}/tools      — List all tools bound to this agent
```

---

## Agent Creation Model

AgentShield supports three tiers of agent creation, all governed by the same safety, OPA, HITL, and tracing pipeline.

### Tier 1: Visual Builder (No-Code) — Phase 2+

**Audience**: Product teams, ops, analysts  
**Tool**: AgentShield Studio (standalone React + React Flow app)  
**Output**: JSON workflow definition → deployed as declarative runner pod

```
┌────────────────────────────────────────────────┐
│ Studio Canvas                                   │
│                                                 │
│  [Agent Node] ──→ [Approval Gate] ──→ [End]    │
│       │                                         │
│       └─────→ [Escalation Agent]               │
│                                                 │
│  Properties: instructions, model, tools, risk   │
└────────────────────────────────────────────────┘
```

Tools defined as HTTP endpoints (no Python):
- Method + URL + headers + body template
- Input/output mapping via `{{variable}}` syntax
- Test button for validation

### Tier 2: SDK Declarative (Code-Light) — Phase 1

**Audience**: Developers building simple tool-calling agents  
**Pattern**: OpenAI Agent SDK style — `Agent()` constructor, no graph wiring

```python
from agentshield_sdk import Agent, Runner, tool

@tool(risk="high")
def issue_refund(order_id: str, amount: float) -> str: ...

agent = Agent(
    name="refund-agent",
    instructions="Process refund requests up to $500.",
    tools=[lookup_order, issue_refund],
    handoffs=[escalation_agent],
    model="claude-sonnet-4-20250514",
)

result = await Runner.run(agent, input="Refund order #12345")
```

SDK transparently provides: safety scanning, OPA policy check, HITL pause for high-risk tools, Langfuse tracing, SSE streaming.

### Tier 3: SDK Graph (Full Control) — Phase 1

**Audience**: Engineers building complex multi-step workflows  
**Pattern**: Explicit LangGraph `StateGraph` with full control over branching, parallel execution, and state

```python
from agentshield_sdk import AgentGraph
from langgraph.graph import StateGraph

graph = StateGraph(MyState)
graph.add_node("verify", verify_node)
graph.add_conditional_edges("verify", route_by_risk)
# ...
agent = AgentGraph(graph, name="complex-agent")
```

Same governance as Tier 2 — the `AgentGraph` wrapper injects safety, tracing, and policy hooks around the graph execution.

### Platform Separation of Concerns

| App | Purpose | Tech |
|-----|---------|------|
| AgentShield Studio | Agent creation (visual builder) | React, React Flow, TypeScript |
| Appsmith | Operations (registry, deploy, approvals, dashboards) | Appsmith (low-code) |
| Registry API | Backend for both UIs | FastAPI, Postgres |
| Deploy Controller | Reconciles K8s state | Python, K8s client |

---

## Declarative Runner Execution Model

The Declarative Runner is a generic Python service (~600 lines) that loads a workflow JSON definition at startup and builds a LangGraph StateGraph from it. It uses the agentshield-sdk internally — no separate framework.

### Startup
On pod start, the runner reads `WORKFLOW_JSON` env var (base64-encoded), parses it, and calls `build_graph(definition)` to construct a compiled LangGraph `StateGraph`. The compiled graph is cached in memory for the pod lifetime.

### Node Execution Model

**State:** The runner maintains a typed state dict `WorkflowState` with:
- `messages: list[BaseMessage]` — conversation history
- `outputs: dict[str, Any]` — named outputs from each node (key = node_id)
- `session_id: str` — from Safety Orchestrator scan response
- `__interrupt__: dict | None` — pending approval context

**Agent Node:**
- Creates a temporary `Agent(instructions, tools, model)` from node config
- Calls `Runner.run(agent, input=state["messages"])` via SDK
- Stores LLM output in `state["outputs"][node_id]`
- LLM output is parsed to extract named slot values using the node's `output_mapping` config (e.g., `{"order_id": "$.order_id"}` — JSONPath expression against structured LLM output or regex against free text)

**HTTP Tool Node:**
- Reads URL template (e.g., `https://api.internal/orders/{{order_id}}/status`)
- Resolves `{{variable}}` placeholders by looking up `state["outputs"]` — variable name maps to a key in a prior node's output
- Executes HTTP call via `httpx`
- Stores response in `state["outputs"][node_id]`

**Approval Gate Node:**
- Evaluates condition expression against `state["outputs"]` (e.g., `state["outputs"]["lookup_node"]["amount"] > 100`)
- If condition met: creates approval record via Registry API, calls LangGraph `interrupt()` — same mechanism as SDK agents
- The LangGraph `interrupt()` checkpoints state to Postgres via `AsyncPostgresSaver`
- Resume works identically to SDK agents: Postgres NOTIFY → `Runner.run(agent, Command(resume=decision))` called on the runner's `/resume/{thread_id}` endpoint

**Router Node:**
- Evaluates `if/else-if/default` conditions against `state["outputs"]`
- Returns next node_id as a `Command(goto=next_node_id)` to LangGraph

### Variable Binding Rules
- Variables are resolved left-to-right in execution order
- A node can only reference outputs from nodes that have already executed
- Unresolved variables raise a `WorkflowExecutionError` with the unresolved placeholder name
- The node's `output_mapping` config defines how to extract named values from unstructured LLM text

### Dual Approval Gate Decision (Gap C-08)
Studio Approval Gate nodes and OPA tool risk classification are **complementary, not conflicting**:
- **OPA always runs first** on every tool call — if the tool is risk=high, OPA triggers HITL regardless of any Studio gate
- **Studio Approval Gate** adds conditional logic on top: even if OPA would auto-approve (risk=low), the gate can escalate based on parameter values (e.g., `amount > $100`)
- Both create records in the same `approvals` table and appear in the same Appsmith queue
- A tool call can only have one pending approval at a time (UNIQUE constraint on `thread_id + tool_name WHERE status='pending'`)

---

## Constraints

- 100% self-hosted on Kubernetes — zero SaaS dependencies
- Kubernetes 1.27+ required (for native sidecar support)
- All components Apache 2.0 or MIT licensed
- Agent developers use Python + LangGraph + agentshield-sdk for custom agents (Phase 1); visual builder available Phase 2+
- Fail-closed design: if any safety component is unreachable, block the request
- OPA policies stored in git, deployed via bundle — no runtime policy edits
- Agents cannot reach LLM providers directly — must route through Safety → Portkey
- Agent pods require two Postgres connection strings: `DATABASE_URL` (via PgBouncer, transaction-pool mode — for normal queries) and `DIRECT_DATABASE_URL` (direct to Postgres primary, bypassing PgBouncer — required for LangGraph `AsyncPostgresSaver` which uses LISTEN/NOTIFY for checkpoint resume). PgBouncer transaction-pool mode does not support LISTEN/NOTIFY. Each agent pod holds one persistent direct connection for LISTEN on its thread channels.
- NeMo YARA rules follow the same policy-as-code workflow as OPA policies: rules stored in `policies/nemo/rules/` in git, validated in CI via `yara -r rules/` syntax check, deployed via a K8s ConfigMap update that triggers a NeMo pod SIGHUP to reload rules without restart. Rule format: standard YARA 4.x syntax.

---

## Success Criteria

### Measurable Outcomes

- **SC-1**: Platform installable by one engineer in under 4 hours (first install)
- **SC-2**: New agent from `git push` to serving traffic in under 10 minutes
- **SC-3**: Safety scanning adds <200ms p99 to request latency
- **SC-4**: 100% of high-risk actions have a human decision record (approved, rejected, or timed out)
- **SC-5**: Zero successful prompt injection attacks in production (as measured by weekly Garak scans and Langfuse trace review)
- **SC-6**: Platform operates with <0.5 FTE ongoing maintenance

---

## Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| LLM Guard 512-token limit misses long injections | High | Injection bypasses detection | Chunk inputs + scan each chunk; NeMo YARA as second layer |
| Single Postgres failure takes down everything | Low | Full platform outage | Synchronous replication + Patroni auto-failover (<30s) |
| SDK coupling prevents non-Python agents | Medium | Teams blocked or bypass platform | Clean module boundaries enable Phase 2 extraction |
| Safety latency degrades user experience | Medium | Developers disable or bypass safety | Parallel fan-out (latency = max scanner, not sum); monitor p99 |
| Keycloak complexity for small team | Low | Auth issues block all access | Start with local users, minimal realm config; LDAP later |
| ClickHouse single-node data loss | Medium | Lose trace history | Daily backups to MinIO; single-node sufficient for <50M traces/month |
| Reviewer fatigue (too many approvals) | Medium | Rubber-stamping defeats purpose | Tune risk thresholds; only truly high-risk goes to queue |
| Base image update breaks agents | Low | Agent outage after platform upgrade | Agents pin base version; CI re-runs evals against new base before promoting |
| Visual builder agents bypass safety | Low | No-code agents skip governance | Declarative runner uses same Safety Orchestrator path as SDK agents — no bypass possible |
| Two UIs (Studio + Appsmith) confuse users | Medium | Unclear which tool to use when | Clear messaging: Studio = build agents, Appsmith = operate/approve. Link between them. |
| Declarative runner performance | Medium | JSON interpretation slower than compiled graph | Benchmark early; cache parsed workflow; upgrade to compiled graph if latency >50ms overhead |
| Tool Registry becomes bottleneck | Low | All agents blocked if registry is down | Registry API has 2+ replicas, read-through cache in Redis for tool schemas; agents cache tool configs at startup |
| MCP server auth credential rotation | Medium | Stale credentials silently fail tool calls | Auth configs reference K8s Secrets; rotation updates the secret, agents pick up on next connection; health-check endpoint per MCP server |

---

## Assumptions

- Kubernetes cluster exists with sufficient capacity (~16 vCPU, ~32GB RAM for platform, plus agent pods)
- Teams have CI/CD (GitHub Actions, GitLab CI, or Jenkins) already running
- Container registry exists (Harbor, GitLab Registry, or similar)
- DNS is available for internal service discovery (kube-dns)
- No existing OIDC provider — Keycloak is net-new
- 3-5 agents in first 3 months, scaling to 50+ over 12 months
- English-language content primarily (NeMo/LLM Guard limitations in other languages)
- LLM providers are reachable from the cluster (outbound HTTPS to OpenAI, Anthropic, etc.)

---

## Out of Scope

- Multi-cluster / multi-region deployment (Phase 3+)
- Non-Python agent runtimes (Phase 3 — contract-based SDK)
- Semantic caching (exact-match Redis cache only)
- Cost allocation/billing per team (tracked in Langfuse but no chargeback system)
- Custom Appsmith UI polish (functional MVP, not beautiful)
- Mobile interface for approvals (desktop web only)
- Agent-to-agent communication (each agent is independent)
- Visual builder import/export to other platforms (Langflow, Dify)

---

## Phased Rollout

### Phase 1 — Foundation + SDK + Basic Studio (Weeks 1-7)

| Week | Deliverable |
|------|-------------|
| 1 | Postgres (HA) + Redis + MinIO + Keycloak deployed; namespaces + NetworkPolicies created |
| 2 | Registry API + Deploy Controller + Appsmith (Registry UI); first agent deployable; Tool Registry API (CRUD for native + HTTP tools, agent-tool bindings, OPA policy auto-generation) |
| 3 | Safety Orchestrator + LLM Guard + Presidio + NeMo; input/output scanning live |
| 4 | OPA sidecar injection + policy generation from agent.yaml; Langfuse deployed |
| 5 | Approval flow (interrupt + Postgres + Appsmith queue); Portkey + Redis cache; Envoy gateway with Keycloak JWT validation |
| 6 | SDK v1: declarative `Agent()` API (OpenAI-style) + SSE streaming endpoint; first agent uses `Agent(instructions, tools)` pattern |
| 7 | Studio v0: basic React + React Flow canvas with Tool Picker (populates from Tool Registry); Agent + HTTP Tool + End nodes; properties panel; save workflow; one-click deploy to declarative runner pod |

**SDK deliverables (Phase 1)**:
- `Agent(name, instructions, tools, model)` — declarative constructor
- `Runner.run()` / `Runner.run_streamed()` — sync and streaming execution
- `@tool(risk="low|high")` decorator with auto OPA + HITL integration
- SSE streaming protocol (text_delta, tool_call_start/end, approval_requested/decided, done)
- `agentshield dev` — local development server with mock safety layer

**Studio v0 deliverables (Phase 1)**:
- React + React Flow canvas with 3 node types: Agent, HTTP Tool, End
- Properties panel: agent instructions, model selector, tool endpoint/method/body
- Save workflow to Registry API (JSON serialization)
- One-click Deploy → declarative runner pod (no container build)
- Declarative Runner pod: generic image that interprets workflow JSON at runtime

**Exit criteria**: A developer can create an agent with `Agent(instructions, tools)` and deploy via SDK path. A product team member can build a simple agent visually in Studio and deploy with one click. Both paths produce governed agents (safety scan, OPA, HITL, tracing).

### Phase 2 — Scale, Multi-Agent & Studio Enhancement (Weeks 8-12)

| Week | Deliverable |
|------|-------------|
| 8 | promptfoo in CI + Garak weekly scans; eval scores pushed to Langfuse |
| 9 | Canary deployments (Envoy weighted routing); promotion/rollback via UI |
| 10 | SDK v2: handoffs (multi-agent), guardrails (`@InputGuardrail`/`@OutputGuardrail`), context injection; MCP server registration + auto-discovery + auth configs |
| 11 | Studio v1: Approval Gate node, Router node, Handoff node added to canvas; sandbox test mode; Tool Picker shows MCP server tools |
| 12 | Studio v1: version history with diff view; alerting (Prometheus + Grafana) |

**Studio v1 additions (over v0)**:
- Approval Gate node: condition builder, reviewer team, timeout config
- Router node: conditional branching (if/else on context values)
- Handoff node: route to another agent
- Sandbox test mode (run workflow with mocked tool responses)
- Version history with visual diff
- Tool test button (execute against real endpoint from config UI)

**Exit criteria**: Visual builder supports multi-step workflows with approval gates and branching. SDK-built agents support multi-agent handoffs and custom guardrails. Canary deploys and alerting operational.

### Phase 3 — Maturity & Ecosystem (Weeks 13+)

| Week | Deliverable |
|------|-------------|
| 13 | Hardening: tune safety thresholds, custom Presidio recognizers, chunked scanning |
| 14 | Studio v2: DB query tool type, code snippet tool type, workflow templates gallery |
| 15 | SDK v3: `AgentGraph(StateGraph(...))` escape hatch documented; full LangGraph power with platform governance |
| 16+ | Ongoing maturity items (below) |

**Ongoing**:
- LLM-as-Judge automated scoring
- Post-hoc annotation queues
- SDK extraction (contract-based mode for non-Python teams)
- Admission webhook for contract validation
- Studio import/export formats
- Multi-cluster support investigation

---

## Open Questions for Reviewers

| # | Question | Context | Options Considered | Blocked Decision |
|---|----------|---------|-------------------|-----------------|
| 1 | Which Envoy deployment model? | Envoy can run as a standalone pod (Envoy Gateway) or as a sidecar per agent | Standalone gateway (simpler, central control) vs. sidecar (more granular) | How to configure rate limiting — per-gateway rules vs per-pod |
| 2 | Sync replica count for Postgres | HA requires at least 1 sync replica; 2 gives stronger durability but higher write latency | 1 sync replica (standard) vs 2 sync replicas (highest durability) | Recovery time vs write performance trade-off |
| 3 | Should canary analysis be automated? | Canary deploys currently require manual promotion. Auto-promote based on error rate is possible but adds complexity. | Manual promote (simpler, human judgment) vs auto-promote (faster, needs good metrics) | Whether to build auto-canary in Phase 2 or defer |
| 4 | Log aggregation stack | Platform needs centralized logs but no stack was selected | EFK (Elasticsearch+Fluentd+Kibana) vs Loki+Grafana (lighter) vs defer to existing | Operational cost vs capability trade-off |
| 5 | PgBouncer pool sizing | Many clients (agents + platform services) sharing one Postgres; pool size affects concurrency | 100 connections (conservative) vs 200 (generous) vs dynamic based on pod count | Right-size without hitting Postgres max_connections |
