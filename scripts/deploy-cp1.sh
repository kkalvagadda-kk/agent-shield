#!/usr/bin/env bash
set -euo pipefail

RELEASE="agentshield"
CHART="charts/agentshield"
NAMESPACE="agentshield-platform"
TIMEOUT="10m"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

echo "==> Updating Helm dependencies..."
helm dependency update "$CHART"

echo "==> Installing $RELEASE (registry-api only; all other app services disabled)..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --timeout "$TIMEOUT" \
  --set deploy-controller.enabled=false \
  --set safety-orchestrator.enabled=false \
  --set llm-guard.enabled=false \
  --set presidio.enabled=false \
  --set nemo.enabled=false \
  --set portkey.enabled=false \
  --set opa.enabled=false \
  --set studio.enabled=false \
  --wait

echo "==> Waiting for all pods in $NAMESPACE to be Ready..."
kubectl wait pods \
  --namespace "$NAMESPACE" \
  --all \
  --for=condition=Ready \
  --timeout="$TIMEOUT"

echo "==> Deployed pods:"
kubectl get pods -n "$NAMESPACE"

echo ""
echo "Checkpoint 1 deploy complete."
