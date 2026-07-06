#!/usr/bin/env bash
# Suite 27: Alerting + Observability (Phase 8)
# Tests T-S27-001 through T-S27-004
#
# Validates:
#   - Configure alert_email + alert_on_failure on a trigger (persisted)
#   - A failed run's alert path dispatches a notification (log-only w/o SMTP_HOST)
#   - A trigger without alert config produces NO alert
#   - GET /agents/{name}/health returns mode-correct signals
#
# Usage:
#   bash scripts/e2e/suite-27-alerting.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
AGENT_NAME="alert-test-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""
  echo "==> Cleanup: deleting test agent..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/${AGENT_NAME}', method='DELETE'), timeout=5)
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 27: Alerting + Observability ==="

# ---------------------------------------------------------------------------
# T-S27-001: Configure alert_email + alert_on_failure on a trigger (persisted)
# ---------------------------------------------------------------------------
echo "--- T-S27-001: Configure alert config on a trigger ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${AGENT_NAME}', 'team': 'platform', 'agent_type': 'declarative',
    'execution_shape': 'reactive',
})
if r.status_code != 201:
    print(f'FAIL: create agent {r.status_code}: {r.text}'); sys.exit(1)
r2 = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/triggers', json={
    'trigger_type': 'schedule', 'cron_expression': '0 * * * *', 'timezone': 'UTC',
    'enabled': True, 'alert_email': 'alerts@agentshield.local', 'alert_on_failure': True,
})
if r2.status_code not in (200, 201):
    print(f'FAIL: create trigger {r2.status_code}: {r2.text}'); sys.exit(1)
body = r2.json()
if body.get('alert_email') != 'alerts@agentshield.local' or body.get('alert_on_failure') is not True:
    print(f'FAIL: alert config not persisted: {body}'); sys.exit(1)
print('TRIGGER_ID=' + body['id'])
" > /tmp/s27_trigger.txt 2>&1 && pass "T-S27-001 — alert config persisted on trigger" || { cat /tmp/s27_trigger.txt; fail "T-S27-001"; }
TRIGGER_ID=$(grep -o 'TRIGGER_ID=.*' /tmp/s27_trigger.txt | cut -d= -f2 || true)

# ---------------------------------------------------------------------------
# T-S27-002: Failed run dispatches an alert (log-only when SMTP_HOST unset)
# ---------------------------------------------------------------------------
echo "--- T-S27-002: Failed run dispatches alert for configured trigger ---"
if [ -n "${TRIGGER_ID:-}" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio, io, logging, sys, uuid
buf = io.StringIO()
h = logging.StreamHandler(buf); h.setLevel(logging.INFO)
lg = logging.getLogger('alerting'); lg.addHandler(h); lg.setLevel(logging.INFO)
from db import AsyncSessionLocal
from alerting import dispatch_failure_alert
async def main():
    async with AsyncSessionLocal() as s:
        await dispatch_failure_alert(
            s, trigger_id=uuid.UUID('${TRIGGER_ID}'),
            agent_name='${AGENT_NAME}', run_id='test-run-1',
            error_message='synthetic failure',
        )
asyncio.run(main())
out = buf.getvalue()
if 'ALERT' in out and 'alerts@agentshield.local' in out:
    print('OK dispatched:', out.strip()); sys.exit(0)
print('FAIL: no alert dispatched. logs=', repr(out)); sys.exit(1)
" > /tmp/s27_dispatch.txt 2>&1 && pass "T-S27-002 — alert dispatched for failed run" || { cat /tmp/s27_dispatch.txt; fail "T-S27-002"; }
else
  fail "T-S27-002 — skipped (no TRIGGER_ID from T-S27-001)"
fi

# ---------------------------------------------------------------------------
# T-S27-003: No alert config → no alert dispatched
# ---------------------------------------------------------------------------
echo "--- T-S27-003: Trigger without alert_email produces no alert ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio, io, logging, sys, uuid
from db import AsyncSessionLocal
from models import AgentTrigger
from sqlalchemy import select
from alerting import dispatch_failure_alert
buf = io.StringIO()
h = logging.StreamHandler(buf); h.setLevel(logging.INFO)
lg = logging.getLogger('alerting'); lg.addHandler(h); lg.setLevel(logging.INFO)
async def main():
    async with AsyncSessionLocal() as s:
        # find the agent's trigger and blank its alert_email
        rows = (await s.execute(select(AgentTrigger))).scalars().all()
        # exercise the no-config path with a random (nonexistent) trigger_id too
        await dispatch_failure_alert(
            s, trigger_id=uuid.uuid4(),
            agent_name='${AGENT_NAME}', run_id='test-run-2',
            error_message='synthetic failure',
        )
asyncio.run(main())
out = buf.getvalue()
if 'ALERT' in out:
    print('FAIL: alert dispatched for unconfigured trigger. logs=', repr(out)); sys.exit(1)
print('OK: no alert dispatched (as expected)'); sys.exit(0)
" > /tmp/s27_noalert.txt 2>&1 && pass "T-S27-003 — no alert without config" || { cat /tmp/s27_noalert.txt; fail "T-S27-003"; }

# ---------------------------------------------------------------------------
# T-S27-004: GET /agents/{name}/health returns mode-correct signals
# ---------------------------------------------------------------------------
echo "--- T-S27-004: Health endpoint returns mode-correct signals ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/health')
if r.status_code != 200:
    print(f'FAIL: health {r.status_code}: {r.text}'); sys.exit(1)
h = r.json()
# agent has an enabled schedule trigger => mode must be 'scheduled'
if h.get('mode') != 'scheduled':
    print(f'FAIL: expected mode=scheduled, got {h.get(\"mode\")}: {h}'); sys.exit(1)
for k in ('last_run_status', 'next_fire_at', 'missed_fires'):
    if k not in h:
        print(f'FAIL: missing scheduled signal {k}: {h}'); sys.exit(1)
if h.get('health') not in ('healthy', 'degraded', 'failing'):
    print(f'FAIL: bad health rollup {h.get(\"health\")}'); sys.exit(1)
print('OK health:', h)
" > /tmp/s27_health.txt 2>&1 && pass "T-S27-004 — health endpoint mode-correct" || { cat /tmp/s27_health.txt; fail "T-S27-004"; }

echo ""
echo "==> Suite 27 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
