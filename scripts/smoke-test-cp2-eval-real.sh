#!/usr/bin/env bash
# scripts/smoke-test-cp2-eval-real.sh
#
# Eval v2 E-0 — Checkpoint 2 REAL-suite smoke (the no-fakes gate).
#
# Runs the load-bearing suite-61 (real reactive dataset -> real EvalRun -> real
# eval-runner Job -> real judge -> persisted dimension_scores/composite,
# save->reload) and asserts every T-S61-00X printed PASS — no FAIL, no SKIP.
# A SKIP here means the eval-runner Job could not run (env limit); after
# deploy-cp2-eval.sh it MUST run, so a SKIP fails this checkpoint.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SUITE="${SCRIPT_DIR}/e2e/suite-61-eval-mode-plumbing.sh"

echo "==> CP2 real-suite smoke — running suite-61 (namespace: $NAMESPACE)"
if [ ! -f "$SUITE" ]; then
  echo "[FATAL] $SUITE not found"; exit 1
fi

OUT=$(NAMESPACE="$NAMESPACE" bash "$SUITE" 2>&1) || true
echo "$OUT"
echo ""

FAIL=0
pass() { echo "[PASS] $1"; }
fail() { echo "[FAIL] $1"; FAIL=1; }

# No test may have FAILed and none may have SKIPped (the Job must have run).
if echo "$OUT" | grep -q "^FAIL "; then
  fail "suite-61 reported a FAIL (a real assertion failed)"
fi
if echo "$OUT" | grep -q "^SKIP "; then
  fail "suite-61 SKIPPED (eval-runner Job did not run) — deploy eval-runner:0.1.5 + Jobs RBAC"
fi

# The core real-run assertions must each be present as PASS.
for tc in \
  "001_agent_deployed_running" \
  "002_real_evalrun_completed" \
  "003_rows_have_response_dimension_and_composite" \
  "004_parity_composite_eq_response_and_judge_real" \
  "005_eval_passed_autoset_on_passing_version"; do
  if echo "$OUT" | grep -q "PASS $tc"; then
    pass "T-S61 $tc"
  else
    fail "T-S61 $tc did not PASS"
  fi
done

echo ""
echo "================================"
if [ "$FAIL" -eq 0 ]; then
  echo "CP2 real-suite smoke: PASS (suite-61 fully green, no fakes)"
  echo "PASS"
  exit 0
fi
echo "CP2 real-suite smoke: FAIL"
echo "FAIL"
exit 1
