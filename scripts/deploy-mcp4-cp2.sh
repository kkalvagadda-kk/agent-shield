#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2a — MCP Phase 4 (WS-2 registry-api dance + internal token endpoint): deploy
#        registry-api (P4–P6).
#
# Deploys the registry-api half of WS-2, all shipping in the SAME image tag as WS-1
# (registry-api 0.2.229 — the tag is already pinned in scripts/deploy-cpe2e.sh +
# charts/agentshield/values.yaml; this script does NOT re-bump it, it asserts it):
#   - migration 0074 — mcp_servers.external_auth_mode / oauth_client_ref + mcp_oauth_grants
#   - mcp_oauth.py — discovery / DCR / PKCE / code-exchange / refresh-with-rotation / state
#   - routers/mcp_oauth.py — authorize / callback / status / disconnect (registered in main.py)
#   - routers/internal_mcp.py — POST /oauth/access-token (TokenReview'd, subject-pinned)
#   - registry-api ClusterRole `tokenreviews: create` (chart rbac.yaml) — the token endpoint
#     TokenReviews the proxy SA; registry-api never needed this before.
#
# The mcp-proxy is NOT part of CP2 (the proxy OAuth read lands in CP3). The stub OAuth AS
# is the in-pod fixture scripts/e2e/fixtures/oauth_mcp_server.py (started by the CP2
# behaviour smoke inside the registry-api pod).
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys every
# service. This is the registry-api-scoped WS-2 deploy.
set -euo pipefail

echo "=== Checkpoint MCP4-CP2: deploy registry-api (migration 0074 + OAuth dance + token endpoint) ==="

RELEASE="${RELEASE:-agentshield}"
CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"
EXPECTED_REGISTRY_API_TAG="${EXPECTED_REGISTRY_API_TAG:-0.2.229}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Resolve + assert the registry-api tag from values.yaml (source of truth) ───
read_tag() {  # $1 = top-level chart key
  local t
  t="$(yq ".[\"$1\"].image.tag" "$CHART/values.yaml")"
  if [[ -z "$t" || "$t" == "null" ]]; then
    echo "FAIL: could not read $1 image tag from $CHART/values.yaml" >&2
    exit 1
  fi
  printf '%s' "$t"
}
REGISTRY_API_TAG="$(read_tag registry-api)"
if [[ "$REGISTRY_API_TAG" != "$EXPECTED_REGISTRY_API_TAG" ]]; then
  echo "FAIL: registry-api tag in values.yaml is ${REGISTRY_API_TAG}, expected ${EXPECTED_REGISTRY_API_TAG}" >&2
  echo "      (bump REGISTRY_API_TAG in scripts/deploy-cpe2e.sh + registry-api.image.tag in $CHART/values.yaml)." >&2
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

# ── 4. Apply alembic migrations (upgrade head -> 0074) ────────────────────────
echo "--- [4/4] Applying alembic migrations (upgrade head -> 0074) ..."
kubectl exec -n "$NAMESPACE" deploy/agentshield-registry-api -c registry-api -- alembic upgrade head

echo ""
echo "Checkpoint MCP4-CP2 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp4-cp2-infra.sh && bash scripts/smoke-mcp4-cp2-behaviour.sh"
echo "PASS"
