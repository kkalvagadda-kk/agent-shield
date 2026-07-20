#!/usr/bin/env bash
# CP2 deploy — adds Deploy Controller to the running AgentShield install.
# Assumes CP1 is already deployed and all infra pods are Running.
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="20m"
PG_PASS="DevPass2024"
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:0.2.2"
DEPLOY_CONTROLLER_IMAGE="registry.internal/agentshield/deploy-controller:0.1.1"
ECHO_AGENT_IMAGE="registry.internal/agentshield/echo-agent:0.1.0"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Step 1: Build images ───────────────────────────────────────────────────────
echo "==> Building registry-api image: ${REGISTRY_API_IMAGE} ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

echo "==> Building deploy-controller image: ${DEPLOY_CONTROLLER_IMAGE} ..."
docker build -t "$DEPLOY_CONTROLLER_IMAGE" services/deploy-controller/

echo "==> Building echo-agent image: ${ECHO_AGENT_IMAGE} ..."
docker build -t "$ECHO_AGENT_IMAGE" services/echo-agent/

# ── Step 2: Create agentshield-secrets (idempotent) ───────────────────────────
echo "==> Creating agentshield-secrets in ${NAMESPACE} ..."
kubectl create secret generic agentshield-secrets \
  -n "$NAMESPACE" \
  --from-literal=registry-api-url="http://agentshield-registry-api.agentshield-platform:8000" \
  --from-literal=database-url="postgresql://postgres:${PG_PASS}@${RELEASE}-postgresql:5432/agentshield" \
  --dry-run=client -o yaml | kubectl apply -f -

# ── Step 3: Apply namespace manifests (creates agents-platform if absent) ─────
echo "==> Applying namespace manifests ..."
kubectl apply -f infra/namespaces/agentshield-platform.yaml
kubectl apply -f infra/namespaces/agents-platform.yaml

# ── Step 4: Helm dependency update ───────────────────────────────────────────
echo "==> Updating Helm dependencies ..."
helm dependency update "$CHART"

# ── Step 5: Helm upgrade — enable deploy-controller, keep all else disabled ───
echo "==> Upgrading ${RELEASE} with deploy-controller enabled ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --timeout "$TIMEOUT" \
  \
  --set deploy-controller.enabled=true \
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
  --set redis.master.persistence.size=1Gi

# ── Step 6: Wait for deploy-controller rollout ────────────────────────────────
echo "==> Waiting for deploy-controller rollout ..."
kubectl rollout status deployment/agentshield-deploy-controller \
  -n "$NAMESPACE" \
  --timeout=3m

# Keep Langfuse trace-link SSO working after any deploy (self-skips if langfuse not deployed).
bash "$(dirname "$0")/reconcile-langfuse-hostalias.sh" "$NAMESPACE"

echo ""
echo "Checkpoint 2 deploy complete."
echo "Run scripts/smoke-test-cp2-deploy.sh and scripts/smoke-test-cp2-opa.sh to verify."
