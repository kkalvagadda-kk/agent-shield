# AgentShield — Architecture Specification

**Status**: PROPOSED — Pending team review  
**Date**: 2026-06-27  
**Author**: Karthik + Claude  
**Version**: 1.2.0

## Component Specifications

This document is the high-level overview. Detailed implementation specs live in `docs/design/`:

| Spec | Covers | Status |
|------|--------|--------|
| [authorization-model-spec.md](design/authorization-model-spec.md) | Agent machine identity (K8s SA tokens + Istio Ambient), OPA policy enforcement, asset lifecycle (private → publish → grant), deploy gate, HITL approval authority (per-agent/tool/skill), Playground authorization | Draft |
| [playground-spec.md](design/playground-spec.md) | Interactive test console, sandbox mode (grant-bypass), per-run trace panel, LLM-as-Judge, dataset curation, eval runner, version comparison, Playground HITL (self-approval), Playground namespace | Draft |

Requirements that drove these specs:
- `docs/authorization-model.md` — authorization requirements (REQ-AUTH, REQ-PUB, REQ-DEPLOY, REQ-RT, REQ-AUDIT)
- `docs/decisions.md` — architecture decisions (D16–D19 cover auth model choices)

---

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

1. **Given** a user opens Studio, **When** they drag two Agent nodes, connect them with a conditional edge, attach tools from the Registry via the PropertiesPanel, and click Deploy, **Then** the workflow is live within 90s.
2. **Given** a visual workflow exists, **When** user edits agent instructions or changes tool associations and redeploys, **Then** new version serves within 60s (no container build needed).
3. **Given** a visual agent is deployed, **When** a high-risk tool is called, **Then** the same HITL approval flow fires as for SDK-built agents.
4. **Given** a multi-agent workflow, **When** the first agent's output contains a routing keyword, **Then** the declarative runner routes to the correct downstream agent via conditional edge.

---

### User Story 1c — Developer Tests Agent in Playground (Priority: P2)

A developer or product team member opens the Playground tab in Studio, sends a message to an agent version, and sees the full response plus an inline trace — all without touching production traffic.

**Why this priority**: Closes the inner loop for agent iteration. Without a test surface, every change requires a full deploy-and-curl cycle. The Playground makes agent development self-service.

**Independent Test**: Send a message to a deployed agent in sandbox mode. Verify: response streams via SSE, trace panel shows LLM call + tool call + safety scan scores, no real tool side effects occur.

**Acceptance Scenarios**:

1. **Given** a deployed or draft agent, **When** developer opens the Playground tab and sends a message, **Then** the response streams in real time (SSE) and a trace panel expands showing all LLM calls, tool invocations, safety scan scores, and OPA decisions for that run.
2. **Given** sandbox mode is enabled, **When** developer sends a message, **Then** tool calls return mocked responses, no external APIs are called, and the trace panel marks each tool call as `[sandbox]`.
3. **Given** a test message was run, **When** developer clicks "Save to Dataset", **Then** the input/output pair is appended to a named Langfuse dataset and visible in the Eval Runner.
4. **Given** a passing run, **When** LLM-as-Judge scoring completes (async, <10s), **Then** a score badge appears on the run; developer can click thumbs-up/down to override and the feedback is stored.
5. **Given** two versions of an agent exist, **When** developer enters comparison mode and sends a message, **Then** both versions run in parallel and outputs, trace depth, and token cost appear side by side.
6. **Given** an eval suite exists for this agent, **When** developer clicks "Run Evals" in the Playground, **Then** promptfoo executes against the selected version, and pass/fail results per assertion appear inline within 60s.

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
| FR-007 | P0 | Route high-risk actions to approval queue; approval rights scoped per-agent/tool/skill | Agent pauses, record created, only authorized reviewers notified — see [authorization-model-spec.md](design/authorization-model-spec.md) §7 |
| FR-008 | P0 | Approve/reject via ops dashboard; Playground HITL self-approved by asset owner | Decision stored, agent resumes within 5s |
| FR-009 | P0 | Auto-reject on timeout (30min default) | Agent resumes with denial, event logged |
| FR-010 | P0 | Full trace capture for every request | Trace in Langfuse within 10s of completion |
| FR-011 | P0 | Run eval suite in CI on every PR | PR blocked if assertions fail |
| FR-012 | P0 | Registry UI: list agents, versions, deploy buttons | Functional Appsmith dashboard |
| FR-013 | P1 | Canary deployment with traffic percentage | Envoy routes configured split |
| FR-014 | P1 | Slack notification on pending approval | Webhook within 10s |
| FR-015 | P1 | Cost tracking per agent/team/model | Visible in Langfuse dashboard |
| FR-016 | P1 | Weekly Garak vulnerability scan | Scheduled CI, alert on findings |
| FR-017 | P1 | Chunked scanning for inputs >512 tokens | Each chunk scanned independently |
| FR-018 | P2 | LLM-as-Judge automated scoring | Async evaluator on all traces; score surfaced inline in Playground within 10s of run completion |
| FR-019 | P2 | Time-based policy constraints | Rego rules reference current time |
| FR-027 | P2 | Agent machine identity via K8s SA tokens + Istio Ambient mTLS | OPA policy keyed on SA subject, not agent name string — see [authorization-model-spec.md](design/authorization-model-spec.md) |
| FR-028 | P2 | Asset lifecycle: private → pending_review → published with admin approval | Publish gate, risk-tiered approval, explicit team grants |
| FR-029 | P2 | Deploy gate: team ownership + tool grants + eval passed checks | All 5 pre-flight checks must pass before deployment record is created |
| FR-030 | P2 | HITL approval authority scoped per-agent/tool/skill | Reviewers see only requests within their granted scope; self-approval prohibited in production |
| FR-020 | P2 | Playground: interactive test console in Studio | Message streams via SSE; works for both declarative and SDK agents |
| FR-021 | P2 | Playground: per-run trace panel | Every run shows LLM calls, tool calls, safety scores, OPA decisions inline — no Langfuse context-switch needed |
| FR-022 | P2 | Playground: sandbox mode | Tool calls return mocked responses; no external side effects; trace labels each call `[sandbox]` |
| FR-023 | P2 | Playground: dataset curation | User saves any input/output pair to a named Langfuse dataset from the Playground UI |
| FR-024 | P2 | Playground: LLM-as-Judge feedback override | User can thumbs-up/down a judge score; override stored as Langfuse annotation |
| FR-025 | P2 | Playground: on-demand eval runner | Run a promptfoo eval suite against a specific agent version from Studio; pass/fail per assertion shown inline |
| FR-026 | P2 | Playground: side-by-side version comparison | Run the same prompt against two agent versions in parallel; compare output, trace depth, token cost |

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
| Langfuse Public API | Internal | HTTPS | Playground trace retrieval + dataset sync (Registry API → Langfuse; credentials stay server-side) |

