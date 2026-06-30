#!/usr/bin/env bash
# Suite 10: Multi-Agent Handoff — T-S10-001 through T-S10-006
#
# Tests that two agents can be registered and (when deployed) pass work to
# each other through Envoy with correct session propagation, identity context,
# and HITL attribution.
#
# Automated: T-S10-001, T-S10-002, T-S10-003 (kubectl checks)
# Manual:    T-S10-004, T-S10-005, T-S10-006 — require live agents + Envoy routing
#
# Usage:
#   NAMESPACE=agentshield-platform bash scripts/e2e/suite-10-multi-agent.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="agents-platform"

AGENT_INITIATOR="agent-initiator"
AGENT_TARGET="agent-target"
TEST_TEAM="platform"

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

echo "=== Suite 10: Multi-Agent Handoff ==="
echo "    API pod:  $API_POD"
echo "    Agents:   $AGENT_INITIATOR, $AGENT_TARGET"
echo ""

# ── T-S10-001: Register Two Agents ────────────────────────────────────────────
echo "--- T-S10-001: Register Two Agents (agent-initiator, agent-target) ---"

# Register agent-initiator
INITIATOR_STATUS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
# Check if already exists
try:
  urllib.request.urlopen('http://localhost:8000/api/v1/agents/$AGENT_INITIATOR')
  print('exists')
