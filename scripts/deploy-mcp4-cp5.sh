#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP5a — MCP Phase 4 (full Phase-4 e2e + regression): deploy ALL Phase-4 services.
#
# Full-platform deploy at the tags pinned in charts/agentshield/values.yaml
# (registry-api 0.2.229, mcp-proxy 0.1.4, studio 0.1.163 — this script ASSERTS them, it
# does NOT re-bump). Uses the canonical build+deploy path (`scripts/deploy-cpe2e.sh`),
# which builds every image (mcp-proxy with a REPO-ROOT context), applies secrets, runs
# `helm upgrade`, waits for rollouts, and seeds — then waits for the three Phase-4 rollouts.
set -euo pipefail

echo "=== Checkpoint MCP4-CP5: full-platform deploy (registry-api + mcp-proxy + studio) ==="

CHART="${CHART:-charts/agentshield}"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TIMEOUT="${TIMEOUT:-10m}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Assert the Phase-4 tags are pinned before a full build ────────────────────
read_tag() {  # $1 = top-level chart key
  local t
  t="$(yq ".[\"$1\"].image.tag" "$CHART/values.yaml")"
  if [[ -z "$t" || "$t" == "null" ]]; then
    echo "FAIL: could not read $1 image tag from $CHART/values.yaml" >&2
    exit 1
  fi
  printf '%s' "$t"
}
assert_tag() {  # $1 = key, $2 = expected
  local got; got="$(read_tag "$1")"
  [[ "$got" == "$2" ]] || { echo "FAIL: $1 tag is ${got}, expected ${2}" >&2; exit 1; }
  echo "--- $1: ${got}"
}
assert_tag registry-api "${EXPECTED_REGISTRY_API_TAG:-0.2.229}"
assert_tag mcp-proxy   "${EXPECTED_MCP_PROXY_TAG:-0.1.4}"
assert_tag studio      "${EXPECTED_STUDIO_TAG:-0.1.163}"

# ── Full build + deploy via the canonical script ──────────────────────────────
echo "--- [1/2] bash scripts/deploy-cpe2e.sh (build + secrets + helm + rollout + seed) ..."
bash scripts/deploy-cpe2e.sh

# ── Belt-and-suspenders: wait for the three Phase-4 rollouts ───────────────────
echo "--- [2/2] Waiting for registry-api + mcp-proxy + studio rollouts ..."
kubectl rollout status deployment/agentshield-registry-api -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-mcp-proxy -n "$NAMESPACE" --timeout="$TIMEOUT"
kubectl rollout status deployment/agentshield-studio -n "$NAMESPACE" --timeout="$TIMEOUT"

echo ""
echo "Checkpoint MCP4-CP5 deploy complete. Verify with:"
echo "  bash scripts/smoke-mcp4-cp5-suites.sh && bash scripts/smoke-mcp4-cp5-studio.sh"
echo "PASS"
