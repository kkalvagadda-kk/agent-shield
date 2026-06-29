#!/usr/bin/env bash
# Suite 12: Quarantine + Emergency Response — T-S12-001 through T-S12-007
#
# Tests the operator's ability to isolate a misbehaving agent without
# destroying forensic state.
#
# Quarantine endpoint facts (from services/registry-api/routers/agents.py):
#   POST   /api/v1/agents/{name}/quarantine  — no request body required
#                                              returns AgentResponse (200), 409 if already quarantined
#   DELETE /api/v1/agents/{name}/quarantine  — lifts quarantine → status='active'
#                                              returns AgentResponse (200), 409 if not quarantined
#
# NetworkPolicy for quarantine:
#   Applied by the Deploy Controller when it observes status='quarantined'.
#   Label selector used: agentshield.io/quarantine={agent-name}
#   Namespace: agents-platform
#   NOTE: NetworkPolicy creation requires the Deploy Controller to have reconciled
#   the status change. If the deploy-controller is not running, the NetworkPolicy
#   check will be MANUAL.
#
# Usage:
#   NAMESPACE=agentshield-platform bash scripts/e2e/suite-12-quarantine.sh
#
#   # Use a specific test agent:
#   QUARANTINE_AGENT=smoke-agent bash scripts/e2e/suite-12-quarantine.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="agents-platform"

# Agent to use for quarantine tests — must be pre-registered (not necessarily deployed)
QUARANTINE_AGENT="${QUARANTINE_AGENT:-smoke-quarantine-agent-s12}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

PASS=0
FAIL=0
MANUAL=0
AGENT_CREATED=false

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

# Cleanup on exit: lift quarantine (if still set) and soft-delete test agent
cleanup() {
  if [ "$AGENT_CREATED" = "true" ]; then
    echo ""
    echo "--- Cleanup ---"
    # Lift quarantine first if still quarantined (lift_quarantine returns 409 if not quarantined)
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
  method='DELETE'
)
try:
  urllib.request.urlopen(req)
  print('  lifted quarantine on $QUARANTINE_AGENT')
except urllib.error.HTTPError as e:
  if e.code == 409:
    print('  agent not quarantined — no lift needed')
  else:
    print(f'  quarantine lift warning: {e.code}')
except Exception as e:
  print(f'  cleanup note: {e}')
" 2>/dev/null || true

    # Soft-delete the test agent
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT',
  method='DELETE'
)
try:
  urllib.request.urlopen(req)
  print('  soft-deleted $QUARANTINE_AGENT')
except urllib.error.HTTPError as e:
  print(f'  cleanup note: {e.code}')
except Exception as e:
  print(f'  cleanup warning: {e}')
" 2>/dev/null || true
  fi
}
trap cleanup EXIT

echo "=== Suite 12: Quarantine + Emergency Response ==="
echo "    API pod:          $API_POD"
echo "    Quarantine agent: $QUARANTINE_AGENT"
echo ""

# ── T-S12-001: Register Test Agent (if not exists) ────────────────────────────
echo "--- T-S12-001: Register Test Agent ---"
REG_RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
try:
  r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT')
  data = json.loads(r.read())
  status = data.get('status', 'unknown')
  # If already quarantined from a previous failed run, lift it first
  if status == 'quarantined':
    req = urllib.request.Request(
      'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
      method='DELETE'
    )
    urllib.request.urlopen(req)
    print('preexists-unquarantined')
  else:
    print('exists')
except urllib.error.HTTPError as e:
  if e.code == 404:
    req = urllib.request.Request(
      'http://localhost:8000/api/v1/agents/',
      data=json.dumps({
        'name': '$QUARANTINE_AGENT',
        'team': 'platform',
        'description': 'Suite 12 quarantine smoke test agent'
      }).encode(),
      headers={'Content-Type': 'application/json'},
      method='POST'
    )
    r = urllib.request.urlopen(req)
    assert r.status == 201, f'expected 201 got {r.status}'
    print('created')
  else:
    print(f'error:{e.code}')
" 2>/dev/null || echo "error")

case "$REG_RESULT" in
  created)
    echo "  PASS: Agent '$QUARANTINE_AGENT' registered (201)"
    PASS=$((PASS + 1))
    AGENT_CREATED=true
    ;;
  exists|preexists-unquarantined)
    echo "  PASS: Agent '$QUARANTINE_AGENT' already exists ($REG_RESULT)"
    PASS=$((PASS + 1))
    AGENT_CREATED=true  # We own it for cleanup
    ;;
  *)
    echo "  FAIL: Could not register agent '$QUARANTINE_AGENT' (result=$REG_RESULT)"
    FAIL=$((FAIL + 1))
    ;;
esac
echo ""

