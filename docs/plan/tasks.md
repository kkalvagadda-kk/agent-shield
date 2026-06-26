# AgentShield — Task List (Reordered for E2E-First Delivery)

**Total tasks:** 193 (178 implementation + 15 checkpoint)
**Phases:** 19 (13 implementation + 6 checkpoint gates)
**Parallel opportunities:** noted inline with [P]
**Checkpoint gates:** CP1 (after Phase 2), CP2 (after Phase 3), Checkpoint E2E (after Phase 8), CP3 (after Phase 9), CP4 (after Phase 11)
**Gap register:** see `docs/plan/gaps.md` for full gap tracking

---

## Reordering Rationale

**What changed and why:**

The original plan ordered phases by architectural layer — infra → registry → safety → OPA → HITL → SDK → Studio. Sound build-up order, but it means you cannot see an agent actually running until Phase 8 (SDK) and cannot interact with it via a browser until Phase 9 (Studio). Security scanners and deep observability block the functional path.

The reordering prioritises *create → deploy → invoke* first. A working end-to-end experience is visible after Phase 7/8. Safety scanners (LLM Guard, Presidio, NeMo), Portkey, Redis pub/sub, Slack, and dashboard integrations move to the end — they layer onto an already-functional system rather than blocking it.

**Concrete changes:**

| What moved | Original phase | New phase | Why |
|---|---|---|---|
| Studio UI MVP (new simplified 3-screen app) | Phase 9 (Studio v0) | **New Phase 5** | Register and deploy agents from a browser early |
| LLM Provider Config (new) | — | **New Phase 5b** | Agents can't call LLMs without credentials injected at deploy time; must precede SDK |
| SDK v1 | Phase 8 | **New Phase 6** | Developers need this to write agent code before safety is wired |
| Basic HITL (no Redis, no Slack) | Phase 7 | **New Phase 7** | Minimal approval flow + Envoy routing needed for E2E demo |
| Declarative Runner + Studio Canvas | Phase 9 | **New Phase 8** | Canvas extends MVP; runner enables workflow deploy |
| Safety Orchestrator + Scanners | Phase 5 | **New Phase 9** | Not needed to prove core functionality; added after E2E works |
| OPA policy generation + Langfuse | Phase 6 | **New Phase 10** | Observability added after functionality is confirmed |
| Redis pub/sub, Slack, Portkey | Phase 7 | **New Phase 11** | Notification and LLM proxy integrations are enhancements |
| Dashboards (Langfuse, Appsmith) | Phase 9b | **New Phase 12** | Depends on observability stack |

**Tasks deferred within phases (not removed — just done later):**
- T049 (OPA policy generator): Phase 4 → Phase 9
- T078 (Redis pub/sub), T079 (Slack notifier), T083–T084 (Portkey), T148–T150: Phase 7 → Phase 11
- T152 (cross-agent trace stitching), T154 (approval_timeout SSE): Phase 6 → Phase 10

**New tasks added:**
- T162: Agent List page (Studio MVP)
- T163: Create Agent form page (Studio MVP)
- T164: Deploy Agent page (Studio MVP)
- CPE-a, CPE-b, CPE-c: E2E Demo checkpoint scripts
- T165–T178: Phase 5b — LLM Provider Configuration (model, crypto layer, migration, router, K8s RBAC, deploy controller injection, Studio Providers page + nav)

---

## Phase 1 — Infra Setup ✅
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

## Phase 2 — Registry API ✅ (core complete; T132–T136 pending)
_Depends on: Phase 1 complete (Postgres running, Keycloak realm configured)_
_Note: T132–T136 must be completed before starting Phase 5 (teams endpoint) and Phase 7 (approvals router)_

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

**Verification:** `curl -X POST http://registry-api:8000/api/v1/agents -d '{"name":"echo-agent","team":"platform"}'` returns 201; `/health` returns `{"status":"ok"}`; GET /teams returns empty list

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

## Phase 3 — Deploy Controller ✅
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

## Checkpoint 2 — First Agent Deployed End-to-End ✅
_Gate: Phases 1-3 must be complete. Run before starting Phase 4._
_What you prove: Deploy Controller reconciles K8s state; echo-agent pod appears with OPA sidecar; agent /health endpoint responds._

- [X] [CP2a] Helm upgrade script: enable deploy-controller in umbrella chart — `scripts/deploy-cp2.sh`
- [X] [CP2b] Agent deploy smoke test: register echo-agent version, POST /deploy, poll until pod Running (timeout 90s), verify 2 containers (echo-agent + opa), curl agent /health — `scripts/smoke-test-cp2-deploy.sh`
- [X] [CP2c] OPA sidecar smoke test: port-forward OPA sidecar (localhost:8181), POST to /v1/data with a known tool name, assert allow decision returned — `scripts/smoke-test-cp2-opa.sh`

> **To run:** `bash scripts/deploy-cp2.sh` → `bash scripts/smoke-test-cp2-deploy.sh && bash scripts/smoke-test-cp2-opa.sh`
> **Pass criteria:** echo-agent pod Running with 2 containers, OPA returns `{"result":{"allow":true}}`

---

## Phase 3b — Operations & Runbooks ✅
_Depends on: Phase 1 complete (Helm infra deployed); can run in parallel with Phase 4_
_Gaps addressed: C-15, M-10, M-15, M-17, M-18, M-19_