### Key Entities

| Entity | Description | Key Attributes | Relationships |
|--------|-------------|---------------|---------------|
| Agent | A registered AI agent | name, team, description, agent_class (daemon/user_delegated), status (private/pending_review/published) | Has many versions, deployments, approvals; references many tools |
| AgentVersion | A specific build of an agent | image_tag, tools[], eval_passed, adversarial_eval_passed | Belongs to agent, deployed as deployment |
| Deployment | An active deployment of a version | status, replicas, canary_percent, agent_identity_id | Links agent to version; machine identity provisioned at first deploy |
| AgentIdentity | Machine identity for a deployed agent (K8s SA + SPIFFE) | k8s_service_account, sa_subject, issued_at, revoked_at | 1:1 with Deployment; sa_subject is the OPA policy key — see [authorization-model-spec.md](design/authorization-model-spec.md) |
| Approval | A human decision on a high-risk action | status, context (production/playground), user_id, reviewer_id, expires_at | Production: routed to authorized reviewers; Playground: self-approved by asset owner |
| ApprovalAuthority | *(Deprecated — replaced by ArtifactRoleGrant)* Who can approve HITL for a given resource | resource_type, resource_id, approver_user_id, approver_role | Kept for historical records; new HITL routing uses artifact_role_grants |
| ArtifactRoleGrant | Artifact-scoped RBAC role grant (Decision 25) | artifact_type (agent/workflow), artifact_id, role (agent-admin/approver), grantee_type (user/team), grantee_id | Many-per-user; creator auto-granted agent-admin; see [rbac-design.md](design/rbac-design.md) |
| OPADecision | Audit log for every OPA policy evaluation | agent_identity_id, tool_name, decision, deny_reason, user_id, context (production/playground) | Written on every tool-call authorization |
| OPAPolicy | Centralized bundle (OPA Bundle Server) | registered_agents (SA-keyed), team_grants | One bundle for all agents; sidecars poll every 30s; replaces per-agent ConfigMaps |
| PublishRequest | Tracks asset transition from private to shared | asset_id, dependency_declaration, highest_risk_level, status | Created on publish; admin approves/rejects |
| AssetGrant | A team's access to a published asset | asset_id, grantee_team, granted_by, expires_at, revoked_at | Required for binding, deployment, and runtime OPA grant check |
| Tool | A reusable, independently-managed tool definition | name, type (native/http/mcp_tool/python), input_schema, risk_level, auth_config_id, python_code, test_fixture | Many-to-many with agents; Python tools executed by Python Executor microservice |
| Skill | A named bundle of tools reusable across agents | name, team, description, tool_ids[], status | Selected on AgentNode in Studio; flattened to constituent tools at runtime |
| AuthConfig | Credential configuration decoupled from tool definition | type (api_key/oauth2/bearer/mtls), k8s_secret_ref, owner_team | Referenced by many tools |
| MCPServer | A registered MCP server whose tools are auto-discovered | server_url, transport, auth_config_id, status | Has many tools (discovered); referenced by agents |
| Workflow | A visual agent workflow definition (Studio canvas) | name, team, definition (JSON), version_count | Deployed as a declarative runner pod |
| PlaygroundRun | A single test execution from the Playground UI | run_id, user_id, team, sandbox, context='playground', langfuse_trace_id, judge_score | User-scoped and private — see [playground-spec.md](design/playground-spec.md) |
| PlaygroundFeedback | User thumbs-up/down override on a judge score | run_id, rating, comment, reviewer | Stored as Langfuse score annotation |
| PlaygroundDataset | A named collection of saved input/output pairs | dataset_name, owner_user_id, langfuse_dataset_id | User-scoped (not team-scoped); items stored in Langfuse |
| EvalRun | An on-demand promptfoo eval execution | eval_run_id, user_id, k8s_namespace='agentshield-playground', status, assertions | Runs in playground namespace; Job uses triggering user's identity |

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
| Safety Orchestrator | Acts as input proxy — receives requests from Envoy, scans (LLM Guard + Presidio + NeMo in parallel), proxies sanitized request to agent pod; also called by agent SDK for output scanning and PII de-anonymization at tool-call time. **Must also scan Event Gateway webhook payloads** — external webhook bodies are a second untrusted-input entry point (alongside chat), so when this component is (re)enabled its scope includes wiring the event-gateway → input-scan path for event-driven agents (Phase 9 T-10 ships without this; input-scan deferred here). Fail-closed: blocked or error → 422, never reaches agent. Must include a PodDisruptionBudget with `minAvailable: 1` — a rolling update that takes all scanner pods down simultaneously causes a complete platform traffic blackout. | Scan orchestration logic, PII session mappings (scoped to request lifetime) | LLM Guard, Presidio, NeMo |
| LLM Guard | Prompt injection detection (DeBERTa), toxicity, secrets. Must include a PodDisruptionBudget with `minAvailable: 1`. | ML models, thresholds | None (stateless) |
| Presidio | PII detection and anonymization. Must include a PodDisruptionBudget with `minAvailable: 1`. | Entity recognizers, PII mapping | Postgres (mapping store) |
| NeMo Guardrails | YARA injection rules, AlignScore fact-checking. Must include a PodDisruptionBudget with `minAvailable: 1`. | Rule definitions | None (stateless) |
| Portkey OSS | LLM provider routing, retries, fallbacks, load balancing; called by agent pods for LLM inference — agents set OPENAI_BASE_URL=http://portkey:8787/v1 | Provider configs, routing rules | Redis (cache), LLM providers |
| OPA | Policy enforcement per tool call | Rego policies, decision logs | Policy bundles from git |
| Keycloak | OIDC identity provider — users, roles, service accounts | Realms, clients, sessions | Postgres (keycloak DB) |
| Registry API | CRUD for agents/versions/deployments, webhook receiver | Agent metadata, version state | Postgres (agentshield DB) |
| Deploy Controller | Reconcile K8s state with desired state from Registry | K8s manifests (generated) | Registry API, K8s API |
| Appsmith | UI for approval queue, agent registry, dashboards, ops | Dashboard config | Registry API, Postgres |
| AgentShield Studio | Visual drag-and-drop agent builder (React + React Flow) with embedded Playground tab for interactive testing, trace inspection, eval runs, and dataset curation | Workflow definitions (JSON), playground session state | Registry API, Tool Registry, Langfuse API, Python Executor |
| Declarative Runner | Generic pod that interprets visual workflow JSON at runtime | Runtime execution of no-code agents | Postgres, Safety Orchestrator, Langfuse, Tool Registry, Python Executor |
| Python Executor | Sandboxed Python code runner — receives `{code, args}`, executes `run_tool(args)` in a subprocess, returns `{result, error}` | Subprocess isolation, per-call timeout | None (stateless microservice) |
| Event Gateway (Phase 9) | Public webhook ingress (`POST /hooks/{agent}/{token}`) for event-driven agents. Validates SHA-256 token (constant-time, agent-scoped, uniform 401), 2D Redis sliding-window rate limit (per-agent + per-IP, fail-closed), replay protection (timestamp/nonce), filter evaluation (ReDoS-bounded), then dispatches to `/api/v1/internal/runs/start`. Persists every event to `agent_events` (matched/filtered/rejected). Only publicly-exposed service; threat-modeled (`docs/design/event-gateway-threat-model.md`). Ingress exposes ONLY `/hooks/*` — never `/internal`. | Token validation, rate/replay state (Redis) | Postgres, Redis, Registry API (internal endpoint) |
| Tool Registry | First-class CRUD for tools (native, HTTP, MCP server, Python) and auth configs | Tool definitions, auth configs, agent-tool bindings | Postgres, K8s Secrets |
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
GET    /api/v1/agents/{name}/stats       — Last-24h run aggregates (run_count, p50/p95 latency, error_rate, cost)
GET    /api/v1/agents/{name}/events      — Event Gateway webhook log (Phase 9): paginated, filter by trigger_id + status (matched|filtered|rejected)
POST   /api/v1/agents/{name}/triggers/{id}/rotate-token — Rotate a webhook trigger's token (Phase 9); returns plaintext + webhook_url ONCE, old hash invalidated
GET    /api/v1/agents/{name}/health      — Mode-aware health signals (Phase 8):
  — reactive:     p95_latency_ms, error_rate, runs_24h, cost_24h
  — durable:      awaiting_approval_count, failed_24h, avg_duration_ms
  — scheduled:    last_run_status, next_fire_at, missed_fires
  — event-driven: match_rate_24h, rejected_count_24h
  — rolled up to health = healthy | degraded | failing (Studio agent-list dots)

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

