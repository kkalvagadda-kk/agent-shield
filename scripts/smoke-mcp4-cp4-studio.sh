#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP4b — MCP Phase 4 (WS-2 Studio): the authorize journey (Playwright).
#
# Runs the Studio browser E2E for the MCP OAuth journey against the DEPLOYED Studio
# (real Keycloak login via e2e/global-setup.ts):
#   - register an External+OAuth server → detail page → click Authorize →
#     page.waitForResponse on POST …/oauth/authorize returns an authorization_url
#     (assert the redirect was attempted; upstream consent is NOT followed);
#   - stub the ?oauth=connected callback landing → reload → assert the Connected badge
#     (save → reload → assert survived).
#
# scripts/studio-e2e.sh port-forwards (or uses the https gateway) + runs Playwright.
# First-time setup: `cd studio && npx playwright install chromium`.
# Exit 0 iff the spec is green. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP4: Studio OAuth authorize journey (Playwright) ==="

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SPEC="${SPEC:-e2e/mcp-servers.spec.ts}"
echo "--- running scripts/studio-e2e.sh $SPEC ---"
bash scripts/studio-e2e.sh "$SPEC" || { echo "FAIL: mcp-servers.spec.ts (OAuth journey) not green" >&2; exit 1; }

echo "PASS"
