#!/usr/bin/env bash
# smoke-test-cp3-behaviour.sh — Execution-modes CP3 behaviour smoke test.
#
# Proves the three execution-modes surfaces work together end-to-end:
#   1. Scheduler fires a cron trigger → an agent_run (trigger_type=schedule) is
#      created; the run fails (no real agent pod) → a failure ALERT is dispatched.
#   2. Event Gateway: matching webhook → 202 + run; bad token → 401;
#      non-matching filter → 202 filtered.
#
# A minimal agent_version + running deployment are inserted via SQL so the
# internal run-start endpoint actually creates runs (it requires a running
# deployment). The runs then fail on dispatch (no pod) — which is exactly what
# lets us prove failure-alerting in the same flow.
#
# Usage: bash scripts/smoke-test-cp3-behaviour.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

TS=$(date +%s)
AGENT="cp3-beh-${TS}"
ALERT_EMAIL="cp3-${TS}@agentshield.local"
GW="http://agentshield-event-gateway:8091"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }
pyexec() { kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$1" 2>&1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agent..."
  pyexec "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/${AGENT}', method='DELETE'), timeout=5)
except Exception: pass
" >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== CP3 Behaviour Smoke Test ==="

# ---------------------------------------------------------------------------
# Setup: agent + schedule trigger (w/ alert) + webhook trigger (w/ filter)
#        + fake running deployment
# ---------------------------------------------------------------------------
echo "--- Setup ---"
pyexec "
import asyncio, httpx, sys
async def main():
    r = httpx.post('http://localhost:8000/api/v1/agents/', json={
        'name': '${AGENT}', 'team': 'platform', 'agent_type': 'declarative', 'execution_shape': 'reactive'})
    if r.status_code != 201: print('SETUP_FAIL agent', r.status_code, r.text); sys.exit(1)
    # schedule trigger every minute, with failure alert
    rs = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/triggers', json={
        'trigger_type': 'schedule', 'cron_expression': '* * * * *', 'timezone': 'UTC',
        'enabled': True, 'alert_email': '${ALERT_EMAIL}', 'alert_on_failure': True})
    if rs.status_code not in (200,201): print('SETUP_FAIL schedule', rs.status_code, rs.text); sys.exit(1)
    # webhook trigger with a filter
    rw = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/triggers', json={
        'trigger_type': 'webhook', 'enabled': True,
        'filter_conditions': [{'field': 'event', 'op': 'eq', 'value': 'ping'}]})
    if rw.status_code not in (200,201): print('SETUP_FAIL webhook', rw.status_code, rw.text); sys.exit(1)
    token = rw.json().get('token')
    # fake version + running deployment so internal/runs/start creates runs
    from db import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as s:
        aid=(await s.execute(text(\"select id from agents where name=:n\"), {'n':'${AGENT}'})).scalar()
        vid=(await s.execute(text(\"insert into agent_versions (agent_id, version_number, tools) values (:a,1,'[]'::jsonb) returning id\"), {'a':aid})).scalar()
        await s.execute(text(\"insert into deployments (agent_id, version_id, environment, status, replicas, k8s_namespace) values (:a,:v,'production','running',1,'agents-platform')\"), {'a':aid,'v':vid})
        await s.commit()
    print('TOKEN=' + token)
asyncio.run(main())
" > /tmp/cp3_setup.txt 2>&1
if grep -q "TOKEN=" /tmp/cp3_setup.txt; then pass "Setup — agent + triggers + running deployment"; else cat /tmp/cp3_setup.txt; fail "Setup"; exit 1; fi
TOKEN=$(grep -o 'TOKEN=.*' /tmp/cp3_setup.txt | cut -d= -f2-)

# ---------------------------------------------------------------------------
# 1a. Scheduler fires cron → agent_run (trigger_type=schedule) created
# ---------------------------------------------------------------------------
echo "--- 1a. Scheduler fires cron → run created (waits up to ~110s) ---"
FOUND=0
for i in $(seq 1 22); do
  sleep 5
  HIT=$(pyexec "
import urllib.request, json
runs = json.loads(urllib.request.urlopen('http://localhost:8000/api/v1/agent-runs?agent_name=${AGENT}&limit=10').read())
items = runs if isinstance(runs, list) else runs.get('items', [])
print('yes' if any(x.get('trigger_type')=='schedule' for x in items) else 'no')
" 2>/dev/null | tail -1)
  if [ "$HIT" = "yes" ]; then FOUND=1; break; fi
done
if [ "$FOUND" -eq 1 ]; then pass "scheduler fired cron → agent_run (trigger_type=schedule) created"; else fail "no scheduled run created within ~110s"; fi

# ---------------------------------------------------------------------------
# 1b. Scheduled run failed → failure ALERT dispatched (log-only)
# ---------------------------------------------------------------------------
echo "--- 1b. Failure alert dispatched for the failed scheduled run ---"
sleep 4  # let the async dispatch + alert log land
ALERT_HIT=0
for p in $(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api -o name 2>/dev/null); do
  if kubectl logs -n "$NAMESPACE" "$p" --tail=400 2>/dev/null | grep -q "${ALERT_EMAIL}"; then ALERT_HIT=1; break; fi
done
if [ "$ALERT_HIT" -eq 1 ]; then pass "failure alert dispatched (ALERT log line for ${ALERT_EMAIL})"; else fail "no alert log line found for ${ALERT_EMAIL}"; fi

# ---------------------------------------------------------------------------
# 2a. Event Gateway — matching webhook → 202 + run created
# ---------------------------------------------------------------------------
echo "--- 2a. Webhook matched → 202 + run ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT}/${TOKEN}', json={'event': 'ping'}, timeout=20)
print('status', r.status_code, r.json() if r.headers.get('content-type','').startswith('application/json') else '')
sys.exit(0 if r.status_code == 202 and r.json().get('run_id') else 1)
" | grep -q "status 202" && pass "webhook matched → 202 + run_id" || fail "webhook matched path"

# ---------------------------------------------------------------------------
# 2b. Event Gateway — bad token → 401
# ---------------------------------------------------------------------------
echo "--- 2b. Webhook bad token → 401 ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT}/bad-token', json={'event': 'ping'}, timeout=10)
print('status', r.status_code)
sys.exit(0 if r.status_code == 401 else 1)
" | grep -q "status 401" && pass "webhook bad token → 401" || fail "webhook bad token"

# ---------------------------------------------------------------------------
# 2c. Event Gateway — non-matching filter → 202 filtered
# ---------------------------------------------------------------------------
echo "--- 2c. Webhook non-matching filter → 202 filtered ---"
pyexec "
import httpx, sys
r = httpx.post('${GW}/hooks/${AGENT}/${TOKEN}', json={'event': 'other'}, timeout=10)
b = r.json()
print('status', r.status_code, 'body', b)
sys.exit(0 if r.status_code == 202 and b.get('status') == 'filtered' else 1)
" | grep -q "'status': 'filtered'" && pass "webhook non-matching filter → 202 filtered" || fail "webhook filtered path"

echo ""
echo "==> CP3 Behaviour Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