**Phase 9 Implementation Notes:**
- `scanner_clients.py`: per-scanner in-process `CircuitBreaker` (5 failures → 30s open); exponential-backoff retry (100ms / 500ms / 2s). When circuit is open, raises immediately so the orchestrator returns `blocked=true` fail-closed.
- `orchestrator.py`: Presidio runs first (PII anonymization before scanning); then LLM Guard + NeMo fanned out in parallel via `asyncio.gather`; 5s overall `asyncio.wait_for` timeout. Any exception or timeout → blocked.
- `pii_store.py`: standalone SQLAlchemy async session connecting to the shared Postgres `pii_mappings` table; TTL defaults 24h; entries purged on demand.
- `policy_generator.py` (registry-api): called from `deploy_agent` after deployment record created; non-fatal in dev if K8s is unreachable. Risk→action map: low→allow, medium→log, high→require_approval, critical→deny.
- PodDisruptionBudgets (`minAvailable: 1`) on all 4 safety chart deployments (LLM Guard, Presidio, NeMo, Safety Orchestrator).

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

#### Playground API (Registry API extensions)

```
# Run a test message against an agent or workflow
POST   /api/v1/playground/run
  Body: {
    target_type: "workflow" | "agent",   — workflow uses declarative runner; agent uses SDK pod
    target_id: "<workflow_id | agent_name>",
    version: "<version_tag>",            — optional; defaults to latest deployed
    message: "<user message>",
    sandbox: true | false,               — if true, tool calls return mocked responses
    session_id: "<uuid>"                 — optional; pass to continue a multi-turn conversation
  }
  Returns: SSE stream (same protocol as /chat/stream: text_delta, tool_call_start/end,
           approval_requested, done events) + a run_id for trace retrieval
  Note: sandbox=true routes to POST /api/v1/workflows/{id}/test (declarative) or a
        mock tool layer injected by the SDK agent (SDK path); no real tool calls fire

# Get trace for a completed playground run
GET    /api/v1/playground/runs/{run_id}/trace
  Returns: {
    run_id, agent_name, version, duration_ms, total_tokens, cost_usd,
    sandbox: bool,
    spans: [
      {
        type: "llm_call" | "tool_call" | "safety_scan" | "opa_decision" | "handoff",
        name, started_at, duration_ms,
        input, output,                   — redacted to placeholders if PII
        metadata: {                      — type-specific fields
          model, input_tokens, output_tokens,   — llm_call
          tool_name, risk_level, result,        — tool_call
          scanner, score, blocked,              — safety_scan
          decision, policy_version,             — opa_decision
        }
      }
    ],
    judge_score: float | null,           — null until async scoring completes
    judge_reasoning: str | null
  }

# Save a run's input/output pair to a Langfuse dataset
POST   /api/v1/playground/runs/{run_id}/save-to-dataset
  Body: { dataset_name: "<name>" }       — creates dataset if it doesn't exist
  Returns: { dataset_id, item_id }

# Submit user feedback (thumbs up/down) overriding judge score
POST   /api/v1/playground/runs/{run_id}/feedback
  Body: { rating: "positive" | "negative", comment: "<optional>" }
  Stores as a Langfuse score annotation on the trace; overrides judge_score display in UI

# List saved datasets for a team
GET    /api/v1/playground/datasets?team=<team>
  Returns: [{ dataset_name, item_count, created_at, last_updated }]

# Run eval suite on-demand against an agent version
POST   /api/v1/playground/evals/run
  Body: {
    agent_name: "<name>",
    version: "<version_tag>",
    suite: "<promptfoo config name | dataset_name>",
    baseline_version: "<version_tag>"   — optional; enables diff view
  }
  Returns: { eval_run_id }              — poll GET below for results

# Get eval run results
GET    /api/v1/playground/evals/{eval_run_id}
  Returns: {
    status: "running" | "complete" | "failed",
    summary: { total, passed, failed, pass_rate },
    assertions: [
      {
        test_case, prompt, expected, actual,
        passed: bool, score: float,
        baseline_actual: str | null,     — present only if baseline_version provided
        baseline_passed: bool | null
      }
    ]
  }

# Compare two versions side-by-side
POST   /api/v1/playground/compare
  Body: {
    agent_name: "<name>",
    version_a: "<version_tag>",
    version_b: "<version_tag>",
    message: "<user message>",
    sandbox: bool
  }
  Returns: { run_id_a, run_id_b }       — fetch traces independently via /runs/{id}/trace
```

