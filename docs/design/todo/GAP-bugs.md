# Consolidated Open Gaps & Bugs

**Last updated:** 2026-07-09
**Sources:** slice-implementation-assessment.md, execution-models-gap-analysis.md, future-improvements.md, cost-tracking.md, unified-artifact-deployment-navigation.md, gaps.md, testing-gaps-plan.md, execution-modes-production.md, production-workflow-deployment.md

Items marked FIXED/DONE/RESOLVED in source docs are excluded. Duplicates across docs are merged (source noted in parentheses).

---

## Priority 1 — Blocks core user journeys

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P1-01 | Eval | Large | Workflow eval Job targets single agent, not workflow orchestration endpoint. DB linkage wired but eval-runner needs workflow invocation mode. | slice G21, gaps C-06 |
| P1-02 | Lifecycle | Small | No Publish button on WorkflowDetailPage — backend supports it, no UI entry point | slice G11 |
| P1-03 | RBAC | Large | RBAC table exists but zero enforcement on catalog/deployment actions — any authenticated user can do anything | slice G14, gaps M-11, unified-artifact auth stub |
| P1-04 | Alerting | Medium | Failed scheduled/event-driven runs logged but nobody notified — no email, Slack, or webhook alerts | exec-gap TODO-2, exec-prod P-6 |
| P1-05 | Eval | Small | `adversarial_eval_passed` has no auto-set path and no UI trigger — high/critical-risk agents require manual PATCH | slice G18 |
| P1-06 | Cost | Medium | Agent pods bypass Portkey proxy — `manifest_builder.py` must inject `OPENAI_BASE_URL` so LLM traffic routes through Portkey | cost Gap 2 |
| P1-07 | Cost | Medium | No cost writeback — on run completion, cost/token data never written to `agent_runs` (columns exist, always NULL) | cost Gap 4 |
| P1-08 | Cost | Small | Portkey disabled in Helm — needs `portkey.enabled: true` + virtual key config | cost Gap 1 |
| ~~P1-09~~ | ~~Tools~~ | ~~Medium~~ | ~~RESOLVED 2026-07-09: Full credential management — K8s Secret auto-create, deploy-controller envFrom mount, header `{{var}}` substitution from env, Studio Credentials page + tool dropdown. registry-api:0.2.121, deploy-controller:0.1.27, declarative-runner:0.1.19, studio:0.1.103~~ | ~~bug-investigation~~ |

---

## Priority 2 — Workflow/deployment parity gaps

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P2-01 | Workflow | Small | Workflow deployment has no memory tab | slice G2, G17 |
| P2-02 | Workflow | Small | Workflow deployment has no upgrade action — must terminate+redeploy | slice G3 |
| P2-03 | Workflow | Medium | No member-dependency check before workflow deploy — workflow can deploy when member agents aren't running | slice G9 |
| P2-04 | Workflow | Medium | No member-health display on workflow overview | slice G10 |
| P2-05 | Workflow | Medium | No workflow deployment chat surface | slice G16 |
| P2-06 | Workflow | Large | No per-step execution breakdown in workflow overview | slice G12 |
| P2-07 | Prod WF | Medium | Production workflow pods: sequential mode only (sandbox supports all 4 orchestration modes) | prod-wf deferred #1 |
| P2-08 | Prod WF | Medium | No HITL in production workflow pods — checkpoint/resume is sandbox-only | prod-wf deferred #2 |
| P2-09 | Prod WF | Small | Edge graph support in pod orchestrator — uses position ordering, not topological sort | prod-wf deferred #3 |
| P2-10 | Prod WF | Medium | Graceful failover — if orchestrator pod crashes mid-run, runs stay `running` forever | prod-wf deferred #4 |
| P2-11 | Prod WF | Small | Version drift detection — no warning when member agent's deployed version differs from workflow snapshot | prod-wf deferred #5 |

---

