#!/usr/bin/env bash
# AgentShield E2E Master Runner
#
# Runs all 12 test suites in order and aggregates suite-level pass/fail.
# Suites 5-12 are stubs — they print a clear "NOT YET IMPLEMENTED" message
# and exit 0 so the runner continues (they don't fail the suite).
#
# Usage:
#   bash scripts/e2e/run-all.sh
#   NAMESPACE=my-ns bash scripts/e2e/run-all.sh
#   bash scripts/e2e/run-all.sh --auto-pf   # passed through to suites that accept it
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAMESPACE="${NAMESPACE:-agentshield-platform}"
TOTAL_PASS=0
TOTAL_FAIL=0
FAILED_SUITES=()

# Pass --auto-pf through to suites that support it
EXTRA_ARGS=()
for arg in "$@"; do
  [[ "$arg" == "--auto-pf" ]] && EXTRA_ARGS+=("--auto-pf")
done

run_suite() {
  local name="$1" script="$2"
  local script_path="${SCRIPT_DIR}/${script}"
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  $name"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  if [ ! -f "$script_path" ]; then
    echo "  SKIP: $script not found — suite not yet implemented"
    return 0  # Don't count missing future suites as failures
  fi
  if NAMESPACE="$NAMESPACE" bash "$script_path" "${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"}"; then
    TOTAL_PASS=$((TOTAL_PASS + 1))
  else
    TOTAL_FAIL=$((TOTAL_FAIL + 1))
    FAILED_SUITES+=("$name")
  fi
}

echo "AgentShield E2E Test Suite"
echo "Namespace: $NAMESPACE"
echo "Date:      $(date)"

run_suite "Suite 1:  Platform Health"           "suite-1-health.sh"
run_suite "Suite 2:  Agent Lifecycle"           "suite-2-lifecycle.sh"
run_suite "Suite 3:  Safety Scanning"           "suite-3-safety.sh"
run_suite "Suite 4:  HITL Approval Flow"        "suite-4-hitl.sh"
run_suite "Suite 5:  HITL Authority Scoping"    "suite-5-hitl-authority.sh"
run_suite "Suite 6:  Asset Lifecycle"           "suite-6-asset-lifecycle.sh"
run_suite "Suite 7:  Machine Identity"          "suite-7-machine-identity.sh"
run_suite "Suite 8:  Playground"                "suite-8-playground.sh"
run_suite "Suite 9:  Eval Runner"               "suite-9-eval.sh"
run_suite "Suite 10: Multi-Agent Handoff"       "suite-10-multi-agent.sh"
run_suite "Suite 11: Resilience"                "suite-11-resilience.sh"
run_suite "Suite 12: Quarantine"                "suite-12-quarantine.sh"

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  FINAL RESULTS"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Suites passed: $TOTAL_PASS"
echo "  Suites failed: $TOTAL_FAIL"
if [ ${#FAILED_SUITES[@]} -gt 0 ]; then
  echo "  Failed suites:"
  for s in "${FAILED_SUITES[@]}"; do echo "    - $s"; done
fi
[ "$TOTAL_FAIL" -eq 0 ] && echo "  STATUS: ALL PASS" && exit 0
echo "  STATUS: FAILURES DETECTED" && exit 1