**Playground trace panel data flow:**
1. Studio sends `POST /playground/run` → gets `run_id` + streams SSE tokens into chat panel
2. On SSE `done` event, Studio calls `GET /playground/runs/{run_id}/trace` → populates trace panel
3. Registry API fetches the Langfuse trace by `run_id` (stored as `external_id` in Langfuse) and reshapes it into the above schema — Studio never calls Langfuse directly
4. Judge scoring runs async: Registry API polls Langfuse for the score and updates its local `playground_runs` table; Studio polls `GET /trace` every 3s until `judge_score` is non-null (max 30s)

---

#### Tool Registry API

```
# Tool CRUD
POST   /api/v1/tools                    — Create tool definition (type: native|http|mcp_tool|python)
                                          Python tools additionally accept python_code: str
                                          (defines run_tool(args: dict) -> str function body)
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
┌──────────────────────────────────────────────────────────────┐
│ Studio Canvas                                                 │
│                                                              │
│  [Agent: Triage] ──"refund"──→ [Agent: Refund Handler]      │
│        │                               │                     │
│        │──"default"──→ [End]      ──→ [End]                 │
│                                                              │
│  PropertiesPanel (selected agent):                          │
│    Instructions, Model, Risk                                 │
│    Tools:  ☑ lookup_order  ☑ get_customer                   │
│    Skills: ☑ Order Management (bundles 3 tools)             │
│                                                              │
│  PropertiesPanel (selected edge):                           │
│    Condition: "refund"  (keyword matched in agent output)   │
└──────────────────────────────────────────────────────────────┘
```

