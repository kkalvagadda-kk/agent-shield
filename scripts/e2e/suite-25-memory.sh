#!/usr/bin/env bash
# Suite 25: Agent Memory (CRUD + clear + disabled guard)
# Tests T-S25-001 through T-S25-006
#
# Validates:
#   - Save a conversation turn to an agent with memory_enabled
#   - List memory (all + filtered by thread)
#   - Memory-disabled agent rejects saves with 400
#   - Delete a thread (GDPR) + clear all memory
#
# Usage:
#   bash scripts/e2e/suite-25-memory.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
AGENT_NAME="mem-test-${TS}"
NOMEM_AGENT="mem-nomem-${TS}"
THREAD_ID="thread-${TS}"

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
  for a in "${AGENT_NAME}" "${NOMEM_AGENT}"; do
    kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${a}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
  done
}
trap cleanup EXIT

echo "=== Suite 25: Agent Memory ==="

# ---------------------------------------------------------------------------
# Setup: create a memory-enabled agent + a memory-disabled agent
# ---------------------------------------------------------------------------
echo "--- Setup: create memory-enabled + memory-disabled agents ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${AGENT_NAME}', 'team': 'platform',
    'description': 'Memory test agent', 'agent_type': 'declarative',
    'memory_enabled': True,
})
assert r.status_code == 201, f'setup mem agent failed: {r.status_code} {r.text}'
r2 = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${NOMEM_AGENT}', 'team': 'platform', 'agent_type': 'declarative',
})
assert r2.status_code == 201, f'setup nomem agent failed: {r2.status_code} {r2.text}'
print('OK')
" || { echo "FATAL: setup failed"; exit 1; }

# ---------------------------------------------------------------------------
# T-S25-001 — Save a turn to memory
# ---------------------------------------------------------------------------
echo "--- T-S25-001: Save a conversation turn ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory', json={
    'thread_id': '${THREAD_ID}', 'user_id': 'test-user',
    'messages': [
        {'role': 'user', 'content': 'Hello, remember me'},
        {'role': 'assistant', 'content': 'I will remember you!'},
    ],
})
if r.status_code != 201:
    print(f'FAIL: expected 201, got {r.status_code}: {r.text}'); sys.exit(1)
data = r.json()
if len(data) != 2:
    print(f'FAIL: expected 2 messages, got {len(data)}'); sys.exit(1)
if data[0]['role'] != 'user' or data[1]['role'] != 'assistant':
    print(f'FAIL: unexpected roles: {[m[\"role\"] for m in data]}'); sys.exit(1)
print('OK')
" && pass "T-S25-001 — save turn" || fail "T-S25-001"

# ---------------------------------------------------------------------------
# T-S25-002 — List memory for a thread
# The transcript store is conversation-keyed (POC-0), so a read is scoped to a
# thread_id rather than an agent-wide "list all".
# ---------------------------------------------------------------------------
echo "--- T-S25-002: List memory for a thread ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory', params={'thread_id': '${THREAD_ID}'})
if r.status_code != 200:
    print(f'FAIL: expected 200, got {r.status_code}: {r.text}'); sys.exit(1)
if len(r.json()) < 2:
    print(f'FAIL: expected >=2 messages, got {len(r.json())}'); sys.exit(1)
print('OK')
" && pass "T-S25-002 — list thread" || fail "T-S25-002"

# ---------------------------------------------------------------------------
# T-S25-003 — List memory filtered by thread
# ---------------------------------------------------------------------------
echo "--- T-S25-003: List memory filtered by thread ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory', params={'thread_id': '${THREAD_ID}'})
if r.status_code != 200:
    print(f'FAIL: expected 200, got {r.status_code}: {r.text}'); sys.exit(1)
data = r.json()
if len(data) != 2:
    print(f'FAIL: expected 2 messages, got {len(data)}'); sys.exit(1)
if not all(m['thread_id'] == '${THREAD_ID}' for m in data):
    print('FAIL: thread filter returned wrong thread'); sys.exit(1)
print('OK')
" && pass "T-S25-003 — list filtered by thread" || fail "T-S25-003"

# ---------------------------------------------------------------------------
# T-S25-004 — Memory-disabled agent rejects save with 400
# ---------------------------------------------------------------------------
echo "--- T-S25-004: Memory-disabled agent returns 400 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/${NOMEM_AGENT}/memory', json={
    'thread_id': 'x', 'messages': [{'role': 'user', 'content': 'hi'}],
})
if r.status_code != 400:
    print(f'FAIL: expected 400, got {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" && pass "T-S25-004 — disabled agent 400" || fail "T-S25-004"

# ---------------------------------------------------------------------------
# T-S25-005 — Delete thread (GDPR)
# ---------------------------------------------------------------------------
echo "--- T-S25-005: Delete thread ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.delete('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory/${THREAD_ID}')
if r.status_code != 204:
    print(f'FAIL: expected 204, got {r.status_code}: {r.text}'); sys.exit(1)
r2 = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory', params={'thread_id': '${THREAD_ID}'})
if r2.status_code != 200 or len(r2.json()) != 0:
    print(f'FAIL: thread not empty after delete: {r2.status_code} {r2.text}'); sys.exit(1)
print('OK')
" && pass "T-S25-005 — delete thread" || fail "T-S25-005"

# ---------------------------------------------------------------------------
# T-S25-006 — Clear all memory
# ---------------------------------------------------------------------------
echo "--- T-S25-006: Clear all memory ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory', json={
    'thread_id': 'thread-clear', 'messages': [{'role': 'user', 'content': 'data to clear'}],
})
if r.status_code != 201:
    print(f'FAIL: seed for clear failed: {r.status_code} {r.text}'); sys.exit(1)
r2 = httpx.delete('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory/clear')
if r2.status_code != 204:
    print(f'FAIL: expected 204, got {r2.status_code}: {r2.text}'); sys.exit(1)
r3 = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/memory')
if r3.status_code != 200 or len(r3.json()) != 0:
    print(f'FAIL: memory not empty after clear: {r3.status_code} {r3.text}'); sys.exit(1)
print('OK')
" && pass "T-S25-006 — clear all" || fail "T-S25-006"

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Suite 25 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
