#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP3a — MCP Phase 2 (WS-C internal identity): deploy mcp-proxy + registry-api +
#        declarative-runner.
#
# Deploys the identity slice at the tags pinned in charts/agentshield/values.yaml:
#   - mcp-proxy          identity.resolve_headers + keycloak_client mint + Keycloak
#                        client-secret file mount + egress NetworkPolicy (P9/P10)
#   - registry-api       mcp_secrets carries identity_mode/identity_audience (P8 —
#                        already in the CP1/CP2 image; redeployed for standalone CP3)
#   - declarative-runner McpToolNodeExecutor x-user-sub emission (P10) + rebuilt sdk 0.2.4
#
# PREREQUISITE (quickstart.md "Keycloak client"): a Keycloak CONFIDENTIAL client
# (client-credentials/service-account grant) named per mcp-proxy.keycloak.clientId must
# exist, and its secret must be in the chart (mcp-proxy.keycloak.clientSecret or an
# existingSecret). Without it, service_identity minting fails (a 200 identity-error
# body, never a 5xx) — CP3 assertions that need a minted token will not pass.
#
# The mcp-proxy image MUST build with a REPO-ROOT build context (see CP1a's note).
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP3: deploy mcp-proxy + registry-api + declarative-runner (identity) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
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
DECLARATIVE_RUNNER_TAG="$(read_tag declarative-runner)"

MCP_PROXY_IMAGE="registry.internal/agentshield/mcp-proxy:${MCP_PROXY_TAG}"
REGISTRY_API_IMAGE="registry.internal/agentshield/registry-api:${REGISTRY_API_TAG}"
DECLARATIVE_RUNNER_IMAGE="registry.internal/agentshield/declarative-runner:${DECLARATIVE_RUNNER_TAG}"
echo "--- mcp-proxy:          ${MCP_PROXY_IMAGE}"
echo "--- registry-api:       ${REGISTRY_API_IMAGE}"
echo "--- declarative-runner: ${DECLARATIVE_RUNNER_IMAGE}"

echo "--- [1/5] Building mcp-proxy image (repo-root build context) ..."
docker build -f services/mcp-proxy/Dockerfile -t "$MCP_PROXY_IMAGE" .

echo "--- [2/5] Building registry-api + declarative-runner images ..."
docker build -t "$REGISTRY_API_IMAGE" services/registry-api/
docker build -t "$DECLARATIVE_RUNNER_IMAGE" services/declarative-runner/

echo "--- [3/5] helm upgrade ${RELEASE} ..."
helm upgrade --install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" \
  --create-namespace \
  --reset-values \
  --timeout "$TIMEOUT"

echo "--- [4/5] Waiting for mcp-proxy + registry-api rollouts ..."
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"

# ── 5. Assert the Keycloak client Secret is mounted (WS-C prerequisite) ────────
echo "--- [5/5] Asserting the mcp-proxy Keycloak client Secret exists ..."
if ! kubectl get secret "${RELEASE}-mcp-proxy-keycloak" -n "$NAMESPACE" >/dev/null 2>&1; then
  echo "WARN: secret ${RELEASE}-mcp-proxy-keycloak not found in ${NAMESPACE} — service_identity minting" >&2
  echo "      will fail until the Keycloak confidential client + secret are provisioned (quickstart.md)." >&2
fi

echo ""
echo "Checkpoint MCP2-CP3 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp2-cp3-infra.sh && bash scripts/smoke-mcp2-cp3-behaviour.sh"
echo "PASS"
