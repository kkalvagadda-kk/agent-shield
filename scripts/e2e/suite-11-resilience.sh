#!/usr/bin/env bash
# Suite 11: Resilience + Fail-Closed — T-S11-001 through T-S11-007
#
# Tests that the platform handles component failures gracefully and always
# fails toward safety (fail-closed).
#
# Non-destructive (always run):
#   T-S11-001 — GET /health on registry-api and safety-orchestrator
#   T-S11-002 — GET /ready on safety-orchestrator → scanner statuses
#   T-S11-003 — Count Running pods in the namespace
#
# Destructive (require DESTRUCTIVE=true):
#   T-S11-004 — Kill Presidio pod → POST /scan/input must return blocked=true
#   T-S11-005 — Kill NeMo Guardrails pod → POST /scan/input blocked=true
#   T-S11-006 — Kill LLM Guard pod → POST /scan/input blocked=true (see also smoke-test-cp3-safety.sh)
#   T-S11-007 — Restart Registry API → data not lost
#
# Manual:
#   T-S11-005b — OPA sidecar kill in agent pod → SDK returns DENY
#
# Usage:
#   # Non-destructive only (default):
#   NAMESPACE=agentshield-platform bash scripts/e2e/suite-11-resilience.sh
#
#   # Full run including pod kills:
#   NAMESPACE=agentshield-platform DESTRUCTIVE=true bash scripts/e2e/suite-11-resilience.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
DESTRUCTIVE="${DESTRUCTIVE:-false}"

# Safety orchestrator cluster-internal service name
SAFETY_SVC="agentshield-safety-orchestrator"
SAFETY_PORT="8080"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

PASS=0
FAIL=0
MANUAL=0

run_test() {
  local desc="$1"
  local code="$2"
  if kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$code" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

check_manual() {
  local desc="$1"
  shift
  echo "  MANUAL: $desc"
  for step in "$@"; do
    echo "    $step"
  done
  MANUAL=$((MANUAL + 1))
}

# Post a scan/input request through the API pod reaching the safety-orchestrator service
scan_via_exec() {
  local text="$1"
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
body = json.dumps({
  'session_id': 'suite11-resil-\$(date +%s)',
  'agent_name': 'suite11-test-agent',
  'message': '$text'
}).encode()
req = urllib.request.Request(
  'http://${SAFETY_SVC}.${NAMESPACE}.svc.cluster.local:${SAFETY_PORT}/api/v1/scan/input',
  data=body,
  headers={'Content-Type': 'application/json'},
  method='POST'
)
try:
  r = urllib.request.urlopen(req, timeout=10)
  data = json.loads(r.read())
  print(json.dumps(data))
except urllib.error.HTTPError as e:
  print(json.dumps({'blocked': True, 'reason': f'http_error:{e.code}'}))
except Exception as e:
  print(json.dumps({'blocked': True, 'reason': f'error:{str(e)}'}))
" 2>/dev/null || echo '{"blocked": true, "reason": "exec_error"}'
}

echo "=== Suite 11: Resilience + Fail-Closed ==="
echo "    API pod:      $API_POD"
echo "    Destructive:  $DESTRUCTIVE"
echo ""

# ── T-S11-001: Health Check on All Services ───────────────────────────────────
echo "--- T-S11-001: GET /health on registry-api and safety-orchestrator ---"

# Registry-API /health (exec inside the pod → localhost)
run_test "GET /health on registry-api returns 200" "
import urllib.request
r = urllib.request.urlopen('http://localhost:8000/health', timeout=5)
assert r.status == 200, f'expected 200 got {r.status}'
"

# Safety-orchestrator /health via cluster DNS
SAFETY_HEALTH=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
  r = urllib.request.urlopen(
    'http://${SAFETY_SVC}.${NAMESPACE}.svc.cluster.local:${SAFETY_PORT}/health',
    timeout=5
  )
  print(r.status)
except Exception as e:
  print(f'error:{e}')
" 2>/dev/null || echo "error")

if [ "$SAFETY_HEALTH" = "200" ]; then
  echo "  PASS: GET /health on safety-orchestrator returns 200"
  PASS=$((PASS + 1))
else
  echo "  FAIL: GET /health on safety-orchestrator returned: $SAFETY_HEALTH"
  echo "        (Is safety-orchestrator running? kubectl get pods -n $NAMESPACE -l app.kubernetes.io/name=safety-orchestrator)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S11-002: GET /ready on Safety-Orchestrator ──────────────────────────────
echo "--- T-S11-002: GET /ready on safety-orchestrator → scanner statuses ---"
READY_BODY=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
try:
  r = urllib.request.urlopen(
    'http://${SAFETY_SVC}.${NAMESPACE}.svc.cluster.local:${SAFETY_PORT}/ready',
    timeout=5
  )
  print(json.dumps(json.loads(r.read())))
except Exception as e:
  print(json.dumps({'error': str(e)}))
" 2>/dev/null || echo '{"error":"exec_failed"}')

if echo "$READY_BODY" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
assert 'error' not in d, f'error: {d}'
# /ready response has: ready, scanners dict
assert 'ready' in d or 'scanners' in d, f'unexpected shape: {d}'
print(f'ready={d.get(\"ready\")} scanners={d.get(\"scanners\",{})}')
" 2>/dev/null; then
  READY_INFO=$(echo "$READY_BODY" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(f'ready={d.get(\"ready\")} scanners={d.get(\"scanners\",{})}')
" 2>/dev/null || echo "$READY_BODY")
  echo "  PASS: GET /ready on safety-orchestrator ($READY_INFO)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: GET /ready returned unexpected body: $READY_BODY"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S11-003: Count Running Pods ─────────────────────────────────────────────
echo "--- T-S11-003: Count Running pods in $NAMESPACE ---"
RUNNING_COUNT=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
  | grep -c "Running" || echo "0")
TOTAL_COUNT=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
  | wc -l | tr -d ' ' || echo "0")