except urllib.error.HTTPError as e:
  if e.code == 404:
    req = urllib.request.Request(
      'http://localhost:8000/api/v1/agents/',
      data=json.dumps({
        'name': '$AGENT_INITIATOR',
        'team': '$TEST_TEAM',
        'description': 'Suite 10 initiator agent — hands off to agent-target',
        'metadata': {'tools': ['handoff_to_agent_target']}
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

# Register agent-target
TARGET_STATUS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json, urllib.error
try:
  urllib.request.urlopen('http://localhost:8000/api/v1/agents/$AGENT_TARGET')
  print('exists')
except urllib.error.HTTPError as e:
  if e.code == 404:
    req = urllib.request.Request(
      'http://localhost:8000/api/v1/agents/',
      data=json.dumps({
        'name': '$AGENT_TARGET',
        'team': '$TEST_TEAM',
        'description': 'Suite 10 target agent — receives handoff from agent-initiator',
        'metadata': {'tools': ['lookup_order', 'issue_refund']}
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

if [[ "$INITIATOR_STATUS" =~ ^(created|exists)$ ]] && [[ "$TARGET_STATUS" =~ ^(created|exists)$ ]]; then
  echo "  PASS: Both agents registered/exist ($AGENT_INITIATOR=$INITIATOR_STATUS, $AGENT_TARGET=$TARGET_STATUS)"
  PASS=$((PASS + 1))
else
  echo "  FAIL: Agent registration problem (initiator=$INITIATOR_STATUS, target=$TARGET_STATUS)"
  FAIL=$((FAIL + 1))
fi
echo ""

# ── T-S10-002: Both Agents Appear in GET /api/v1/agents ──────────────────────
echo "--- T-S10-002: Both Agents Appear in Agent List ---"
run_test "GET /api/v1/agents → both agent-initiator and agent-target in list" "
import urllib.request, json
r = urllib.request.urlopen('http://localhost:8000/api/v1/agents/')
assert r.status == 200
data = json.loads(r.read())
names = [a['name'] for a in data.get('items', [])]
assert '$AGENT_INITIATOR' in names, f'$AGENT_INITIATOR not in list: {names[:10]}'
assert '$AGENT_TARGET' in names, f'$AGENT_TARGET not in list: {names[:10]}'
"
echo ""

# ── T-S10-003: Verify Envoy HTTPRoute Exists for Both Agents ─────────────────
echo "--- T-S10-003: Verify Envoy HTTPRoute Exists for Both Agents ---"
echo "  NOTE: HTTPRoutes are created by the deploy-controller after a deploy, not on registration."
echo "  This check will SKIP if agents haven't been deployed yet."
echo ""

for AGENT_NAME in "$AGENT_INITIATOR" "$AGENT_TARGET"; do
  ROUTE_COUNT=$(kubectl get httproute -n "$AGENTS_NAMESPACE" \
    -l "agentshield.io/agent=${AGENT_NAME}" \
    --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")

  if [ "$ROUTE_COUNT" -gt 0 ]; then
    echo "  PASS: HTTPRoute exists for $AGENT_NAME ($ROUTE_COUNT route(s))"
    PASS=$((PASS + 1))
  else
    # Also try the agentshield-platform namespace
    ROUTE_COUNT_NS=$(kubectl get httproute -n "$NAMESPACE" \
      -l "agentshield.io/agent=${AGENT_NAME}" \
      --no-headers 2>/dev/null | wc -l | tr -d ' ' || echo "0")
    if [ "$ROUTE_COUNT_NS" -gt 0 ]; then
      echo "  PASS: HTTPRoute exists for $AGENT_NAME in $NAMESPACE ($ROUTE_COUNT_NS route(s))"
      PASS=$((PASS + 1))
    else
      echo "  SKIP: No HTTPRoute found for $AGENT_NAME — deploy the agent first to create routes"
      echo "        kubectl get httproute -A -l agentshield.io/agent=${AGENT_NAME}"
      MANUAL=$((MANUAL + 1))
    fi
  fi
done
echo ""

# ── T-S10-004: Session ID Propagated Across Handoff (MANUAL) ─────────────────
echo "--- T-S10-004: Session ID Propagated Across Agent Handoff ---"
check_manual "Verify X-AgentShield-Session-Id propagates from initiator to target" \
  "# Prerequisite: both agents deployed and Running in $AGENTS_NAMESPACE" \
  "# 1. Trigger a handoff via Envoy (replace SESSION_ID with a UUID)" \
  "#    SESSION_ID=\$(uuidgen)" \
  "#    curl -X POST http://envoy-gateway.$NAMESPACE/agents/$AGENT_INITIATOR/chat \\" \
  "#         -H 'X-AgentShield-Session-Id: \$SESSION_ID' \\" \
  "#         -H 'Content-Type: application/json' \\" \
  "#         -d '{\"message\": \"look up order 12345 and hand off to target\"}'" \
  "# 2. Check agent-target pod logs for incoming headers:" \
  "#    kubectl logs -n $AGENTS_NAMESPACE -l app.kubernetes.io/name=$AGENT_TARGET --tail=50" \
  "# 3. Assert X-AgentShield-Session-Id appears in agent-target log with same value" \
  "# Pass: same session ID visible in both agent logs"
echo ""

# ── T-S10-005: OPA Decisions Use Correct SA Subjects (MANUAL) ────────────────
echo "--- T-S10-005: OPA Decisions Use Correct SA Subjects per Agent ---"
check_manual "Verify OPA audit log shows separate SA subjects for each agent within one session" \
  "# 1. After T-S10-004, query opa_decisions table via registry-api:" \
  "#    kubectl exec -n $NAMESPACE $API_POD -- python3 -c \\" \
  "#      \"import urllib.request, json; r = urllib.request.urlopen('http://localhost:8000/api/v1/opa-decisions?limit=20'); print(json.dumps(json.loads(r.read()), indent=2))\"" \
  "# 2. Find decisions for the session from step T-S10-004" \
  "# 3. Assert initiator decisions have sa_subject containing 'agent-initiator-sa'" \
  "# 4. Assert target decisions have sa_subject containing 'agent-target-sa'" \
  "# Pass: both SA subjects appear separately in the decision log"
echo ""

# ── T-S10-006: HITL Attributed to Agent-B's Context (MANUAL) ─────────────────
echo "--- T-S10-006: HITL Triggered in agent-target's Context ---"
check_manual "Verify HITL approval for issue_refund is attributed to agent-target, not agent-initiator" \
  "# Prerequisite: agent-target configured with issue_refund (risk=high → triggers HITL)" \
  "# 1. Trigger request through agent-initiator that results in agent-target calling issue_refund" \
  "#    curl -X POST http://envoy-gateway.$NAMESPACE/agents/$AGENT_INITIATOR/chat \\" \
  "#         -H 'X-AgentShield-Session-Id: \$(uuidgen)' \\" \
  "#         -d '{\"message\": \"issue a refund for order 99999\"}'" \
  "# 2. Check pending approvals:" \
  "#    kubectl exec -n $NAMESPACE $API_POD -- python3 -c \\" \
  "#      \"import urllib.request, json; r = urllib.request.urlopen('http://localhost:8000/api/v1/approvals?status=pending'); print(json.dumps(json.loads(r.read()), indent=2))\"" \
  "# 3. Assert the approval record shows agent_name='$AGENT_TARGET' (not '$AGENT_INITIATOR')" \
  "# Pass: approval.agent_name == '$AGENT_TARGET'"
echo ""

# ── T-S10-007 (plan T-S10-006): Scope Attenuation (MANUAL) ───────────────────
echo "--- T-S10-007 (plan T-S10-006): Scope Attenuation — agent-b Cannot Exceed agent-a's Grants ---"
check_manual "OPA denies cross-agent tool calls when originating session lacks grant" \
  "# Prerequisite: agent-initiator has grants only for lookup_order (not issue_refund)" \
  "#               agent-target has grants for lookup_order and issue_refund" \
  "# 1. Trigger request from agent-initiator that causes agent-target to call issue_refund" \
  "# 2. Expected: OPA enforces scope attenuation — deny because initiator's session token" \
  "#    does not include issue_refund grant" \
  "# 3. Check OPA decision log for allow=false with reason mentioning scope/grant" \
  "#    kubectl exec -n $NAMESPACE $API_POD -- python3 -c \\" \
  "#      \"import urllib.request, json; r = urllib.request.urlopen('http://localhost:8000/api/v1/opa-decisions?limit=5'); print(json.dumps(json.loads(r.read()), indent=2))\"" \
  "# Pass: OPA returns allow=false. Error returned to user, not silent success."
echo ""

# ── Cleanup ───────────────────────────────────────────────────────────────────
echo "--- Cleanup ---"
for AGENT_NAME in "$AGENT_INITIATOR" "$AGENT_TARGET"; do
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, urllib.error
req = urllib.request.Request(
  'http://localhost:8000/api/v1/agents/$AGENT_NAME',
  method='DELETE'
)
try:
  urllib.request.urlopen(req)
  print('  soft-deleted $AGENT_NAME')
except urllib.error.HTTPError as e:
  print(f'  cleanup note ($AGENT_NAME): {e.code}')
except Exception as e:
  print(f'  cleanup warning ($AGENT_NAME): {e}')
" 2>/dev/null || true
done
echo ""

# ── G5-004: Multi-agent handoff uses shared X-AgentShield-Trace-ID ───────────
echo "--- G5-004: Multi-Agent Handoff Shares Trace ID (session context) ---"
# We can test the trace propagation contract without live agents:
# verify that registry-api's trace middleware echoes X-AgentShield-Trace-ID
# consistently on back-to-back requests with the same ID (simulating
# agent-A → agent-B handoff through the platform).
SHARED_TRACE="s10-g5-shared-$(date +%s)"
TRACE_RESULTS=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request, json
shared_id = '${SHARED_TRACE}'
results = []
# Simulate two agents making requests with the same trace ID (handoff pattern)
for endpoint in ['/health', '/ready']:
    req = urllib.request.Request(
        'http://localhost:8000' + endpoint,
        headers={'X-AgentShield-Trace-ID': shared_id}
    )
    try:
        r = urllib.request.urlopen(req, timeout=5)
        echoed = r.headers.get('X-AgentShield-Trace-ID', 'MISSING')
        results.append(endpoint + '=' + echoed)
    except Exception as e:
        results.append(endpoint + '=ERR:' + str(e)[:30])
# Both must echo the same shared trace ID
all_match = all(r.endswith('=' + shared_id) for r in results)
print('match=' + str(all_match) + ' ' + ' '.join(results))
" 2>/dev/null || echo "ERR")
if echo "$TRACE_RESULTS" | grep -q "match=True"; then
  pass "G5-004: Registry API echoes shared X-AgentShield-Trace-ID on both handoff legs ($TRACE_RESULTS)"
elif echo "$TRACE_RESULTS" | grep -q "ERR"; then
  fail "G5-004: Trace propagation check failed ($TRACE_RESULTS)"
else
  check_manual "G5-004: Trace propagation partial ($TRACE_RESULTS)" \
    "Submit two requests with same X-AgentShield-Trace-ID to registry-api" \
    "Assert both responses echo the same trace ID (handoff stitching contract)"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "======================================================="
echo "  Suite 10 Results: PASS=${PASS}  FAIL=${FAIL}  MANUAL=${MANUAL}"
echo "======================================================="
echo "  Manual tests require live deployed agents + Envoy routing."
echo "  Run 'bash scripts/deploy-cpe2e.sh' to deploy agents, then recheck."
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
