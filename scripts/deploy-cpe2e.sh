#!/usr/bin/env bash
# deploy-cpe2e.sh — Checkpoint E2E deploy (fresh cluster or after restart)
#
# Creates all required secrets, builds Phase 9.3 + 10.x images, and deploys
# the full AgentShield stack:
#   - registry-api:0.2.105 (Slice 4: RBAC foundation — rbac.py module, migration 0044 artifact_role_grants, creator auto-grant, /me enrichment, role normalization)
#   - studio:0.1.85        (Slice 4: RequireRole guard, isAtLeast() in AuthContext, admin sidebar gated, route-level platform-admin guards)
#   - registry-api:0.2.104 (Slices 2+3: delete-version cascade (409 on published), workflow artifact page endpoints, WorkflowMiniGraph topology data)
#   - studio:0.1.84        (Slices 2+3: agent versions table with deploy/delete, WorkflowDetailPage, WorkflowMiniGraph SVG, workflow deployment overview topology)
#   - registry-api:0.2.103 (Slice 1b: workflow_versions + workflow_deployments tables (migrations 0042/0043); workflow version snapshot + deploy + lifecycle + stats/runs endpoints; agent_runs.workflow_deployment_id FK)
#   - studio:0.1.83        (Slice 1b: WorkflowDeploymentOverviewPage + /workflows/:id/d/:depId route; workflow version/deployment API client functions)
#   - registry-api:0.2.102 (Slice 1a: sandbox deployment lifecycle — migration 0041 (status enum +suspending/suspended/terminating, +suspended_at, +ttl_hours); PATCH /agents/{name}/deployments/{id} suspend/resume/terminate/upgrade; AgentResponse.latest_version_number)
#   - studio:0.1.82        (Slice 1a: DeploymentActions (state-based lifecycle) on Overview + Deployments tab; DeployModal (replicas+TTL); agent list name-link + version col + deploy modal)
#   - deploy-controller:0.1.23 (Slice 1a: sandbox suspend→scale0→suspended / terminate→delete→terminated handling in poll loop)
#   - registry-api:0.2.101 (Slice 1 deployment overview: deployments.name + agent_runs.sandbox_deployment_id (0040); GET /deployments/{id}/stats+/runs; AgentRunCreate accepts *_deployment_id)
#   - studio:0.1.81        (Slice 1: DeploymentOverviewPage + /agents/:name/d/:depId; artifact page = deployments/versions/settings; Overview*/RunsTab deployment-scoped)
#   - registry-api:0.2.84  (Production artifact isolation: catalog API, production_deployments, run isolation via production_deployment_id)
#   - studio:0.1.66        (CatalogDetailPage: versions/deployments/runs tabs, deploy/upgrade/suspend/resume actions)
#   - deploy-controller:0.1.13 (Production reconciler: poll catalog internal API, reconcile production pods from config_snapshot)
#   - registry-api:0.2.82  (Workflow publish endpoint + run_by/langfuse_trace_id for internal runs + trace_url in agent-runs response)
#   - studio:0.1.64        (Workflow publish button, grant form published-only filter + workflow support, Catalog uses CompositeWorkflows, Runs trace external link)
#   - registry-api:0.2.80  (Fix publish flow: auto-resolve agent_version_id in eval-run creation + pass AGENT_VERSION_ID to eval Job)
#   - studio:0.1.62        (Publish flow guard + runs/stats display: expandable rows, input preview, honest cost card)
#   - registry-api:0.2.75  (Fix Langfuse trace URL: use full /project/{pid}/traces/{tid} path to avoid redirect losing /langfuse/ prefix behind Gateway)
#   - registry-api:0.2.73  (Eval results publish lifecycle: expected_output column, langfuse_trace_id in schema, trace-by-id endpoint, admin publish eval evidence)
#   - studio:0.1.58        (Eval results UX: expandable rows, failed filter, score colors, action CTAs, TraceDrawer, publish eval gate, admin eval column, datasets eval runs)
#   - eval-runner:0.1.4    (Eval-mode LLM judge: sync POST /judge + markdown-strip keyword fallback)
#   - eval-runner:0.1.2    (Include expected_output in result POST)
#   - registry-api:0.2.72  (Batch eval fixes: judge Bedrock support via boto3, fix decrypt_json import, evalRunnerImage in values.yaml, save_run_to_dataset expected_output)
#   - registry-api:0.2.64  (Pausable workflow-HITL: migration 0032 agent_runs.orchestrator_state JSONB checkpoint; workflow_orchestrator per-child thread_id + authoritative pending-Approval pause detection (halts all 4 modes at awaiting_approval); resume_orchestration re-entry (sequential auto-advance); decide_approval _resume_and_advance workflow hook)
#   - deploy-controller:0.1.8  (inject AGENTSHIELD_OPA_URL=http://localhost:8181 into agent pods so SDK exits DEV_MODE and consults real OPA — fixes global mock-allow governance bypass)
#   - studio:0.1.48        (awaiting_approval amber badge in workflow run tree + RunsTab status filter option)
#   - registry-api:0.2.63  (Decision 24 impl#3: composable agent filter (?composable=true); workflow-level trigger CRUD (/workflows/{id}/triggers); production HITL resume (PATCH /approvals fires agent pod /resume); migration 0031 agent_events.workflow_id)
#   - studio:0.1.47        (Decision 24 impl#3: AddAgentModal composable filter + reactive/durable inline toggle; WorkflowTriggersPanel + Triggers button; execution_shape in workflow Save modal)
#   - scheduler:0.1.1      (fires workflow schedule triggers via UNION over agent+workflow trigger rows)
#   - event-gateway:0.1.1  (POST /hooks/workflow/{name}/{token} fires workflow webhook triggers)
#   - registry-api:0.2.62  (Per-schedule input_payload on agent_triggers (migration 0030); internal.py resolves scheduled input from the trigger; type-aware instruction templates support)
#   - studio:0.1.46        (Type-aware create-wizard instruction templates (scheduled/event-driven) + JSON input-payload field in wizard & Settings new-schedule form)
#   - registry-api:0.2.59  (Decision 22: composite workflows — rename agent_graphs, workflow members, run-tree orchestration, trigger_type=workflow)
#   - studio:0.1.43        (Decision 22: agent-graphs rename + composite workflow builder — add existing agents)
#   - declarative-runner:0.1.7 (Decision 22: WorkflowOrchestrator module + /workflow-run — future-state)
#   - registry-api:0.2.55  (Bug fix: deny-by-default agent/playground-run visibility for anonymous callers)
#   - registry-api:0.2.54  (Phase 9: agent_events + /events + rotate-token; FIX internal.py Deployment.agent_id/deployed_at; FIX AgentEventResponse INET→str coercion)
#   - event-gateway:0.1.0  (Phase 9 NEW: public webhook ingress — token + rate-limit + replay + filter + dispatch)
#   - studio:0.1.42        (Phase 9: OverviewEventDriven + webhook token rotation in Settings)
#   - registry-api:0.2.52  (Phase 8: alert config on triggers + SMTP failure alerts + /health endpoint; create_trigger persists alert fields)
#   - studio:0.1.41        (Phase 8: trigger alert config in Settings + health dots on agent list)
#   - registry-api:0.2.42  (Phase 4: AgentRun production tracking + /stats endpoint)
#   - safety-orchestrator:0.1.3 (per-scanner Langfuse spans + trace_id propagation)
#   - deploy-controller:0.1.7 (Phase 9.1 ensure_service_account wired in)
#   - studio:0.1.37        (Phase 4: AgentDetail tabbed layout + RunsTab + OverviewReactive)
#   - eval-runner:0.1.1    (batch eval 403 fix (service-identity) + Haiku judge poll)
#   - declarative-runner:0.1.4 (Phase 4: production AgentRun creation + completion tracking)
#   - python-executor:0.1.0 (sandboxed Python code runner)
#   - Langfuse:3.x         (LLM observability — auto-bootstrapped, internal to platform)
#   - PostgreSQL, Redis (infra)
#
# Seeded by step 8: 6 tools, 2 skills, 3 workflows, 5 agents
#
# Usage: bash scripts/deploy-cpe2e.sh
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="25m"

