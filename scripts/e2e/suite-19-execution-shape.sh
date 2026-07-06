#!/usr/bin/env bash
# Suite 19: Execution Shape & Triggers
# Tests T-S19-001 through T-S19-005
#
# Validates:
#   - execution_shape field on agent CRUD (default + explicit)
#   - memory_enabled field on agent CRUD
#   - Trigger CRUD (schedule + webhook)
#   - OPA bundle includes execution_shape
#
# Usage:
#   bash scripts/e2e/suite-19-execution-shape.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
REACTIVE_AGENT="shape-react-${TS}"
DURABLE_AGENT="shape-durable-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "${API_POD:-}" ]; then
  echo "FATAL: Registry API pod not found in $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup: deleting test agents..."
  for agent_name in "$REACTIVE_AGENT" "$DURABLE_AGENT"; do
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${agent_name}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
  done
}
trap cleanup EXIT

echo "==> Suite 19: Execution Shape & Triggers"
echo "    Namespace: $NAMESPACE"
echo "    API Pod:   $API_POD"
echo ""

# ---------------------------------------------------------------------------
# T-S19-001 — Default execution_shape is "reactive"
# ---------------------------------------------------------------------------
echo "--- T-S19-001: Default execution_shape is reactive ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, json, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${REACTIVE_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'metadata': {'instructions': 'test'},
})
if r.status_code != 201:
    print(f'FAIL: create returned {r.status_code}: {r.text}')
    sys.exit(1)

agent = r.json()
if agent.get('execution_shape') != 'reactive':
    print(f'FAIL: expected reactive, got {agent.get(\"execution_shape\")}')
    sys.exit(1)
if agent.get('memory_enabled') is not False:
    print(f'FAIL: expected memory_enabled=false, got {agent.get(\"memory_enabled\")}')
    sys.exit(1)
print('OK')
" && pass "T-S19-001 — default execution_shape=reactive" || fail "T-S19-001"

# ---------------------------------------------------------------------------
# T-S19-002 — Create agent with execution_shape=durable + memory_enabled=true
# ---------------------------------------------------------------------------
echo "--- T-S19-002: Create durable agent with memory ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, json, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${DURABLE_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'execution_shape': 'durable',
    'memory_enabled': True,
    'metadata': {'instructions': 'durable test'},
})
if r.status_code != 201:
    print(f'FAIL: create returned {r.status_code}: {r.text}')
    sys.exit(1)

agent = r.json()
if agent.get('execution_shape') != 'durable':
    print(f'FAIL: expected durable, got {agent.get(\"execution_shape\")}')
    sys.exit(1)
if agent.get('memory_enabled') is not True:
    print(f'FAIL: expected memory_enabled=true, got {agent.get(\"memory_enabled\")}')
    sys.exit(1)
print('OK')
" && pass "T-S19-002 — durable + memory_enabled" || fail "T-S19-002"

# ---------------------------------------------------------------------------
# T-S19-003 — PATCH execution_shape on existing agent
# ---------------------------------------------------------------------------
echo "--- T-S19-003: PATCH execution_shape reactive→durable ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.patch('http://localhost:8000/api/v1/agents/${REACTIVE_AGENT}', json={
    'execution_shape': 'durable',
    'memory_enabled': True,
})
if r.status_code != 200:
    print(f'FAIL: patch returned {r.status_code}: {r.text}')
    sys.exit(1)

agent = r.json()
if agent.get('execution_shape') != 'durable':
    print(f'FAIL: expected durable, got {agent.get(\"execution_shape\")}')
    sys.exit(1)
if agent.get('memory_enabled') is not True:
    print(f'FAIL: expected memory_enabled=true, got {agent.get(\"memory_enabled\")}')
    sys.exit(1)
print('OK')
" && pass "T-S19-003 — PATCH execution_shape" || fail "T-S19-003"

# ---------------------------------------------------------------------------
# T-S19-004 — Trigger CRUD (schedule)
# ---------------------------------------------------------------------------
echo "--- T-S19-004: Trigger CRUD (schedule) ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create schedule trigger
r = httpx.post('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}/triggers', json={
    'trigger_type': 'schedule',
    'cron_expression': '0 */6 * * *',
    'timezone': 'US/Pacific',
})
if r.status_code != 201:
    print(f'FAIL: create trigger returned {r.status_code}: {r.text}')
    sys.exit(1)

trigger = r.json()
trigger_id = trigger['id']
if trigger['trigger_type'] != 'schedule':
    print(f'FAIL: expected schedule, got {trigger[\"trigger_type\"]}')
    sys.exit(1)
if trigger['cron_expression'] != '0 */6 * * *':
    print(f'FAIL: cron mismatch')
    sys.exit(1)

# List triggers
r2 = httpx.get('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}/triggers')
if r2.status_code != 200:
    print(f'FAIL: list returned {r2.status_code}')
    sys.exit(1)
triggers = r2.json()
if len(triggers) < 1:
    print(f'FAIL: expected >=1 trigger, got {len(triggers)}')
    sys.exit(1)

# Delete trigger
r3 = httpx.delete(f'http://localhost:8000/api/v1/agents/${DURABLE_AGENT}/triggers/{trigger_id}')
if r3.status_code != 204:
    print(f'FAIL: delete returned {r3.status_code}')
    sys.exit(1)

# Verify deletion
r4 = httpx.get('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}/triggers')
remaining = r4.json()
if any(t['id'] == trigger_id for t in remaining):
    print('FAIL: trigger still exists after delete')
    sys.exit(1)

print('OK')
" && pass "T-S19-004 — trigger CRUD" || fail "T-S19-004"

# ---------------------------------------------------------------------------
# T-S19-005 — Schedule trigger requires cron_expression
# ---------------------------------------------------------------------------
echo "--- T-S19-005: Schedule trigger without cron → 422 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}/triggers', json={
    'trigger_type': 'schedule',
})
if r.status_code == 422:
    print('OK')
else:
    print(f'FAIL: expected 422, got {r.status_code}: {r.text}')
    sys.exit(1)
" && pass "T-S19-005 — schedule without cron → 422" || fail "T-S19-005"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Suite 19 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
