#!/usr/bin/env bash
# AgentShield E2E Master Runner
#
# Runs all 13 test suites in order and aggregates suite-level pass/fail.
# Suites 5-12 are stubs — they print a clear "NOT YET IMPLEMENTED" message
# and exit 0 so the runner continues (they don't fail the suite).
# Suite 13 (Observability) is a CRITICAL gate — failure means the platform is dark.
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
run_suite "Suite 13: Observability (CRITICAL)"  "suite-13-observability.sh"
run_suite "Suite 14: Consumer Chat"             "suite-14-consumer-chat.sh"
run_suite "Suite 15: Artifact Isolation"        "suite-15-artifact-isolation.sh"
run_suite "Suite 16: Create Agent Flow"         "suite-16-create-agent.sh"
run_suite "Suite 17: Eval Gate (Decision 20)"   "suite-17-eval-gate.sh"
run_suite "Suite 18: OPA Governance"            "suite-18-opa-governance.sh"
run_suite "Suite 19: Execution Shape & Triggers" "suite-19-execution-shape.sh"
run_suite "Suite 20: Durable Playground"          "suite-20-durable-playground.sh"
run_suite "Suite 21: Scheduled Playground"        "suite-21-scheduled-playground.sh"
run_suite "Suite 22: Event-Driven Playground"     "suite-22-event-playground.sh"
run_suite "Suite 23: Production Runs"             "suite-23-production-runs.sh"
run_suite "Suite 24: Durable Production"          "suite-24-durable-production.sh"
run_suite "Suite 25: Agent Memory"                "suite-25-memory.sh"
run_suite "Suite 26: Scheduler Service"           "suite-26-scheduler.sh"
run_suite "Suite 27: Alerting + Observability"    "suite-27-alerting.sh"
run_suite "Suite 28: Event Gateway"               "suite-28-event-gateway.sh"
run_suite "Suite 29: Composite Workflow"          "suite-29-workflow-composite.sh"
run_suite "Suite 30: Orchestration Modes"         "suite-30-orchestration-modes.sh"
run_suite "Suite 31: Wizard Triggers + Memory"    "suite-31-wizard-triggers.sh"
run_suite "Suite 32: Per-schedule Input Payload"  "suite-32-schedule-payload.sh"
run_suite "Suite 33: Composable Agent Filter"     "suite-33-composable-agents.sh"
run_suite "Suite 34: Workflow Triggers"           "suite-34-workflow-triggers.sh"
run_suite "Suite 35: Approval Resume"             "suite-35-approval-resume.sh"
run_suite "Suite 36: Workflow HITL Pause-Resume"  "suite-36-workflow-hitl-pause-resume.sh"
run_suite "Suite 37: Workflow HITL OPA (gated)"   "suite-37-workflow-hitl-opa.sh"
run_suite "Suite 38: Deployment Overview"          "suite-38-deployment-overview.sh"
run_suite "Suite 39: Deployment Lifecycle"         "suite-39-deployment-lifecycle.sh"
run_suite "Suite 40: Workflow Deploy"              "suite-40-workflow-deploy.sh"
run_suite "Suite 41: Version Delete Cascade"       "suite-41-version-delete.sh"
run_suite "Suite 42: RBAC Foundations"              "suite-42-rbac.sh"
run_suite "Suite 43: Memory Isolation + TTL"        "suite-43-memory-isolation-ttl.sh"
run_suite "Suite 44: Version Management"              "suite-44-version-management.sh"
run_suite "Suite 45: HITL E2E (sandbox+prod)"       "suite-45-hitl-e2e.sh"
run_suite "Suite 46: Chat Deployment Pinning"       "suite-46-chat-deployment-pinning.sh"
run_suite "Suite 47: Deployment Chat Tracing"       "suite-47-deployment-chat-tracing.sh"
run_suite "Suite 48: Feedback Dashboard Panel"      "suite-48-feedback-dashboard.sh"
run_suite "Suite 49: Judge Score -> AgentRun"       "suite-49-judge-agentrun-score.sh"
run_suite "Suite 50: Version Dedup on Deploy"       "suite-50-version-dedup.sh"
run_suite "Suite 51: Credential Validation"         "suite-51-credential-validation.sh"
run_suite "Suite 52: Reconcile Drift Recovery"      "suite-52-reconcile-drift.sh"
run_suite "Suite 53: Cost Tracking"                 "suite-53-cost-tracking.sh"
run_suite "Suite 54: agent_class + shape dispatch"  "suite-54-agent-class-shape-dispatch.sh"
run_suite "Suite 55: Durable engine (park/resume)"   "suite-55-durable-engine.sh"

# ── Global Safety-Net Cleanup ─────────────────────────────────────────────────
# Catches leaked test artifacts from crashed suites (best-effort, never fails run)
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Post-Run Cleanup (safety net)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -n "$API_POD" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json

base = 'http://localhost:8000/api/v1'

# Delete datasets owned by test users
test_users = ['dev', 'smoke-user', 'eval-runner', 'e2e-suite9-user', 's9',
              's13-user', 's13-fb-user', 's13-test', 'test-user-s16', 'mallory-not-owner']
for user in test_users:
    try:
        req = urllib.request.Request(base + '/playground/datasets',
            headers={'X-User-Sub': user})
        r = urllib.request.urlopen(req, timeout=5)
        datasets = json.loads(r.read())
        for ds in datasets:
            try:
                dreq = urllib.request.Request(
                    base + '/playground/datasets/' + str(ds['id']),
                    headers={'X-User-Sub': user}, method='DELETE')
                urllib.request.urlopen(dreq, timeout=5)
            except Exception:
                pass
    except Exception:
        pass

print('  cleanup: test datasets purged')
" 2>/dev/null || true
  echo "  done"
fi

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