# ── Credentials (dev defaults — change in production) ─────────────────────────
PG_PASS="DevPass2024"
REDIS_PASS="RedisPass2024"
MINIO_USER="agentshield-admin"
MINIO_PASS="MinioPass2024"
KC_ADMIN_PASS="AdminPass2024"
KC_PLATFORM_ADMIN_PASS="PlatformAdmin2024"
KC_REVIEWER_PASS="Reviewer2024"
# Fernet key for LLM credential encryption (32-byte base64 URL-safe)
ENCRYPTION_KEY="dGVzdGtleS10ZXN0a2V5LXRlc3RrZXktdGVzdGtleTA="

# ── Image tags ────────────────────────────────────────────────────────────────
REGISTRY_API_TAG="0.2.116"
SAFETY_ORCHESTRATOR_TAG="0.1.3"
DEPLOY_CONTROLLER_TAG="0.1.26"
STUDIO_TAG="0.1.98"
EVAL_RUNNER_TAG="0.1.4"
DECLARATIVE_RUNNER_TAG="0.1.17"
PYTHON_EXECUTOR_TAG="0.1.0"
SCHEDULER_TAG="0.1.1"
EVENT_GATEWAY_TAG="0.1.1"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> AgentShield CPE2E Deploy — $(date)"
echo ""

