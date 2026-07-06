#!/usr/bin/env bash
# Suite 20: Durable Playground
# Tests T-S20-001 through T-S20-004
#
# Validates:
#   - Durable agent creation + sandbox deploy
#   - POST /playground/runs with execution_shape=durable returns run_id
#   - Step update callback creates run_steps rows
#   - Playground run steps endpoint returns step data
#
# Usage:
#   bash scripts/e2e/suite-20-durable-playground.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
DURABLE_AGENT="durable-pg-${TS}"

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
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 20: Durable Playground"
echo "    Namespace: $NAMESPACE"
echo "    API Pod:   $API_POD"
echo ""

# ---------------------------------------------------------------------------
# T-S20-001 — Create durable agent
# ---------------------------------------------------------------------------
echo "--- T-S20-001: Create durable agent ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${DURABLE_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'execution_shape': 'durable',
    'metadata': {'instructions': 'durable playground test'},
})
if r.status_code != 201:
    print(f'FAIL: create returned {r.status_code}: {r.text}')
    sys.exit(1)

agent = r.json()
if agent.get('execution_shape') != 'durable':
    print(f'FAIL: expected durable, got {agent.get(\"execution_shape\")}')
    sys.exit(1)
print('OK')
" && pass "T-S20-001 — create durable agent" || fail "T-S20-001"

# ---------------------------------------------------------------------------
# T-S20-002 — POST /playground/runs with durable shape returns run_id
# ---------------------------------------------------------------------------
echo "--- T-S20-002: Launch durable playground run ---"
RUN_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/playground/runs', json={
    'agent_name': '${DURABLE_AGENT}',
    'execution_shape': 'durable',
    'input_payload': {'message': 'test durable'},
})
if r.status_code != 201:
    print(f'FAIL: create run returned {r.status_code}: {r.text}', file=sys.stderr)
    sys.exit(1)

data = r.json()
if 'run_id' not in data:
    print('FAIL: missing run_id in response', file=sys.stderr)
    sys.exit(1)
if data.get('execution_shape') != 'durable':
    print(f'FAIL: expected execution_shape=durable, got {data.get(\"execution_shape\")}', file=sys.stderr)
    sys.exit(1)

print(data['run_id'])
" 2>/dev/null) && pass "T-S20-002 — durable playground run created" || fail "T-S20-002"

# ---------------------------------------------------------------------------
# T-S20-003 — Step update callback creates run_steps
# ---------------------------------------------------------------------------
echo "--- T-S20-003: Step update callback ---"
if [ -n "${RUN_ID:-}" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

run_id = '${RUN_ID}'

# Post step update
r1 = httpx.post(f'http://localhost:8000/api/v1/playground/runs/{run_id}/step-update', json={
    'step_number': 1,
    'step_name': 'test_step',
    'status': 'completed',
    'output': {'result': 'ok'},
})
if r1.status_code != 200:
    print(f'FAIL: step-update returned {r1.status_code}: {r1.text}')
    sys.exit(1)

# Read steps
r2 = httpx.get(f'http://localhost:8000/api/v1/playground/runs/{run_id}/steps')
if r2.status_code != 200:
    print(f'FAIL: steps returned {r2.status_code}: {r2.text}')
    sys.exit(1)

steps = r2.json()
if len(steps) < 1:
    print(f'FAIL: expected >=1 step, got {len(steps)}')
    sys.exit(1)

if steps[0].get('name') != 'test_step':
    print(f'FAIL: expected step name test_step, got {steps[0].get(\"name\")}')
    sys.exit(1)

print('OK')
" && pass "T-S20-003 — step update callback" || fail "T-S20-003"
else
  fail "T-S20-003 — skipped (no run_id from T-S20-002)"
fi

# ---------------------------------------------------------------------------
# T-S20-004 — GET /playground/runs/{id} reflects durable shape
# ---------------------------------------------------------------------------
echo "--- T-S20-004: GET run reflects durable shape ---"
if [ -n "${RUN_ID:-}" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

run_id = '${RUN_ID}'
r = httpx.get(f'http://localhost:8000/api/v1/playground/runs/{run_id}')
if r.status_code != 200:
    print(f'FAIL: get run returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if data.get('execution_shape') != 'durable':
    print(f'FAIL: expected durable, got {data.get(\"execution_shape\")}')
    sys.exit(1)

print('OK')
" && pass "T-S20-004 — GET run reflects durable shape" || fail "T-S20-004"
else
  fail "T-S20-004 — skipped (no run_id from T-S20-002)"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Suite 20 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
