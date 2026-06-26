# AgentShield Phase 1 — Task List

**Total tasks:** 169 (161 implementation + 8 checkpoint)
**Phases:** 15 (11 implementation + 4 checkpoint gates) — added Phase 3b (Operations & Runbooks) and Phase 9b (Observability & Dashboards)
**Parallel opportunities:** noted inline with [P]
**Checkpoint phases:** CP1 (after Phase 2), CP2 (after Phase 3), CP3 (after Phase 5), CP4 (after Phase 7)
**Gap register:** see `docs/plan/gaps.md` for full gap tracking (19 critical, 31 major, 5 minor)

---

## Phase 1 — Infra Setup (Week 1)
_Depends on: Nothing — this is the foundation_

- [X] [T001] Umbrella chart metadata and sub-chart dependency list — `charts/agentshield/Chart.yaml`
- [X] [T002] Top-level Helm values with sub-chart enable flags and global overrides — `charts/agentshield/values.yaml`
- [X] [T003] Platform namespace manifest with labels for network policy selection — `infra/namespaces/agentshield-platform.yaml`
- [X] [T004] First team namespace manifest (agents-platform template) — `infra/namespaces/agents-platform.yaml`
- [X] [T005] Kustomization file wiring both namespace manifests — `infra/namespaces/kustomization.yaml`
- [X] [T006] [P] Default-deny NetworkPolicy for agentshield-platform namespace — `infra/network-policies/platform-default-deny.yaml`
- [X] [T007] [P] Allow-ingress NetworkPolicy: Envoy→Safety, Safety→Scanners, Registry→Postgres — `infra/network-policies/platform-allow-ingress.yaml`
- [X] [T008] [P] Default-deny NetworkPolicy template for agents-{team} namespaces — `infra/network-policies/agents-default-deny.yaml`
- [X] [T009] [P] Allow-egress NetworkPolicy for agent pods: safety, postgres, langfuse, dns, internal APIs — `infra/network-policies/agents-allow-egress.yaml`
- [X] [T010] [P] CloudNativePG Postgres HA sub-chart values: sync replication, 5-database init SQL ConfigMap, DB users — `charts/agentshield/charts/postgresql/values.yaml`
- [X] [T011] [P] PgBouncer sub-chart values: transaction pool mode, max_client_conn=200, pool_size=25 — `charts/agentshield/charts/pgbouncer/values.yaml`
- [X] [T012] [P] Redis 7 sub-chart values: AOF persistence enabled, password Secret reference — `charts/agentshield/charts/redis/values.yaml`
- [X] [T013] [P] MinIO sub-chart values: 4 buckets, access/secret key Secret reference — `charts/agentshield/charts/minio/values.yaml`
- [X] [T014] [P] Keycloak 24 sub-chart values: postgres backend URL, admin credentials, realm init job config — `charts/agentshield/charts/keycloak/values.yaml`
- [X] [T015] [P] Keycloak realm init Job manifest: creates agentshield realm, registry-api and envoy-gateway clients, initial users — `charts/agentshield/charts/keycloak/templates/realm-init-job.yaml`
- [X] [T016] [P] ArgoCD Application resource pointing to charts/agentshield with automated sync and self-heal — `infra/argocd/agentshield-app.yaml`

- [ ] [T130] [P] Update top-level Helm values with `imagePullSecrets` configuration block for private registry (Harbor or ECR); add `.Values.global.imagePullSecrets` that propagates to all sub-chart Deployment templates via `helm.sh/chart` convention (Gap M-25) — `charts/agentshield/values.yaml`
- [ ] [T131] [P] Add `imagePullSecrets` Secret template: creates a Kubernetes Secret with registry credentials from Helm values, referenced by all Deployments (Gap M-25) — `charts/agentshield/templates/registry-secret.yaml`

**Verification:** `kubectl get pods -n agentshield-platform` — all pods Running; `psql -c "\l"` shows 5 databases; Keycloak realm accessible at `/realms/agentshield`

---

## Phase 2 — Registry API (Week 2)
_Depends on: Phase 1 complete (Postgres running, Keycloak realm configured)_

- [X] [T017] Settings class reading DATABASE_URL, KEYCLOAK_URL, PORT via pydantic-settings — `services/registry-api/config.py`
- [X] [T018] SQLAlchemy async engine and session factory (async_sessionmaker) — `services/registry-api/db.py`
- [X] [T019] SQLAlchemy ORM models: Agent, AgentVersion, Deployment, Approval, AgentPolicy, Workflow, WorkflowVersion, PiiMapping — `services/registry-api/models.py`
- [X] [T020] Pydantic request/response schemas matching the registry-api.yaml OpenAPI contract — `services/registry-api/schemas.py`
- [X] [T021] Agents router: POST/GET /agents, GET/PUT/DELETE /agents/{name} — `services/registry-api/routers/agents.py`
- [X] [T022] [P] Versions router: POST/GET /agents/{name}/versions, PATCH /agents/{name}/versions/{id} — `services/registry-api/routers/versions.py`
- [X] [T023] [P] Deployments router: POST /agents/{name}/deploy, POST /agents/{name}/rollback, GET /agents/{name}/deployments — `services/registry-api/routers/deployments.py`
- [X] [T024] [P] Workflows router: POST/GET /workflows, GET/PUT /workflows/{id}, POST /workflows/{id}/deploy, GET /workflows/{id}/versions — `services/registry-api/routers/workflows.py`
- [X] [T025] FastAPI app factory: mounts all routers, lifespan startup (DB pool), /health endpoint — `services/registry-api/main.py`
- [X] [T026] alembic.ini config pointing to DATABASE_URL env var — `services/registry-api/alembic.ini`
- [X] [T027] Alembic env.py wiring async engine to Base.metadata for autogenerate — `services/registry-api/alembic/env.py`
- [X] [T028] Initial migration: creates all Phase 1 tables with indexes, constraints, and pgcrypto extension — `services/registry-api/alembic/versions/0001_initial_schema.py`
- [X] [T029] requirements.txt pinning fastapi, uvicorn, sqlalchemy[asyncio], asyncpg, alembic, pydantic-settings — `services/registry-api/requirements.txt`
- [X] [T030] Dockerfile: python:3.12-slim base, install requirements, uvicorn CMD — `services/registry-api/Dockerfile`
- [X] [T031] Registry API Helm Chart.yaml — `charts/agentshield/charts/registry-api/Chart.yaml`
- [X] [T032] Registry API Deployment template: 2 replicas, env vars from Secrets, alembic init container — `charts/agentshield/charts/registry-api/templates/deployment.yaml`
- [X] [T033] Registry API Service template: ClusterIP on port 8000 — `charts/agentshield/charts/registry-api/templates/service.yaml`

