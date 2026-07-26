#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP5b — MCP Phase 4 (full backend e2e + regression): suite smoke.
#
# Runs the two new Phase-4 backend suites AND the impacted regression neighbours (the
# shared credential path — WS-1 rewired every AuthConfig write + every MCP server
# materialization, so a break there would surface in suite-84/85 + suite-81):
#   - suite-86 (CredentialProvider seam: put/get/rotate/delete, byte-identity, dual-read)
#   - suite-87 (external OAuth: register→authorize→callback→grant, token endpoint, rotation)
#   - suite-84 (MCP Phase-1 register/discover/authorize/bundle) — must stay green
#   - suite-85 (MCP Phase-2 health/list_changed/identity) — must stay green
#   - suite-81 (deploy-time tool-access auto-grant) — an AuthConfig-consuming path
#
# Each suite is exit-keyed (0 == green). Any red suite fails the checkpoint.
# Exit 0 iff ALL are green. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP5: backend suites (86 + 87) + regression (84 + 85 + 81) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SUITES=(
  "scripts/e2e/suite-86-credential-provider.sh"
  "scripts/e2e/suite-87-mcp-oauth.sh"
  "scripts/e2e/suite-84-mcp-tools.sh"
  "scripts/e2e/suite-85-mcp-phase2.sh"
  "scripts/e2e/suite-81-deploy-tool-autograt.sh"
)

FAILED=()
for suite in "${SUITES[@]}"; do
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $suite"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  if [ ! -f "$suite" ]; then
    echo "FAIL: $suite not found" >&2
    FAILED+=("$suite (missing)")
    continue
  fi
  if NAMESPACE="$NAMESPACE" bash "$suite"; then
    echo "  → GREEN"
  else
    echo "  → RED" >&2
    FAILED+=("$suite")
  fi
done

echo ""
if [ ${#FAILED[@]} -eq 0 ]; then
  echo "All Phase-4 + regression suites green."
  echo "PASS"
else
  echo "FAIL: red suites:" >&2
  for s in "${FAILED[@]}"; do echo "  - $s" >&2; done
  exit 1
fi
