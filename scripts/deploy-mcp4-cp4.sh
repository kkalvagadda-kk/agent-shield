#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP4a — MCP Phase 4 (WS-2 Studio): deploy studio.
#
# Deploys the Studio OAuth journey at the tag pinned in charts/agentshield/values.yaml
# (studio 0.1.163 — already pinned in scripts/deploy-cpe2e.sh + values.yaml; this script
# ASSERTS it, it does NOT re-bump):
#   - mcpServersApi.ts — startMcpOAuth / getMcpOAuthStatus / disconnectMcpOAuth + types
#   - McpServerDetailPage — OAuth Connection panel (Authorize / Connected + Disconnect) +
#     ?oauth= callback landing (toast + query invalidate + strip param)
#   - McpServersPage — register OAuth toggle (External only, hides the credential picker)
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh`. This is the studio-scoped deploy.
set -euo pipefail

echo "=== Checkpoint MCP4-CP4: deploy studio (OAuth Connection panel + register toggle) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"
EXPECTED_STUDIO_TAG="${EXPECTED_STUDIO_TAG:-0.1.163}"

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
STUDIO_TAG="$(read_tag studio)"
[[ "$STUDIO_TAG" == "$EXPECTED_STUDIO_TAG" ]] \
  || { echo "FAIL: studio tag is ${STUDIO_TAG}, expected ${EXPECTED_STUDIO_TAG} (bump deploy-cpe2e.sh + values.yaml)" >&2; exit 1; }
STUDIO_IMAGE="registry.internal/agentshield/studio:${STUDIO_TAG}"
echo "--- studio image: ${STUDIO_IMAGE}"

echo "--- [1/3] Building studio image ..."
docker build -t "$STUDIO_IMAGE" studio/

echo "--- [2/3] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

echo "--- [3/3] Waiting for studio rollout ..."
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout="$TIMEOUT"

echo ""
echo "Checkpoint MCP4-CP4 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp4-cp4-studio.sh"
echo "PASS"