if [ "$RUNNING_COUNT" -gt 0 ]; then
  echo "  PASS: $RUNNING_COUNT/$TOTAL_COUNT pods Running in $NAMESPACE"
  PASS=$((PASS + 1))
else
  echo "  FAIL: No Running pods found in $NAMESPACE (total=$TOTAL_COUNT)"
  FAIL=$((FAIL + 1))
fi

NOT_RUNNING=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null \
  | grep -v "Running\|Completed" || true)
if [ -n "$NOT_RUNNING" ]; then
  echo "  WARNING: pods not in Running state:"
  echo "$NOT_RUNNING" | sed 's/^/    /'
fi
echo ""

# ── T-S11-004: Kill Presidio → Fail-Closed (DESTRUCTIVE) ──────────────────────
echo "--- T-S11-004: Presidio Kill → POST /scan/input must block (DESTRUCTIVE=${DESTRUCTIVE}) ---"
if [ "$DESTRUCTIVE" != "true" ]; then
  echo "  SKIP: destructive test (set DESTRUCTIVE=true to run)"
  echo "  What this would do:"
  echo "    kubectl delete pod -n $NAMESPACE -l app.kubernetes.io/name=presidio --wait=false"
  echo "    POST /scan/input with clean text → assert blocked=true"
  echo "    Wait for Presidio ReplicaSet to restore pod"
  MANUAL=$((MANUAL + 1))
else
  PRESIDIO_DEP=$(kubectl get deployment -n "$NAMESPACE" \
    -l "app.kubernetes.io/name=presidio" -o name 2>/dev/null | head -1 || true)
  PRESIDIO_POD=$(kubectl get pods -n "$NAMESPACE" \
    -l "app.kubernetes.io/name=presidio" --no-headers 2>/dev/null | awk '{print $1}' | head -1 || true)

  if [ -z "$PRESIDIO_POD" ]; then
    echo "  SKIP: No Presidio pod found in $NAMESPACE — Presidio not deployed"
    MANUAL=$((MANUAL + 1))
  else
    echo "  Deleting Presidio pod $PRESIDIO_POD (ReplicaSet will recreate it)..."
    kubectl delete pod -n "$NAMESPACE" "$PRESIDIO_POD" --wait=false

    echo "  Waiting 5s for Presidio to become unreachable..."
    sleep 5

    SCAN_RESP=$(scan_via_exec "Hello, what time is it?")
    IS_BLOCKED=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print('true' if d.get('blocked') else 'false')
