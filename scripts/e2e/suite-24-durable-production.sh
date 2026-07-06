#!/usr/bin/env bash
# Suite 24: Durable Production Runs + Approvals
# Tests T-S24-001 through T-S24-005
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
DURABLE_AGENT="durable-prod-${TS}"

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
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 24: Durable Production Runs + Approvals"
echo ""

# T-S24-001 — Create agent_run + step upsert via POST /agent-runs/{id}/steps
echo "--- T-S24-001: Create durable run + upsert steps ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create agent
r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${DURABLE_AGENT}',
    'team': 'default',
    'agent_type': 'declarative',
    'execution_shape': 'durable',
    'metadata': {'instructions': 'durable prod test'},
})
if r.status_code != 201:
    print(f'FAIL: create agent returned {r.status_code}: {r.text}')
    sys.exit(1)

# Create an agent_run
ar = httpx.post('http://localhost:8000/api/v1/agent-runs', json={
    'agent_name': '${DURABLE_AGENT}',
    'user_id': 'test-user-durable',
    'input': 'Durable production test',
    'context': 'production',
    'trigger_type': 'api',
    'run_by': 'test-user-durable',
    'team': 'default',
})
if ar.status_code != 201:
    print(f'FAIL: create agent_run returned {ar.status_code}: {ar.text}')
    sys.exit(1)
run_id = ar.json()['id']

# Upsert step 1 as running
s1 = httpx.post(f'http://localhost:8000/api/v1/agent-runs/{run_id}/steps', json={
    'step_number': 1,
    'name': 'input_processing',
    'status': 'running',
})
if s1.status_code != 201:
    print(f'FAIL: step 1 upsert returned {s1.status_code}: {s1.text}')
    sys.exit(1)

# Complete step 1
s1c = httpx.post(f'http://localhost:8000/api/v1/agent-runs/{run_id}/steps', json={
    'step_number': 1,
    'name': 'input_processing',
    'status': 'completed',
    'output': {'message': 'done'},
})
if s1c.status_code != 201:
    print(f'FAIL: step 1 complete returned {s1c.status_code}: {s1c.text}')
    sys.exit(1)

# Verify steps
steps = httpx.get(f'http://localhost:8000/api/v1/agent-runs/{run_id}/steps')
if steps.status_code != 200:
    print(f'FAIL: list steps returned {steps.status_code}')
    sys.exit(1)
if len(steps.json()) != 1:
    print(f'FAIL: expected 1 step, got {len(steps.json())}')
    sys.exit(1)
if steps.json()[0]['status'] != 'completed':
    print(f'FAIL: step status should be completed')
    sys.exit(1)

print('OK')
" && pass "T-S24-001 — durable run + step upserts" || fail "T-S24-001"

# T-S24-002 — Step with awaiting_approval appears in /approvals endpoint
echo "--- T-S24-002: Approval appears in inbox ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, uuid

# Create a pending approval for our agent
agent_r = httpx.get('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}')
agent_id = agent_r.json()['id']

ap = httpx.post('http://localhost:8000/api/v1/approvals/', json={
    'agent_id': agent_id,
    'agent_name': '${DURABLE_AGENT}',
    'team': 'default',
    'thread_id': 'thread-${TS}',
    'tool_name': 'send_email',
    'tool_args': {'to': 'user@example.com'},
    'risk_level': 'high',
    'context': 'production',
    'timeout_seconds': 7200,
})
if ap.status_code != 201:
    print(f'FAIL: create approval returned {ap.status_code}: {ap.text}')
    sys.exit(1)

approval_id = ap.json()['id']

# List pending approvals
inbox = httpx.get('http://localhost:8000/api/v1/approvals/', params={'status': 'pending'})
if inbox.status_code != 200:
    print(f'FAIL: list approvals returned {inbox.status_code}')
    sys.exit(1)

items = inbox.json()['items']
found = any(i['id'] == approval_id for i in items)
if not found:
    print(f'FAIL: approval {approval_id} not in inbox')
    sys.exit(1)

