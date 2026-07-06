#!/usr/bin/env bash
# Suite 21: Scheduled Playground
# Tests T-S21-001 through T-S21-003
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
SCHED_AGENT="sched-pg-${TS}"

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
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${SCHED_AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 21: Scheduled Playground"
echo ""

# T-S21-001 — Create scheduled agent + trigger
echo "--- T-S21-001: Create scheduled agent + trigger ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${SCHED_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'metadata': {'instructions': 'scheduled test'},
})
if r.status_code != 201:
    print(f'FAIL: create returned {r.status_code}: {r.text}')
    sys.exit(1)

t = httpx.post('http://localhost:8000/api/v1/agents/${SCHED_AGENT}/triggers', json={
    'trigger_type': 'schedule',
    'cron_expression': '0 */6 * * *',
})
if t.status_code != 201:
    print(f'FAIL: trigger create returned {t.status_code}: {t.text}')
    sys.exit(1)

print('OK')
" && pass "T-S21-001 — scheduled agent + trigger" || fail "T-S21-001"

# T-S21-002 — Run Now via POST /playground/runs
echo "--- T-S21-002: Run Now creates playground run ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/playground/runs', json={
    'agent_name': '${SCHED_AGENT}',
    'input_message': 'Manual test-fire',
})
if r.status_code != 201:
    print(f'FAIL: create run returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if 'run_id' not in data:
    print('FAIL: missing run_id')
    sys.exit(1)

print('OK')
" && pass "T-S21-002 — run now" || fail "T-S21-002"

# T-S21-003 — Multiple test-fires appear in run history
echo "--- T-S21-003: Multiple test-fires appear in history ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create second run
r2 = httpx.post('http://localhost:8000/api/v1/playground/runs', json={
    'agent_name': '${SCHED_AGENT}',
    'input_message': 'Second test-fire',
})
if r2.status_code != 201:
    print(f'FAIL: second run returned {r2.status_code}')
    sys.exit(1)

# List runs — pass the caller identity (playground runs are user-scoped; runs
# created without an explicit X-User-Sub default to user_id='dev', and the list
# is deny-by-default for anonymous callers).
runs = httpx.get('http://localhost:8000/api/v1/playground/runs', headers={'X-User-Sub': 'dev'})
if runs.status_code != 200:
    print(f'FAIL: list returned {runs.status_code}')
    sys.exit(1)

agent_runs = [r for r in runs.json() if r.get('agent_name') == '${SCHED_AGENT}']
if len(agent_runs) < 2:
    print(f'FAIL: expected >=2 runs, got {len(agent_runs)}')
    sys.exit(1)

print('OK')
" && pass "T-S21-003 — multiple test-fires in history" || fail "T-S21-003"

echo ""
echo "==> Suite 21 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
