#!/usr/bin/env bash
# scripts/smoke-test-cp3-e1-behaviour.sh
#
# Eval v2 E-1 Checkpoint 3 — BEHAVIOUR gate (CP3c). This is the acceptance gate for
# E-1: the no-fakes real-durable-eval suite, plus the frontend gates that keep the
# durable UI honest.
#
#   T-CP3C-001 — bash suite-72 (the REAL durable trajectory + tool-call gate)
#                exits 0: a durable agent that answers well but calls the WRONG
#                tools FAILS the composite, proven end-to-end through the real
#                dispatch→pod→callback→judge path with the score read back from
#                the DB (no fakes anywhere in the seam that hid the 11 bugs).
#   T-CP3C-002 — studio typecheck clean (durable types wired, no orphan).
#   T-CP3C-003 — studio Vitest green (DatasetsPage durable editor + EvalResultsPage
#                trajectory/tool-diff render component tests).
#   T-CP3C-004 — Playwright eval-v2-durable.spec.ts green (real durable authoring
#                save→reload + durable evidence render against a real EvalRun).
#
# T-CP3C-004 is a SEPARATE gate (real browser) and is run last; set
# CP3_SKIP_PLAYWRIGHT=1 to skip it in a headless CI without a browser.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0; FAIL=0
ok()   { echo "PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1"; FAIL=$((FAIL+1)); }

echo "=== Eval v2 E-1 CP3c: behaviour gate ==="
echo ""

# ── T-CP3C-001 — the no-fakes durable eval suite ──────────────────────────────
echo "--- T-CP3C-001: bash suite-72 (real durable trajectory + tool-call gate) ---"
if bash scripts/e2e/suite-72-eval-v2-durable.sh; then
  ok "T-CP3C-001 suite-72 real durable eval gate PASSED"
else
  bad "T-CP3C-001 suite-72 real durable eval gate FAILED"
fi

# ── T-CP3C-002 — studio typecheck ─────────────────────────────────────────────
echo ""
echo "--- T-CP3C-002: studio typecheck ---"
if (cd studio && npm run typecheck); then
  ok "T-CP3C-002 studio typecheck clean"
else
  bad "T-CP3C-002 studio typecheck"
fi

# ── T-CP3C-003 — studio Vitest ────────────────────────────────────────────────
echo ""
echo "--- T-CP3C-003: studio Vitest (durable editor + results render) ---"
if (cd studio && npm run test -- --run src/pages/DatasetsPage.test.tsx src/pages/EvalResultsPage.test.tsx); then
  ok "T-CP3C-003 studio Vitest green (durable editor + results render)"
else
  bad "T-CP3C-003 studio Vitest (durable editor + results render)"
fi

# ── T-CP3C-004 — Playwright durable journey ───────────────────────────────────
echo ""
if [ "${CP3_SKIP_PLAYWRIGHT:-0}" = "1" ]; then
  echo "--- T-CP3C-004: Playwright SKIPPED (CP3_SKIP_PLAYWRIGHT=1) ---"
else
  echo "--- T-CP3C-004: Playwright eval-v2-durable.spec.ts (real, no route stub) ---"
  if bash scripts/studio-e2e.sh e2e/eval-v2-durable.spec.ts; then
    ok "T-CP3C-004 Playwright durable journey green"
  else
    bad "T-CP3C-004 Playwright durable journey"
  fi
fi

echo ""
echo "=== CP3c behaviour gate: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "CP3c BEHAVIOUR GATE FAILED"; exit 1; }
echo "CP3c BEHAVIOUR GATE PASSED — E-1 is DONE"