## Priority 3 — UX improvements

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P3-01 | UX | Medium | No config viewer on version row — users can't verify what they're deploying | slice G5 |
| P3-02 | UX | Medium | No version diff (compare v1 vs v2) | slice G7 |
| P3-03 | UX | Small | No `suspended_at` timestamp tracked on deployments | slice G4 |
| P3-04 | UX | Small | Global Approvals Inbox — sidebar nav has no pending-count badge, no real-time indicator | exec-gap TODO-1 |
| P3-05 | UX | Medium | CatalogDetailPage doesn't reuse mode-aware Overview components (OverviewReactive/Durable/Scheduled/EventDriven) | exec-gap TODO-4 |
| P3-06 | UX | Small | No single production deployment GET endpoint (`GET /catalog/{id}/deployments/{did}`) | slice G13 |
| P3-07 | UX | N/A | Approve doesn't auto-deploy — may be intentional but undocumented | slice G15 |
| P3-08 | Lifecycle | Medium | No "Fork from Catalog" to sandbox — lifecycle is one-way (sandbox → catalog, no reverse) | slice G23 |
| P3-09 | UX | Large | Overview pages not shared/kind-aware — agent and workflow are separate implementations | slice G1 |

---

## Priority 4 — Cost tracking (Portkey integration)

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P4-01 | Cost | Medium | SDK uses native Anthropic client — `ChatAnthropic` doesn't respect `OPENAI_BASE_URL`; needs Portkey pass-through | cost Gap 3 |
| P4-02 | Cost | Medium | No cost UI in Studio — no token/cost badges on runs, no cost dashboard | cost Gap 5 |
| P4-03 | Tracing | Small | Agent pods missing Langfuse env vars — `AGENTSHIELD_LANGFUSE_KEY` + `AGENTSHIELD_LANGFUSE_HOST` not injected by deploy-controller | cost Gap 6 |
| P4-04 | Cost | N/A | Open Q: Portkey virtual keys — one per team (attribution) or one global? | cost OQ-1 |
| P4-05 | Cost | N/A | Open Q: Multi-model runs — sum into one `cost_usd` or per-step breakdown? | cost OQ-4 |

---

## Priority 5 — Testing gaps

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P5-01 | Test | Small | SDK has zero tests — no pytest suite for `sdk/agentshield_sdk/` | testing Gap 1 |
| P5-02 | Test | Medium | Vitest SSE path never exercised — `MockEventSource` never fires `onmessage` | testing Gap 2 |
| P5-03 | Test | Small | Vitest fixtures are all happy-path strings — object/array/null results never tested | testing Gap 3 |
| P5-04 | Test | Medium | Playwright never runs an agent — no browser test for SSE→render→trace→HITL flow | testing Gap 4 |
| P5-05 | Test | Medium | Bash e2e never chains cross-suite journeys — save-to-dataset then eval never connected | testing Gap 5 |
| P5-06 | Test | N/A | No test coverage for: Catalog CRUD, Consumer Chat UI, Admin pages, Tools/Skills/Providers CRUD, Observability pages, DatasetsPage, EvalResultsPage | slice test matrix |
| P5-07 | Test | Small | Playwright `global-setup` Keycloak login broken — browser-journey E2E gate cannot run | unified-artifact known gap #3 |

---

## Priority 6 — Security & safety (specced, not yet implemented)

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P6-01 | Safety | Large | Multi-agent handoff bypasses Safety — receiving agent gets unscanned input | gaps C-03 |
| P6-02 | Safety | Medium | `session_id` not propagated across agent handoffs | gaps C-04 |
| P6-03 | Safety | Medium | `intake-agent` execution model during handoff undefined (30-min HITL wait) | gaps C-05 |
| P6-04 | Safety | Medium | Approval timeout enforcement — no timeout in Phase 1 | gaps C-12 |
| P6-05 | Safety | Medium | Resume mechanism after timeout-triggered denial unspecified | gaps C-13 |
| P6-06 | Safety | Small | No post-install Keycloak runbook (`scripts/post-install.sh`) | gaps C-15 |
| P6-07 | Safety | Small | No PodDisruptionBudgets for safety scanner pods | gaps C-16 |
| P6-08 | Safety | Medium | No emergency per-pod quarantine endpoint | gaps C-19 |
| P6-09 | Security | Medium | NetworkPolicy egress too broad — `10.0.0.0/8:443` CIDR catch-all | gaps M-12 |
| P6-10 | Safety | Large | Safety Orchestrator connection management unspecified (retry, circuit breaker) | gaps M-13 |
| P6-11 | Safety | Medium | No CI red-team probes — agents ship without red-team scanning | gaps M-01 |
| P6-12 | Safety | Medium | NeMo YARA integration deferred — Phase 1 has LLM Guard + Presidio only | gaps M-02 |
| P6-13 | Safety | Small | Slack approval notifications — no push notification | gaps M-03 |
| P6-14 | Safety | Medium | No automated PII-in-traces audit | gaps M-16 |
| P6-15 | Safety | Medium | No incident response runbook | gaps M-19 |
| P6-16 | Safety | Medium | No security alerting beyond Prometheus — no SIEM, PagerDuty | gaps M-20 |

