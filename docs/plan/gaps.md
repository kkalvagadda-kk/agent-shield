# AgentShield — Gap Register
_Last updated: 2026-06-25_
_Source: E2E scenario validation across 7 personas_

## Status Legend
- 🔴 Open — not yet addressed
- 🟡 Specced — added to spec/PRD but no implementation task yet
- 🟢 Fixed — spec updated AND implementation task exists (or already implemented)

## Critical Gaps (19)

| ID | Description | Status | Fixed In | Phase |
|----|-------------|--------|----------|-------|
| C-01 | PII in approval card — reviewer sees `<EMAIL_0>` placeholder, can't identify customer | 🟢 Fixed | `approvals.session_id` added to models + migration; FR-027a in PRD; reviewer RBAC de-anonymize path in spec | Phase 2 |
| C-02 | LISTEN/NOTIFY vs PgBouncer — agents need direct Postgres connection for LangGraph resume | 🟢 Fixed | `allow-agent-egress-postgres-direct` NetworkPolicy added; spec Constraints updated | Phase 1 |
| C-03 | Multi-agent handoff bypasses Safety — receiving agent gets unscanned input | 🟡 Specced | Multi-Agent Handoff Path section added to spec; needs SDK implementation task in Phase 8 | Phase 8 |
| C-04 | `session_id` not propagated across agent handoffs | 🟡 Specced | Covered by C-03 header propagation; needs SDK task | Phase 8 |
| C-05 | `intake-agent` execution model during handoff undefined — how does it wait for `refund-agent` HITL (30 min) | 🟡 Specced | Needs SDK v2 implementation task in Phase 8 | Phase 8 |
| C-06 | Declarative runner `{{variable}}` interpolation unspecified | 🟡 Specced | Declarative Runner Execution Model section added to spec; needs Phase 9 implementation | Phase 9 |
| C-07 | Studio Approval Gate condition evaluator unspecified — context variable flow | 🟡 Specced | Covered by Declarative Runner spec; needs Phase 9 task | Phase 9 |
| C-08 | Dual approval gating conflict — Studio gate vs OPA | 🟢 Fixed | Resolved in spec: OPA always first, Studio gate adds conditional escalation | Phase 6 |
| C-09 | Declarative runner interrupt/resume unspecified | 🟡 Specced | Spec says "wraps LangGraph"; needs Phase 9 implementation task | Phase 9 |
| C-10 | No API endpoint for workflow version restore | 🟢 Fixed | `POST /workflows/{id}/versions/{v}/restore` added to `routers/workflows.py` | Phase 2 |
| C-11 | Workflow rollback/restore-to-deploy undefined | 🟢 Fixed | Same as C-10; PUT now auto-versions | Phase 2 |
| C-12 | Approval timeout enforcement is Phase 2 — no timeout in Phase 1 | 🟡 Specced | Needs cron job implementation task in Phase 3 | Phase 3 |
| C-13 | Resume mechanism after timeout-triggered denial unspecified | 🟡 Specced | Timeout worker must issue Postgres NOTIFY; needs Phase 3 task | Phase 3 |
| C-14 | Helm startup dependency ordering — Registry API crashes before Keycloak ready | 🟢 Fixed | `wait-for-postgres` and `wait-for-keycloak` init containers added to Helm deployment | Phase 1 |
| C-15 | No post-install Keycloak runbook | 🟡 Specced | Needs `scripts/post-install.sh` task | Phase 1 |
| C-16 | No PodDisruptionBudgets for safety scanner pods | 🟡 Specced | NFR-017a added to PRD; needs PDB templates added to scanner Helm charts in Phase 5 | Phase 5 |
| C-17 | OPA decision log schema unspecified — FR-025 P0 has no data model | 🟢 Fixed | `opa_decisions` table in models.py + migration | Phase 2 |
| C-18 | No cross-reference OPA decisions ↔ approvals | 🟢 Fixed | `opa_decision_id` FK on `Approval` model | Phase 2 |
| C-19 | No emergency per-pod quarantine endpoint | 🟡 Specced | FR-009a in PRD; needs `POST /agents/{name}/quarantine` endpoint in Phase 3 | Phase 3 |

