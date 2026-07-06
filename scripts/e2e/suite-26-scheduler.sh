#!/usr/bin/env bash
# Suite 26: Scheduler Service (Phase 7)
# Tests T-S26-001 through T-S26-004
#
# Validates:
#   - Create scheduled agent + cron trigger
#   - Scheduler picks up the trigger (registers a cron job) after reload
#   - Disabling the trigger removes the job (no more fires)
#   - Scheduler runs HA (2 replicas)
#
# Usage:
#   bash scripts/e2e/suite-26-scheduler.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
AGENT_NAME="sched-test-${TS}"

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

echo "=== Suite 26: Scheduler Service ==="

# Helper: read a scheduler pod's /health scheduled_jobs count (max across replicas)
sched_jobs() {
  local max=0
  for p in $(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=scheduler --no-headers 2>/dev/null | grep Running | awk '{print $1}'); do
    n=$(kubectl exec -n "$NAMESPACE" "$p" -- python3 -c "
import httpx
try: print(httpx.get('http://localhost:8090/health',timeout=4).json().get('scheduled_jobs',0))
except Exception: print(0)
" 2>/dev/null | tail -1)
    [ "${n:-0}" -gt "$max" ] && max=$n
  done
  echo "$max"
}

# ---------------------------------------------------------------------------
# T-S26-004: Scheduler HA — 2 replicas Running
# ---------------------------------------------------------------------------
echo "--- T-S26-004: Scheduler HA (2 replicas Running) ---"
RUNNING=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=scheduler --no-headers 2>/dev/null | grep -c Running || echo 0)
if [ "${RUNNING:-0}" -ge 2 ]; then
  pass "T-S26-004 — scheduler HA: ${RUNNING} replicas Running"
else
  fail "T-S26-004 — expected >=2 scheduler replicas Running, got ${RUNNING}"
fi

BASELINE=$(sched_jobs)
echo "  (baseline scheduled_jobs=${BASELINE})"

# ---------------------------------------------------------------------------
# T-S26-001: Create scheduled agent + cron trigger
# ---------------------------------------------------------------------------
echo "--- T-S26-001: Create scheduled agent + cron trigger ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${AGENT_NAME}', 'team': 'platform', 'agent_type': 'declarative',
    'execution_shape': 'reactive',
})
if r.status_code != 201:
    print(f'FAIL: create agent {r.status_code}: {r.text}'); sys.exit(1)
r2 = httpx.post('http://localhost:8000/api/v1/agents/${AGENT_NAME}/triggers', json={
    'trigger_type': 'schedule', 'cron_expression': '* * * * *', 'timezone': 'UTC', 'enabled': True,
})
if r2.status_code not in (200, 201):
    print(f'FAIL: create trigger {r2.status_code}: {r2.text}'); sys.exit(1)
print('TRIGGER_ID=' + r2.json()['id'])
" > /tmp/s26_trigger.txt 2>&1 && pass "T-S26-001 — scheduled agent + trigger created" || { cat /tmp/s26_trigger.txt; fail "T-S26-001"; }
TRIGGER_ID=$(grep -o 'TRIGGER_ID=.*' /tmp/s26_trigger.txt | cut -d= -f2 || true)

# ---------------------------------------------------------------------------
# T-S26-002: Scheduler registers the cron job after reload
# ---------------------------------------------------------------------------
echo "--- T-S26-002: Scheduler registers job (reload within ~70s) ---"
FOUND=0
for i in $(seq 1 15); do
  sleep 6
  CUR=$(sched_jobs)
  if [ "${CUR:-0}" -gt "${BASELINE:-0}" ]; then FOUND=1; break; fi
done
if [ "$FOUND" -eq 1 ]; then
  pass "T-S26-002 — scheduler registered the cron job (scheduled_jobs ${BASELINE}→${CUR})"
else
  fail "T-S26-002 — scheduler did not register job within 90s (still ${CUR})"
fi

# ---------------------------------------------------------------------------
# T-S26-003: Disable trigger → job removed on reload
# ---------------------------------------------------------------------------
echo "--- T-S26-003: Disable trigger removes the job ---"
if [ -n "${TRIGGER_ID:-}" ]; then
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT_NAME}/triggers/${TRIGGER_ID}', json={'enabled': False})
sys.exit(0 if r.status_code in (200,204) else 1)
" 2>/dev/null || true
  AFTER_DISABLE=$(sched_jobs); PREV=$AFTER_DISABLE
  REMOVED=0
  for i in $(seq 1 15); do
    sleep 6
    CUR=$(sched_jobs)
    if [ "${CUR:-0}" -lt "${PREV:-0}" ] || [ "${CUR:-0}" -le "${BASELINE:-0}" ]; then REMOVED=1; break; fi
  done
  if [ "$REMOVED" -eq 1 ]; then
    pass "T-S26-003 — disabled trigger removed from scheduler"
  else
    fail "T-S26-003 — job still registered after disable (${CUR})"
  fi
else
  fail "T-S26-003 — skipped (no TRIGGER_ID from T-S26-001)"
fi

echo ""
echo "==> Suite 26 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