- [ ] [T132] Full approvals router with `session_id` and `opa_decision_id` fields: POST /approvals, GET /approvals, PATCH /approvals/{id} (approve/reject with optimistic lock), GET /approvals/{id} (Gaps C-01, C-17, C-18) — `services/registry-api/routers/approvals.py`
- [ ] [T133] [P] OPA decisions router: POST /opa-decisions (create audit log entry), GET /opa-decisions?agent=&decision= (query audit log) (Gap C-17) — `services/registry-api/routers/opa_decisions.py`
- [ ] [T134] [P] Agent quarantine endpoints: POST /agents/{name}/quarantine (sets agent status=quarantined, scales pod to 0), DELETE /agents/{name}/quarantine (restores); mount in agents router (Gap C-19) — `services/registry-api/routers/agents.py`
- [ ] [T135] [P] Teams router: POST /teams, GET /teams, GET /teams/{id}, PUT /teams/{id}, GET /teams/{id}/agents — team is a first-class entity with name, namespace, keycloak_role_id fields (Gap M-11) — `services/registry-api/routers/teams.py`
- [ ] [T136] Alembic migration for `teams` table with indexes on name and keycloak_role_id; add `team_id` FK to `agents` table (Gap M-11) — `services/registry-api/alembic/versions/0002_add_teams.py`

**Verification:** `curl -X POST http://registry-api:8000/api/v1/agents -d '{"name":"echo-agent","team":"platform"}'` returns 201; `/health` returns `{"status":"ok"}`

---

## Checkpoint 1 — Deploy Data Layer + Registry API
_Gate: Phases 1-2 must be complete. Run before starting Phase 3._
_What you prove: Postgres, Redis, MinIO, Keycloak all healthy; Registry API accepting registrations; schema migrations applied._

- [ ] [CP1a] Helm deploy script: `helm dependency update` + `helm install agentshield` with custom services disabled (registry-api enabled, all others disabled) — `scripts/deploy-cp1.sh`
- [ ] [CP1b] Data layer smoke test: kubectl checks all platform pods Running, psql verifies 5 databases, Keycloak realm endpoint responds, Redis ping, MinIO bucket list — `scripts/smoke-test-cp1-infra.sh`
- [ ] [CP1c] Registry API smoke test: POST /agents creates echo-agent (expect 201), GET /agents lists it, GET /health returns ok, GET /api/v1/tools returns empty list — `scripts/smoke-test-cp1-registry.sh`

> **To run:** `bash scripts/deploy-cp1.sh` → wait for pods → `bash scripts/smoke-test-cp1-infra.sh && bash scripts/smoke-test-cp1-registry.sh`
> **Pass criteria:** All assertions exit 0, no pod in CrashLoopBackOff

---

## Phase 3 — Deploy Controller (Week 2 cont.)
_Depends on: Phase 2 complete (Registry API running and accepting agent registrations)_

- [X] [T034] Settings class reading REGISTRY_API_URL, KUBECONFIG, POLL_INTERVAL_SECONDS — `services/deploy-controller/config.py`
- [X] [T035] Kubernetes Python client wrapper: create/update/delete Deployments and Services in agents-* namespaces — `services/deploy-controller/k8s_client.py`
- [X] [T036] Manifest builder: generates K8s Deployment YAML from AgentVersion data with OPA sidecar container, resource limits, liveness/readiness probes — `services/deploy-controller/manifest_builder.py`
- [X] [T037] Reconciler: compares desired state from Registry to actual K8s Deployments; creates/updates/deletes as needed — `services/deploy-controller/reconciler.py`
- [X] [T038] Main polling loop: polls Registry API every 5s for pending deployments, calls reconciler — `services/deploy-controller/main.py`
- [X] [T039] requirements.txt pinning kubernetes, httpx, pydantic-settings — `services/deploy-controller/requirements.txt`
- [X] [T040] Dockerfile: python:3.12-slim base, install requirements — `services/deploy-controller/Dockerfile`
- [X] [T041] Deploy Controller Chart.yaml — `charts/agentshield/charts/deploy-controller/Chart.yaml`
- [X] [T042] Deploy Controller Deployment template: 1 replica, ServiceAccount with agents-* RBAC — `charts/agentshield/charts/deploy-controller/templates/deployment.yaml`
- [X] [T043] ClusterRole and ClusterRoleBinding: create/update/delete Deployments, Services, ConfigMaps in agents-* namespaces — `charts/agentshield/charts/deploy-controller/templates/clusterrole.yaml`

- [X] [T137] `services/deploy-controller/timeout_worker.py` — background worker that queries `approvals WHERE status='pending' AND expires_at < now()`, updates `status='timeout'`, issues Postgres NOTIFY on `approvals` channel to wake the waiting agent thread (Gaps C-12, C-13) — `services/deploy-controller/timeout_worker.py`
- [X] [T138] Update `services/deploy-controller/main.py` to start timeout_worker as asyncio background task on startup; add graceful shutdown on SIGTERM (Gap C-12) — `services/deploy-controller/main.py`
- [X] [T139] `POST /approvals/{id}/reopen` endpoint — re-triggers a timed-out or rejected approval: resets status to pending, updates expires_at, re-issues Redis pub/sub notification (Gap M-29) — `services/registry-api/routers/approvals.py`