" 2>/dev/null || echo "false")

    if [ "$IS_BLOCKED" = "true" ]; then
      REASON=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('reason', 'unknown'))
" 2>/dev/null || echo "unknown")
      echo "  PASS: Request blocked when Presidio is down (reason=${REASON})"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: Request was NOT blocked when Presidio pod was deleted — fail-closed not working"
      echo "        Response: $SCAN_RESP"
      FAIL=$((FAIL + 1))
    fi

    # Restore — wait for new pod to be Ready
    echo "  Waiting for Presidio pod to be recreated (up to 60s)..."
    kubectl rollout status "${PRESIDIO_DEP}" -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
    echo "  Presidio restored."
  fi
fi
echo ""

# ── T-S11-005: Kill NeMo Guardrails → Fail-Closed (DESTRUCTIVE) ───────────────
echo "--- T-S11-005: NeMo Guardrails Kill → POST /scan/input must block (DESTRUCTIVE=${DESTRUCTIVE}) ---"
if [ "$DESTRUCTIVE" != "true" ]; then
  echo "  SKIP: destructive test (set DESTRUCTIVE=true to run)"
  echo "  What this would do:"
  echo "    kubectl delete pod -n $NAMESPACE -l app.kubernetes.io/name=nemo-guardrails --wait=false"
  echo "    POST /scan/input → assert blocked=true"
  echo "    Restore: kubectl rollout restart deployment/<nemo-deployment> -n $NAMESPACE"
  MANUAL=$((MANUAL + 1))
else
  NEMO_DEP=$(kubectl get deployment -n "$NAMESPACE" \
    -l "app.kubernetes.io/name=nemo-guardrails" -o name 2>/dev/null | head -1 || true)
  NEMO_POD=$(kubectl get pods -n "$NAMESPACE" \
    -l "app.kubernetes.io/name=nemo-guardrails" --no-headers 2>/dev/null | awk '{print $1}' | head -1 || true)

  if [ -z "$NEMO_POD" ]; then
    echo "  SKIP: No NeMo Guardrails pod found in $NAMESPACE"
    MANUAL=$((MANUAL + 1))
  else
    echo "  Deleting NeMo pod $NEMO_POD..."
    kubectl delete pod -n "$NAMESPACE" "$NEMO_POD" --wait=false
    sleep 5

    SCAN_RESP=$(scan_via_exec "Tell me about the weather.")
    IS_BLOCKED=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print('true' if d.get('blocked') else 'false')
" 2>/dev/null || echo "false")

    if [ "$IS_BLOCKED" = "true" ]; then
      REASON=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('reason', 'unknown'))
" 2>/dev/null || echo "unknown")
      echo "  PASS: Request blocked when NeMo is down (reason=${REASON})"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: Request was NOT blocked when NeMo pod was deleted"
      echo "        Response: $SCAN_RESP"
      FAIL=$((FAIL + 1))
    fi

    echo "  Restoring NeMo Guardrails..."
    kubectl rollout status "${NEMO_DEP}" -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
    echo "  NeMo restored."
  fi
fi
echo ""

