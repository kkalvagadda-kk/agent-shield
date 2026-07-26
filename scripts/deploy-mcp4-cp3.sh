#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP3a — MCP Phase 4 (WS-2 proxy token-read): deploy mcp-proxy + registry-api.
#
# Deploys the proxy half of WS-2 at the tags pinned in charts/agentshield/values.yaml
# (mcp-proxy 0.1.4, registry-api 0.2.229 — already pinned in scripts/deploy-cpe2e.sh +
# values.yaml; this script ASSERTS them, it does NOT re-bump):
#   - mcp-proxy oauth_tokens.py — pulls a fresh access token from registry-api's internal
#     endpoint using its projected agentshield-registry-api SA token; identity.resolve_headers
#     first-checked OAuth branch (fail-closed); main.py tools_call OAuth arms + evict-retry.
#   - chart mcp-proxy/deployment.yaml — the projected SA-token volume (audience
#     agentshield-registry-api, mounted at /var/run/secrets/registry-api/token) + the three
#     OAuth env vars.
#   - registry-api redeployed (the token endpoint the proxy calls; same 0.2.229 image).
#
# The mcp-proxy image MUST build with a REPO-ROOT build context (its Dockerfile COPYs
# scripts/e2e/fixtures/ — see CP1a's note): `docker build -f services/mcp-proxy/Dockerfile .`.
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP3: deploy mcp-proxy + registry-api (proxy OAuth token-read) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"
EXPECTED_MCP_PROXY_TAG="${EXPECTED_MCP_PROXY_TAG:-0.1.4}"
EXPECTED_REGISTRY_API_TAG="${EXPECTED_REGISTRY_API_TAG:-0.2.229}"

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
[[ "$MCP_PROXY_TAG" == "$EXPECTED_MCP_PROXY_TAG" ]] \
  || { echo "FAIL: mcp-proxy tag is ${MCP_PROXY_TAG}, expected ${EXPECTED_MCP_PROXY_TAG} (bump deploy-cpe2e.sh + values.yaml)" >&2; exit 1; }
[[ "$REGISTRY_API_TAG" == "$EXPECTED_REGISTRY_API_TAG" ]] \
  || { echo "FAIL: registry-api tag is ${REGISTRY_API_TAG}, expected ${EXPECTED_REGISTRY_API_TAG}" >&2; exit 1; }

MCP_PROXY_IMAGE="registry.internal/agentshield/mcp-proxy:${MCP_PROXY_TAG}"
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
echo "--- mcp-proxy:    ${MCP_PROXY_IMAGE}"
echo "--- registry-api: ${REGISTRY_API_IMAGE}"

# ── 1. Build mcp-proxy (REPO-ROOT context) + registry-api ─────────────────────
echo "--- [1/4] Building mcp-proxy image (repo-root build context) ..."
docker build -f services/mcp-proxy/Dockerfile -t "$MCP_PROXY_IMAGE" .
echo "--- [2/4] Building registry-api image ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/

# ── 2. helm upgrade (tags baked into values.yaml, no --set) ───────────────────
echo "--- [3/4] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

# ── 3. Wait for BOTH rollouts ─────────────────────────────────────────────────
echo "--- [4/4] Waiting for mcp-proxy + registry-api rollouts ..."
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

echo ""
echo "Checkpoint MCP4-CP3 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp4-cp3-infra.sh && bash scripts/smoke-mcp4-cp3-behaviour.sh"
echo "PASS"
