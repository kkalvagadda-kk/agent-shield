#!/usr/bin/env bash
# deploy-cpe2e.sh — Checkpoint E2E deploy (fresh cluster or after restart)
#
# Creates all required secrets, builds Phase-8 images, and deploys the full
# AgentShield stack needed for the E2E checkpoint:
#   - registry-api (0.2.6)
#   - deploy-controller (0.1.2 — Phase 8 declarative runner support)
#   - studio (0.1.6 — Phase 8 canvas)
#   - PostgreSQL, Redis, Keycloak, MinIO (infra)
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
REGISTRY_API_TAG="0.2.8"
DEPLOY_CONTROLLER_TAG="0.1.3"
STUDIO_TAG="0.1.7"
DECLARATIVE_RUNNER_TAG="0.1.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> AgentShield CPE2E Deploy — $(date)"
echo ""

# ── Step 1: Build images ──────────────────────────────────────────────────────
echo "[1/6] Building images..."
echo "  → registry-api:${REGISTRY_API_TAG} (canvas redesign — skills API + migration 0004)"
docker build -t "registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}" services/registry-api/

echo "  → deploy-controller:${DEPLOY_CONTROLLER_TAG} (manifest_builder REGISTRY_API_URL injection)"
docker build -t "registry.internal/agentshield/deploy-controller:${DEPLOY_CONTROLLER_TAG}" services/deploy-controller/

echo "  → declarative-runner:${DECLARATIVE_RUNNER_TAG} (conditional routing + tool/skill resolution)"
docker build -t "registry.internal/agentshield/declarative-runner:${DECLARATIVE_RUNNER_TAG}" services/declarative-runner/

echo "  → studio:${STUDIO_TAG} (canvas redesign — agent-only canvas, tool/skill selectors)"
docker build -t "registry.internal/agentshield/studio:${STUDIO_TAG}" studio/

# ── Step 2: Namespaces ────────────────────────────────────────────────────────
echo ""
echo "[2/6] Applying namespaces..."
kubectl apply -f infra/namespaces/agentshield-platform.yaml
kubectl apply -f infra/namespaces/agents-platform.yaml

# ── Step 3: Secrets (all required by chart templates) ─────────────────────────
echo ""
echo "[3/6] Creating secrets..."

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

# Langfuse tracing keys — public-key AND secret-key both required
kubectl create secret generic langfuse-api-keys \
  -n "$NAMESPACE" \
  --from-literal=public-key="pk-lf-placeholder-dev-00000000" \
  --from-literal=secret-key="sk-lf-placeholder-dev-00000000" \
  --from-literal=nextauth-secret="cp1-placeholder-nextauth-32chars!!" \
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
echo "[4/6] Updating Helm dependencies..."
helm dependency update "$CHART" 2>/dev/null || true

# ── Step 5: Helm upgrade ──────────────────────────────────────────────────────
echo ""
echo "[5/6] Helm upgrade/install '${RELEASE}'..."

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
  \
  --set deploy-controller.enabled=true \
  --set studio.enabled=true \
  --set safety-orchestrator.enabled=false \
  --set llm-guard.enabled=false \
  --set presidio.enabled=false \
  --set nemo.enabled=false \
  --set portkey.enabled=false \
  --set opa.enabled=false \
  --set langfuse.enabled=false \
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
echo "[6/6] Waiting for rollouts..."
kubectl rollout status statefulset/agentshield-postgresql -n "$NAMESPACE" --timeout=5m
kubectl rollout status statefulset/agentshield-redis-master -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout=5m
kubectl rollout status deployment/agentshield-deploy-controller -n "$NAMESPACE" --timeout=3m
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout=3m

echo ""
echo "================================================================"
echo "  AgentShield CPE2E Deploy — COMPLETE"
echo "================================================================"
echo ""
kubectl get pods -n "$NAMESPACE" --no-headers | sort
echo ""
echo "Port-forward commands:"
echo "  Registry API:  kubectl port-forward svc/agentshield-registry-api -n ${NAMESPACE} 8000:8000"
echo "  Studio:        kubectl port-forward svc/agentshield-studio       -n ${NAMESPACE} 5173:80"
echo ""
echo "Next: bash scripts/smoke-test-cpe2e-studio.sh"