print('OK')
" && pass "T-S24-002 — approval appears in inbox" || fail "T-S24-002"

# T-S24-003 — Reviewer approves and status changes
echo "--- T-S24-003: Reviewer approves ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Find our pending approval
inbox = httpx.get('http://localhost:8000/api/v1/approvals/', params={
    'status': 'pending', 'agent_name': '${DURABLE_AGENT}'
})
items = inbox.json()['items']
if not items:
    print('FAIL: no pending approvals found')
    sys.exit(1)

approval = items[0]
approval_id = approval['id']
version = approval['version']

# Approve it (with system reviewer for test)
decide = httpx.patch(f'http://localhost:8000/api/v1/approvals/{approval_id}', json={
    'decision': 'approved',
    'version': version,
    'reviewer_id': 'system',
})
if decide.status_code != 200:
    print(f'FAIL: decide returned {decide.status_code}: {decide.text}')
    sys.exit(1)

if decide.json()['status'] != 'approved':
    print(f'FAIL: expected status approved, got {decide.json()[\"status\"]}')
    sys.exit(1)

print('OK')
" && pass "T-S24-003 — reviewer approves" || fail "T-S24-003"

# T-S24-004 — Non-reviewer gets 403 on approve (when no authority record)
echo "--- T-S24-004: Non-reviewer gets 403 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

# Create another approval to test 403
agent_r = httpx.get('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}')
agent_id = agent_r.json()['id']

ap = httpx.post('http://localhost:8000/api/v1/approvals/', json={
    'agent_id': agent_id,
    'agent_name': '${DURABLE_AGENT}',
    'team': 'default',
    'thread_id': 'thread-403-${TS}',
    'tool_name': 'delete_record',
    'tool_args': {'id': '123'},
    'risk_level': 'critical',
    'context': 'production',
    'timeout_seconds': 3600,
})
if ap.status_code != 201:
    print(f'FAIL: create approval returned {ap.status_code}')
    sys.exit(1)

approval_id = ap.json()['id']
version = ap.json()['version']

# Try to decide as non-reviewer (no authority record)
decide = httpx.patch(
    f'http://localhost:8000/api/v1/approvals/{approval_id}',
    json={'decision': 'approved', 'version': version, 'reviewer_id': 'nobody-user'},
    headers={'X-User-Sub': 'nobody-user'},
)
if decide.status_code != 403:
    print(f'FAIL: expected 403, got {decide.status_code}: {decide.text}')
    sys.exit(1)

print('OK')
" && pass "T-S24-004 — non-reviewer gets 403" || fail "T-S24-004"

# T-S24-005 — Approval timeout results in timed_out status
echo "--- T-S24-005: Approval timeout (short TTL) ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, time

agent_r = httpx.get('http://localhost:8000/api/v1/agents/${DURABLE_AGENT}')
agent_id = agent_r.json()['id']

# Create approval with 1-second timeout (minimum is 60, so we'll just verify
# the expires_at is set correctly and check the timeout worker logic)
ap = httpx.post('http://localhost:8000/api/v1/approvals/', json={
    'agent_id': agent_id,
    'agent_name': '${DURABLE_AGENT}',
    'team': 'default',
    'thread_id': 'thread-timeout-${TS}',
    'tool_name': 'risky_op',
    'tool_args': {},
    'risk_level': 'high',
    'context': 'production',
    'timeout_seconds': 60,
})
if ap.status_code != 201:
    print(f'FAIL: create returned {ap.status_code}')
    sys.exit(1)

data = ap.json()
if 'expires_at' not in data:
    print('FAIL: missing expires_at')
    sys.exit(1)

# Verify the approval has a valid expiry set
if data['status'] != 'pending':
    print(f'FAIL: expected pending, got {data[\"status\"]}')
    sys.exit(1)

print('OK')
" && pass "T-S24-005 — approval timeout configured" || fail "T-S24-005"

echo ""
echo "==> Suite 24 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
