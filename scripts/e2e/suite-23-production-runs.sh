#!/usr/bin/env bash
# Suite 23: Production Runs (AgentRun tracking)
# Tests T-S23-001 through T-S23-004
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
PROD_AGENT="prod-run-${TS}"

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
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${PROD_AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 23: Production Runs"
echo ""

# T-S23-001 — Create agent_run via POST /agent-runs (simulating production invoke)
echo "--- T-S23-001: Create agent_run row ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create an agent first
r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${PROD_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'metadata': {'instructions': 'prod test'},
})
if r.status_code != 201:
    print(f'FAIL: create agent returned {r.status_code}: {r.text}')
    sys.exit(1)

# Create an agent_run
ar = httpx.post('http://localhost:8000/api/v1/agent-runs', json={
    'agent_name': '${PROD_AGENT}',
    'user_id': 'test-user-prod',
    'input': 'Hello production',
    'context': 'production',
    'trigger_type': 'api',
    'run_by': 'test-user-prod',
    'team': 'default',
})
if ar.status_code != 201:
    print(f'FAIL: create agent_run returned {ar.status_code}: {ar.text}')
    sys.exit(1)

data = ar.json()
if data.get('context') != 'production':
    print(f'FAIL: context should be production, got {data.get(\"context\")}')
    sys.exit(1)
if data.get('trigger_type') != 'api':
    print(f'FAIL: trigger_type should be api, got {data.get(\"trigger_type\")}')
    sys.exit(1)

print('OK')
" && pass "T-S23-001 — create agent_run with context=production" || fail "T-S23-001"

# T-S23-002 — GET /agent-runs?agent_name=X returns the run
echo "--- T-S23-002: List agent runs filtered by agent_name ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.get('http://localhost:8000/api/v1/agent-runs', params={
    'agent_name': '${PROD_AGENT}',
})
if r.status_code != 200:
    print(f'FAIL: list returned {r.status_code}: {r.text}')
    sys.exit(1)

runs = r.json()
if len(runs) < 1:
    print(f'FAIL: expected >=1 run, got {len(runs)}')
    sys.exit(1)

if runs[0]['agent_name'] != '${PROD_AGENT}':
    print(f'FAIL: wrong agent_name: {runs[0][\"agent_name\"]}')
    sys.exit(1)

print('OK')
" && pass "T-S23-002 — list agent_runs by agent_name" || fail "T-S23-002"

# T-S23-003 — GET /agents/{name}/stats returns metrics
echo "--- T-S23-003: Stats endpoint returns run_count >= 1 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.get('http://localhost:8000/api/v1/agents/${PROD_AGENT}/stats')
if r.status_code != 200:
    print(f'FAIL: stats returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if data.get('run_count', 0) < 1:
    print(f'FAIL: expected run_count >= 1, got {data.get(\"run_count\")}')
    sys.exit(1)

if 'error_rate' not in data:
    print('FAIL: missing error_rate field')
    sys.exit(1)

print('OK')
" && pass "T-S23-003 — stats returns run_count >= 1" || fail "T-S23-003"

# T-S23-004 — Filter by trigger_type returns correct subset
echo "--- T-S23-004: Filter by trigger_type=schedule returns empty ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.get('http://localhost:8000/api/v1/agent-runs', params={
    'agent_name': '${PROD_AGENT}',
    'trigger_type': 'schedule',
})
if r.status_code != 200:
    print(f'FAIL: list returned {r.status_code}: {r.text}')
    sys.exit(1)

runs = r.json()
if len(runs) != 0:
    print(f'FAIL: expected 0 schedule runs, got {len(runs)}')
    sys.exit(1)

print('OK')
" && pass "T-S23-004 — trigger_type filter returns empty for schedule" || fail "T-S23-004"

echo ""
echo "==> Suite 23 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