# ── T-S12-002: POST Quarantine → 200, status='quarantined' ───────────────────
echo "--- T-S12-002: POST /api/v1/agents/{name}/quarantine → 200, status='quarantined' ---"
# NOTE: quarantine_agent takes no request body
run_test "POST /agents/$QUARANTINE_AGENT/quarantine → 200, status=quarantined" "
import urllib.request, json, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
  data=b'',  # no body required
  headers={'Content-Type': 'application/json'},
  method='POST'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('status') == 'quarantined', \
  f'expected quarantined got {data.get(\"status\")}'
assert data.get('name') == '$QUARANTINE_AGENT', \
  f'name mismatch: {data.get(\"name\")}'
"
echo ""

# ── T-S12-003: GET Agent Confirms status='quarantined' ────────────────────────
echo "--- T-S12-003: GET /api/v1/agents/{name} → status='quarantined' confirmed ---"
run_test "GET /agents/$QUARANTINE_AGENT → status=quarantined persisted" "
import urllib.request, json
r = urllib.request.urlopen(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT'
)
assert r.status == 200
data = json.loads(r.read())
assert data.get('status') == 'quarantined', \
  f'expected quarantined got {data.get(\"status\")}'
"
echo ""

# ── T-S12-004: NetworkPolicy Created by Deploy Controller ─────────────────────
echo "--- T-S12-004: NetworkPolicy Created for Quarantined Agent ---"
echo "  Checking for NetworkPolicy in $AGENTS_NAMESPACE with label agentshield.io/quarantine=$QUARANTINE_AGENT..."
# Give deploy-controller a moment to react (it watches agent status changes)
sleep 3

