#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP2a — MCP as a Tool Source (Phase 1): deploy the register->discover MVP.
#
# Builds + deploys the three services the MVP slice needs, at the tags pinned in
# charts/agentshield/values.yaml:
#   - mcp-proxy         (NEW — 0.1.0)   the centralized MCP wire client
#   - registry-api                       mcp_servers router + proxy client
#   - deploy-controller                  agent pods project the 2nd SA token
#
# IMPORTANT: the mcp-proxy image MUST build with a REPO-ROOT build context —
# its Dockerfile COPYs scripts/e2e/fixtures/stub_mcp_server.py, which lives
# outside services/mcp-proxy/. Hence `docker build -f services/mcp-proxy/Dockerfile .`
# (the trailing `.` = repo root), NOT `docker build services/mcp-proxy/`.
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys
# every service (it must carry the same repo-root mcp-proxy build line). This
# script is the CP2-scoped targeted deploy (CP2a's "helm upgrade with the new
# tags" option) so it is correct regardless of the deploy-cpe2e.sh build line.
set -euo pipefail

echo "=== Checkpoint CP2: deploy mcp-proxy + registry-api + deploy-controller ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
TIMEOUT="${TIMEOUT:-10m}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Resolve image tags from values.yaml (the source of truth) ─────────────────
read_tag() {  # $1 = top-level chart key
  local t
  t="$(yq ".[\"$1\"].image.tag" "$CHART/values.yaml")"
  if [[ -z "$t" || "$t" == "null" ]]; then
    echo "FAIL: could not read $1 image tag from $CHART/values.yaml" >&2
    exit 1
  fi
  printf '%s' "$t"
}
MCP_PROXY_TAG="$(read_tag mcp-proxy)"
REGISTRY_API_TAG="$(read_tag registry-api)"
DEPLOY_CONTROLLER_TAG="$(read_tag deploy-controller)"

MCP_PROXY_IMAGE="registry.internal/agentshield/mcp-proxy:${MCP_PROXY_TAG}"
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
DEPLOY_CONTROLLER_IMAGE="registry.internal/agentshield/deploy-controller:${DEPLOY_CONTROLLER_TAG}"
echo "--- mcp-proxy:         ${MCP_PROXY_IMAGE}"
echo "--- registry-api:      ${REGISTRY_API_IMAGE}"
echo "--- deploy-controller: ${DEPLOY_CONTROLLER_IMAGE}"

# ── 1. Build the mcp-proxy image with REPO-ROOT context ───────────────────────
echo "--- [1/5] Building mcp-proxy image (repo-root build context) ..."
docker build -f services/mcp-proxy/Dockerfile -t "$MCP_PROXY_IMAGE" .

# ── 2. Build registry-api + deploy-controller at their pinned tags ────────────
echo "--- [2/5] Building registry-api + deploy-controller images ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/
docker build -t "$DEPLOY_CONTROLLER_IMAGE" services/deploy-controller/

# ── 3. helm upgrade (tags baked into values.yaml, no --set; creates the ───────
#       mcp-proxy sub-chart's agentshield-mcp namespace + least-priv RBAC) ─────
echo "--- [3/5] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── 4. Wait for the mcp-proxy + registry-api rollouts ─────────────────────────
echo "--- [4/5] Waiting for mcp-proxy + registry-api rollouts ..."
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

# ── 5. Assert the dedicated per-server-secret namespace exists ────────────────
echo "--- [5/5] Asserting namespace ${MCP_NS} exists ..."
kubectl get ns "$MCP_NS" >/dev/null 2>&1 || { echo "FAIL: namespace ${MCP_NS} not found" >&2; exit 1; }
echo "  OK: namespace ${MCP_NS} present"

echo ""
echo "Checkpoint CP2 deploy complete. Verify with:"
echo "  bash scripts/smoke-cp2-infra.sh && bash scripts/smoke-cp2-behaviour.sh"
echo "PASS"