**Canvas node types**: `AgentNode` and `EndNode` only — tools are Registry resources, not canvas nodes.

**Tool and Skill association**: Agents declare which tools/skills they use via multi-select checkboxes in the PropertiesPanel. The declarative runner fetches tool definitions from the Registry API at pod startup and builds the LangGraph agent from them.

**Agent-to-agent routing**: Edges carry an optional `condition` string. When an agent's output contains the condition keyword (case-insensitive match), the declarative runner routes to that edge's target. The `default` condition (or blank) is the fallback path. One agent can fan out to multiple downstream agents via multiple conditional edges.

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
| AgentShield Studio | Agent creation (visual builder) + Playground (interactive testing, evals) | React, React Flow, TypeScript |
| Appsmith | Operations (registry, deploy, approvals, dashboards) | Appsmith (low-code) |
| Registry API | Backend for both UIs + Playground API + Eval runner orchestration | FastAPI, Postgres |
| Deploy Controller | Reconciles K8s state | Python, K8s client |

---

## Declarative Runner Execution Model

The Declarative Runner is a generic Python service (~600 lines) that loads a workflow JSON definition at startup and builds a LangGraph StateGraph from it. It uses the agentshield-sdk internally — no separate framework.

### Startup
On pod start, the runner reads `WORKFLOW_JSON` env var (base64-encoded), parses it, and calls `build_graph(definition)` to construct a compiled LangGraph `StateGraph`. The compiled graph is cached in memory for the pod lifetime.

### Workflow JSON Schema

The workflow definition stored in the `workflows.definition` column and base64-encoded into `WORKFLOW_JSON` env var:

```json
{
  "nodes": [
    {
      "id": "triage-agent",
      "type": "agent",
      "position": {"x": 100, "y": 200},
      "config": {
        "name": "triage-agent",
        "instructions": "Classify the user request and route to the right team.",
        "model": "claude-sonnet-4-6",
        "risk": "low",
        "tool_ids": ["uuid-of-lookup-order-tool"],
        "skill_ids": ["uuid-of-order-management-skill"]
      }
    },
    {"id": "end", "type": "end", "position": {"x": 600, "y": 200}, "config": {"output_mapping": {}}}
  ],
  "edges": [
    {"id": "e1", "source": "triage-agent", "target": "refund-agent", "condition": "refund"},
    {"id": "e2", "source": "triage-agent", "target": "end", "condition": "default"}
  ]
}
```

Only `agent` and `end` node types exist. `http_tool` nodes are gone from the canvas; HTTP tools are managed in the Tool Registry and referenced by UUID.

### Node Execution Model

**State:** The runner maintains a typed state dict `WorkflowState` with:
- `messages: list[BaseMessage]` — conversation history
- `outputs: dict[str, Any]` — named outputs from each node (key = node_id)
- `session_id: str` — from Safety Orchestrator scan response
- `__interrupt__: dict | None` — pending approval context

**Startup tool resolution**: Before building the graph, the runner fetches tool and skill definitions from the Registry API:
1. For each `skill_id` on an agent node → `GET /api/v1/skills/{id}` → collect its `tool_ids[]`
2. For each resolved `tool_id` → `GET /api/v1/tools/{id}` → fetch the tool definition
3. Build executor callables from fetched configs based on `type`:
   - `type=http` → `HttpToolExecutor` — makes an httpx call with `{{variable}}` substitution
   - `type=python` → `PythonToolExecutor` — POSTs `{code, args}` to `http://python-executor:8080/execute`; the Python Executor microservice runs `run_tool(args)` in a sandboxed subprocess and returns the string result
4. Pass executor callables to `create_react_agent(llm, tools=[...])` for that agent node

**Agent Node:**
- Creates a temporary `create_react_agent(llm, tools, checkpointer)` from node config + fetched tools
- LLM output stored in `state["messages"]`

**Conditional routing (edges with `condition`):**
- Outgoing edges from an agent node are grouped; if any have a `condition`, `add_conditional_edges` is used
- Routing function: examines the last AI message content for the condition keyword (case-insensitive substring match)
- If no keyword matches, takes the `default` edge (or first unconditional edge)
- One agent can fan out to multiple downstream agents

**Approval Gate Node** (Phase 2):
- Evaluates condition expression against `state["outputs"]`
- If met: creates approval record via Registry API, calls LangGraph `interrupt()`
- The LangGraph `interrupt()` checkpoints state to Postgres via `AsyncPostgresSaver`
- Resume: Postgres NOTIFY → `Runner.run(agent, Command(resume=decision))` on `/resume/{thread_id}`

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
- Visual builder import/export to other platforms (Langflow, Dify)

---

## Default Resource Catalog

AgentShield ships with a curated set of default tools, skills, agents, and workflows seeded at install time via `scripts/seed-defaults.sh`. The script is idempotent (409 = already exists → skip) and is called as step 8 in `scripts/deploy-cpe2e.sh`.

### Default Tools

