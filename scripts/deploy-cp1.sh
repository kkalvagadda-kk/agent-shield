#!/usr/bin/env bash
# CP1 bootstrap + deploy — runs idempotently (safe to re-run).
# Creates namespace, secrets, builds local images, then installs the AgentShield
# Helm chart with infra + registry-api only (future-phase services disabled).
#
# Bitnami sub-charts for Keycloak, MinIO, and PgBouncer are DISABLED here because
# their image tags (2025-dated) are no longer on Docker Hub. Raw replacements using
# official images are in charts/agentshield/templates/keycloak-raw.yaml and
# charts/agentshield/templates/minio-raw.yaml.
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="15m"

# Local dev credentials — rotate before any non-local environment.
PG_PASS="DevPass2024"
REDIS_PASS="RedisPass2024"
MINIO_USER="agentshield-admin"
MINIO_PASS="MinioPass2024"
KC_ADMIN_PASS="AdminPass2024"
KC_PLATFORM_ADMIN_PASS="PlatformAdmin2024"
KC_REVIEWER_PASS="Reviewer2024"

REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:0.1.0"
MINIO_IMAGE="registry.internal/agentshield/minio-cp1:0.1.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Step 1: Build local images ────────────────────────────────────────────────
echo "==> Building registry-api image: ${REGISTRY_API_IMAGE} ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

echo "==> Building minio-cp1 image (adds mc client to official minio): ${MINIO_IMAGE} ..."
docker build -t "$MINIO_IMAGE" services/minio-cp1/

# ── Step 2: Apply platform namespace ─────────────────────────────────────────
echo "==> Applying namespace ${NAMESPACE} ..."
kubectl apply -f infra/namespaces/agentshield-platform.yaml

# ── Step 3: Create secrets (idempotent via --dry-run=client | apply) ─────────
echo "==> Creating secrets in ${NAMESPACE} ..."

# Registry-api uses direct PostgreSQL connection (no PgBouncer in CP1).
# agentshield_user password is set equal to the postgres superuser password
# by the 03_set_passwords.sh initdb script.
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

kubectl create secret generic redis-password \
  -n "$NAMESPACE" \
  --from-literal=redis-password="${REDIS_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic minio-credentials \
  -n "$NAMESPACE" \
  --from-literal=root-user="${MINIO_USER}" \
  --from-literal=root-password="${MINIO_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic keycloak-admin-password \
  -n "$NAMESPACE" \
  --from-literal=admin-password="${KC_ADMIN_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic keycloak-user-passwords \
  -n "$NAMESPACE" \
  --from-literal=platform-admin="${KC_PLATFORM_ADMIN_PASS}" \
  --from-literal=agent-reviewer="${KC_REVIEWER_PASS}" \
  --dry-run=client -o yaml | kubectl apply -f -

# Langfuse and Slack are disabled in CP1; placeholders keep the registry-api
# deployment template from failing on missing secretKeyRef.
kubectl create secret generic langfuse-api-keys \
  -n "$NAMESPACE" \
  --from-literal=public-key="cp1-placeholder-public-key" \
  --from-literal=secret-key="cp1-placeholder-secret-key" \
  --from-literal=salt="cp1-placeholder-salt-minimum-32-chars!!" \
  --from-literal=nextauth-secret="cp1-placeholder-nextauth-32chars!!" \
  --dry-run=client -o yaml | kubectl apply -f -

kubectl create secret generic slack-credentials \
  -n "$NAMESPACE" \
  --from-literal=webhook-url="http://localhost:12345/disabled-in-cp1" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── Step 4: Helm dependency update ───────────────────────────────────────────
echo "==> Updating Helm dependencies ..."
helm dependency update "$CHART"

# ── Step 5: Helm install ──────────────────────────────────────────────────────
echo "==> Installing ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --timeout "$TIMEOUT" \
  \
  --set deploy-controller.enabled=false \
  --set safety-orchestrator.enabled=false \
  --set llm-guard.enabled=false \
  --set presidio.enabled=false \
  --set nemo.enabled=false \
  --set portkey.enabled=false \
  --set opa.enabled=false \
  --set studio.enabled=false \
  --set langfuse.enabled=false \
  --set clickhouse.enabled=false \
  --set appsmith.enabled=false \
  \
  --set keycloak.enabled=false \
  --set minio.enabled=false \
  --set pgbouncer.enabled=false \
  \
  --set global.postgresHost="${RELEASE}-postgresql" \
  \
  --set postgresql.readReplicas.replicaCount=0 \
  --set postgresql.primary.replication.synchronousCommit=off \
  --set postgresql.primary.replication.numSynchronousReplicas=0 \
  --set postgresql.primary.persistence.size=5Gi \
  --set redis.master.persistence.size=1Gi \
  \
  --wait

# ── Step 6: Wait for all pods ────────────────────────────────────────────────
echo "==> Waiting for all pods in ${NAMESPACE} to be Ready ..."
kubectl wait pods \
  --namespace "$NAMESPACE" \
  --all \
  --for=condition=Ready \
  --timeout="$TIMEOUT"

echo "==> Deployed pods:"
kubectl get pods -n "$NAMESPACE"

echo ""
echo "Checkpoint 1 deploy complete."
echo "Run scripts/smoke-test-cp1-infra.sh and scripts/smoke-test-cp1-registry.sh to verify."
