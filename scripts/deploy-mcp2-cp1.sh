#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1a — MCP Phase 2 (WS-A health loop): deploy mcp-proxy + registry-api.
#
# Deploys the two services the health-loop slice needs, at the tags pinned in
# charts/agentshield/values.yaml:
#   - mcp-proxy      POST /internal/health probe route (P2)
#   - registry-api   mcp_health_loop lifespan task + mcp_proxy_client.health_check_server (P3)
#
# IMPORTANT: the mcp-proxy image MUST build with a REPO-ROOT build context — its
# Dockerfile COPYs scripts/e2e/fixtures/stub_mcp_server.py, which lives outside
# services/mcp-proxy/. Hence `docker build -f services/mcp-proxy/Dockerfile .`
# (the trailing `.` = repo root), NOT `docker build services/mcp-proxy/`.
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys every
# service (it must carry the same repo-root mcp-proxy build line). This is the
# CP1-scoped targeted deploy.
set -euo pipefail

echo "=== Checkpoint MCP2-CP1: deploy mcp-proxy + registry-api (health loop) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
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

MCP_PROXY_IMAGE="registry.internal/agentshield/mcp-proxy:${MCP_PROXY_TAG}"
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
echo "--- mcp-proxy:    ${MCP_PROXY_IMAGE}"
echo "--- registry-api: ${REGISTRY_API_IMAGE}"

# ── 1. Build the mcp-proxy image with REPO-ROOT context ───────────────────────
echo "--- [1/4] Building mcp-proxy image (repo-root build context) ..."
docker build -f services/mcp-proxy/Dockerfile -t "$MCP_PROXY_IMAGE" .

# ── 2. Build registry-api at its pinned tag ───────────────────────────────────
echo "--- [2/4] Building registry-api image ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

# ── 3. helm upgrade (tags baked into values.yaml, no --set) ───────────────────
echo "--- [3/4] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── 4. Wait for BOTH rollouts ─────────────────────────────────────────────────
echo "--- [4/4] Waiting for mcp-proxy + registry-api rollouts ..."
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

echo ""
echo "Checkpoint MCP2-CP1 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp2-cp1-infra.sh && bash scripts/smoke-mcp2-cp1-behaviour.sh"
echo "PASS"