**Verification:** Register echo-agent, POST deploy → within 60s `kubectl get pods -n agents-platform -l agent=echo-agent` shows Running pod with 2 containers (echo-agent + opa)

---

## Checkpoint 2 — First Agent Deployed End-to-End
_Gate: Phases 1-3 must be complete. Run before starting Phase 4._
_What you prove: Deploy Controller reconciles K8s state; echo-agent pod appears with OPA sidecar; agent /health endpoint responds._

- [X] [CP2a] Helm upgrade script: enable deploy-controller in umbrella chart — `scripts/deploy-cp2.sh`
- [X] [CP2b] Agent deploy smoke test: register echo-agent version, POST /deploy, poll until pod Running (timeout 90s), verify 2 containers (echo-agent + opa), curl agent /health — `scripts/smoke-test-cp2-deploy.sh`
- [X] [CP2c] OPA sidecar smoke test: port-forward OPA sidecar (localhost:8181), POST to /v1/data with a known tool name, assert allow decision returned — `scripts/smoke-test-cp2-opa.sh`

> **To run:** `bash scripts/deploy-cp2.sh` → `bash scripts/smoke-test-cp2-deploy.sh && bash scripts/smoke-test-cp2-opa.sh`
> **Pass criteria:** echo-agent pod Running with 2 containers, OPA returns `{"result":{"allow":true}}`

---

## Phase 3b — Operations & Runbooks (Week 3 — parallel with Phase 4)
_Depends on: Phase 1 complete (Helm infra deployed); can run in parallel with Phase 4_
_Gaps addressed: C-15, M-10, M-15, M-17, M-18, M-19_

- [X] [T140] `scripts/post-install.sh` — detailed post-install checklist: verify Keycloak realm and clients, create first platform-admin user, confirm all agentshield-platform pods Running, print service URLs and connection strings (Gap C-15) — `scripts/post-install.sh`
- [X] [T141] [P] `scripts/onboard-team.sh` — new team onboarding automation: creates agents-{team} namespace, applies default-deny and allow-egress NetworkPolicies from templates, creates Keycloak role for the team, registers team via POST /api/v1/teams (Gap M-10) — `scripts/onboard-team.sh`
- [X] [T142] [P] `docs/runbooks/incident-response.md` — emergency response runbook: quarantine steps (use POST /agents/{name}/quarantine), forensic evidence collection (Langfuse trace export, OPA decision log query), incident timeline template, escalation contacts (Gap M-19) — `docs/runbooks/incident-response.md`
- [X] [T143] [P] `scripts/run-garak.sh` — ad-hoc Garak scan script: accepts --agent, --probes (comma-separated probe list), --output (report path); runs garak CLI against the agent's /chat endpoint; exits non-zero on critical findings (Gap M-17) — `scripts/run-garak.sh`
- [X] [T144] [P] `policies/nemo/rules/default.yar` — default YARA rules covering: SQL injection patterns, XSS payloads, Jinja/template injection, Python code injection, system prompt extraction attempts (Gap M-18) — `policies/nemo/rules/default.yar`
- [X] [T145] [P] `policies/nemo/rules/Makefile` — validate YARA rule syntax (`yara --syntax-only`), package rules into ConfigMap YAML, apply ConfigMap update to cluster, send SIGHUP to NeMo pod for hot-reload; usable in CI (Gap M-15) — `policies/nemo/rules/Makefile`

**Verification:** `bash scripts/post-install.sh` exits 0 with all checks green; `bash scripts/onboard-team.sh fraud-team` creates namespace `agents-fraud-team` and Keycloak role; `make -C policies/nemo/rules validate` exits 0; `yara policies/nemo/rules/default.yar /dev/null` runs without syntax errors

---

## Phase 4 — Tool Registry (Week 2 cont.)
_Depends on: Phase 2 complete (Registry API running with DB models)_

- [ ] [T044] SQLAlchemy models for Tool, AuthConfig, MCPServer, AgentToolBinding — append to `services/registry-api/models.py`
- [ ] [T045] Pydantic schemas for Tool, AuthConfig, MCPServer CRUD matching tool-registry API contract — append to `services/registry-api/schemas.py`
- [ ] [T046] Tools router: POST/GET /tools, GET/PUT/DELETE /tools/{id}, GET /tools/{id}/agents, POST /tools/{id}/test — `services/registry-api/routers/tools.py`
- [ ] [T047] [P] Auth configs router: POST/GET /auth-configs, PUT/DELETE /auth-configs/{id} — `services/registry-api/routers/auth_configs.py`
- [ ] [T048] [P] Agent-tool bindings router: POST/DELETE/GET /agents/{name}/tools — `services/registry-api/routers/agent_tools.py`
- [ ] [T049] OPA policy generator: takes AgentVersion tools list, produces Rego policy text, stores in agent_policies table and writes K8s ConfigMap — `services/registry-api/policy_generator.py`
- [ ] [T050] Alembic migration for Tool, AuthConfig, MCPServer, AgentToolBinding tables — `services/registry-api/alembic/versions/0002_tool_registry.py`
- [ ] [T051] Mount new routers in main.py and wire policy_generator call on deploy/version create — `services/registry-api/main.py`

**Verification:** `POST /api/v1/tools` creates tool; `POST /api/v1/agents/echo-agent/tools` attaches it; OPA ConfigMap created in agents-platform namespace

---

## Phase 5 — Safety Orchestrator (Week 3)
_Depends on: Phase 1 complete (Postgres for PII mappings); Phase 2 for agent routing_

