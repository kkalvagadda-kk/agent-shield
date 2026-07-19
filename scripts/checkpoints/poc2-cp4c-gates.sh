#!/usr/bin/env bash
set -euo pipefail
# CP4c — final gates: no-orphan, typecheck, vitest, Playwright browser gate.
echo "=== Checkpoint 4c: POC-2 final gates ==="
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"; cd "$ROOT"
echo "--- no-orphan (every new symbol has a live caller) ---"
bash scripts/checkpoints/poc2-cp2b-no-orphans.sh >/dev/null && echo "frontend symbols: no orphans"
grep -q '"author"' services/registry-api/routers/chat.py || { echo "FAIL: chat.py author frame missing"; exit 1; }
grep -q 'memory_enabled' studio/src/pages/WorkflowBuilderPage.tsx || { echo "FAIL: toggle wiring missing"; exit 1; }
echo "--- typecheck + vitest ---"
( cd studio && npm run typecheck && npm run test )
echo "--- browser Playwright gate (deployed studio) ---"
bash scripts/studio-e2e.sh
echo "PASS"
