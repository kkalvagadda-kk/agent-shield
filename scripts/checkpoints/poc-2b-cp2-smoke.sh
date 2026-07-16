#!/usr/bin/env bash
# scripts/checkpoints/poc-2b-cp2-smoke.sh
#
# POC-2b Checkpoint 2 (frontend) smoke — run AFTER `bash scripts/deploy-eks.sh`
# has rolled out studio 0.1.143 serving the live CatalogChatPage console
# (user-gated; shared-cluster hazard, No-Merge-to-Main).
#
# Gate (tasks.md CP2b):
#   1. `cd studio && npm run typecheck` (tsc --noEmit) clean.
#   2. `npm run test` (Vitest component + reducer suites) 100% green.
#   3. `bash scripts/studio-e2e.sh e2e/poc2b-rich-console.spec.ts` — the Playwright
#      rich-console spec proves the real browser journey (progressive reveal +
#      avatars + tool chip + rationale toggle + save→reload) against the deployed
#      Studio over the https gateway. Playwright exits 0 on PASS or a capacity
#      test.skip; a rendered-but-wrong run exits non-zero and fails this gate.
#
# Each step gates via `set -e` — the first non-zero exit stops the script.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
echo "=== POC-2b CP2 frontend smoke ==="

echo ""
echo "--- Step 1: TypeScript typecheck (studio src) ---"
( cd "$REPO_ROOT/studio" && npm run typecheck )

echo ""
echo "--- Step 2: Vitest component + reducer suites ---"
( cd "$REPO_ROOT/studio" && npm run test )

echo ""
echo "--- Step 3: Playwright rich-console spec (deployed Studio) ---"
bash "$REPO_ROOT/scripts/studio-e2e.sh" e2e/poc2b-rich-console.spec.ts

echo ""
echo "=== POC-2b CP2 frontend smoke: PASS ==="