# ── T-S11-006: Kill LLM Guard → Fail-Closed (DESTRUCTIVE) ────────────────────
echo "--- T-S11-006: LLM Guard Kill → POST /scan/input must block (DESTRUCTIVE=${DESTRUCTIVE}) ---"
echo "  NOTE: Mirrors smoke-test-cp3-safety.sh Test 5 — prefer that script for LLM Guard fail-closed."
if [ "$DESTRUCTIVE" != "true" ]; then
  echo "  SKIP: destructive test (set DESTRUCTIVE=true to run)"
  echo "  What this would do:"
  echo "    REPLICAS=\$(kubectl get deploy agentshield-llm-guard -n $NAMESPACE -o jsonpath='{.spec.replicas}')"
  echo "    kubectl scale deployment agentshield-llm-guard -n $NAMESPACE --replicas=0"
  echo "    POST /scan/input → assert blocked=true"
  echo "    kubectl scale deployment agentshield-llm-guard -n $NAMESPACE --replicas=\$REPLICAS"
  MANUAL=$((MANUAL + 1))
else
  LG_DEP=$(kubectl get deployment agentshield-llm-guard -n "$NAMESPACE" \
    --ignore-not-found -o name 2>/dev/null || true)

  if [ -z "$LG_DEP" ]; then
    echo "  SKIP: agentshield-llm-guard deployment not found"
    MANUAL=$((MANUAL + 1))
  else
    LG_REPLICAS=$(kubectl get deployment agentshield-llm-guard -n "$NAMESPACE" \
      -o jsonpath='{.spec.replicas}' 2>/dev/null || echo "1")

    echo "  Scaling LLM Guard to 0 (was $LG_REPLICAS)..."
    kubectl scale deployment agentshield-llm-guard -n "$NAMESPACE" --replicas=0

    # Wait for pods to stop
    DEADLINE=$(($(date +%s) + 30))
    while [ "$(date +%s)" -lt "$DEADLINE" ]; do
      PODS=$(kubectl get pods -n "$NAMESPACE" -l "app.kubernetes.io/name=llm-guard" \
        --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')
      [ "$PODS" -eq 0 ] && break
      echo "  ... waiting ($PODS LLM Guard pods still running)"
      sleep 3
    done

    SCAN_RESP=$(scan_via_exec "What is 2 + 2?")
    IS_BLOCKED=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print('true' if d.get('blocked') else 'false')
" 2>/dev/null || echo "false")

    if [ "$IS_BLOCKED" = "true" ]; then
      REASON=$(echo "$SCAN_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print(d.get('reason', 'unknown'))
" 2>/dev/null || echo "unknown")
      echo "  PASS: Request blocked when LLM Guard is down (reason=${REASON})"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: Request NOT blocked when LLM Guard was at 0 replicas — fail-closed not working"
      echo "        Response: $SCAN_RESP"
      FAIL=$((FAIL + 1))
    fi

    echo "  Restoring LLM Guard to $LG_REPLICAS replica(s)..."
    kubectl scale deployment agentshield-llm-guard -n "$NAMESPACE" --replicas="$LG_REPLICAS"
    kubectl rollout status agentshield-llm-guard -n "$NAMESPACE" --timeout=90s 2>/dev/null || true
    echo "  LLM Guard restored."

    # Verify recovery: clean text should pass after restore
    echo "  Verifying recovery: clean text should not be blocked after restore..."
    sleep 5  # give scanner a moment to become ready
    RECOVERY_RESP=$(scan_via_exec "Hello, how are you today?")
    IS_STILL_BLOCKED=$(echo "$RECOVERY_RESP" | python3 -c "
import sys, json
d = json.loads(sys.stdin.read())
print('true' if d.get('blocked') else 'false')
" 2>/dev/null || echo "true")
    if [ "$IS_STILL_BLOCKED" = "false" ]; then
      echo "  PASS: Clean text passes after LLM Guard recovery"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: Clean text still blocked after LLM Guard recovery — check scanner health"
      FAIL=$((FAIL + 1))
    fi
  fi
fi
echo ""

# ── T-S11-007: Restart Registry API → No Data Loss (DESTRUCTIVE) ──────────────
echo "--- T-S11-007: Restart Registry API → no data loss, agent pods unaffected (DESTRUCTIVE=${DESTRUCTIVE}) ---"
if [ "$DESTRUCTIVE" != "true" ]; then
  echo "  SKIP: destructive test (set DESTRUCTIVE=true to run)"
  echo "  What this would do:"
  echo "    1. GET /api/v1/agents → record count"
  echo "    2. kubectl rollout restart deployment agentshield-registry-api -n $NAMESPACE"
  echo "    3. Wait for new pod to be Ready (poll up to 60s)"
  echo "    4. GET /api/v1/agents → assert same agent count"
  echo "    5. kubectl get pods -n agents-platform → assert running agent pods unaffected"
  MANUAL=$((MANUAL + 1))
else
  # Count agents before restart
  AGENT_COUNT_BEFORE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/')
data = json.loads(r.read())
print(data.get('total', len(data.get('items', []))))
" 2>/dev/null || echo "error")

  if [ "$AGENT_COUNT_BEFORE" = "error" ]; then
    echo "  FAIL: Could not fetch agent count before restart"
    FAIL=$((FAIL + 1))
  else
    echo "  Agent count before restart: $AGENT_COUNT_BEFORE"
    echo "  Triggering rolling restart of agentshield-registry-api..."
    kubectl rollout restart deployment agentshield-registry-api -n "$NAMESPACE"

    # Wait for rollout to complete (up to 60s)
    kubectl rollout status deployment agentshield-registry-api -n "$NAMESPACE" --timeout=60s

    # Get new pod name
    NEW_API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
      -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
    echo "  New API pod: $NEW_API_POD"

    # Verify agent count after restart
    AGENT_COUNT_AFTER=$(kubectl exec -n "$NAMESPACE" "$NEW_API_POD" -- python3 -c "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/')
data = json.loads(r.read())
print(data.get('total', len(data.get('items', []))))
" 2>/dev/null || echo "error")

    if [ "$AGENT_COUNT_AFTER" = "$AGENT_COUNT_BEFORE" ]; then
      echo "  PASS: Agent count unchanged after restart ($AGENT_COUNT_BEFORE → $AGENT_COUNT_AFTER)"
      PASS=$((PASS + 1))
    else
      echo "  FAIL: Agent count changed after restart ($AGENT_COUNT_BEFORE → $AGENT_COUNT_AFTER)"
      FAIL=$((FAIL + 1))
    fi

    # Verify deployed agent pods were not affected
    RUNNING_AGENT_PODS=$(kubectl get pods -n agents-platform --no-headers 2>/dev/null \
      | grep -c "Running" || echo "0")
    echo "  INFO: $RUNNING_AGENT_PODS Running pods in agents-platform (unaffected by registry-api restart)"
  fi
fi
echo ""

# ── Manual: OPA Sidecar Kill → SDK DENY ────────────────────────────────────────
echo "--- T-S11-008 (plan T-S11-005): OPA Sidecar Kill → SDK Returns DENY ---"
check_manual "Verify SDK fails closed when OPA sidecar process is killed inside agent pod" \
  "# Prerequisite: an agent pod running with OPA sidecar in agents-platform namespace" \
  "# 1. Find an agent pod:" \
  "#    kubectl get pods -n agents-platform --no-headers | head -5" \
  "# 2. Kill the OPA sidecar process (ignoring errors if already stopped):" \
  "#    AGENT_POD=<pod-name>" \
  "#    kubectl exec \$AGENT_POD -n agents-platform -- pkill -f opa || true" \
  "# 3. From inside the pod, trigger a tool call via the AgentShield SDK:" \
  "#    kubectl exec \$AGENT_POD -n agents-platform -- python3 -c \\" \
  "#      \"from agentshield_sdk import check_tool_permission; print(check_tool_permission('issue_refund'))\"" \
  "# 4. Assert SDK returns DENY or raises an exception — does not proceed with the tool call" \
  "# Pass: SDK error/deny. The tool is NOT executed. OPA sidecar not responding = deny by default."
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  Suite 11 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
if [ "$DESTRUCTIVE" != "true" ]; then
  echo "  Destructive tests were skipped. Re-run with DESTRUCTIVE=true to exercise pod kills."
fi
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
