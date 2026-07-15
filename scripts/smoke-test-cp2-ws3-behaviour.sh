#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws3-behaviour.sh
#
# WS-3 Checkpoint 2 — BEHAVIOUR smoke (CP2c). The no-fakes WORKFLOW + ALERT gate.
# Runs the REAL scheduled acceptance suite (suite-71) and asserts the WORKFLOW +
# alert portion (T-S71-003 parent/child workflow service identity, 004 4-mode
# park→resume, 005 alert-on-failure log observable) is PASS. Exits 0 only if every
# CP2-subset case passed.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== WS-3 CP2c: behaviour smoke (runs suite-71, asserts WORKFLOW+ALERT portion T-S71-003/004/005) ==="
echo ""

OUT=$(bash "$REPO_ROOT/scripts/e2e/suite-71-scheduled-e2e.sh" 2>&1) || true
echo "$OUT"
echo ""
echo "--- CP2c subset assertions (T-S71-003/004/005) ---"

RC=0
require_pass() {  # <id-substring> <label>
  if echo "$OUT" | grep -E "^PASS " | grep -q "$1"; then
    echo "PASS  CP2c :: $2"
  else
    echo "FAIL  CP2c :: $2 (no PASS line matching '$1')"
    RC=1
  fi
}
require_pass "T-S71-003" "scheduled daemon workflow parent+members carry WORKFLOW service identity"
require_pass "T-S71-004" "scheduled workflow modes park→async reviewer approve→resume"
require_pass "T-S71-005a" "alert FIRED with alert_email (alert_on_failure=true)"
require_pass "T-S71-005b" "NO alert when alert_on_failure=false"

echo ""
if [ "$RC" -ne 0 ]; then echo "CP2c BEHAVIOUR SMOKE FAILED"; exit 1; fi
echo "CP2c BEHAVIOUR SMOKE PASSED"
