#!/usr/bin/env bash
# Suite 22: Event-Driven Playground
# Tests T-S22-001 through T-S22-004
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
EVENT_AGENT="event-pg-${TS}"

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
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${EVENT_AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 22: Event-Driven Playground"
echo ""

# T-S22-001 — Create event-driven agent + webhook trigger with filter
echo "--- T-S22-001: Create event-driven agent + webhook trigger ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${EVENT_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'metadata': {'instructions': 'event test'},
})
if r.status_code != 201:
    print(f'FAIL: create returned {r.status_code}: {r.text}')
    sys.exit(1)

t = httpx.post('http://localhost:8000/api/v1/agents/${EVENT_AGENT}/triggers', json={
    'trigger_type': 'webhook',
    'filter_conditions': [
        {'field': 'event', 'op': 'eq', 'value': 'push'},
        {'field': 'repository', 'op': 'exists'},
    ],
})
if t.status_code != 201:
    print(f'FAIL: trigger create returned {t.status_code}: {t.text}')
    sys.exit(1)

print('OK')
" && pass "T-S22-001 — event agent + webhook trigger" || fail "T-S22-001"

# T-S22-002 — Test event with matching payload creates run
echo "--- T-S22-002: Matching payload creates run ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/playground/test-event', json={
    'agent_name': '${EVENT_AGENT}',
    'payload': {'event': 'push', 'repository': 'my-repo', 'branch': 'main'},
})
if r.status_code != 200:
    print(f'FAIL: test-event returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if not data.get('matched'):
    print(f'FAIL: expected matched=true, got {data}')
    sys.exit(1)
if 'run_id' not in data:
    print(f'FAIL: matched but no run_id')
    sys.exit(1)

print('OK')
" && pass "T-S22-002 — matching payload creates run" || fail "T-S22-002"

# T-S22-003 — Non-matching payload returns matched=false
echo "--- T-S22-003: Non-matching payload returns filtered ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/playground/test-event', json={
    'agent_name': '${EVENT_AGENT}',
    'payload': {'event': 'pull_request', 'action': 'opened'},
})
if r.status_code != 200:
    print(f'FAIL: test-event returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if data.get('matched'):
    print(f'FAIL: expected matched=false for non-matching payload')
    sys.exit(1)

print('OK')
" && pass "T-S22-003 — non-matching payload filtered" || fail "T-S22-003"

# T-S22-004 — No webhook triggers returns matched=false
echo "--- T-S22-004: Agent without triggers returns not matched ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create a bare agent with no triggers
bare = 'bare-${TS}'
httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': bare,
    'team': 'default',
    'agent_type': 'declarative',
    'metadata': {'instructions': 'no triggers'},
})

r = httpx.post('http://localhost:8000/api/v1/playground/test-event', json={
    'agent_name': bare,
    'payload': {'event': 'push'},
})
if r.status_code != 200:
    print(f'FAIL: test-event returned {r.status_code}: {r.text}')
    sys.exit(1)

data = r.json()
if data.get('matched'):
    print('FAIL: expected matched=false for agent with no triggers')
    sys.exit(1)

# Cleanup bare agent
httpx.delete(f'http://localhost:8000/api/v1/agents/{bare}')
print('OK')
" && pass "T-S22-004 — no triggers returns not matched" || fail "T-S22-004"

echo ""
echo "==> Suite 22 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
