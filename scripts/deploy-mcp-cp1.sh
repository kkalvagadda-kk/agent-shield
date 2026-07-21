#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP1a — MCP as a Tool Source (Phase 1): deploy registry-api ONLY.
#
# Deploys the registry-api changes for Phases 2-3 (migration 0072 with the six
# mcp_servers columns + tools.pii_deanonymize_allowed, the MCPServer/Tool ORM,
# the shared team_may_use_tool resolver, and the internal MCP authorize-tool-call
# endpoint). The mcp-proxy is NOT part of CP1 (that lands in CP2).
#
# What it does (exactly what CP1a specifies):
#   1. Build the registry-api image at the tag pinned in charts/agentshield/values.yaml
#   2. helm upgrade the chart (tags baked into values.yaml — no --set)
#   3. kubectl rollout status deploy/agentshield-registry-api
#   4. kubectl exec ... alembic upgrade head  (applies 0072)
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys
# every service. This script is the registry-api-scoped CP1 deploy.
set -euo pipefail

echo "=== Checkpoint CP1: deploy registry-api (migration 0072 + internal MCP authz) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Resolve the registry-api image tag from values.yaml (the source of truth) ──
REGISTRY_API_TAG="$(yq '.["registry-api"].image.tag' "$CHART/values.yaml")"
if [[ -z "$REGISTRY_API_TAG" || "$REGISTRY_API_TAG" == "null" ]]; then
  echo "FAIL: could not read registry-api image tag from $CHART/values.yaml" >&2
  exit 1
fi
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
echo "--- registry-api image: ${REGISTRY_API_IMAGE}"

# ── 1. Build the registry-api image at the pinned tag ─────────────────────────
echo "--- [1/4] Building registry-api image ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

# ── 2. helm upgrade (tags baked into values.yaml, no --set) ───────────────────
echo "--- [2/4] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── 3. Wait for the registry-api rollout ──────────────────────────────────────
echo "--- [3/4] Waiting for registry-api rollout ..."
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

# ── 4. Apply alembic migration 0072 (upgrade head) ────────────────────────────
echo "--- [4/4] Applying alembic migrations (upgrade head) ..."
kubectl exec -n "$NAMESPACE" deploy/agentshield-registry-api -c registry-api -- alembic upgrade head

echo ""
echo "Checkpoint CP1 deploy complete. Verify with:"
echo "  bash scripts/smoke-cp1-infra.sh && bash scripts/smoke-cp1-behaviour.sh"
echo "PASS"