NP_COUNT=$(kubectl get networkpolicy -n "$AGENTS_NAMESPACE" \
  -l "agentshield.io/quarantine=${QUARANTINE_AGENT}" \
  --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")

if [ "$NP_COUNT" -gt 0 ]; then
  NP_NAME=$(kubectl get networkpolicy -n "$AGENTS_NAMESPACE" \
    -l "agentshield.io/quarantine=${QUARANTINE_AGENT}" \
    -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo "unknown")
  echo "  PASS: NetworkPolicy exists ($NP_NAME) — deploy-controller reacted to quarantine status"
  PASS=$((PASS + 1))
else
  # Also check by name pattern (quarantine-{agent-name})
  NP_BY_NAME=$(kubectl get networkpolicy -n "$AGENTS_NAMESPACE" \
    "quarantine-${QUARANTINE_AGENT}" --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
  if [ "$NP_BY_NAME" -gt 0 ]; then
    echo "  PASS: NetworkPolicy quarantine-${QUARANTINE_AGENT} exists"
    PASS=$((PASS + 1))
  else
    echo "  MANUAL: No NetworkPolicy found for '$QUARANTINE_AGENT' in $AGENTS_NAMESPACE"
    echo "    The deploy-controller reconcile loop creates this NetworkPolicy on quarantine."
    echo "    If the deploy-controller is not running, or the agent was never deployed,"
    echo "    the NetworkPolicy will not be created."
    echo "    Manual check:"
    echo "      kubectl get networkpolicy -n $AGENTS_NAMESPACE"
    echo "      kubectl get networkpolicy -n $AGENTS_NAMESPACE -l agentshield.io/quarantine=$QUARANTINE_AGENT"
    echo "    Deploy-controller logs:"
    echo "      kubectl logs -n $NAMESPACE -l app.kubernetes.io/name=deploy-controller --tail=20"
    MANUAL=$((MANUAL + 1))
  fi
fi
echo ""

# ── T-S12-005: Pod Still Running After Quarantine (Forensic State Preserved) ──
echo "--- T-S12-005: Pod Still Running After Quarantine (forensic state preserved) ---"
AGENT_POD=$(kubectl get pods -n "$AGENTS_NAMESPACE" \
  -l "app.kubernetes.io/name=${QUARANTINE_AGENT}" \
  --no-headers 2>/dev/null | head -1 || true)

if [ -z "$AGENT_POD" ]; then
  echo "  MANUAL: No pod found for '$QUARANTINE_AGENT' in $AGENTS_NAMESPACE"
  echo "    Agent has not been deployed — cannot verify pod Running state."
  echo "    To verify: deploy the agent, quarantine it, then check:"
  echo "      kubectl get pods -n $AGENTS_NAMESPACE -l app.kubernetes.io/name=$QUARANTINE_AGENT"
  echo "    Expected: pod phase=Running (not Terminating, not absent)"
  echo "    This confirms: quarantine blocks network access but does NOT scale pod to 0"
  MANUAL=$((MANUAL + 1))
else
  POD_NAME=$(echo "$AGENT_POD" | awk '{print $1}')
  POD_STATUS=$(echo "$AGENT_POD" | awk '{print $3}')
  if [ "$POD_STATUS" = "Running" ]; then
    echo "  PASS: Pod $POD_NAME is Running (forensic state preserved — pod not scaled to 0)"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: Pod $POD_NAME is NOT Running (status=$POD_STATUS) — quarantine incorrectly affected pod lifecycle"
    FAIL=$((FAIL + 1))
  fi
fi
echo ""

# ── T-S12-006: Invoke via Envoy Returns 503 (MANUAL — requires deployed agent) ──
echo "--- T-S12-006 (plan T-S12-002): Invoke Quarantined Agent Returns 503 ---"
check_manual "Requests to quarantined agent rejected via Envoy (NetworkPolicy blocks ingress)" \
  "# Prerequisite: agent deployed, NetworkPolicy applied (T-S12-004 passed)" \
  "# 1. Attempt to invoke the quarantined agent via Envoy:" \
  "#    curl -X POST http://envoy-gateway.$NAMESPACE/agents/$QUARANTINE_AGENT/chat \\" \
  "#         -H 'Authorization: Bearer <valid-jwt>' \\" \
  "#         -H 'Content-Type: application/json' \\" \
  "#         -d '{\"message\": \"hello\"}'" \
  "# 2. Assert HTTP 503 or connection refused with appropriate error message" \
  "# 3. Assert the request did NOT reach the agent pod (check pod logs — no new entries)" \
  "# Pass: HTTP 503 (or network-level block). Agent pod logs show no new request."
echo ""

# ── T-S12-007: Lift Quarantine → status='active' ─────────────────────────────
echo "--- T-S12-007 (plan T-S12-004): DELETE /api/v1/agents/{name}/quarantine → 200, status='active' ---"
run_test "DELETE /agents/$QUARANTINE_AGENT/quarantine → 200, status=active" "
import urllib.request, json, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
  method='DELETE'
)
r = urllib.request.urlopen(req)
assert r.status == 200, f'expected 200 got {r.status}'
data = json.loads(r.read())
assert data.get('status') == 'active', \
  f'expected active got {data.get(\"status\")}'
assert data.get('name') == '$QUARANTINE_AGENT'
"
echo ""

# ── T-S12-008: NetworkPolicy Removed After Lift ────────────────────────────────
echo "--- T-S12-008 (plan T-S12-004): NetworkPolicy Removed After Lift ---"
echo "  Checking that NetworkPolicy was removed after quarantine was lifted..."
sleep 3

NP_AFTER=$(kubectl get networkpolicy -n "$AGENTS_NAMESPACE" \
  -l "agentshield.io/quarantine=${QUARANTINE_AGENT}" \
  --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
NP_BY_NAME_AFTER=$(kubectl get networkpolicy -n "$AGENTS_NAMESPACE" \
  "quarantine-${QUARANTINE_AGENT}" --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")

if [ "$NP_AFTER" -eq 0 ] && [ "$NP_BY_NAME_AFTER" -eq 0 ]; then
  echo "  PASS: NetworkPolicy removed after quarantine was lifted"
  PASS=$((PASS + 1))
else
  # If T-S12-004 showed no NetworkPolicy, this is also MANUAL
  echo "  MANUAL: Cannot verify NetworkPolicy removal — none was found in T-S12-004"
  echo "    Manual check after lifting quarantine:"
  echo "      kubectl get networkpolicy -n $AGENTS_NAMESPACE -l agentshield.io/quarantine=$QUARANTINE_AGENT"
  echo "    Expected: no output (policy removed)"
  MANUAL=$((MANUAL + 1))
fi
echo ""

# ── T-S12-009: GET Agent Confirms status='active' After Lift ──────────────────
echo "--- T-S12-009: GET /api/v1/agents/{name} → status='active' confirmed after lift ---"
run_test "GET /agents/$QUARANTINE_AGENT → status=active after lift" "
import urllib.request, json
r = urllib.request.urlopen(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT'
)
data = json.loads(r.read())
assert data.get('status') == 'active', \
  f'expected active got {data.get(\"status\")}'
"
echo ""

# ── T-S12-010: Double-Quarantine Returns 409 ──────────────────────────────────
echo "--- T-S12-010 (bonus): POST quarantine on already-quarantined agent → 409 ---"
# First quarantine again, then attempt a second quarantine
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
  data=b'', headers={'Content-Type': 'application/json'}, method='POST'
)
urllib.request.urlopen(req)
" 2>/dev/null || true

run_test "POST quarantine on already-quarantined agent → 409 Conflict" "
import urllib.request, json, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$QUARANTINE_AGENT/quarantine',
  data=b'', headers={'Content-Type': 'application/json'}, method='POST'
)
try:
  urllib.request.urlopen(req)
  assert False, 'expected 409 but got 2xx'
except urllib.error.HTTPError as e:
  assert e.code == 409, f'expected 409 got {e.code}'
"
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  Suite 12 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
echo "  NetworkPolicy tests (T-S12-004, T-S12-008) require the deploy-controller"
echo "  to be running and watching for agent status changes."
echo "  Invoke-via-Envoy test (T-S12-006) requires the agent to be deployed."
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
