#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster (Playwright) + a Node toolchain (Vitest).
# =============================================================================
# CP5c — MCP Phase 4 (full Studio validation): Vitest + Playwright smoke.
#
# The frontend half of the Phase-4 gate:
#   1. Vitest (component tests) — McpServerDetailPage / McpServersPage OAuth cases must be
#      green (`cd studio && npm run test`).
#   2. Playwright — the MCP OAuth browser journey against the deployed Studio
#      (`bash scripts/studio-e2e.sh e2e/mcp-servers.spec.ts`): register External+OAuth →
#      Authorize (waitForResponse on the authorize POST) → Connected-after-reload.
#
# Exit 0 iff BOTH are green. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP5: Studio Vitest + Playwright (MCP OAuth) ==="

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SPEC="${SPEC:-e2e/mcp-servers.spec.ts}"

# ── 1. Vitest component tests ─────────────────────────────────────────────────
echo "--- [1/2] Studio Vitest (npm run test) ---"
( cd studio && npm run test ) || { echo "FAIL: Studio Vitest not green" >&2; exit 1; }

# ── 2. Playwright browser E2E (MCP OAuth journey) ─────────────────────────────
echo "--- [2/2] Playwright $SPEC (bash scripts/studio-e2e.sh) ---"
bash scripts/studio-e2e.sh "$SPEC" || { echo "FAIL: mcp-servers.spec.ts (OAuth journey) not green" >&2; exit 1; }

echo "PASS"