- [X] [T140] `scripts/post-install.sh` — detailed post-install checklist: verify Keycloak realm and clients, create first platform-admin user, confirm all agentshield-platform pods Running, print service URLs and connection strings (Gap C-15) — `scripts/post-install.sh`
- [X] [T141] [P] `scripts/onboard-team.sh` — new team onboarding automation: creates agents-{team} namespace, applies default-deny and allow-egress NetworkPolicies from templates, creates Keycloak role for the team, registers team via POST /api/v1/teams (Gap M-10) — `scripts/onboard-team.sh`
- [X] [T142] [P] `docs/runbooks/incident-response.md` — emergency response runbook: quarantine steps (use POST /agents/{name}/quarantine), forensic evidence collection (Langfuse trace export, OPA decision log query), incident timeline template, escalation contacts (Gap M-19) — `docs/runbooks/incident-response.md`
- [X] [T143] [P] `scripts/run-garak.sh` — ad-hoc Garak scan script: accepts --agent, --probes (comma-separated probe list), --output (report path); runs garak CLI against the agent's /chat endpoint; exits non-zero on critical findings (Gap M-17) — `scripts/run-garak.sh`
- [X] [T144] [P] `policies/nemo/rules/default.yar` — default YARA rules covering: SQL injection patterns, XSS payloads, Jinja/template injection, Python code injection, system prompt extraction attempts (Gap M-18) — `policies/nemo/rules/default.yar`
- [X] [T145] [P] `policies/nemo/rules/Makefile` — validate YARA rule syntax (`yara --syntax-only`), package rules into ConfigMap YAML, apply ConfigMap update to cluster, send SIGHUP to NeMo pod for hot-reload; usable in CI (Gap M-15) — `policies/nemo/rules/Makefile`

**Verification:** `bash scripts/post-install.sh` exits 0 with all checks green; `bash scripts/onboard-team.sh fraud-team` creates namespace `agents-fraud-team` and Keycloak role; `make -C policies/nemo/rules validate` exits 0

---

## Phase 4 — Tool Registry
_Depends on: Phase 2 complete (Registry API running with DB models); T132–T136 also complete (teams FK needed)_

- [X] [T044] SQLAlchemy models for Tool, AuthConfig, MCPServer, AgentToolBinding — append to `services/registry-api/models.py`
- [X] [T045] Pydantic schemas for Tool, AuthConfig, MCPServer CRUD matching tool-registry API contract — append to `services/registry-api/schemas.py`
- [X] [T046] Tools router: POST/GET /tools, GET/PUT/DELETE /tools/{id}, GET /tools/{id}/agents, POST /tools/{id}/test — `services/registry-api/routers/tools.py`
- [X] [T047] [P] Auth configs router: POST/GET /auth-configs, PUT/DELETE /auth-configs/{id} — `services/registry-api/routers/auth_configs.py`
- [X] [T048] [P] Agent-tool bindings router: POST/DELETE/GET /agents/{name}/tools — `services/registry-api/routers/agent_tools.py`
- [ ] [T049] ⚠️ **Deferred to Phase 9** — OPA policy generator: takes AgentVersion tools list, produces Rego policy text, stores in agent_policies table and writes K8s ConfigMap — `services/registry-api/policy_generator.py` _(skip in Phase 4; complete when Phase 9 Safety work begins)_
- [X] [T050] Alembic migration for Tool, AuthConfig, MCPServer, AgentToolBinding tables — already created in `0001_initial_schema.py`
- [X] [T051] Mount new routers in main.py and wire policy_generator call on deploy/version create (policy_generator wiring skipped until T049 completes in Phase 9) — `services/registry-api/main.py`

**Verification:** `POST /api/v1/tools` creates tool; `POST /api/v1/agents/echo-agent/tools` attaches it; GET /tools returns list

---

## Phase 5 — Studio UI MVP
_Depends on: Phase 2 + T132–T136 complete (Registry API with agents + teams endpoints)_
_Scope: Simplified 3-screen app — Agent List, Create Agent, Deploy Agent. No visual canvas (that comes in Phase 8). Calls Registry API directly._