| Name | Type | API / Execution | Credentials |
|------|------|----------------|-------------|
| `web-search` | http | `POST https://google.serper.dev/search` | `X-API-KEY: {{serper_api_key}}` in header |
| `weather-lookup` | http | `GET https://api.open-meteo.com/v1/forecast?latitude={{latitude}}&longitude={{longitude}}&current_weather=true` | Free, no key |
| `ip-geolocation` | http | `GET http://ip-api.com/json/{{ip}}` | Free, no key |
| `slack-notify` | http | `POST {{webhook_url}}` body `{"text":"{{message}}"}` | Webhook URL passed as `{{webhook_url}}` at call time |
| `http-echo` | http | `GET https://httpbin.org/anything/{{path}}` | Free, no key — useful for testing |
| `calculator` | python | `run_tool({"expression": "(5+3)*2"})` → AST-safe arithmetic via `ast` module | None — pure Python, no external API |

### Default Skills

| Name | Team | Bundled Tools |
|------|------|--------------|
| `web-research-skill` | platform | web-search, weather-lookup, ip-geolocation |
| `notification-skill` | platform | slack-notify |

### Default Agents

**Declarative agents** (run via declarative runner; each paired with a starter workflow):

| Name | Instructions summary | Tools / Skills |
|------|---------------------|----------------|
| `research-assistant` | Search the web, look up weather when location mentioned, geolocate IPs, summarize findings | skill: web-research-skill |
| `calculator-bot` | Never calculate in your head — always use the calculator tool | tool: calculator |
| `slack-notifier` | When asked to send a message, use slack-notify; confirm what was sent | skill: notification-skill |

**SDK reference agents** (agent_type=sdk, Registry entry only — code lives in source):

| Name | Source | Description |
|------|--------|-------------|
| `echo-agent` | `services/echo-agent/` | Minimal HTTP server — /health + /ready only. Reference implementation. |
| `order-agent` | `examples/order-agent/` | Order lookup + refund with HITL approval gate. SDK example. |

### Default Workflows (starter canvases for declarative agents)

| Workflow | Nodes | Edge |
|----------|-------|------|
| `research-workflow` | research-assistant → end | unconditional |
| `calculator-workflow` | calculator-bot → end | unconditional |
| `notification-workflow` | slack-notifier → end | unconditional |

---

## Future Improvements

### Conditional Routing Enhancements (Studio Canvas)

The current Phase 8 implementation uses **Option 1** (keyword match). Two stronger alternatives are deferred:

**Option 1 — Keyword match (current, implemented)**
The condition string on an edge is a plain keyword. The declarative runner checks whether the last agent output contains it (case-insensitive substring). Simple, zero-latency overhead, good for clear routing signals. Limitation: fragile if the agent phrases its response differently.

**Option 2 — Structured output field (planned, Phase 10)**
The agent is instructed to emit a JSON field (`route_to: "condition_name"`) as the last message. The runner parses `route_to` from the AI response. No keyword guessing — explicit contract between agent and router. Requires agent instruction discipline.

**Option 3 — LLM-evaluated condition (future)**
You write a natural-language condition on the edge (e.g., "the user mentioned a refund"). After each agent step, a small, fast LLM call evaluates the condition against the agent's output and returns `true/false`. Most flexible — conditions can express semantic intent not tied to specific wording. Tradeoff: adds one LLM round-trip of latency per edge evaluated, plus token cost. Suitable for complex routing logic where precision matters more than speed.

### In-Browser SDK Agent Editor (HIGHEST PRIORITY post-roadmap)

A Monaco editor embedded in Studio allows users to write `agent.py` directly in the browser. A Kaniko build service compiles it into a Docker image without requiring a local toolchain. Users never need to clone the repo, install Python, or run Docker locally to create SDK agents. The platform handles the full build-push-deploy loop from a browser tab.

### Execution-Mode Runtime + Memory (Phase 3 design, deferred)

Four first-class execution modes (reactive / durable / scheduled / event-driven) plus layered agent memory are designed but not yet implemented. Specs:
- `docs/design/execution-models-and-memory.md` — backend/data model (revised by Decisions 20–21)
- `docs/design/playground-execution-modes.md` — pre-publish evaluate surface (all modes)
- `docs/design/execution-modes-production.md` — production runtime + operations (all modes)

Scope decisions recorded 2026-07-03 (ship-simple-first, ideal deferred):
- **Reactive entry points** — both a consumer chat page *and* a public API endpoint for third-party integration.
- **Failure alerting** — launches **email-only**; multi-channel (Slack / webhook / PagerDuty), per-agent routing, and digests are future work. _Implemented (Phase 8):_ `agent_triggers.alert_email` + `alert_on_failure` columns; when an internal (scheduled/webhook) run completes `status=failed`, `alerting.dispatch_failure_alert` emails the configured recipient over SMTP (`SMTP_HOST/PORT/FROM` env; log-only fallback when `SMTP_HOST` unset). Configured per-trigger in Studio Settings.
- **Webhook token rotation** — launches **manual** (button); automatic expiry + dual-token overlap during cutover is future work.
- **System-run identity** — launches with a service-account name string as the run principal (scheduler/webhook runs); a dedicated managed Keycloak service principal with scoped RBAC + full audit is future work.
- **Internal-auth on `/api/v1/internal/*`** — the cluster-internal run-start endpoint (called by scheduler + event-gateway) launches protected by **NetworkPolicy only** (Phase 9 decision 2026-07-05). Adding a shared internal token / mTLS between callers and registry-api — so a rogue in-namespace pod can't dispatch runs freely — is a tracked future improvement (see `docs/design/event-gateway-threat-model.md` T-8).

