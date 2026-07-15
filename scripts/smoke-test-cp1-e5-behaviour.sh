#!/usr/bin/env bash
# scripts/smoke-test-cp1-e5-behaviour.sh
#
# Eval v2 E-5 Checkpoint 1 — BEHAVIOUR gate. The acceptance gate for E-5: the
# no-fakes real-workflow-run-tree eval suite, plus the frontend gates that keep the
# workflow eval UI honest.
#
#   T-CP1E5B-001 — bash suite-73 (the REAL workflow run-tree / member-path gate)
#                  exits 0: real member agents + a real CompositeWorkflow → real
#                  pods → a real EvalRun → the real eval-runner Job → a real durable
#                  workflow run → the real run tree → the real judge → the member
#                  path scored, a wrong route scored <1.0, all read back from the DB
#                  (no fakes anywhere in the tree/judge seam).
#   T-CP1E5B-002 — studio typecheck clean (workflow types wired, no orphan).
#   T-CP1E5B-003 — studio Vitest green (DatasetsPage workflow editor + EvalResultsPage
#                  member-path / per-member render component tests).
#   T-CP1E5B-004 — Playwright eval-v2-workflow.spec.ts green (real workflow dataset
#                  authoring save→reload + run-tree evidence render against a real
#                  EvalRun).
#
# T-CP1E5B-004 is a SEPARATE gate (real browser) and is run last; set
# E5_SKIP_PLAYWRIGHT=1 to skip it in a headless CI without a browser.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PASS=0; FAIL=0
ok()  { echo "PASS  $1"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }

echo "=== Eval v2 E-5 CP1: behaviour gate ==="
echo ""

# ── T-CP1E5B-001 — the no-fakes workflow run-tree eval suite ───────────────────
echo "--- T-CP1E5B-001: bash suite-73 (real workflow run-tree / member-path gate) ---"
if bash scripts/e2e/suite-73-eval-v2-workflow.sh; then
  ok "T-CP1E5B-001 suite-73 real workflow run-tree eval gate PASSED"
else
  bad "T-CP1E5B-001 suite-73 real workflow run-tree eval gate FAILED"
fi

# ── T-CP1E5B-002 — studio typecheck ───────────────────────────────────────────
echo ""
echo "--- T-CP1E5B-002: studio typecheck ---"
if (cd studio && npm run typecheck); then
  ok "T-CP1E5B-002 studio typecheck clean"
else
  bad "T-CP1E5B-002 studio typecheck"
fi

# ── T-CP1E5B-003 — studio Vitest ──────────────────────────────────────────────
echo ""
echo "--- T-CP1E5B-003: studio Vitest (workflow editor + member-path render) ---"
if (cd studio && npm run test -- --run src/pages/DatasetsPage.test.tsx src/pages/EvalResultsPage.test.tsx); then
  ok "T-CP1E5B-003 studio Vitest green (workflow editor + member-path render)"
else
  bad "T-CP1E5B-003 studio Vitest (workflow editor + member-path render)"
fi

# ── T-CP1E5B-004 — Playwright workflow journey ────────────────────────────────
echo ""
if [ "${E5_SKIP_PLAYWRIGHT:-0}" = "1" ]; then
  echo "--- T-CP1E5B-004: Playwright SKIPPED (E5_SKIP_PLAYWRIGHT=1) ---"
else
  echo "--- T-CP1E5B-004: Playwright eval-v2-workflow.spec.ts (real, no route stub) ---"
  if bash scripts/studio-e2e.sh e2e/eval-v2-workflow.spec.ts; then
    ok "T-CP1E5B-004 Playwright workflow journey green"
  else
    bad "T-CP1E5B-004 Playwright workflow journey"
  fi
fi

echo ""
echo "=== CP1 E-5 behaviour gate: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || { echo "CP1 E-5 BEHAVIOUR GATE FAILED"; exit 1; }
echo "CP1 E-5 BEHAVIOUR GATE PASSED — E-5 MVP is DONE"