## Major Gaps (31)

| ID | Description | Status | Fixed In | Phase |
|----|-------------|--------|----------|-------|
| M-01 | CI red-team probes (FR-043) are Phase 3 — agents ship without red-team scanning until then | 🔴 Open | Phase gap, known, no task yet | Phase 3 |
| M-02 | NeMo YARA integration is Phase 2 (Week 8) — Phase 1 has LLM Guard + Presidio only | 🔴 Open | Phase gap, known | Phase 5 |
| M-03 | Slack approval notifications are Phase 2 (Week 9) — no push notification in Phase 1 | 🔴 Open | Phase gap, known | Phase 7 |
| M-04 | Garak weekly scans are Phase 3 (Week 14) | 🔴 Open | Phase gap, known | Phase 3 |
| M-05 | `handoffs=[...]` is SDK v2 Phase 2 | 🔴 Open | Phase gap, known | Phase 8 |
| M-06 | Approval Gate node is Studio Phase 2 | 🔴 Open | Phase gap, known | Phase 9 |
| M-07 | Diff view is Studio Phase 2/P2 | 🔴 Open | Phase gap, known | Phase 9 |
| M-08 | No cross-agent trace stitching — each agent emits independent Langfuse traces | 🟡 Specced | Specced in C-03/C-04; needs SDK Phase 8 task | Phase 8 |
| M-09 | Eval failure details not persisted to Langfuse when CI fails (FR-045 is Phase 3) | 🔴 Open | — | Phase 3 |
| M-10 | No new-team onboarding automation — namespace, NetworkPolicy, Keycloak role all manual | 🔴 Open | Needs `scripts/onboard-team.sh` task | Phase 3b |
| M-11 | Team not a first-class entity — no `teams` table, no team CRUD API | 🔴 Open | Needs data model + API task | Phase 2 |
| M-12 | NetworkPolicy egress too broad — `10.0.0.0/8:443` CIDR catch-all | 🔴 Open | Architectural decision needed | Phase 1 |
| M-13 | Safety Orchestrator connection management unspecified — retry, circuit breaker on scanner restart | 🔴 Open | Needs Phase 5 task | Phase 5 |
| M-14 | No staging environment concept — no way to validate scanner changes before production | 🔴 Open | Needs `infra/namespaces/agentshield-staging.yaml` and pattern | Phase 5 |
| M-15 | NeMo YARA rule deployment lifecycle unspecified | 🟡 Specced | ConfigMap + SIGHUP in spec; needs `policies/nemo/rules/` scaffold and CI validation task | Phase 3b |
| M-16 | No automated PII-in-traces audit | 🔴 Open | Needs Langfuse custom dashboard or ClickHouse query task | Phase 9b |
| M-17 | No ad-hoc Garak run capability | 🔴 Open | Needs `scripts/run-garak.sh` task | Phase 3b |
| M-18 | NeMo YARA rule format and default rule set unspecified | 🟡 Specced | Needs `policies/nemo/rules/default.yar` task | Phase 3b |
| M-19 | No incident response runbook | 🔴 Open | Needs `docs/runbooks/incident-response.md` task | Phase 3b |
| M-20 | No security alerting beyond Prometheus — no SIEM, PagerDuty | 🔴 Open | Phase 5+ task | Phase 5 |
| M-21 | Workflow `PUT` auto-versioning not implemented | 🟢 Fixed | `routers/workflows.py` PUT now auto-archives before overwriting | Phase 2 |
| M-22 | No workflow naming/first-save UX in Studio | 🔴 Open | Phase 9 Studio task | Phase 9 |
| M-23 | Auth config scope in Studio v0 unclear | 🔴 Open | Phase 9 Studio task | Phase 9 |
| M-24 | Appsmith bootstrap undefined | 🔴 Open | Needs `appsmith/import-apps.sh` or documented import files task | Phase 9b |
| M-25 | No Harbor/private registry integration (imagePullSecrets) | 🔴 Open | Phase 1 Helm values task | Phase 1 |
| M-26 | `approvals.context` content undefined — SDK/runner must specify what to populate | 🔴 Open | Needs spec addition + SDK task | Phase 8 |
| M-27 | Conflict UX unspecified — second reviewer gets opaque error | 🔴 Open | Phase 7 Appsmith task | Phase 7 |
| M-28 | SSE protocol missing `approval_timeout` event | 🔴 Open | Phase 8 SDK task | Phase 8 |
| M-29 | No re-trigger for timed-out approvals | 🔴 Open | Needs API endpoint | Phase 3 |
| M-30 | No Slack notification when approval times out | 🔴 Open | Phase 3 task | Phase 7 |
| M-31 | Langfuse trace schema — team/agent_id metadata tags unspecified | 🔴 Open | Phase 8 SDK task | Phase 8 |