Also resolved 2026-07-03 (v1, not deferred): reviewer approval view is **anonymized** (PII never shown to reviewer/LLM/agents); scheduler runs **2 replicas + distributed lock**; long-running run timeout is a **configurable** per-agent setting.

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

**Studio v0 deliverables (Phase 1 — implemented in Phase 8)**:
- React + React Flow canvas with 2 node types: **AgentNode** and **EndNode** (HttpToolNode removed from canvas)
- PropertiesPanel per node type:
  - AgentNode: instructions, model selector, risk, **tool multi-select** (fetched from Tool Registry), **skill multi-select** (fetched from Skill Registry)
  - EdgePanel: condition keyword for conditional routing between agents
  - EndNode: output mapping config
- Conditional agent-to-agent edges: one agent fans out to multiple agents via condition keywords
- Skills Registry: named bundles of tools reusable across agents; CRUD at `/api/v1/skills/`
- Save workflow to Registry API (JSON serialization)
- First-save modal: workflow name + team assignment
- One-click Deploy → declarative runner pod (no container build)
- Declarative Runner pod: fetches tool/skill definitions from Registry API at startup; uses `add_conditional_edges` for routing
- **Tools page**: list + create + **edit** (inline form pre-populated with existing fields; `name` and `type` read-only in edit; python_code textarea for python tools) + delete
- **Agents page**: list + register + **edit** (inline form; editable: description, status) + **delete** (soft-delete → status=deprecated) + deploy

**Exit criteria**: A developer can create an agent with `Agent(instructions, tools)` and deploy via SDK path. A product team member can build a multi-agent workflow visually in Studio, link tools from the Registry, set routing conditions between agents, and deploy with one click. Both paths produce governed agents (safety scan, OPA, HITL, tracing).

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

---

### Phase 2.5 — Agent Playground (Weeks 10-13, overlaps Phase 2)

> **Full spec**: [`docs/design/playground-spec.md`](design/playground-spec.md)

The Playground is a tab in Studio — not a separate deployment. It shares Registry API, Langfuse, and the existing `/chat/stream` endpoint. Runs in a dedicated `agentshield-playground` namespace; all governance (safety scanning, OPA, HITL) still applies.

| Week | Deliverable |
|------|-------------|
| 10 | Playground tab: interactive chat panel (SSE), sandbox mode toggle, Class A/B daemon handling |
| 11 | Inline trace panel: LLM calls, tool calls, safety scores, OPA decisions; Registry API proxies Langfuse |
| 12 | Dataset curation, LLM-as-Judge async scoring (FR-018), user feedback override |
| 13 | On-demand eval runner (K8s Job + promptfoo), side-by-side version comparison |

Key design choices — see spec for detail and rationale:
- **User-scoped, private**: runs are visible only to the user who created them
- **Any owned version testable** regardless of publication status (private/pending_review/published)
- **Sandbox grant-bypass**: OPA skips the team grant check in sandbox mode; agent scope still enforced
- **Playground HITL**: self-approved by asset owner via an inline panel; no Slack notification; separate from the production approval queue
- **Eval Jobs use the triggering user's identity**; daemon agents run with playground SA (no user JWT)
- All traces tagged `context=playground` in Langfuse; excluded from production cost dashboards

**Exit criteria**: Developer opens an agent in Playground, sends a test message in sandbox mode, sees the inline trace, self-approves any HITL, saves the run to a dataset, runs an eval suite, and compares two versions — all without leaving Studio and without touching production traffic.

### Phase 3 — Maturity & Ecosystem (Weeks 13+)

| Week | Deliverable |
|------|-------------|
| 13 | Hardening: tune safety thresholds, custom Presidio recognizers, chunked scanning |
| 14 | Studio v2: DB query tool type, code snippet tool type, workflow templates gallery |
| 15 | SDK v3: `AgentGraph(StateGraph(...))` escape hatch documented; full LangGraph power with platform governance |
| 16+ | Ongoing maturity items (below) |

**Ongoing**:
- LLM-as-Judge automated scoring (Phase 2.5 delivers inline Playground scoring; post-hoc annotation queues on all production traces are Phase 3+)
- SDK extraction (contract-based mode for non-Python teams)
- Admission webhook for contract validation
- Studio import/export formats
- Multi-cluster support investigation

---

## Authorization Model

> **Full spec**: [`docs/design/authorization-model-spec.md`](design/authorization-model-spec.md)  
> **Requirements**: [`docs/authorization-model.md`](authorization-model.md)

Authorization covers three lifecycle stages: authoring (private workspace), control plane (publish + grant + deploy gate), and data plane (runtime enforcement). The current implementation has OPA risk labels but no identity-based enforcement, no publish/grant lifecycle, and no deploy gate. This section describes the target state.

**Agent identity** — each deployed agent gets a dedicated K8s ServiceAccount. Istio Ambient Mesh (ztunnel) provides L4 mTLS between pods using SPIFFE/SVID certificates minted per-SA. OPA policy is keyed on the SA subject string (`system:serviceaccount:agentshield:agent-{name}-sa`), not the agent name — a rogue pod that sends the correct name but can't present the matching SA token gets denied.