---

## Priority 7 — Infrastructure & platform (deferred / future)

| ID | Area | Effort | Description | Source |
|----|------|--------|-------------|--------|
| P7-01 | Infra | Medium | No staging environment concept | gaps M-14 |
| P7-02 | Infra | Medium | No Harbor/private registry integration (`imagePullSecrets`) | gaps M-25 |
| P7-03 | Infra | Medium | No new-team onboarding automation (namespace, NetworkPolicy, Keycloak role all manual) | gaps M-10 |
| P7-04 | Infra | Small | Role-based run/memory filtering — no `agent:user`/`agent:reviewer`/`agent:admin` filtering | exec-gap TODO-5 |
| P7-05 | Infra | Low | Redis memory hot path — all memory reads are direct PG queries, no Redis caching | exec-gap TODO-6 |
| P7-06 | Infra | Low | Sandbox run TTL / auto-cancel — stuck durable runs hang indefinitely | exec-gap TODO-7 |
| P7-07 | Infra | Medium | No cross-agent trace stitching — independent Langfuse traces per agent in handoffs | gaps M-08 |
| P7-08 | Infra | Medium | Langfuse trace schema — team/agent_id metadata tags unspecified | gaps M-31 |
| P7-09 | Data | Small | Legacy playground data — pre-migration-0040 runs have `sandbox_deployment_id = NULL` | unified-artifact known gap #1 |
| P7-10 | Data | Small | Legacy memory — existing `agent_memory` rows have `deployment_id = NULL` | unified-artifact known gap #2 |

---

## Future improvements (nice-to-have, no timeline)

| ID | Area | Description | Source |
|----|------|-------------|--------|
| F-01 | Deploy | Traffic splitting / canary support — gradual rollout between two deployments | future-improvements, unified-artifact |
| F-02 | UX | Deployment event log — live infra event stream alongside overview | future-improvements, unified-artifact |
| F-03 | Deploy | Blue-green rollback with in-flight request draining | future-improvements, unified-artifact |
| F-04 | Alerting | Evolve alerting from email to multi-channel (Slack, webhook, PagerDuty) | exec-prod FI-1 |
| F-05 | Security | Webhook token rotation — automatic expiry + dual-token overlap | exec-prod FI-2 |
| F-06 | Identity | System-run identity — evolve from service-account name to Keycloak managed identity | exec-prod FI-3 |

---

## Summary

| Priority | Count | Theme |
|----------|-------|-------|
| P1 — Blocks core journeys | 9 | Workflow eval, RBAC, alerting, cost plumbing, tool auth |
| P2 — Workflow/deploy parity | 11 | Workflow memory, upgrade, health, production modes |
| P3 — UX improvements | 9 | Version viewer, diff, approvals badge, fork-from-catalog |
| P4 — Cost tracking | 5 | Portkey integration, cost UI, Langfuse injection |
| P5 — Testing gaps | 7 | SDK tests, SSE Vitest, Playwright, cross-suite journeys |
| P6 — Security & safety | 16 | Handoff safety, RBAC, red-team, PII audit, incident runbook |
| P7 — Infra & platform | 10 | Staging, Harbor, onboarding, Redis cache, trace stitching |
| F — Future | 6 | Canary, event log, blue-green, alerting channels |
| **Total** | **73** | |
