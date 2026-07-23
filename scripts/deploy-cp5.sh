#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# Requires a live cluster.
# =============================================================================
# CP5a — MCP as a Tool Source (Phase 1): deploy the Studio UI (Phases 12-14).
#
# Deploys studio at the tag pinned in charts/agentshield/values.yaml (0.1.161):
#   - MCP Servers list + register modal + detail (discovered-tools / settings)
#   - Sidebar "MCP Servers" (Settings) + /mcp-servers[/:id] routes
#   - ToolsPage read-only mcp_tool rows + PII de-anon checkbox
#   - ToolsPicker source-server badge
#
# Full-stack alternative: `bash scripts/deploy-cpe2e.sh` rebuilds + redeploys
# every service. This script is the CP5-scoped targeted deploy.
set -euo pipefail

echo "=== Checkpoint CP5: deploy studio (MCP tool-source UI) ==="

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
    echo "ERROR: could not read image tag for '$1' from $CHART/values.yaml" >&2
    exit 1
  fi
  echo "$t"
}

STUDIO_TAG="$(read_tag studio)"
echo "--- studio tag from values.yaml: $STUDIO_TAG ---"

# ── Build + push the studio image (skip with SKIP_BUILD=1 to helm-only) ───────
if [[ "${SKIP_BUILD:-0}" != "1" ]]; then
  echo "--- docker build studio:$STUDIO_TAG ---"
  docker build -t "registry.internal/agentshield/studio:$STUDIO_TAG" studio/
  docker push "registry.internal/agentshield/studio:$STUDIO_TAG" 2>/dev/null || true
fi

# ── helm upgrade (tags baked into values.yaml — no --set) ─────────────────────
echo "--- helm upgrade $RELEASE ---"
helm upgrade --install "$RELEASE" "$CHART" -n "$NAMESPACE" --wait --timeout "$TIMEOUT"

echo "--- rollout status deploy/agentshield-studio ---"
kubectl rollout status deploy/agentshield-studio -n "$NAMESPACE" --timeout=180s

echo "PASS"