**Agent classes** — every agent is classified at publish time:
- **Class A (Daemon)**: no user present; runs on its own machine identity; rejects any request carrying a user JWT
- **Class B (User-Delegated)**: user identity threaded to every tool call; OPA enforces the intersection rule: `effective_permissions = agent_registered_scope ∩ user_granted_permissions`

**Asset lifecycle** — assets move through `private → pending_review → published`. Admin approval gates the transition and grants access to specific teams. A team must have an active grant to every tool in an agent's dependency graph before deployment is permitted.

**HITL approval authority** — approval rights are scoped per-agent via the `approver` artifact-scoped role (see RBAC below). Reviewers see only HITL requests for agents they hold the `approver` role on. In the Playground, the asset owner self-approves; no Slack notification fires; production and playground approval queues are completely separate.

**Platform RBAC (Decision 25)** — two-tier role model for control-plane authorization:

> **Full spec**: [`docs/design/rbac-design.md`](design/rbac-design.md)

- **Global roles** (one per user, stored in `user_team_assignments.role`): `platform-admin` (full access), `contributor` (create/deploy sandbox/submit publish), `viewer` (read-only, no playground)
- **Artifact-scoped roles** (many per user, stored in `artifact_role_grants`): `agent-admin` (manage production deployments, delegate roles), `approver` (receive and decide HITL requests)
- Grants target users or teams (polymorphic grantee). Creator auto-receives `agent-admin` on artifact creation. Production deploy requires `platform-admin` or `agent-admin`. HITL routed to `approver` holders.

**Implementation phasing** (3 phases — see spec for detail):
- Phase 1: OPA Bundle Server + K8s SA tokens + agent_class field (replaces per-agent ConfigMaps)
- Phase 2: publish/grant lifecycle + deploy gate in Registry API
- Phase 3: migrate from OPA sidecar per-pod to centralized Waypoint + OPA (Option B — no intermediate phase)

Key decisions captured in [`docs/decisions.md`](decisions.md) (D16–D19, D25).

---

## ⚠️ TODO — Highest Priority (Post-Implementation)

### In-Browser SDK Agent Editor + Platform-Managed Image Build

**Why:** The current SDK agent workflow is CLI-first — developers write `agent.py` locally, build and push their own Docker image, then register via `agentshield deploy --image <tag>`. This creates friction for non-DevOps users and breaks the self-service experience.

**Desired experience:**
1. User opens Studio → Create Agent → selects type "SDK"
2. Studio presents a Monaco code editor pre-populated with an `agent.py` template
3. User writes tools and `Agent(...)` constructor in the browser
4. On submit, the platform:
   - Saves `agent.py` to a versioned object store (MinIO bucket `agent-source`)
   - Spawns a Kaniko build job in K8s (or BuildKit daemon) to build the image
   - Streams build logs back to Studio via SSE
   - On success, pushes image to internal registry and auto-creates an `agent_version` record
   - Triggers deployment if user checked "deploy immediately"

**Scope to build:**
- Studio: Monaco editor component in `CreateAgentPage.tsx` (and a new `EditAgentPage`)
- Build service: new `services/build-service/` — FastAPI that accepts `{agent_name, source_code}`, runs Kaniko job via K8s Jobs API, streams logs, updates version status
- Registry API: `agent_versions.source_url` column (points to MinIO object) + `build_status` field
- MinIO: `agent-source` bucket with per-version paths `{team}/{agent}/{version}/agent.py`
- Studio: build log stream panel (EventSource on `GET /api/v1/agents/{name}/versions/{id}/build-logs`)
- Dockerfile template: baked into build service, not user-editable (security constraint)

**Security constraints:**
- User-supplied `agent.py` runs inside a container, not on the platform host
- Kaniko runs in a dedicated `agentshield-builds` namespace with network egress limited to registry + PyPI
- No `FROM` override — the base image is always `python:3.12-slim` + `agentshield-sdk`
- Source stored in MinIO, not Git (Git integration is a future enhancement)

---

## Open Questions for Reviewers

| # | Question | Context | Options Considered | Blocked Decision |
|---|----------|---------|-------------------|-----------------|
| 1 | Which Envoy deployment model? | Envoy can run as a standalone pod (Envoy Gateway) or as a sidecar per agent | Standalone gateway (simpler, central control) vs. sidecar (more granular) | How to configure rate limiting — per-gateway rules vs per-pod |
| 2 | Sync replica count for Postgres | HA requires at least 1 sync replica; 2 gives stronger durability but higher write latency | 1 sync replica (standard) vs 2 sync replicas (highest durability) | Recovery time vs write performance trade-off |
| 3 | Should canary analysis be automated? | Canary deploys currently require manual promotion. Auto-promote based on error rate is possible but adds complexity. | Manual promote (simpler, human judgment) vs auto-promote (faster, needs good metrics) | Whether to build auto-canary in Phase 2 or defer |
| 4 | Log aggregation stack | Platform needs centralized logs but no stack was selected | EFK (Elasticsearch+Fluentd+Kibana) vs Loki+Grafana (lighter) vs defer to existing | Operational cost vs capability trade-off |
| 5 | PgBouncer pool sizing | Many clients (agents + platform services) sharing one Postgres; pool size affects concurrency | 100 connections (conservative) vs 200 (generous) vs dynamic based on pod count | Right-size without hitting Postgres max_connections |