## Minor Gaps (5)

| ID | Description | Status | Notes |
|----|-------------|--------|-------|
| Mi-01 | Diff view deferred to Phase 2/P2 | 🔴 Open | Known phase gap |
| Mi-02 | Slack notification routing (channel vs DM) unspecified | 🔴 Open | — |
| Mi-03 | OPA policy propagation via ConfigMap may not meet 60s promise — no active reload | 🔴 Open | — |
| Mi-04 | Per-team onboarding time estimate excludes platform-engineer setup | 🔴 Open | — |
| Mi-05 | False positive rate not measurable until Phase 4 annotation queue | 🔴 Open | Known phase gap |
| Mi-06 | **OPA governance bypassed globally via DEV_MODE** — `deploy-controller/manifest_builder.py` injected `OPA_URL` but the SDK reads `AGENTSHIELD_OPA_URL` (`sdk/agentshield_sdk/config.py:21`). With `AGENTSHIELD_OPA_URL` unset the SDK set `DEV_MODE=True`, used a mock OPA client, and returned `require_approval=False, allow=True` for every tool call on every deployed agent — silently. The injected OPA sidecar was never consulted. **Fix applied:** `manifest_builder.py` now also injects `AGENTSHIELD_OPA_URL=http://localhost:8181`. **Remaining (not-yet-wired, debt):** canary-verify the real OPA allow-path (projected SA token identity + bundle load via `/api/v1/bundle/bundle.tar.gz` → nginx bundle-sync → sidecar poll) before relying on `require_approval`. Flipping real OPA on globally is a behavior change — every deployed agent transitions from mock-allow to enforced. **Live-verified 2026-07-06:** a running `opa-gov-*` agent pod (deployed by controller 0.1.7) has `OPA_URL=http://localhost:8181` but no `AGENTSHIELD_OPA_URL` — confirming the bug on a real pod. **Rollout is soft, not a hard global flip:** a pod's env is baked at creation, so already-running agents keep mock-allow until they are next redeployed; only new/redeployed agents (via controller ≥0.1.8) pick up enforced OPA. So the canary can be done one agent at a time. Cross-ref: Mi-03 (OPA policy propagation timing), Decision 26. | 🟡 Specced | env fix in `manifest_builder.py` (controller 0.1.8 deployed); allow-path canary not yet verified |

---

## Gaps Fixed by Phase

### Gaps Fixed in Phase 1 Implementation
- C-02 (`allow-agent-egress-postgres-direct` NetworkPolicy)
- C-14 (`wait-for-postgres` and `wait-for-keycloak` init containers)

### Gaps Fixed in Phase 2 Implementation
- C-01 (`approvals.session_id`, FR-027a in PRD)
- C-10 (`POST /workflows/{id}/versions/{v}/restore`)
- C-11 (PUT auto-versioning)
- C-17 (`opa_decisions` table in models.py + migration)
- C-18 (`opa_decision_id` FK on Approval model)
- M-21 (PUT auto-archives before overwriting)

### Gaps Fixed in Spec/PRD Only (need implementation tasks)
C-03, C-04, C-05, C-06, C-07, C-09, C-12, C-13, C-15, C-16, C-19, M-08, M-15, M-18

### Gaps Still Open (need architectural decision or are known phase gaps)
M-01, M-02, M-03, M-04, M-05, M-06, M-07, M-09, M-12, M-14, M-20, Mi-01, Mi-02, Mi-03, Mi-04, Mi-05
