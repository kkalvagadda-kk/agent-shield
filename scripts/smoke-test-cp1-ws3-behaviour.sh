#!/usr/bin/env bash
# scripts/smoke-test-cp1-ws3-behaviour.sh
#
# WS-3 Checkpoint 1 — BEHAVIOUR smoke (CP1c). The no-fakes AGENT behaviour gate.
# Runs the REAL scheduled acceptance suite (suite-71) and asserts the AGENT portion
# (T-S71-000 parity + 001 scheduled fire/run_by/run_steps + 002 park/403/resume)
# is PASS. Scoped as the CP1 entry point so the checkpoint reads uniformly
# (deploy → infra → behaviour). Exits 0 only if every CP1-subset case passed.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== WS-3 CP1c: behaviour smoke (runs suite-71, asserts AGENT portion T-S71-000/001/002) ==="
echo ""

OUT=$(bash "$REPO_ROOT/scripts/e2e/suite-71-scheduled-e2e.sh" 2>&1) || true
echo "$OUT"
echo ""
echo "--- CP1c subset assertions (T-S71-000/001/002) ---"

RC=0
require_pass() {  # <id-substring> <label>
  if echo "$OUT" | grep -E "^PASS " | grep -q "$1"; then
    echo "PASS  CP1c :: $2"
  else
    echo "FAIL  CP1c :: $2 (no PASS line matching '$1')"
    RC=1
  fi
}
require_pass "T-S71-000" "parity: no scheduled-only dispatch fork"
require_pass "T-S71-001a" "scheduled fire: trigger_type='schedule' + run_by=service identity"
require_pass "T-S71-001b" "scheduled durable run committed real run_steps"
require_pass "T-S71-001c" "schedule trigger armed_by persisted"
require_pass "T-S71-002a" "scheduled run parks: principal_display + reviewer_scope"
require_pass "T-S71-002b" "non-reviewer decide 403"
require_pass "T-S71-002c" "reviewer decide resumes run"

echo ""
if [ "$RC" -ne 0 ]; then echo "CP1c BEHAVIOUR SMOKE FAILED"; exit 1; fi
echo "CP1c BEHAVIOUR SMOKE PASSED"
