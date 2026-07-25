#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2a — MCP Phase 2 (WS-B list_changed): deploy mcp-proxy + registry-api.
#
# Deploys the two services the list_changed auto-resync slice needs, at the tags
# pinned in charts/agentshield/values.yaml:
#   - mcp-proxy      subscription_manager + connect_and_initialize(message_handler) (P6)
#   - registry-api   POST /internal/mcp/list-changed + _materialize_and_discover extract (P7)
#
# The registry-api image is the SAME one CP1 deploys (P3/P7/P8 share one bump); this
# script re-runs the deploy so CP2 can be exercised standalone. The mcp-proxy image
# MUST build with a REPO-ROOT build context (see CP1a's note).
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP2: deploy mcp-proxy + registry-api (list_changed) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

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

echo "--- [1/4] Building mcp-proxy image (repo-root build context) ..."
docker build -f services/mcp-proxy/Dockerfile -t "$MCP_PROXY_IMAGE" .

echo "--- [2/4] Building registry-api image ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

echo "--- [3/4] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

echo "--- [4/4] Waiting for mcp-proxy + registry-api rollouts ..."
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

echo ""
echo "Checkpoint MCP2-CP2 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp2-cp2-infra.sh && bash scripts/smoke-mcp2-cp2-behaviour.sh"
echo "PASS"