- [X] [T115] package.json — MVP deps: react@18, typescript@5, vite@5, @tanstack/react-query@5, axios, react-router-dom@6; note @xyflow/react@12 and zustand@5 added in Phase 8 when canvas is built — `studio/package.json`
- [X] [T116] Vite config: TypeScript React plugin, dev server proxy `/api` → Registry API at `http://registry-api:8000` — `studio/vite.config.ts`
- [X] [T118] Registry API axios client — MVP scope: `listAgents()`, `createAgent()`, `getAgent()`, `createVersion()`, `deployAgent()`, `getDeployments()`, `listTeams()` — `studio/src/api/registryApi.ts`
- [X] [T162] Agent List page — `studio/src/pages/AgentListPage.tsx`
- [X] [T163] [P] Create Agent form — `studio/src/pages/CreateAgentPage.tsx`
- [X] [T164] [P] Deploy Agent page — `studio/src/pages/DeployAgentPage.tsx`
- [X] [T126] [P] App.tsx — MVP routing wired — `studio/src/App.tsx`
- [X] [T127] [P] Studio Helm Chart.yaml updated — `charts/agentshield/charts/studio/Chart.yaml`
- [X] [T128] [P] Studio Deployment + ConfigMap (nginx.conf proxying /api/* to registry-api) — `charts/agentshield/charts/studio/templates/`
- [X] [T129] [P] Studio Service template: ClusterIP port 80 — `charts/agentshield/charts/studio/templates/service.yaml`

**Verification:** `kubectl port-forward svc/studio 5173:80`; navigate to http://localhost:5173; click "Register New Agent" → fill form → submit → agent appears in list; click Deploy → enter image tag → click Deploy button → status badge transitions to Running within 90s

---

## Phase 5b — LLM Provider Configuration
_Depends on: Phase 5 complete (Studio UI MVP); Phase 3 complete (Deploy Controller)_
_Why before Phase 6: The SDK reads LLM credentials from env vars injected into the pod. Without the deploy controller injecting them, agents deployed through the platform cannot make LLM calls even after the SDK is built._

**Design decisions:**
- Providers: `anthropic` and `bedrock` only (no OpenAI/Azure/Custom in this phase)
- Credentials stored AES-256 (Fernet) encrypted in Postgres as a JSON blob — Postgres is the source of truth, not K8s etcd; cluster can be wiped and recovered by redeploying against the same DB
- K8s Secret is a derived artifact: Registry API decrypts and writes it at deploy trigger time; deploy controller never sees plaintext credentials
- Provider is team-scoped: agents can only use providers belonging to the same team
- `credentials_encrypted` is write-only — never returned in any API response
- Anthropic credentials shape: `{"ANTHROPIC_API_KEY": "sk-ant-..."}` — one field
- Bedrock credentials shape: `{"AWS_ACCESS_KEY_ID": "AKIA...", "AWS_SECRET_ACCESS_KEY": "...", "AWS_DEFAULT_REGION": "us-east-1"}` — IAM user, no role assumption
- Registry API gets K8s RBAC: create/update/delete Secrets in `agentshield-platform` namespace only

**Anthropic models:** `claude-opus-4-8`, `claude-sonnet-4-6`, `claude-haiku-4-5`
**Bedrock models:** `anthropic.claude-3-5-sonnet-20241022-v2:0`, `anthropic.claude-3-haiku-20240307-v1:0`, `amazon.titan-text-lite-v1`

- [X] [T165] Add `LLMProvider` SQLAlchemy model: `id` (uuid pk), `name` (varchar 128), `provider` (varchar 32 — `anthropic|bedrock`), `default_model` (varchar 256), `credentials_encrypted` (Text — Fernet-encrypted JSON blob, never returned in responses), `team` (varchar 128, indexed), `created_at`, `updated_at`; UniqueConstraint on (name, team) — append to `services/registry-api/models.py`
- [X] [T166] Add nullable `llm_provider_id` UUID FK on `Agent` model (ON DELETE SET NULL); add `llm_provider` relationship — `services/registry-api/models.py`. Add `llm_secret_name` (varchar 256, nullable) and `llm_env_keys` (JSONB, nullable — e.g. `["ANTHROPIC_API_KEY"]`) to `Deployment` model so manifest builder can wire secretKeyRef without knowing provider type — `services/registry-api/models.py`
- [X] [T167] `services/registry-api/crypto.py` (NEW): Fernet helpers `encrypt_json(dict) -> str` and `decrypt_json(str) -> dict`; reads `AGENTSHIELD_ENCRYPTION_KEY` env var; raises clear error if key is missing — `services/registry-api/crypto.py`
- [X] [T168] Pydantic schemas — `services/registry-api/schemas.py`: `LLMProviderCreate` (name, provider, default_model, team, credentials as `AnthropicCredentials | BedrockCredentials` discriminated union); `LLMProviderUpdate` (all optional except credentials); `LLMProviderResponse` (id, name, provider, default_model, team, created_at, updated_at — credentials NEVER included); `AnthropicCredentials` (ANTHROPIC_API_KEY); `BedrockCredentials` (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION); update `AgentCreate` to accept optional `llm_provider_id` UUID; update `AgentResponse` to include nested `llm_provider: LLMProviderResponse | None`
- [X] [T169] LLM Providers router — `services/registry-api/routers/llm_providers.py`: `POST /api/v1/llm-providers/` (encrypts credentials, validates provider+model combo, validates team match); `GET /api/v1/llm-providers/` (supports `?team=` filter); `GET /api/v1/llm-providers/{id}`; `PUT /api/v1/llm-providers/{id}` (re-encrypts if credentials provided); `DELETE /api/v1/llm-providers/{id}` (hard delete — returns 409 if any active agent references it)
- [X] [T170] Mount `llm_providers_router` in `main.py`; add `AGENTSHIELD_ENCRYPTION_KEY` to `config.py` Settings — `services/registry-api/main.py`, `services/registry-api/config.py`
- [X] [T171] Alembic migration `0003_add_llm_providers.py`: CREATE TABLE llm_providers; ALTER TABLE agents ADD COLUMN llm_provider_id UUID REFERENCES llm_providers(id) ON DELETE SET NULL; ALTER TABLE deployments ADD COLUMN llm_secret_name VARCHAR(256), ADD COLUMN llm_env_keys JSONB — `services/registry-api/alembic/versions/0003_add_llm_providers.py`
- [X] [T172] Update deployments router `POST /agents/{name}/deploy` — `services/registry-api/routers/deployments.py`: if agent has `llm_provider`, decrypt credentials, create/upsert K8s Secret `agentshield-llm-{provider_id}` in `agentshield-platform` namespace using in-cluster kubernetes client; store `secret_name` and `list(credentials.keys())` as `llm_secret_name` and `llm_env_keys` on the Deployment record; add `kubernetes` and `cryptography` to `requirements.txt`
- [X] [T173] Add ServiceAccount + RBAC to registry-api Helm chart — `charts/agentshield/charts/registry-api/templates/serviceaccount.yaml`, `rbac.yaml`: ClusterRole allowing create/update/delete of Secrets resource only in `agentshield-platform` namespace; add `AGENTSHIELD_ENCRYPTION_KEY` secretKeyRef to Deployment template env vars — `charts/agentshield/charts/registry-api/templates/deployment.yaml`
- [X] [T174] Update `manifest_builder.py` — `services/deploy-controller/manifest_builder.py`: read `deployment.llm_secret_name` and `deployment.llm_env_keys` from the deployment record; if both are set, append to agent container env: for each key in `llm_env_keys` add a `secretKeyRef` entry pointing to `llm_secret_name`; also inject `LLM_PROVIDER` and `LLM_MODEL` as plain env vars from provider metadata included in the deployment payload
- [X] [T175] [P] `registryApi.ts` additions — `studio/src/api/registryApi.ts`: `LLMProvider` type (no credentials fields); `listProviders(team?)`, `createProvider()`, `updateProvider()`, `deleteProvider()` functions
- [X] [T176] [P] Studio Providers page — `studio/src/pages/ProvidersPage.tsx`: table (name, provider badge, default model, team, actions); "Add Provider" side-panel form with dynamic fields — Anthropic shows one masked API key field, Bedrock shows Access Key ID + masked Secret Key + Region dropdown; model dropdown filtered by provider type; edit re-shows form with masked placeholders for credentials (blank = keep existing); delete shows 409 error if agents reference the provider
- [X] [T177] [P] Update `CreateAgentPage.tsx` — `studio/src/pages/CreateAgentPage.tsx`: add LLM Provider dropdown filtered to the selected team's providers (`listProviders(team)`); re-fetches when team changes; shows "No providers for this team — add one in Providers →" when empty; sends `llm_provider_id` in createAgent payload
- [X] [T178] [P] Update `App.tsx` — `studio/src/App.tsx`: add `/providers` route → `ProvidersPage`; add "Providers" nav link in top bar

**Verification:** `POST /api/v1/llm-providers/` with Anthropic credentials creates provider; `GET` response omits credentials; create agent with `llm_provider_id`; `POST /agents/{name}/deploy` → `kubectl get secret agentshield-llm-{id} -n agentshield-platform` exists; `kubectl describe pod -n agents-platform <pod>` shows `ANTHROPIC_API_KEY` mounted via secretKeyRef; Studio /providers page shows dynamic form (Anthropic vs Bedrock); Create Agent form filters providers by team

---

## Phase 6 — SDK v1
_Depends on: Phase 3 (Deploy Controller for agent pods), Phase 4 (Tool Registry models for tool metadata), Phase 5b (LLM Provider config — so deployed agents receive credentials)_
_Note: T152 (cross-agent trace stitching) and T154 (approval_timeout SSE) deferred to Phase 10 — Langfuse not yet deployed_

- [X] [T089] SDK config: reads AGENTSHIELD_SAFETY_URL, AGENTSHIELD_LANGFUSE_KEY, AGENTSHIELD_OPA_URL, OPENAI_BASE_URL, AGENT_NAME from env — `sdk/agentshield_sdk/config.py`
- [X] [T090] Agent dataclass: name, instructions, tools, model, handoffs fields; validates tool list on construction — `sdk/agentshield_sdk/agent.py`
- [X] [T091] Tool decorator: @tool(risk="low|high") wraps callable, attaches .risk and .name attributes, registers metadata for OPA — `sdk/agentshield_sdk/tool_decorator.py`
- [X] [T092] Safety client: async call to Safety Orchestrator POST /scan/input and POST /scan/output; raises SafetyBlockedError on blocked=True; in Phase 6 connects to mock safety if AGENTSHIELD_SAFETY_URL not set — `sdk/agentshield_sdk/safety_client.py`
- [X] [T093] OPA client: calls OPA sidecar at http://localhost:8181/v1/data/agentshield/agent/{agent_name}; returns OPADecision(allow, require_approval, reason) — `sdk/agentshield_sdk/opa_client.py`
- [X] [T094] Tracing module: Langfuse client wrapper; emits trace spans for each run, tool call, safety scan, and approval event; no-ops gracefully when AGENTSHIELD_LANGFUSE_KEY is not set — `sdk/agentshield_sdk/tracing.py`
- [X] [T095] HITL module: require_approval() calls Registry API POST /approvals then calls LangGraph interrupt() to pause; handles resume — `sdk/agentshield_sdk/hitl.py`
- [X] [T096] Graph builder: constructs LangGraph StateGraph from Agent; injects OPA check before each tool node; wires HITL pause for high-risk tools — `sdk/agentshield_sdk/graph_builder.py`
- [X] [T097] Streaming module: converts LangGraph astream_events() to SSE events per sse-protocol.md (text_delta, tool_call_start/end, approval_requested/decided, done, error) — `sdk/agentshield_sdk/streaming.py`
- [X] [T098] Runner class: run() calls graph invoke with safety pre/post scan; run_streamed() calls graph astream_events() and yields SSE events — `sdk/agentshield_sdk/runner.py`
- [X] [T099] Mock safety layer: always returns {"blocked": false, "sanitized_text": input} — for local dev with agentshield dev — `sdk/agentshield_sdk/mock_safety.py`
- [X] [T100] Mock OPA client: always returns {"allow": true, "require_approval": false} — for local dev — `sdk/agentshield_sdk/mock_opa.py`
- [X] [T101] FastAPI server: POST /chat (sync), POST /chat/stream (SSE), POST /resume/{thread_id}, GET /health, GET /ready (checks all deps), GET /metrics (Prometheus) — `sdk/agentshield_sdk/server.py`
- [X] [T102] CLI: agentshield dev (starts server with mock backends), agentshield dev --safety (real safety), agentshield register, agentshield deploy — `sdk/agentshield_sdk/cli.py`
- [X] [T103] SDK __init__.py exporting Agent, Runner, tool, AgentGraph — `sdk/agentshield_sdk/__init__.py`
- [X] [T104] SDK setup.py or pyproject.toml: package name agentshield-sdk, dependencies (fastapi, langgraph>=0.3, langfuse, httpx, click) — `sdk/pyproject.toml`
- [X] [T105] LangGraph checkpointer setup for SDK: AsyncPostgresSaver using DIRECT_DATABASE_URL — `sdk/agentshield_sdk/checkpointer.py`
- [X] [T106] Example order-agent agent.py: lookup_order (@tool risk=low) and issue_refund (@tool risk=high), Agent constructor — `examples/order-agent/agent.py`
- [X] [T107] [P] Example order-agent agent.yaml: name, team, description, tools with risk levels — `examples/order-agent/agent.yaml`
- [X] [T108] [P] Example order-agent Dockerfile: python:3.12-slim, pip install agentshield-sdk, COPY agent.py, CMD agentshield dev — `examples/order-agent/Dockerfile`
- [X] [T151] `agentshield_sdk/handoff.py` — multi-agent handoff via Envoy ingress URL (not K8s DNS): `handoff(target_agent, message, session_id)` sends POST to `http://envoy/agents/{name}/chat` with `X-AgentShield-Session-Id` header propagated; receiving agent's input is scanned by Safety Orchestrator via Envoy ingress path (Gaps C-03, C-04) — `sdk/agentshield_sdk/handoff.py`
- [ ] [T152] ⚠️ **Deferred to Phase 10** — Update `agentshield_sdk/tracing.py`: add `parent_trace_id` and `source_agent` metadata to all Langfuse spans; cross-agent trace stitching via shared `trace_id = session_id`; add `team` and `agent_name` as top-level trace metadata tags (Gaps M-08, M-31) — `sdk/agentshield_sdk/tracing.py`
- [X] [T153] [P] Update `agentshield_sdk/runner.py` — populate `approvals.context` dict before calling require_approval(): include conversation history (last 10 turns), tool name and parameters, LLM reasoning step if available, agent state snapshot (current node, pending tool calls) (Gap M-26) — `sdk/agentshield_sdk/runner.py`
- [ ] [T154] ⚠️ **Deferred to Phase 10** — Update `agentshield_sdk/streaming.py`: add `approval_timeout` SSE event type with payload schema `{event: "approval_timeout", data: {approval_id, thread_id, reason: "timeout", reopen_url}}`; consumed by client UX to show timeout state (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** `cd examples/order-agent && agentshield dev` starts; `curl -X POST localhost:8080/chat -d '{"message":"status of order 123"}'` returns response; `curl -N -X POST localhost:8080/chat/stream -d '{"message":"issue refund for 123"}'` yields approval_requested SSE event (using mock OPA + mock approval flow)

---

## Phase 7 — Basic HITL (Envoy Gateway + Core Approval Flow)
_Depends on: Phase 5 (Studio MVP for approval UI), Phase 6 (SDK HITL module); T132 from Phase 2 must be complete (approvals router)_
_Scope: Minimal approval flow using direct DB — no Redis pub/sub, no Slack, no Portkey. Those come in Phase 11._

- [X] [T077] Approvals router: POST /api/v1/approvals, GET /api/v1/approvals, GET/PATCH /api/v1/approvals/{id} with optimistic lock (version field) — `services/registry-api/routers/approvals.py` _(builds on T132; T077 adds the version-field optimistic lock and ensures router is finalized)_
- [X] [T080] Mount approvals router in main.py — `services/registry-api/main.py` _(Note: do NOT wire approval_notifier in this phase — T078 Redis pub/sub comes in Phase 11; mount router only)_
- [X] [T081] HITL module: require_approval() using LangGraph interrupt(); creates approval record via Registry API, calls interrupt() to checkpoint and pause — `sdk/agentshield_sdk/hitl.py`
- [X] [T082] LangGraph PostgresSaver setup: AsyncPostgresSaver using DIRECT_DATABASE_URL (bypasses PgBouncer) for LISTEN/NOTIFY — `sdk/agentshield_sdk/checkpointer.py`
- [X] [T085] [P] Envoy Gateway GatewayClass and Gateway resource definitions — `infra/envoy/gateway.yaml`. Gateway routes `/agents/{name}/chat` and `/agents/{name}/chat/stream` to `safety-orchestrator.agentshield-platform:8080` (Safety acts as the ingress proxy); routes `/api/v1/*` to `registry-api`. In Phase 7, safety-orchestrator is not yet deployed — route `/agents/{name}/chat` directly to agent pod until Phase 9.
- [X] [T086] [P] Envoy SecurityPolicy for JWT validation: Keycloak issuer URL, remote JWKS URI — `infra/envoy/jwt-auth-policy.yaml`
- [X] [T087] [P] Envoy HTTPRoute — `infra/envoy/httproutes.yaml`: `/api/v1/*` → `registry-api`; `/agents/{name}/chat` and `/agents/{name}/chat/stream` → agent pod (direct in Phase 7; updated to safety-orchestrator in Phase 9)
- [X] [T088] Approval timeout background task: scans approvals WHERE expires_at < now() AND status = 'pending'; updates to timed_out; notifies agent via POST /resume/{thread_id} — `services/registry-api/approval_timeout_worker.py`

_Deferred to Phase 11 — complete after Redis and Slack are available:_
- [ ] [T078] ⚠️ **Deferred to Phase 11** — Approval notifier: on INSERT with status=pending, publish to Redis pub/sub channel approvals:pending — `services/registry-api/approval_notifier.py`
- [ ] [T079] ⚠️ **Deferred to Phase 11** — Slack notifier: reads SLACK_WEBHOOK_URL; sends formatted message with approval context, tool args, and Studio deep link — `services/registry-api/slack_notifier.py`
- [ ] [T083] ⚠️ **Deferred to Phase 11** — Portkey sub-chart values: OpenAI and Anthropic provider configs, Redis cache URL — `charts/agentshield/charts/portkey/values.yaml`
- [ ] [T084] ⚠️ **Deferred to Phase 11** — Portkey Chart.yaml — `charts/agentshield/charts/portkey/Chart.yaml`
- [ ] [T148] ⚠️ **Deferred to Phase 11** — Slack notification on approval timeout (Gap M-30) — `services/registry-api/slack_notifier.py`
- [ ] [T149] ⚠️ **Deferred to Phase 11** — Appsmith approval card conflict resolution UX (Gap M-27) — `appsmith/apps/approval-queue-conflict.js`
- [ ] [T150] ⚠️ **Deferred to Phase 11** — SSE protocol update: approval_timeout event type (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** Send request with invalid JWT to Envoy → assert 401; send valid JWT → forwarded; trigger issue_refund tool via `/chat/stream` → SSE emits `approval_requested`; PATCH approval to approved → stream resumes with `approval_decided` and `done`

---

## Phase 8 — Declarative Runner + Studio Canvas
_Depends on: Phase 6 (SDK for declarative runner backend), Phase 5 (Studio MVP app shell for canvas extension)_
_Parallel streams: Declarative runner backend (T109–T114) vs Studio canvas frontend (T117, T119–T125, T155–T156)_

**Declarative Runner (backend)**

- [X] [T109] Declarative runner config: reads WORKFLOW_JSON (base64 env var), AGENTSHIELD_SAFETY_URL, DATABASE_URL — `services/declarative-runner/config.py`
- [X] [T110] Node executors: AgentNodeExecutor (creates Agent() from config, calls Runner), HttpToolNodeExecutor (httpx call with {{variable}} substitution), EndNodeExecutor (output mapping) — `services/declarative-runner/node_executors.py`
- [X] [T111] Workflow executor: parses WORKFLOW_JSON at module startup; builds LangGraph StateGraph from node/edge definitions; caches graph object — `services/declarative-runner/workflow_executor.py`
- [X] [T112] Declarative runner FastAPI app: POST /chat, POST /chat/stream, POST /resume/{thread_id}, GET /health, GET /ready — `services/declarative-runner/main.py`
- [X] [T113] Declarative runner Dockerfile: python:3.12-slim, pip install agentshield-sdk httpx, COPY . — `services/declarative-runner/Dockerfile`
- [X] [T114] Deploy Controller reconciler update: handles agent_type=declarative; fetches workflow JSON from Registry API; injects WORKFLOW_JSON as base64 env var; uses declarative-runner image — `services/deploy-controller/reconciler.py`

**Studio Canvas Frontend (can run in parallel with T109–T114)**

- [X] [T117] [P] Zustand workflow store: nodes, edges, selectedNodeId, isDirty, setNodes, setEdges, selectNode, markSaved — `studio/src/stores/workflowStore.ts`
- [X] [T119] [P] Workflow serializer: converts React Flow nodes+edges to workflow JSON schema; deserializer for loading saved workflows — `studio/src/utils/workflowSerializer.ts`
- [X] [T120] [P] AgentNode component: icon, name label, source/target handles, click to select — `studio/src/nodes/AgentNode.tsx`
- [X] [T121] [P] HttpToolNode component: method badge, URL label, source/target handles — `studio/src/nodes/HttpToolNode.tsx`
- [X] [T122] [P] EndNode component: terminal indicator, target handle only (no out handle) — `studio/src/nodes/EndNode.tsx`
- [X] [T123] [P] PropertiesPanel component: renders config fields for selected node (Agent: name/instructions/model/risk; HttpTool: name/endpoint/method/headers/body; End: output_mapping) — `studio/src/components/PropertiesPanel.tsx`
- [X] [T124] [P] Toolbar component: Save button (calls saveWorkflow(), shows toast), Deploy button (calls deployWorkflow(), polls deployment status) — `studio/src/components/Toolbar.tsx`
- [X] [T125] [P] Canvas component: React Flow canvas with node types registered, Toolbar rendered above, PropertiesPanel on right; add @xyflow/react and zustand to package.json — `studio/src/components/Canvas.tsx`
- [X] [T155] [P] Studio first-save modal — React dialog shown before first `saveWorkflow()` call: workflow name input (required, validated against existing names), team selector dropdown (GET /teams), submit triggers save with name+team in payload (Gap M-22) — `studio/src/components/FirstSaveModal.tsx`
- [X] [T156] [P] Studio HTTP Tool node auth config selector — add `authConfigId` field to HttpToolNode properties panel: dropdown from GET /auth-configs; selected ID included in workflow JSON (Gap M-23) — `studio/src/components/PropertiesPanel.tsx`

**Verification:** `kubectl port-forward svc/studio 5173:80`; drag Agent+HttpTool+End nodes; fill properties; click Save → first-save modal → enter name and team → workflow_id returned; click Deploy → pod appears with `kubectl get pods -n agents-platform -l workflow={id}`; `curl -X POST localhost:8080/chat -d '{"message":"test"}'` returns response from declarative runner

---

## Checkpoint E2E — Create → Deploy → Invoke
_Gate: Phases 4–8 must be complete. Run before starting Phase 9._
_What you prove: Create agent via Studio UI → register order-agent with SDK → deploy → invoke via Envoy with JWT → see response → trigger high-risk tool → approve via Studio → stream resumes._

- [ ] [CPE-a] E2E demo deploy script: enable studio, deploy-controller, envoy-gateway in umbrella chart; run helm upgrade; wait for studio pod Ready; register and push order-agent example image — `scripts/deploy-cpe2e.sh`
- [ ] [CPE-b] Studio UI smoke test: kubectl port-forward studio; navigate to Create Agent form, fill name=smoke-agent team=platform, submit; verify GET /api/v1/agents returns smoke-agent; navigate to Deploy page, enter echo image tag, deploy; poll until status=Running — `scripts/smoke-test-cpe2e-studio.sh`
- [ ] [CPE-c] Full E2E invoke smoke test: deploy order-agent SDK example; POST /agents/order-agent/chat via Envoy with valid Keycloak JWT; assert 200 response with order status; send chat triggering issue_refund; assert SSE stream emits approval_requested event; PATCH /approvals/{id} to approved; assert SSE stream resumes with approval_decided then done — `scripts/smoke-test-cpe2e-invoke.sh`

> **To run:** `bash scripts/deploy-cpe2e.sh` → `bash scripts/smoke-test-cpe2e-studio.sh && bash scripts/smoke-test-cpe2e-invoke.sh`
> **Pass criteria:** Agent visible in Studio, deployed pod Running, full chat invoke succeeds, HITL pause/resume works end-to-end

---

## Phase 9 — Safety Orchestrator + Scanners
_Depends on: Phase 1 (Postgres for PII mappings), Phase 3 (Deploy Controller for routing); can start in parallel with Phase 10 after CPE gate passes_
_Also complete T049 (deferred from Phase 4) in this phase — OPA policy generator needs safety context to be useful_

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

**Also complete in this phase (deferred from Phase 4):**
- [ ] [T049] OPA policy generator: takes AgentVersion tools list, produces Rego policy text, stores in agent_policies table and writes K8s ConfigMap — `services/registry-api/policy_generator.py` _(then update T051 wiring in main.py to wire policy_generator call on deploy)_

**Also update Envoy HTTPRoutes (deferred from Phase 7):**
After Safety Orchestrator is running, update T087 HTTPRoutes so `/agents/{name}/chat` routes through `safety-orchestrator.agentshield-platform:8080` rather than directly to agent pods.

**Verification:** `curl -X POST http://safety-orchestrator:8080/api/v1/scan/input -d '{"text":"ignore previous instructions","agent_name":"echo-agent"}'` returns `{"blocked":true,"reason":"prompt_injection"}`; GET /ready returns all three scanners "up"

---

## Checkpoint 3 — Safety Pipeline Live
_Gate: Phase 9 must be complete. Run before starting Phase 10._
_What you prove: All 3 scanners healthy; injection blocked; PII redacted; fail-closed behaviour confirmed; agent requests now routed through safety._

- [ ] [CP3a] Helm upgrade script: enable safety-orchestrator, llm-guard, presidio, nemo — `scripts/deploy-cp3.sh`
- [ ] [CP3b] Scanner readiness smoke test: GET /ready on safety-orchestrator asserts all 3 scanners "up"; individual health checks on LLM Guard, Presidio, NeMo — `scripts/smoke-test-cp3-scanners.sh`
- [ ] [CP3c] Safety behaviour smoke test: POST known injection payload → assert blocked=true; POST PII text → assert sanitized_text has redacted values; POST clean text → assert blocked=false, sanitized_text unchanged; POST to /scan/input with LLM Guard pod stopped → assert blocked=true (fail-closed) — `scripts/smoke-test-cp3-safety.sh`

> **To run:** `bash scripts/deploy-cp3.sh` → wait for scanner pods (LLM Guard ~3min model load) → `bash scripts/smoke-test-cp3-scanners.sh && bash scripts/smoke-test-cp3-safety.sh`
> **Pass criteria:** Injection blocked, PII redacted, fail-closed confirmed

---

## Phase 10 — OPA Policies + Langfuse + Tracing SDK
_Depends on: Phase 3 (Deploy Controller for OPA sidecar), Phase 9 (Safety Orchestrator for tracing integration), Phase 6 (SDK for T152, T154)_

- [ ] [T069] Update manifest_builder.py to inject OPA sidecar container (openpolicyagent/opa:0.69.0-static, port 8181, policy-bundle volume from ConfigMap) — `services/deploy-controller/manifest_builder.py`
- [ ] [T070] Update policy_generator.py to write Rego policy to K8s ConfigMap in agents-{team} namespace after generating it — `services/registry-api/policy_generator.py`
- [ ] [T071] Langfuse sub-chart values: Postgres URL, ClickHouse sub-chart config, MinIO bucket, initial API key Secret reference — `charts/agentshield/charts/langfuse/values.yaml`
- [ ] [T072] [P] ClickHouse sub-chart values: single-node, PVC 10Gi, MinIO backup config — `charts/agentshield/charts/clickhouse/values.yaml`
- [ ] [T073] [P] Langfuse Chart.yaml listing clickhouse as dependency — `charts/agentshield/charts/langfuse/Chart.yaml`
- [ ] [T074] Langfuse tracing client wrapper: emits traces to Langfuse for safety scans and agent runs — `services/safety-orchestrator/tracing.py`
- [ ] [T075] [P] Langfuse tracing client for Registry API: emits deploy and approval events — `services/registry-api/tracing.py`
- [ ] [T076] Skeleton OPA client for SDK (calls localhost:8181, returns allow/require_approval/reason) — `sdk/agentshield_sdk/opa_client.py`
- [ ] [T152] Update `agentshield_sdk/tracing.py` — add `parent_trace_id` and `source_agent` metadata to all Langfuse spans; cross-agent trace stitching via shared `trace_id = session_id`; add `team` and `agent_name` as top-level trace metadata tags for team-level cost grouping (Gaps M-08, M-31) — `sdk/agentshield_sdk/tracing.py`
- [ ] [T154] Update `agentshield_sdk/streaming.py` — add `approval_timeout` SSE event type with payload schema `{event: "approval_timeout", data: {approval_id, thread_id, reason: "timeout", reopen_url}}`; consumed by client UX to show timeout state (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** `kubectl get pods -n agents-platform -l agent=echo-agent -o jsonpath='{.items[0].spec.containers[*].name}'` returns `echo-agent opa`; `curl localhost:8181/v1/data/agentshield/agent/echo_agent` returns allow decision; Langfuse UI shows traces with safety + agent spans

---

## Phase 11 — Complete HITL + Portkey
_Depends on: Phase 7 (Basic HITL baseline in place), Phase 10 (Redis available for pub/sub)_

- [ ] [T078] Approval notifier: on INSERT with status=pending, publish to Redis pub/sub channel approvals:pending; includes agent name, tool, Studio queue link — `services/registry-api/approval_notifier.py`
- [ ] [T079] Slack notifier: reads SLACK_WEBHOOK_URL; sends formatted message with approval context, tool args, and Studio deep link — `services/registry-api/slack_notifier.py`
- [ ] [T083] Portkey sub-chart values: OpenAI and Anthropic provider configs (API keys from Secrets), Redis cache URL — `charts/agentshield/charts/portkey/values.yaml`. Agents set OPENAI_BASE_URL=http://portkey:8787 so LLM calls route through Portkey transparently — not in the Envoy routing path.
- [ ] [T084] [P] Portkey Chart.yaml — `charts/agentshield/charts/portkey/Chart.yaml`
- [ ] [T148] Slack notification on approval timeout — extend `services/registry-api/slack_notifier.py` with `notify_timeout()`: sends message including thread_id, agent name, tool name, timed-out timestamp, and link to reopen via `/approvals/{id}/reopen`; called by timeout_worker.py when status transitions to timeout (Gap M-30) — `services/registry-api/slack_notifier.py`
- [ ] [T149] [P] Appsmith approval card conflict resolution UX — update Appsmith approval queue app to handle 409 Conflict on PATCH: display "Already decided by [reviewer_name] at [timestamp]" message and refresh the card to show final decision; prevent double-submit (Gap M-27) — `appsmith/apps/approval-queue-conflict.js`
- [ ] [T150] [P] SSE protocol update — add `approval_timeout` event type to `sdk/agentshield_sdk/streaming.py`: emitted when agent detects approval status = timeout via LISTEN/NOTIFY; payload includes `approval_id`, `thread_id`, `reason: "timeout"` (Gap M-28) — `sdk/agentshield_sdk/streaming.py`

**Verification:** Trigger high-risk tool → Slack message received with approval link; approve via Studio → agent resumes within 5s; verify Portkey in agent pod env: `OPENAI_BASE_URL=http://portkey.agentshield-platform:8787/v1`; timeout scenario: let approval expire → Slack timeout notification received

---

## Phase 12 — Observability Dashboards
_Depends on: Phase 10 complete (Langfuse running with ClickHouse), Phase 7 complete (approval queue baseline)_
_Gaps addressed: M-16, M-24, M-31_

- [ ] [T157] `langfuse/dashboards/safety-dashboard.json` — Langfuse dashboard import file: per-agent injection block rate chart, false positive tracking (blocked=true AND no human override), PII redaction count per team, scanner latency P50/P99 (Gap M-16) — `langfuse/dashboards/safety-dashboard.json`
- [ ] [T158] [P] `langfuse/dashboards/rejection-rate.json` — per-agent rejection rate dashboard: OPA deny rate, Safety block rate, HITL approval rate, combined pass rate funnel chart; filterable by team and time range (Gap M-16) — `langfuse/dashboards/rejection-rate.json`
- [ ] [T159] [P] Langfuse SDK instrumentation audit — update `agentshield_sdk/tracing.py` to ensure ALL traces include `team`, `agent_name`, `session_id` as Langfuse metadata tags; add `model`, `token_count` for cost grouping; write test asserting span metadata completeness (Gap M-31) — `sdk/agentshield_sdk/tracing.py`
- [ ] [T160] [P] `appsmith/apps/approval-queue.json` — Appsmith export/import file for the Approval Queue app: approval list view with status filter, approve/reject buttons with optimistic lock handling, session_id and OPA decision cross-reference panel (Gap M-24) — `appsmith/apps/approval-queue.json`
- [ ] [T161] [P] `appsmith/apps/agent-registry.json` — Appsmith export/import file for the Agent Registry app: agent list with deployment status, version history table, quarantine toggle button, team filter (Gap M-24) — `appsmith/apps/agent-registry.json`

**Verification:** Import `langfuse/dashboards/safety-dashboard.json` via Langfuse UI → dashboard renders with data; import both Appsmith app JSON files → approval queue shows pending approvals; agent registry shows registered agents with deployment status

---

## Checkpoint 4 — Full Governed Request
_Gate: Phases 9–12 must be complete. Run after all safety and observability is wired._
_What you prove: Complete request lifecycle — JWT auth via Envoy, safety scan, OPA policy, HITL approval pause/resume, SSE streaming, trace in Langfuse._

- [ ] [CP4a] Helm upgrade script: enable portkey, langfuse — `scripts/deploy-cp4.sh`
- [ ] [CP4b] Auth smoke test: send request with invalid JWT to Envoy → assert 401; send with valid JWT → assert forwarded to safety-orchestrator → agent — `scripts/smoke-test-cp4-auth.sh`
- [ ] [CP4c] Full E2E HITL smoke test: POST /chat/stream to echo-agent with high-risk tool trigger; assert SSE stream emits approval_requested event; PATCH approval to approved; assert stream resumes with approval_decided then done; check Langfuse trace created with all spans present (safety + OPA + approval) — `scripts/smoke-test-cp4-e2e.sh`

> **To run:** `bash scripts/deploy-cp4.sh` → `bash scripts/smoke-test-cp4-auth.sh && bash scripts/smoke-test-cp4-e2e.sh`
> **Pass criteria:** 401 on bad JWT, safety scan blocks injection, HITL pause/resume works, Langfuse trace visible with safety + approval spans

---

## Summary

**Total tasks:** 179 (164 implementation T001–T164 + 15 checkpoint CP1a–CP4c + CPE-a–CPE-c)

| Phase | Tasks | Parallel | Status | Notes |
|-------|-------|---------|--------|-------|
| Phase 1 — Infra Setup | T001–T016, T130–T131 (18) | T006–T016, T131 (12) | ✅ Core done; T130–T131 pending | Sub-chart values all parallel |
| Phase 2 — Registry API | T017–T033, T132–T136 (22) | T022–T024, T133–T135 (6) | ✅ Core done; T132–T136 pending | Must finish T132–T136 before Ph5+Ph7 |
| **Checkpoint 1** | CP1a–CP1c (3) | — | Pending | Deploy data layer + Registry API |
| Phase 3 — Deploy Controller | T034–T043, T137–T139 (13) | None | ✅ Done | Timeout worker + reopen endpoint done |
| **Checkpoint 2** | CP2a–CP2c (3) | — | ✅ Done | First agent deployed; OPA sidecar verified |
| Phase 3b — Ops & Runbooks | T140–T145 (6) | T141–T145 (5) | ✅ Done | Scripts + runbooks |
| Phase 4 — Tool Registry | T044–T051 (8) | T047–T048 (2) | Pending | T049 deferred to Phase 9 |
| Phase 5 — Studio UI MVP | T115–T116, T118, T126–T129, T162–T164 (10) | T163–T164, T127–T129 (5) | Pending | 3-screen app; canvas in Phase 8 |
| Phase 6 — SDK v1 | T089–T108, T151–T154 (24) | T107–T108, T152–T154 (5) | Pending | T152+T154 deferred to Phase 10 |
| Phase 7 — Basic HITL | T077–T088, T148–T150 (15) | T085–T087 (3) | Pending | No Redis/Slack/Portkey yet; T078–T079, T083–T084, T148–T150 deferred to Phase 11 |
| Phase 8 — Declarative Runner + Canvas | T109–T114, T117, T119–T125, T155–T156 (16) | T117, T119–T125, T155–T156 (9) | Pending | Backend sequential; canvas frontend all parallel |
| **Checkpoint E2E** | CPE-a–CPE-c (3) | — | Pending | Create → deploy → invoke E2E demo |
| Phase 9 — Safety Orchestrator | T052–T068, T146–T147 (19) + T049 | T060–T068, T147 (10) | Pending | T049 (deferred from Phase 4) also done here |
| **Checkpoint 3** | CP3a–CP3c (3) | — | Pending | Safety pipeline live; fail-closed confirmed |
| Phase 10 — OPA + Langfuse + Tracing | T069–T076, T152, T154 (10) | T072–T073, T075–T076 (4) | Pending | OPA policy ConfigMaps + Langfuse charts + SDK tracing |
| Phase 11 — Complete HITL + Portkey | T078–T079, T083–T084, T148–T150 (7) | T084, T149–T150 (3) | Pending | Redis pub/sub + Slack + Portkey |
| Phase 12 — Dashboards | T157–T161 (5) | T158–T161 (4) | Pending | Langfuse dashboards + Appsmith import files |
| **Checkpoint 4** | CP4a–CP4c (3) | — | Pending | Full governed: JWT + safety + HITL + Langfuse trace |

**Parallelism highlights:** Phase 8 canvas frontend (9 parallel tasks), Phase 1 sub-chart values (12 parallel tasks), Phase 9 scanner Helm charts (10 parallel tasks)
**Gap coverage:** 32 gap-tagged tasks cover 14 critical gaps and 17 major gaps; see `docs/plan/gaps.md` for full register
**E2E milestone:** Functional end-to-end (create → deploy → invoke → approve) visible after Phase 7/8 + Checkpoint E2E — before any scanner integration
