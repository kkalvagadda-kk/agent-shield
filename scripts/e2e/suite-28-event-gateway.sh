#!/usr/bin/env bash
# Suite 28: Event Gateway (Phase 9)
# Tests T-S28-001 through T-S28-006
#
# Validates the public webhook ingress: token validation, filter matching,
# dispatch, rate limiting, replay/rotation, and the event log. On the local
# Docker Desktop cluster (no ingress controller) we hit the event-gateway
# Service in-cluster from the registry-api pod.
#
# Setup inserts a minimal agent_version + running deployment via SQL so the
# matched path can actually create a run (internal/runs/start requires a
# running deployment).
#
# Usage:
#   bash scripts/e2e/suite-28-event-gateway.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
AGENT_NAME="evt-test-${TS}"
GW="http://agentshield-event-gateway:8091"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

# Helper: run a python snippet inside the registry-api pod.
pyexec() { kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$1" 2>&1; }

cleanup() {
  echo ""
  echo "==> Cleanup: deleting test agent..."
  pyexec "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/${AGENT_NAME}', method='DELETE'), timeout=5)
except Exception: pass
" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== Suite 28: Event Gateway ==="

# ---------------------------------------------------------------------------
# Setup: agent + webhook trigger (with filter) + fake running deployment
# ---------------------------------------------------------------------------
echo "--- Setup: agent + webhook trigger + running deployment ---"
pyexec "
import asyncio, httpx, sys

async def main():
    # 1. agent
    r = httpx.post('http://localhost:8000/api/v1/agents/', json={
        'name': '${AGENT_NAME}', 'team': 'platform', 'agent_type': 'declarative',
        'execution_shape': 'reactive',
    })
    if r.status_code != 201:
        print('SETUP_FAIL create agent', r.status_code, r.text); sys.exit(1)
    # 2. webhook trigger with a filter (event == order.created)
    r2 = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/triggers', json={
        'trigger_type': 'webhook', 'enabled': True,
        'filter_conditions': [{'field': 'event', 'op': 'eq', 'value': 'order.created'}],
    })
    if r2.status_code not in (200, 201):
        print('SETUP_FAIL create trigger', r2.status_code, r2.text); sys.exit(1)
    body = r2.json()
    token = body.get('token')
    trigger_id = body['id']
    if not token:
        print('SETUP_FAIL no token in create response', body); sys.exit(1)

    # 3. minimal agent_version + running deployment via SQL
    from db import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as s:
        aid = (await s.execute(text(\"SELECT id FROM agents WHERE name=:n\"), {'n': '${AGENT_NAME}'})).scalar()
        vid = (await s.execute(text(
            \"INSERT INTO agent_versions (agent_id, version_number, tools) \"
            \"VALUES (:a, 1, '[]'::jsonb) RETURNING id\"
        ), {'a': aid})).scalar()
        await s.execute(text(
            \"INSERT INTO deployments (agent_id, version_id, environment, status, replicas, k8s_namespace) \"
            \"VALUES (:a, :v, 'production', 'running', 1, 'agents-platform')\"
        ), {'a': aid, 'v': vid})
        await s.commit()
    print('TOKEN=' + token)
    print('TRIGGER_ID=' + trigger_id)

asyncio.run(main())
" > /tmp/s28_setup.txt 2>&1
if grep -q "TOKEN=" /tmp/s28_setup.txt; then
  pass "Setup — agent + webhook trigger + running deployment"
else
  cat /tmp/s28_setup.txt; fail "Setup"; echo "==> Aborting"; exit 1
fi
TOKEN=$(grep -o 'TOKEN=.*' /tmp/s28_setup.txt | cut -d= -f2-)
TRIGGER_ID=$(grep -o 'TRIGGER_ID=.*' /tmp/s28_setup.txt | cut -d= -f2-)

# ---------------------------------------------------------------------------
# T-S28-002: Invalid token → 401
# ---------------------------------------------------------------------------
echo "--- T-S28-002: Invalid token returns 401 ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT_NAME}/not-a-real-token', json={'event': 'order.created'}, timeout=10)
print('status', r.status_code)
sys.exit(0 if r.status_code == 401 else 1)
" | grep -q "status 401" && pass "T-S28-002 — invalid token → 401" || fail "T-S28-002 — expected 401"

# ---------------------------------------------------------------------------
# T-S28-001: Valid token + matching payload → 202 + run created
# ---------------------------------------------------------------------------
echo "--- T-S28-001: Valid token + matching payload creates a run ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT_NAME}/${TOKEN}', json={'event': 'order.created', 'id': 42}, timeout=20)
if r.status_code != 202:
    print('FAIL gateway status', r.status_code, r.text); sys.exit(1)
run_id = r.json().get('run_id')
if not run_id:
    print('FAIL no run_id in', r.json()); sys.exit(1)
# verify an agent_run row with trigger_type=webhook exists
import urllib.request, json
q = urllib.request.urlopen('http://localhost:8000/api/v1/agent-runs?agent_name=${AGENT_NAME}&limit=5')
runs = json.loads(q.read())
items = runs if isinstance(runs, list) else runs.get('items', [])
if any(x.get('trigger_type') == 'webhook' for x in items):
    print('OK run created', run_id); sys.exit(0)
print('FAIL no webhook run found', items[:3]); sys.exit(1)
" > /tmp/s28_matched.txt 2>&1 && pass "T-S28-001 — matched webhook created a run" || { cat /tmp/s28_matched.txt; fail "T-S28-001"; }

# ---------------------------------------------------------------------------
# T-S28-003: Valid token, non-matching filter → 202 filtered, no run
# ---------------------------------------------------------------------------
echo "--- T-S28-003: Non-matching filter returns 202 filtered ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT_NAME}/${TOKEN}', json={'event': 'order.deleted'}, timeout=10)
body = r.json()
print('status', r.status_code, 'body', body)
sys.exit(0 if r.status_code == 202 and body.get('status') == 'filtered' else 1)
" | grep -q "'status': 'filtered'" && pass "T-S28-003 — non-matching filter → 202 filtered" || fail "T-S28-003"

# ---------------------------------------------------------------------------
# T-S28-005: Rotate token → old rejected, new works
# ---------------------------------------------------------------------------
echo "--- T-S28-005: Rotate token — old rejected, new works ---"
pyexec "
import httpx, sys
rot = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/triggers/${TRIGGER_ID}/rotate-token', timeout=10)
if rot.status_code != 200:
    print('FAIL rotate', rot.status_code, rot.text); sys.exit(1)
new_token = rot.json()['token']
# old token now invalid
old = httpx.post('${GW}/hooks/${AGENT_NAME}/${TOKEN}', json={'event': 'order.created'}, timeout=10)
if old.status_code != 401:
    print('FAIL old token not rejected:', old.status_code); sys.exit(1)
# new token works (matched → 202)
new = httpx.post('${GW}/hooks/${AGENT_NAME}/' + new_token, json={'event': 'order.created'}, timeout=20)
if new.status_code != 202:
    print('FAIL new token not accepted:', new.status_code, new.text); sys.exit(1)
print('OK old=401 new=202'); sys.exit(0)
" > /tmp/s28_rotate.txt 2>&1 && pass "T-S28-005 — rotate: old rejected, new works" || { cat /tmp/s28_rotate.txt; fail "T-S28-005"; }

# ---------------------------------------------------------------------------
# T-S28-006: GET /agents/{name}/events shows matched, filtered, rejected
# ---------------------------------------------------------------------------
echo "--- T-S28-006: Event log shows all three statuses ---"
pyexec "
import urllib.request, json, sys
events = json.loads(urllib.request.urlopen('http://localhost:8000/api/v1/agents/${AGENT_NAME}/events?limit=50').read())
statuses = {e['status'] for e in events}
print('statuses', sorted(statuses))
sys.exit(0 if {'matched', 'filtered', 'rejected'}.issubset(statuses) else 1)
" > /tmp/s28_events.txt 2>&1 && pass "T-S28-006 — event log has matched+filtered+rejected" || { cat /tmp/s28_events.txt; fail "T-S28-006"; }

# ---------------------------------------------------------------------------
# T-S28-004: Exceed rate limit → 429  (LAST — floods the shared pod-IP window)
# ---------------------------------------------------------------------------
echo "--- T-S28-004: Exceed rate limit returns 429 ---"
pyexec "
import httpx, sys
saw_429 = False
with httpx.Client(timeout=10) as c:
    for i in range(90):
        r = c.post('${GW}/hooks/${AGENT_NAME}/not-a-real-token', json={'x': i})
        if r.status_code == 429:
            saw_429 = True
            break
print('saw_429', saw_429)
sys.exit(0 if saw_429 else 1)
" | grep -q "saw_429 True" && pass "T-S28-004 — rate limit → 429" || fail "T-S28-004 — no 429 within 90 requests"

echo ""
echo "==> Suite 28 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