- [ ] [T052] Settings class reading LLMGUARD_URL, PRESIDIO_ANALYZER_URL, PRESIDIO_ANONYMIZER_URL, NEMO_URL, DATABASE_URL — `services/safety-orchestrator/config.py`
- [ ] [T053] Pydantic schemas: ScanInputRequest, ScanInputResponse, ScanOutputRequest, ScanOutputResponse, ReadinessResponse — `services/safety-orchestrator/schemas.py`
- [ ] [T054] Async HTTP clients for LLM Guard, Presidio Analyzer, Presidio Anonymizer, and NeMo using httpx.AsyncClient — `services/safety-orchestrator/scanner_clients.py`
- [ ] [T055] PII store: writes Presidio anonymization mappings to pii_mappings table; lookup for de-anonymization — `services/safety-orchestrator/pii_store.py`
- [ ] [T056] Orchestrator: asyncio.gather fan-out to all scanners with 5s timeout; fail-closed on any error or timeout; merges scores — `services/safety-orchestrator/orchestrator.py`
- [ ] [T057] FastAPI app: POST /api/v1/scan/input, POST /api/v1/scan/output, GET /health, GET /ready (scanner ping checks) — `services/safety-orchestrator/main.py`
- [ ] [T058] requirements.txt pinning fastapi, uvicorn, httpx, sqlalchemy[asyncio], asyncpg, pydantic-settings — `services/safety-orchestrator/requirements.txt`
- [ ] [T059] Dockerfile: python:3.12-slim, install requirements — `services/safety-orchestrator/Dockerfile`
- [ ] [T060] [P] LLM Guard Helm chart: Deployment (2 replicas, 4Gi memory), ClusterIP Service on port 8000, env vars for scanner config — `charts/agentshield/charts/llm-guard/templates/deployment.yaml`
- [ ] [T061] [P] LLM Guard Chart.yaml — `charts/agentshield/charts/llm-guard/Chart.yaml`
- [ ] [T062] [P] Presidio Deployment: presidio-analyzer (port 3000) and presidio-anonymizer (port 3001) containers — `charts/agentshield/charts/presidio/templates/deployment.yaml`
- [ ] [T063] [P] Presidio Chart.yaml and Service templates for analyzer and anonymizer — `charts/agentshield/charts/presidio/Chart.yaml`
- [ ] [T064] [P] NeMo Guardrails Deployment (1 replica, 2Gi memory) with YARA rules ConfigMap mounted at /app/rules/ — `charts/agentshield/charts/nemo/templates/deployment.yaml`
- [ ] [T065] [P] NeMo Chart.yaml and YARA rules ConfigMap template — `charts/agentshield/charts/nemo/Chart.yaml`
- [ ] [T066] [P] Safety Orchestrator Helm Chart.yaml — `charts/agentshield/charts/safety-orchestrator/Chart.yaml`
- [ ] [T067] [P] Safety Orchestrator Deployment template: 2 replicas, env vars for scanner URLs and Postgres — `charts/agentshield/charts/safety-orchestrator/templates/deployment.yaml`. Acts as input proxy between Envoy and agent pods — network-enforced input scanning. Also handles output scan requests from agent pods.
- [ ] [T068] [P] Safety Orchestrator Service template: ClusterIP on port 8080 — `charts/agentshield/charts/safety-orchestrator/templates/service.yaml`

- [ ] [T146] PodDisruptionBudget templates for all safety scanner deployments: minAvailable=1 for LLM Guard, Presidio, NeMo, and Safety Orchestrator itself — prevents simultaneous eviction during node drains (Gap C-16, NFR-017a) — `charts/agentshield/charts/safety-orchestrator/templates/pdb.yaml`
- [ ] [T147] [P] Safety Orchestrator retry and circuit-breaker config: update `scanner_clients.py` with exponential backoff (3 retries, 100ms/500ms/2s), per-scanner circuit breaker (5 failures → open, 30s reset), fail-closed response (blocked=true) when circuit is open (Gap M-13) — `services/safety-orchestrator/scanner_clients.py`