# ── Step 0: Pre-deploy backup (best-effort) ──────────────────────────────────
# If Postgres is already running, snapshot it before we touch anything.
PG_POD=$(kubectl get pod -n agentshield-platform -l app.kubernetes.io/name=postgresql \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$PG_POD" ]; then
  echo "[0/8] Pre-deploy Postgres backup..."
  bash "${REPO_ROOT}/scripts/backup-postgres.sh" || echo "  ⚠ backup failed (non-fatal)"
  echo ""
fi

# ── Step 1: Build images ──────────────────────────────────────────────────────
# IMPORTANT: Always use `helm upgrade` to deploy registry-api changes.
# `kubectl set image` only updates the main container — the alembic-migrate
# init container stays pinned to the old Helm-rendered tag, skipping new migrations.
# If you must use kubectl, update BOTH containers:
#   kubectl set image deployment/agentshield-registry-api \
#     alembic-migrate=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     registry-api=registry.internal/agentshield/registry-api:$REGISTRY_API_TAG \
#     -n agentshield-platform

echo "[1/8] Building images..."
echo "  → registry-api:${REGISTRY_API_TAG} (Phase 7 — internal run-start endpoint for scheduler/events)"
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "  → safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG} (per-scanner Langfuse spans, trace_id propagation)"
docker build -t "registry.internal/agentshield/safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG}" services/safety-orchestrator/

echo "  → deploy-controller:${DEPLOY_CONTROLLER_TAG} (pre-flight gate in reconciler)"
docker build -t "registry.internal/agentshield/deploy-controller:${DEPLOY_CONTROLLER_TAG}" services/deploy-controller/

echo "  → declarative-runner:${DECLARATIVE_RUNNER_TAG} (memory context load/save integration)"
docker build -t "registry.internal/agentshield/declarative-runner:${DECLARATIVE_RUNNER_TAG}" -f services/declarative-runner/Dockerfile .

echo "  → studio:${STUDIO_TAG} (MemoryTab component + memory API functions)"
docker build -t "registry.internal/agentshield/studio:${STUDIO_TAG}" studio/

echo "  → eval-runner:${EVAL_RUNNER_TAG} (NEW — batch eval K8s Job image)"
docker build -t "registry.internal/agentshield/eval-runner:${EVAL_RUNNER_TAG}" services/eval-runner/

echo "  → python-executor:${PYTHON_EXECUTOR_TAG} (new — sandboxed Python tool runner)"
docker build -t "registry.internal/agentshield/python-executor:${PYTHON_EXECUTOR_TAG}" services/python-executor/

echo "  → scheduler:${SCHEDULER_TAG} (Phase 7 — fires scheduled agents on cron, HA)"
docker build -t "registry.internal/agentshield/scheduler:${SCHEDULER_TAG}" services/scheduler/

echo "  → event-gateway:${EVENT_GATEWAY_TAG} (Phase 9 — public webhook ingress)"
docker build -t "registry.internal/agentshield/event-gateway:${EVENT_GATEWAY_TAG}" services/event-gateway/

# ── Step 2: Namespaces ────────────────────────────────────────────────────────
echo ""
echo "[2/8] Applying namespaces..."
kubectl apply -f infra/namespaces/agentshield-platform.yaml
kubectl apply -f infra/namespaces/agents-platform.yaml
kubectl apply -f infra/namespaces/agentshield-playground.yaml
kubectl apply -f infra/rbac/playground-runner-clusterrole.yaml

# ── Step 3: Secrets (all required by chart templates) ─────────────────────────
echo ""
echo "[3/8] Creating secrets..."

# Core platform secrets consumed by registry-api init containers + deployment
kubectl create secret generic agentshield-secrets \
  -n "$NAMESPACE" \
  --from-literal=registry-api-url="http://agentshield-registry-api.${NAMESPACE}:8000" \
  --from-literal=database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=direct-database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f -

# Encryption key for LLM provider credentials
# Template expects key named "key"
kubectl create secret generic agentshield-encryption \
  -n "$NAMESPACE" \
  --from-literal=key="${ENCRYPTION_KEY}" \
  --from-literal=AGENTSHIELD_ENCRYPTION_KEY="${ENCRYPTION_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# PostgreSQL passwords (Bitnami existingSecret pattern)
kubectl create secret generic postgres-passwords \
  -n "$NAMESPACE" \
  --from-literal=keycloak="${PG_PASS}" \
  --from-literal=agentshield="${PG_PASS}" \
  --from-literal=langfuse="${PG_PASS}" \
  --from-literal=langgraph="${PG_PASS}" \
  --from-literal=appsmith="${PG_PASS}" \
  --from-literal=registry-api-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --from-literal=registry-api-direct-url="postgresql+asyncpg://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f -

# Redis password (Bitnami existingSecret)
kubectl create secret generic redis-password \
  -n "$NAMESPACE" \
  --from-literal=redis-password="${REDIS_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# MinIO root credentials (used by keycloak-raw.yaml + minio-raw.yaml templates)
kubectl create secret generic minio-credentials \
  -n "$NAMESPACE" \
  --from-literal=root-user="${MINIO_USER}" \
  --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Keycloak admin credentials (keycloak-raw.yaml)
kubectl create secret generic keycloak-admin-password \
  -n "$NAMESPACE" \
  --from-literal=admin-password="${KC_ADMIN_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Keycloak realm user passwords
kubectl create secret generic keycloak-user-passwords \
  -n "$NAMESPACE" \
  --from-literal=platform-admin="${KC_PLATFORM_ADMIN_PASS}" \
  --from-literal=agent-reviewer="${KC_REVIEWER_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Langfuse tracing keys + NextAuth/encryption secrets
# public-key/secret-key are used to auto-bootstrap the AgentShield project on first boot
# and are shared with registry-api/safety-orchestrator for SDK tracing.
LANGFUSE_SALT="$(openssl rand -base64 32 2>/dev/null || echo 'agentshield-dev-salt-placeholder-32')"
LANGFUSE_ENC_KEY="$(openssl rand -hex 32 2>/dev/null || echo 'a1b2c3d4e5f6789012345678901234560123456789012345678901234567890b')"
kubectl create secret generic langfuse-api-keys \
  -n "$NAMESPACE" \
  --from-literal=public-key="pk-lf-agentshield-dev-local-0001" \
  --from-literal=secret-key="sk-lf-agentshield-dev-local-0001" \
  --from-literal=nextauth-secret="agentshield-nextauth-dev-2024-sec" \
  --from-literal=salt="${LANGFUSE_SALT}" \
  --from-literal=encryption-key="${LANGFUSE_ENC_KEY}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Slack webhook (registry-api reads webhook-url key)
kubectl create secret generic slack-credentials \
  -n "$NAMESPACE" \
  --from-literal=bot-token="xoxb-placeholder-dev-token" \
  --from-literal=signing-secret="placeholder-signing-secret-dev" \
  --from-literal=webhook-url="https://hooks.slack.com/services/placeholder/dev" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "  All secrets applied."

# ── Step 4: Helm dependency update ───────────────────────────────────────────
echo ""
echo "[4/8] Updating Helm dependencies..."
helm dependency update "$CHART" 2>/dev/null || true

# Apply Langfuse-specific infra (ClickHouse + S3 alias Services).
# Bitnami sub-charts name services <release>-{chart} but Langfuse derives
# <release>-langfuse-{chart}. These alias Services bridge that naming gap.
kubectl apply -f infra/langfuse/clickhouse-alias-svc.yaml 2>/dev/null || true

# Apply OPA Bundle Server infra (nginx + bundle-sync sidecar).
# The bundle-sync sidecar polls registry-api /api/v1/bundle every 30s so
# OPA sidecars always have fresh policy + data without ConfigMap patches.
kubectl apply -f infra/opa-bundle-server/configmap-nginx-conf.yaml 2>/dev/null || true
kubectl apply -f infra/opa-bundle-server/service.yaml 2>/dev/null || true
kubectl apply -f infra/opa-bundle-server/deployment.yaml 2>/dev/null || true

# opa-sidecar-config ConfigMap must exist in every agents-* namespace — the
# deploy-controller mounts it into each agent pod's OPA sidecar (bundle polling).
# Without it, agent pods hang in ContainerCreating ("configmap opa-sidecar-config
# not found"). The manifest targets agents-platform; mirror it to other team ns.
kubectl apply -f infra/opa-bundle-server/configmap-opa-config.yaml 2>/dev/null || true
for team_ns in agents-operations; do
  kubectl get ns "$team_ns" >/dev/null 2>&1 && \
    kubectl get configmap opa-sidecar-config -n agents-platform -o yaml 2>/dev/null \
    | sed "s/namespace: agents-platform/namespace: ${team_ns}/" \
    | kubectl apply -f - 2>/dev/null || true
done

# ── Step 5: Helm upgrade ──────────────────────────────────────────────────────
echo ""
echo "[5/8] Helm upgrade/install '${RELEASE}'..."

# Clean up stale realm-init job if it exists (hook fails on re-deploy otherwise)
kubectl delete job "${RELEASE}-realm-init" -n "$NAMESPACE" --ignore-not-found=true

# Image tags, component enable/disable toggles, dev sizing, and global.postgresHost
# are now baked into charts/agentshield/values.yaml as the default composition, so
# a plain `helm upgrade --install` (no --set flags) deploys the full local platform.
# Keep image-tag bumps in sync between this script's build steps and values.yaml.
# To override per-environment, add a -f <values-override>.yaml or --set here.
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── Step 6: Wait for rollouts ─────────────────────────────────────────────────
echo ""
echo "[6/8] Waiting for rollouts..."
kubectl rollout status statefulset/agentshield-postgresql -n "$NAMESPACE" --timeout=5m
kubectl rollout status statefulset/agentshield-redis-master -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-python-executor -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-scheduler -n "$NAMESPACE" --timeout=3m || echo "  (Scheduler starting)"
kubectl rollout status deployment/agentshield-langfuse-web -n "$NAMESPACE" --timeout=5m || echo "  (Langfuse web may need DB migrations — check logs if still pending)"
kubectl rollout status deployment/agentshield-langfuse-worker -n "$NAMESPACE" --timeout=3m || echo "  (Langfuse worker starting)"

# Create Keycloak client for Langfuse SSO (idempotent — skips if exists)
echo "  Creating Keycloak client 'langfuse' for SSO..."
kubectl exec -n "$NAMESPACE" deploy/agentshield-registry-api -c registry-api -- python3 -c "
import urllib.request, urllib.parse, json
data = urllib.parse.urlencode({'grant_type':'password','client_id':'admin-cli','username':'admin','password':'AdminPass2024'}).encode()
req = urllib.request.Request('http://agentshield-keycloak/realms/master/protocol/openid-connect/token', data=data)
token = json.loads(urllib.request.urlopen(req).read())['access_token']
client = json.dumps({'clientId':'langfuse','name':'Langfuse','enabled':True,'protocol':'openid-connect','publicClient':False,'secret':'langfuse-client-secret-2024','redirectUris':['https://langfuse.127.0.0.1.nip.io:8443/*'],'webOrigins':['https://langfuse.127.0.0.1.nip.io:8443'],'standardFlowEnabled':True,'directAccessGrantsEnabled':True}).encode()
req2 = urllib.request.Request('http://agentshield-keycloak/admin/realms/agentshield/clients', data=client, headers={'Authorization':f'Bearer {token}','Content-Type':'application/json'})
try:
    urllib.request.urlopen(req2); print('  Created')
except urllib.error.HTTPError as e:
    print('  Already exists (OK)' if e.code==409 else f'  Error: {e.code}')
" 2>/dev/null || echo "  Warning: could not create Langfuse SSO client"

# Create langfuse-media bucket in the Langfuse MinIO (s3) pod.
# MinIO starts with no buckets; Langfuse needs this bucket for event blob storage.
echo "  Creating langfuse-media bucket in MinIO..."
MINIO_POD=$(kubectl get pod -n "$NAMESPACE" --no-headers | grep "agentshield-s3-" | awk '{print $1}' | head -1)
if [ -n "$MINIO_POD" ]; then
  kubectl exec -n "$NAMESPACE" "$MINIO_POD" -- \
    mc alias set local http://localhost:9000 langfuse-admin LangfuseMinio2024 2>/dev/null || true
  kubectl exec -n "$NAMESPACE" "$MINIO_POD" -- \
    mc mb local/langfuse-media 2>/dev/null || true
  echo "  langfuse-media bucket ready."
else
  echo "  Warning: Langfuse MinIO pod not found — create bucket manually."
fi

# ── Step 7: Seed default teams ────────────────────────────────────────────────
echo ""
echo "[7/8] Seeding default teams..."
REGISTRY_URL="http://localhost:8000"
kubectl port-forward svc/agentshield-registry-api -n "$NAMESPACE" 8000:8000 &
PF_PID=$!
sleep 3

for TEAM_NAME in platform operations; do
  NAMESPACE_VAL="agents-${TEAM_NAME}"
  STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${REGISTRY_URL}/api/v1/teams/" \
    -H "Content-Type: application/json" \
    -d "{\"name\":\"${TEAM_NAME}\",\"namespace\":\"${NAMESPACE_VAL}\"}")
  if [ "$STATUS" = "201" ]; then
    echo "  Created team: ${TEAM_NAME}"
  elif [ "$STATUS" = "409" ]; then
    echo "  Team already exists: ${TEAM_NAME} (skipped)"
  else
    echo "  Warning: team ${TEAM_NAME} returned HTTP ${STATUS}"
  fi
done

kill $PF_PID 2>/dev/null || true
wait $PF_PID 2>/dev/null || true

# ── Step 8: Seed default resources ───────────────────────────────────────────
echo ""
echo "[8/8] Seeding default resources (tools, skills, agents, workflows)..."
kubectl port-forward svc/agentshield-registry-api -n "$NAMESPACE" 8001:8000 &
PF2_PID=$!
sleep 3

REGISTRY_URL="http://localhost:8001" bash scripts/seed-defaults.sh || true

kill $PF2_PID 2>/dev/null || true
wait $PF2_PID 2>/dev/null || true

echo ""
echo "================================================================"
echo "  AgentShield CPE2E Deploy — COMPLETE"
echo "================================================================"
echo ""
kubectl get pods -n "$NAMESPACE" --no-headers | sort
echo ""

# --- Envoy Gateway status ---
GW_STATUS=$(kubectl get gateway agentshield-gateway -n "$NAMESPACE" -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' 2>/dev/null || echo "")
if [ "$GW_STATUS" = "True" ]; then
  GW_ADDR=$(kubectl get gateway agentshield-gateway -n "$NAMESPACE" -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || echo "pending")
  echo "Envoy Gateway:  READY (address: ${GW_ADDR})"
  echo ""
  echo "Access (run 'bash scripts/gateway-proxy.sh' for local HTTPS, then):"
  echo "  Studio:        https://agentshield.127.0.0.1.nip.io:8443"
  echo "  Registry API:  https://agentshield.127.0.0.1.nip.io:8443/api/v1/health"
  echo "  Keycloak:      https://agentshield.127.0.0.1.nip.io:8443/realms/agentshield/.well-known/openid-configuration"
  echo "  Langfuse:      https://langfuse.127.0.0.1.nip.io:8443  (SSO via Keycloak — single login)"
  echo "  MinIO Console: https://agentshield.127.0.0.1.nip.io:8443/minio/"
  echo "  Webhooks:      https://agentshield.127.0.0.1.nip.io:8443/webhooks/"
elif [ -n "$GW_STATUS" ]; then
  echo "Envoy Gateway:  NOT READY (status: ${GW_STATUS})"
  echo "  Check: kubectl get gateway -n $NAMESPACE"
  echo ""
  echo "Fallback port-forward commands:"
  echo "  Registry API:      kubectl port-forward svc/agentshield-registry-api    -n ${NAMESPACE} 8000:8000"
  echo "  Studio:            kubectl port-forward svc/agentshield-studio          -n ${NAMESPACE} 5173:80"
else
  echo "Envoy Gateway:  NOT INSTALLED (controller missing or gateway not created)"
  echo "  Install: bash scripts/setup-envoy-gateway.sh"
  echo ""
  echo "Port-forward commands (legacy access):"
  echo "  Registry API:      kubectl port-forward svc/agentshield-registry-api    -n ${NAMESPACE} 8000:8000"
  echo "  Studio:            kubectl port-forward svc/agentshield-studio          -n ${NAMESPACE} 5173:80"
  echo "  Python Executor:   kubectl port-forward svc/agentshield-python-executor  -n ${NAMESPACE} 8081:8080"
  echo "  Langfuse UI:       kubectl port-forward svc/agentshield-langfuse-web    -n ${NAMESPACE} 4000:3000"
fi
echo ""
echo "Langfuse default credentials:"
echo "  URL:      http://agentshield.local/langfuse/ (or http://localhost:4000 via port-forward)"
echo "  Email:    admin@agentshield.local"
echo "  Password: AdminPass2024"
echo "  Project:  AgentShield Platform"
echo "  API Keys: pk-lf-agentshield-dev-local-0001 / sk-lf-agentshield-dev-local-0001"
echo ""
echo "Default resources seeded: 6 tools (5 HTTP + 1 Python), 2 skills, 3 workflows, 5 agents"
echo "Next: bash scripts/smoke-test-cpe2e-studio.sh"
