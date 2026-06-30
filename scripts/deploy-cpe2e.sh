#!/usr/bin/env bash
# deploy-cpe2e.sh — Checkpoint E2E deploy (fresh cluster or after restart)
#
# Creates all required secrets, builds Phase 9.3 + 10.x images, and deploys
# the full AgentShield stack:
#   - registry-api:0.2.18  (+ grant audit endpoint, AdminApprovalAuthorityPage, FK 500→409)
#   - safety-orchestrator:0.1.3 (per-scanner Langfuse spans + trace_id propagation)
#   - deploy-controller:0.1.7 (Phase 9.1 ensure_service_account wired in)
#   - studio:0.1.18        (+ AdminApprovalAuthorityPage, Approvers nav link)
#   - eval-runner:0.1.0    (NEW: batch eval K8s Job image)
#   - declarative-runner:0.1.1 (PythonToolNodeExecutor)
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
REGISTRY_API_TAG="0.2.21"
SAFETY_ORCHESTRATOR_TAG="0.1.3"
DEPLOY_CONTROLLER_TAG="0.1.7"
STUDIO_TAG="0.1.19"
EVAL_RUNNER_TAG="0.1.0"
DECLARATIVE_RUNNER_TAG="0.1.1"
PYTHON_EXECUTOR_TAG="0.1.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> AgentShield CPE2E Deploy — $(date)"
echo ""

# ── Step 1: Build images ──────────────────────────────────────────────────────
echo "[1/8] Building images..."
echo "  → registry-api:${REGISTRY_API_TAG} (agent_runs, trace middleware, bundle endpoint, playground trace/feedback)"
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "  → safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG} (per-scanner Langfuse spans, trace_id propagation)"
docker build -t "registry.internal/agentshield/safety-orchestrator:${SAFETY_ORCHESTRATOR_TAG}" services/safety-orchestrator/

echo "  → deploy-controller:${DEPLOY_CONTROLLER_TAG} (pre-flight gate in reconciler)"
docker build -t "registry.internal/agentshield/deploy-controller:${DEPLOY_CONTROLLER_TAG}" services/deploy-controller/

echo "  → declarative-runner:${DECLARATIVE_RUNNER_TAG} (PythonToolNodeExecutor support)"
docker build -t "registry.internal/agentshield/declarative-runner:${DECLARATIVE_RUNNER_TAG}" services/declarative-runner/

echo "  → studio:${STUDIO_TAG} (AdminApprovalAuthorityPage, Approvers nav link)"
docker build -t "registry.internal/agentshield/studio:${STUDIO_TAG}" studio/

echo "  → eval-runner:${EVAL_RUNNER_TAG} (NEW — batch eval K8s Job image)"
docker build -t "registry.internal/agentshield/eval-runner:${EVAL_RUNNER_TAG}" services/eval-runner/

echo "  → python-executor:${PYTHON_EXECUTOR_TAG} (new — sandboxed Python tool runner)"
docker build -t "registry.internal/agentshield/python-executor:${PYTHON_EXECUTOR_TAG}" services/python-executor/

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

# ── Step 5: Helm upgrade ──────────────────────────────────────────────────────
echo ""
echo "[5/8] Helm upgrade/install '${RELEASE}'..."

# Clean up stale realm-init job if it exists (hook fails on re-deploy otherwise)
kubectl delete job "${RELEASE}-realm-init" -n "$NAMESPACE" --ignore-not-found=true

helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --timeout "$TIMEOUT" \
  \
  --set "registry-api.image.tag=${REGISTRY_API_TAG}" \
  --set "studio.image.tag=${STUDIO_TAG}" \
  --set "deploy-controller.declarativeRunnerTag=${DECLARATIVE_RUNNER_TAG}" \
  --set "python-executor.image.tag=${PYTHON_EXECUTOR_TAG}" \
  \
  --set deploy-controller.enabled=true \
  --set studio.enabled=true \
  --set python-executor.enabled=true \
  --set safety-orchestrator.enabled=false \
  --set safety-orchestrator.llmguardEnabled=false \
  --set safety-orchestrator.presidioEnabled=false \
  --set safety-orchestrator.nemoEnabled=false \
  --set llm-guard.enabled=false \
  --set presidio.enabled=false \
  --set nemo.enabled=false \
  --set portkey.enabled=false \
  --set opa.enabled=false \
  --set langfuse.enabled=true \
  --set clickhouse.enabled=false \
  --set appsmith.enabled=false \
  --set minio.enabled=false \
  --set pgbouncer.enabled=false \
  --set keycloak.enabled=false \
  \
  --set "global.postgresHost=${RELEASE}-postgresql" \
  \
  --set postgresql.readReplicas.replicaCount=0 \
  --set postgresql.primary.replication.synchronousCommit=off \
  --set postgresql.primary.replication.numSynchronousReplicas=0 \
  --set postgresql.primary.persistence.size=5Gi \
  --set redis.master.persistence.size=1Gi

# ── Step 6: Wait for rollouts ─────────────────────────────────────────────────
echo ""
echo "[6/8] Waiting for rollouts..."
kubectl rollout status statefulset/agentshield-postgresql -n "$NAMESPACE" --timeout=5m
kubectl rollout status statefulset/agentshield-redis-master -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-python-executor -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-langfuse-web -n "$NAMESPACE" --timeout=5m || echo "  (Langfuse web may need DB migrations — check logs if still pending)"
kubectl rollout status deployment/agentshield-langfuse-worker -n "$NAMESPACE" --timeout=3m || echo "  (Langfuse worker starting)"

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
echo "Port-forward commands:"
echo "  Registry API:      kubectl port-forward svc/agentshield-registry-api    -n ${NAMESPACE} 8000:8000"
echo "  Studio:            kubectl port-forward svc/agentshield-studio          -n ${NAMESPACE} 5173:80"
echo "  Python Executor:   kubectl port-forward svc/agentshield-python-executor  -n ${NAMESPACE} 8081:8080"
echo "  Langfuse UI:       kubectl port-forward svc/agentshield-langfuse-web    -n ${NAMESPACE} 4000:3000"
echo ""
echo "Langfuse default credentials:"
echo "  URL:      http://localhost:4000"
echo "  Email:    admin@agentshield.local"
echo "  Password: AdminPass2024"
echo "  Project:  AgentShield Platform"
echo "  API Keys: pk-lf-agentshield-dev-local-0001 / sk-lf-agentshield-dev-local-0001"
echo ""
echo "Default resources seeded: 6 tools (5 HTTP + 1 Python), 2 skills, 3 workflows, 5 agents"
echo "Next: bash scripts/smoke-test-cpe2e-studio.sh"