**Verification:** `curl -X POST http://safety-orchestrator:8080/api/v1/scan/input -d '{"text":"ignore previous instructions","agent_name":"echo-agent"}'` returns `{"blocked":true,"reason":"prompt_injection"}`; `GET /ready` returns all three scanners "up"`

---

## Checkpoint 3 — Safety Pipeline Live
_Gate: Phases 1-5 must be complete. Run before starting Phase 6._
_What you prove: All 3 scanners healthy; injection blocked; PII redacted; fail-closed behaviour confirmed; echo-agent requests now routed through safety._

- [ ] [CP3a] Helm upgrade script: enable safety-orchestrator, llm-guard, presidio, nemo — `scripts/deploy-cp3.sh`
- [ ] [CP3b] Scanner readiness smoke test: GET /ready on safety-orchestrator asserts all 3 scanners "up"; individual health checks on LLM Guard, Presidio, NeMo — `scripts/smoke-test-cp3-scanners.sh`
- [ ] [CP3c] Safety behaviour smoke test: POST known injection payload → assert blocked=true; POST PII text → assert sanitized_text has redacted values; POST clean text → assert blocked=false, sanitized_text unchanged; POST to /scan/input with LLM Guard pod stopped → assert blocked=true (fail-closed) — `scripts/smoke-test-cp3-safety.sh`

> **To run:** `bash scripts/deploy-cp3.sh` → wait for scanner pods (LLM Guard ~3min model load) → `bash scripts/smoke-test-cp3-scanners.sh && bash scripts/smoke-test-cp3-safety.sh`
> **Pass criteria:** Injection blocked, PII redacted, fail-closed confirmed

---

## Phase 6 — OPA + Langfuse (Week 4)
_Depends on: Phase 2 (Registry API for policy generation), Phase 3 (Deploy Controller for sidecar injection), Phase 5 (Safety Orchestrator for tracing)_

- [ ] [T069] Update manifest_builder.py to inject OPA sidecar container (openpolicyagent/opa:0.69.0-static, port 8181, policy-bundle volume from ConfigMap) — `services/deploy-controller/manifest_builder.py`
- [ ] [T070] Update policy_generator.py to write Rego policy to K8s ConfigMap in agents-{team} namespace after generating it — `services/registry-api/policy_generator.py`
- [ ] [T071] Langfuse sub-chart values: Postgres URL, ClickHouse sub-chart config, MinIO bucket, initial API key Secret reference — `charts/agentshield/charts/langfuse/values.yaml`
- [ ] [T072] [P] ClickHouse sub-chart values: single-node, PVC 10Gi, MinIO backup config — `charts/agentshield/charts/clickhouse/values.yaml`
- [ ] [T073] [P] Langfuse Chart.yaml listing clickhouse as dependency — `charts/agentshield/charts/langfuse/Chart.yaml`
- [ ] [T074] Langfuse tracing client wrapper: emits traces to Langfuse for safety scans and agent runs — `services/safety-orchestrator/tracing.py`
- [ ] [T075] [P] Langfuse tracing client for Registry API: emits deploy and approval events — `services/registry-api/tracing.py`
- [ ] [T076] Skeleton OPA client for SDK (calls localhost:8181, returns allow/require_approval/reason) — `sdk/agentshield_sdk/opa_client.py`

**Verification:** `kubectl get pods -n agents-platform -l agent=echo-agent -o jsonpath='{.items[0].spec.containers[*].name}'` returns `echo-agent opa`; `curl localhost:8181/v1/data/agentshield/agent/echo_agent` returns allow decision; Langfuse UI shows traces

---

## Phase 7 — HITL + Approval Flow (Week 5)
_Depends on: Phases 1-6 complete (all infrastructure, OPA sidecars, Langfuse)_

- [ ] [T077] Approvals router: POST /api/v1/approvals, GET /api/v1/approvals, GET/PATCH /api/v1/approvals/{id} with optimistic lock (version field) — `services/registry-api/routers/approvals.py`
- [ ] [T078] Approval notifier: on INSERT with status=pending, publish to Redis pub/sub channel approvals:pending; includes agent name, tool, Appsmith queue link — `services/registry-api/approval_notifier.py`
- [ ] [T079] Slack notifier: reads SLACK_WEBHOOK_URL; sends formatted message with approval context, tool args, and Appsmith deep link — `services/registry-api/slack_notifier.py`
- [ ] [T080] Mount approvals router in main.py; wire approval_notifier call on approval creation — `services/registry-api/main.py`
- [ ] [T081] HITL module: require_approval() using LangGraph interrupt(); creates approval record via Registry API, calls interrupt() to checkpoint and pause — `sdk/agentshield_sdk/hitl.py`
- [ ] [T082] LangGraph PostgresSaver setup: AsyncPostgresSaver using DIRECT_DATABASE_URL (bypasses PgBouncer) for LISTEN/NOTIFY — `sdk/agentshield_sdk/checkpointer.py`
- [ ] [T083] Portkey sub-chart values: OpenAI and Anthropic provider configs (API keys from Secrets), Redis cache URL — `charts/agentshield/charts/portkey/values.yaml`. Agents set OPENAI_BASE_URL=http://portkey:8787 so LLM calls route through Portkey transparently — not in the Envoy routing path.
- [ ] [T084] [P] Portkey Chart.yaml — `charts/agentshield/charts/portkey/Chart.yaml`
- [ ] [T085] [P] Envoy Gateway GatewayClass and Gateway resource definitions — `infra/envoy/gateway.yaml`. Gateway routes `/agents/{name}/chat` and `/agents/{name}/chat/stream` to `safety-orchestrator.agentshield-platform:8080` (Safety acts as the ingress proxy and forwards sanitized requests to the correct agent pod); routes `/api/v1/*` to `registry-api`.
- [ ] [T086] [P] Envoy SecurityPolicy for JWT validation: Keycloak issuer URL, remote JWKS URI — `infra/envoy/jwt-auth-policy.yaml`
- [ ] [T087] [P] Envoy HTTPRoute — `infra/envoy/httproutes.yaml`: `/api/v1/*` → `registry-api`; `/agents/{name}/chat` and `/agents/{name}/chat/stream` → `safety-orchestrator.agentshield-platform:8080` (Safety Orchestrator proxies onward to the agent pod after scanning input).
- [ ] [T088] Approval timeout background task: scans approvals WHERE expires_at < now() AND status = 'pending'; updates to timed_out; notifies agent via POST /resume/{thread_id} — `services/registry-api/approval_timeout_worker.py`

- [ ] [T148] Slack notification on approval timeout — extend `services/registry-api/slack_notifier.py` with `notify_timeout()`: sends message including thread_id, agent name, tool name, timed-out timestamp, and link to reopen via `/approvals/{id}/reopen`; called by timeout_worker.py when status transitions to timeout (Gap M-30) — `services/registry-api/slack_notifier.py`
- [ ] [T149] [P] Appsmith approval card conflict resolution UX — update the Appsmith approval queue app to handle 409 Conflict on PATCH: display "Already decided by [reviewer_name] at [timestamp]" message and refresh the card to show final decision; prevent double-submit (Gap M-27) — `appsmith/apps/approval-queue-conflict.js`
- [ ] [T150] [P] SSE protocol update — add `approval_timeout` event type to `sdk/agentshield_sdk/streaming.py`: emitted when agent detects approval status = timeout via LISTEN/NOTIFY; payload includes `approval_id`, `thread_id`, `reason: "timeout"` (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** Trigger high-risk tool → `GET /approvals?status=pending` shows record → `PATCH /approvals/{id}` with approved → agent SSE stream emits approval_decided and done within 5s; `curl -H "Authorization: Bearer invalid" http://envoy/api/v1/agents` returns 401

---

## Checkpoint 4 — Full E2E Governed Request
_Gate: Phases 1-7 must be complete. Run before starting Phase 8 (SDK)._
_What you prove: Complete request lifecycle — JWT auth via Envoy, safety scan, OPA policy, HITL approval pause/resume, SSE streaming, trace in Langfuse._

- [ ] [CP4a] Helm upgrade script: enable portkey, envoy-gateway, langfuse — `scripts/deploy-cp4.sh`
- [ ] [CP4b] Auth smoke test: send request with invalid JWT to Envoy → assert 401; send with valid JWT → assert forwarded to registry-api — `scripts/smoke-test-cp4-auth.sh`
- [ ] [CP4c] Full E2E HITL smoke test: POST /chat/stream to echo-agent with high-risk tool trigger; assert SSE stream emits approval_requested event; PATCH approval to approved; assert stream resumes with approval_decided then done; check Langfuse trace created with all spans present — `scripts/smoke-test-cp4-e2e.sh`

> **To run:** `bash scripts/deploy-cp4.sh` → `bash scripts/smoke-test-cp4-auth.sh && bash scripts/smoke-test-cp4-e2e.sh`
> **Pass criteria:** 401 on bad JWT, HITL pause/resume works, Langfuse trace visible with safety + approval spans

---

## Phase 8 — SDK v1 (Week 6)
_Depends on: Phases 1-7 complete (all backend infrastructure)_

- [ ] [T089] SDK config: reads AGENTSHIELD_SAFETY_URL, AGENTSHIELD_LANGFUSE_KEY, AGENTSHIELD_OPA_URL, OPENAI_BASE_URL, AGENT_NAME from env — `sdk/agentshield_sdk/config.py`
- [ ] [T090] Agent dataclass: name, instructions, tools, model, handoffs fields; validates tool list on construction — `sdk/agentshield_sdk/agent.py`
- [ ] [T091] Tool decorator: @tool(risk="low|high") wraps callable, attaches .risk and .name attributes, registers metadata for OPA — `sdk/agentshield_sdk/tool_decorator.py`
- [ ] [T092] Safety client: async call to Safety Orchestrator POST /scan/input and POST /scan/output; raises SafetyBlockedError on blocked=True — `sdk/agentshield_sdk/safety_client.py`
- [ ] [T093] OPA client: calls OPA sidecar at http://localhost:8181/v1/data/agentshield/agent/{agent_name}; returns OPADecision(allow, require_approval, reason) — `sdk/agentshield_sdk/opa_client.py`
- [ ] [T094] Tracing module: Langfuse client wrapper; emits trace spans for each run, tool call, safety scan, and approval event — `sdk/agentshield_sdk/tracing.py`
- [ ] [T095] HITL module: require_approval() calls Registry API POST /approvals then calls LangGraph interrupt() to pause; handles resume — `sdk/agentshield_sdk/hitl.py`
- [ ] [T096] Graph builder: constructs LangGraph StateGraph from Agent; injects OPA check before each tool node; wires HITL pause for high-risk tools — `sdk/agentshield_sdk/graph_builder.py`
- [ ] [T097] Streaming module: converts LangGraph astream_events() to SSE events per sse-protocol.md (text_delta, tool_call_start/end, approval_requested/decided, done, error) — `sdk/agentshield_sdk/streaming.py`
- [ ] [T098] Runner class: run() calls graph invoke with safety pre/post scan; run_streamed() calls graph astream_events() and yields SSE events — `sdk/agentshield_sdk/runner.py`
- [ ] [T099] Mock safety layer: always returns {"blocked": false, "sanitized_text": input} — for local dev with agentshield dev — `sdk/agentshield_sdk/mock_safety.py`
- [ ] [T100] Mock OPA client: always returns {"allow": true, "require_approval": false} — for local dev — `sdk/agentshield_sdk/mock_opa.py`
- [ ] [T101] FastAPI server: POST /chat (sync), POST /chat/stream (SSE), POST /resume/{thread_id}, GET /health, GET /ready (checks all deps), GET /metrics (Prometheus) — `sdk/agentshield_sdk/server.py`
- [ ] [T102] CLI: agentshield dev (starts server with mock backends), agentshield dev --safety (real safety), agentshield register, agentshield deploy — `sdk/agentshield_sdk/cli.py`
- [ ] [T103] SDK __init__.py exporting Agent, Runner, tool, AgentGraph — `sdk/agentshield_sdk/__init__.py`
- [ ] [T104] SDK setup.py or pyproject.toml: package name agentshield-sdk, dependencies (fastapi, langgraph>=0.3, langfuse, httpx, click) — `sdk/pyproject.toml`
- [ ] [T105] LangGraph checkpointer setup for SDK: AsyncPostgresSaver using DIRECT_DATABASE_URL — `sdk/agentshield_sdk/checkpointer.py`
- [ ] [T106] Example order-agent agent.py: lookup_order (@tool risk=low) and issue_refund (@tool risk=high), Agent constructor — `examples/order-agent/agent.py`
- [ ] [T107] [P] Example order-agent agent.yaml: name, team, description, tools with risk levels — `examples/order-agent/agent.yaml`
- [ ] [T108] [P] Example order-agent Dockerfile: python:3.12-slim, pip install agentshield-sdk, COPY agent.py, CMD agentshield dev — `examples/order-agent/Dockerfile`

- [ ] [T151] `agentshield_sdk/handoff.py` — multi-agent handoff via Envoy ingress URL (not K8s DNS): `handoff(target_agent, message, session_id)` sends POST to `http://envoy/agents/{name}/chat` with `X-AgentShield-Session-Id` header propagated; receiving agent's input is scanned by Safety Orchestrator via Envoy ingress path (Gaps C-03, C-04) — `sdk/agentshield_sdk/handoff.py`
- [ ] [T152] [P] Update `agentshield_sdk/tracing.py` — add `parent_trace_id` and `source_agent` metadata to all Langfuse spans; cross-agent trace stitching via shared `trace_id = session_id`; add `team` and `agent_name` as top-level trace metadata tags for team-level cost grouping (Gaps M-08, M-31) — `sdk/agentshield_sdk/tracing.py`
- [ ] [T153] [P] Update `agentshield_sdk/runner.py` — populate `approvals.context` dict before calling require_approval(): include conversation history (last 10 turns), tool name and parameters, LLM reasoning step if available, agent state snapshot (current node, pending tool calls) (Gap M-26) — `sdk/agentshield_sdk/runner.py`
- [ ] [T154] [P] Update `agentshield_sdk/streaming.py` — add `approval_timeout` SSE event type with payload schema `{event: "approval_timeout", data: {approval_id, thread_id, reason: "timeout", reopen_url}}`; consumed by client UX to show timeout state (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** `cd examples/order-agent && agentshield dev` starts; `curl -X POST localhost:8080/chat -d '{"message":"status of order 123"}'` returns response; `curl -N -X POST localhost:8080/chat/stream -d '{"message":"issue refund for 123"}'` yields approval_requested SSE event

---

## Phase 9 — Studio v0 (Week 7)
_Depends on: Phases 1-8 complete (Registry API workflows endpoint, SDK for declarative runner)_

**Declarative Runner (backend — can run in parallel with Studio frontend after SDK is done)**

- [ ] [T109] Declarative runner config: reads WORKFLOW_JSON (base64 env var), AGENTSHIELD_SAFETY_URL, DATABASE_URL — `services/declarative-runner/config.py`
- [ ] [T110] Node executors: AgentNodeExecutor (creates Agent() from config, calls Runner), HttpToolNodeExecutor (httpx call with {{variable}} substitution), EndNodeExecutor (output mapping) — `services/declarative-runner/node_executors.py`
- [ ] [T111] Workflow executor: parses WORKFLOW_JSON at module startup; builds LangGraph StateGraph from node/edge definitions; caches graph object — `services/declarative-runner/workflow_executor.py`
- [ ] [T112] Declarative runner FastAPI app: POST /chat, POST /chat/stream, POST /resume/{thread_id}, GET /health, GET /ready — `services/declarative-runner/main.py`
- [ ] [T113] Declarative runner Dockerfile: python:3.12-slim, pip install agentshield-sdk httpx, COPY . — `services/declarative-runner/Dockerfile`
- [ ] [T114] Deploy Controller reconciler update: handles agent_type=declarative; fetches workflow JSON from Registry API; injects WORKFLOW_JSON as base64 env var; uses declarative-runner image — `services/deploy-controller/reconciler.py`

**Studio Frontend (can run in parallel with T109-T114)**

- [ ] [T115] [P] package.json with react@18, @xyflow/react@12, typescript@5, vite@5, @tanstack/react-query@5, zustand@5, axios deps — `studio/package.json`
- [ ] [T116] [P] Vite config: TypeScript React plugin, dev server proxy to Registry API — `studio/vite.config.ts`
- [ ] [T117] [P] Zustand workflow store: nodes, edges, selectedNodeId, isDirty, setNodes, setEdges, selectNode, markSaved — `studio/src/stores/workflowStore.ts`
- [ ] [T118] [P] Registry API axios client: saveWorkflow(), listWorkflows(), getWorkflow(), deployWorkflow() with error handling — `studio/src/api/registryApi.ts`
- [ ] [T119] [P] Workflow serializer: converts React Flow nodes+edges to workflow JSON schema; deserializer for loading saved workflows — `studio/src/utils/workflowSerializer.ts`
- [ ] [T120] [P] AgentNode component: icon, name label, source/target handles, click to select — `studio/src/nodes/AgentNode.tsx`
- [ ] [T121] [P] HttpToolNode component: method badge, URL label, source/target handles — `studio/src/nodes/HttpToolNode.tsx`
- [ ] [T122] [P] EndNode component: terminal indicator, target handle only (no out handle) — `studio/src/nodes/EndNode.tsx`
- [ ] [T123] [P] PropertiesPanel component: renders config fields for selected node (Agent: name/instructions/model/risk; HttpTool: name/endpoint/method/headers/body; End: output_mapping) — `studio/src/components/PropertiesPanel.tsx`
- [ ] [T124] [P] Toolbar component: Save button (calls saveWorkflow(), shows toast), Deploy button (calls deployWorkflow(), polls deployment status) — `studio/src/components/Toolbar.tsx`
- [ ] [T125] [P] Canvas component: React Flow canvas with node types registered, Toolbar rendered above, PropertiesPanel on right — `studio/src/components/Canvas.tsx`
- [ ] [T126] [P] App.tsx with routing: / → Canvas, /workflows → workflow list; React Query provider, Zustand store wiring — `studio/src/App.tsx`
- [ ] [T127] [P] Studio Helm Chart.yaml — `charts/agentshield/charts/studio/Chart.yaml`
- [ ] [T128] [P] Studio Deployment template: nginx serving built React app; ConfigMap with nginx.conf proxying /api/* to registry-api — `charts/agentshield/charts/studio/templates/deployment.yaml`
- [ ] [T129] [P] Studio Service template: ClusterIP on port 80 — `charts/agentshield/charts/studio/templates/service.yaml`

- [ ] [T155] Studio first-save modal — React dialog component shown before first `saveWorkflow()` call: workflow name input (required, validated against existing names), team selector dropdown (populated from GET /teams), submit triggers save with name+team in payload (Gap M-22) — `studio/src/components/FirstSaveModal.tsx`
- [ ] [T156] [P] Studio HTTP Tool node auth config selector — add `authConfigId` field to HttpToolNode properties panel: dropdown populated from GET /auth-configs; selected auth config ID included in workflow JSON for the declarative runner to resolve at execution time (Gap M-23) — `studio/src/components/PropertiesPanel.tsx`

**Verification:** `kubectl port-forward svc/studio 5173:80`; drag Agent+HttpTool+End nodes; fill properties; click Save → first-save modal appears → enter name and team → workflow_id returned; click Deploy → pod appears with `kubectl get pods -n agents-platform -l workflow={id}`; `curl -X POST localhost:8080/chat -d '{"message":"test"}'` returns response from declarative runner

---

## Phase 9b — Observability & Dashboards (Week 8 — after Studio v0)
_Depends on: Phase 6 complete (Langfuse running with ClickHouse); Phase 7 complete (Appsmith approval queue working)_
_Gaps addressed: M-16, M-24, M-31_

- [ ] [T157] `langfuse/dashboards/safety-dashboard.json` — Langfuse dashboard import file: per-agent injection block rate chart, false positive tracking (blocked=true AND no human override), PII redaction count per team, scanner latency P50/P99 (Gap M-16) — `langfuse/dashboards/safety-dashboard.json`
- [ ] [T158] [P] `langfuse/dashboards/rejection-rate.json` — per-agent rejection rate dashboard: OPA deny rate, Safety block rate, HITL approval rate, combined pass rate funnel chart; filterable by team and time range (Gap M-16) — `langfuse/dashboards/rejection-rate.json`
- [ ] [T159] [P] Langfuse SDK instrumentation audit — update `agentshield_sdk/tracing.py` to ensure ALL traces include `team`, `agent_name`, `session_id` as Langfuse metadata tags; add `model`, `token_count` for cost grouping; write test asserting span metadata completeness (Gap M-31) — `sdk/agentshield_sdk/tracing.py`
- [ ] [T160] [P] `appsmith/apps/approval-queue.json` — Appsmith export/import file for the Approval Queue app: approval list view with status filter, approve/reject buttons with optimistic lock handling, session_id and OPA decision cross-reference panel (Gap M-24) — `appsmith/apps/approval-queue.json`
- [ ] [T161] [P] `appsmith/apps/agent-registry.json` — Appsmith export/import file for the Agent Registry app: agent list with deployment status, version history table, quarantine toggle button, team filter (Gap M-24) — `appsmith/apps/agent-registry.json`

**Verification:** Import `langfuse/dashboards/safety-dashboard.json` via Langfuse UI → dashboard renders with data; Import both Appsmith app JSON files via Appsmith → approval queue shows pending approvals; agent registry shows registered agents with deployment status

---

## Summary

**Total tasks:** 169 (161 implementation T001–T161 + 8 checkpoint CP1a–CP4c)

| Phase | Tasks | Parallel | Notes |
|-------|-------|---------|-------|
| Phase 1 — Infra Setup | T001–T016, T130–T131 (18) | T006–T016, T131 (12) | Subchart values all parallel after T001-T002; imagePullSecrets parallel |
| Phase 2 — Registry API | T017–T033, T132–T136 (22) | T022–T024, T133–T135 (6) | Routers parallel after models/schemas; new routers parallel |
| **Checkpoint 1** | CP1a–CP1c (3) | — | Deploy data layer + Registry API; prove schema migrations work |
| Phase 3 — Deploy Controller | T034–T043, T137–T139 (13) | None | Sequential build; timeout worker added |
| **Checkpoint 2** | CP2a–CP2c (3) | — | First agent deployed; OPA sidecar verified |
| Phase 3b — Operations & Runbooks | T140–T145 (6) | T141–T145 (5) | Scripts + runbooks; nearly all parallel |
| Phase 4 — Tool Registry | T044–T051 (8) | T047–T048 (2) | Auth configs and agent-tools routers parallel |
| Phase 5 — Safety Orchestrator | T052–T068, T146–T147 (19) | T060–T068, T147 (10) | Scanner Helm charts all parallel; PDB + circuit breaker added |
| **Checkpoint 3** | CP3a–CP3c (3) | — | Safety pipeline live; injection blocked; fail-closed confirmed |
| Phase 6 — OPA + Langfuse | T069–T076 (8) | T072–T073, T075–T076 (4) | Langfuse charts parallel |
| Phase 7 — HITL + Approval Flow | T077–T088, T148–T150 (15) | T084–T087, T149–T150 (6) | Envoy + Portkey charts parallel; timeout notif + conflict UX added |
| **Checkpoint 4** | CP4a–CP4c (3) | — | Full E2E: JWT auth + HITL approval + Langfuse trace |
| Phase 8 — SDK v1 | T089–T108, T151–T154 (24) | T107–T108, T152–T154 (5) | SDK core sequential; handoff + tracing + context tasks added |
| Phase 9 — Studio v0 | T109–T129, T155–T156 (23) | T115–T129, T156 (16) | Runner backend sequences; Studio frontend all parallel; first-save modal added |
| Phase 9b — Observability & Dashboards | T157–T161 (5) | T158–T161 (4) | Dashboards + Appsmith import files; nearly all parallel |

**Most parallelism:** Phase 9 (Studio frontend: 16 parallel tasks), Phase 5 (scanner Helm charts: 10 parallel tasks), Phase 1 (sub-chart values: 12 parallel tasks)
**Gap coverage:** 32 new tasks cover 14 critical gaps (C-03 to C-07, C-09, C-12, C-13, C-15, C-16, C-19, M-08 to M-31 where Open/Specced); see `docs/plan/gaps.md` for full register

---

## Suggested First 10 Tasks (Validation Sequence)

These 10 tasks establish the base layer and produce the first verifiable checkpoint:

1. **T001** — Umbrella chart skeleton (unblocks all Helm work)
2. **T002** — Top-level values.yaml (unblocks sub-chart config)
3. **T003** — Platform namespace manifest (required before any pods)
4. **T006** — Platform default-deny NetworkPolicy (security baseline)
5. **T010** — Postgres sub-chart values (unblocks Registry API + Safety)
6. **T017** — Registry API config.py (first service file; validates settings pattern)
7. **T018** — Registry API db.py (validates async SQLAlchemy setup)
8. **T019** — Registry API models.py (validates full data model compiles)
9. **T026** — alembic.ini (validates migration toolchain)
10. **T028** — Initial migration 0001 (validates Postgres schema creation end-to-end)

After these 10, you can run `alembic upgrade head` against a local Postgres to confirm the full schema is correct before committing to the K8s deployment path.
