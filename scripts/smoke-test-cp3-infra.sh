#!/usr/bin/env bash
# smoke-test-cp3-infra.sh — Execution-modes CP3 infrastructure smoke test.
#
# Proves the full-platform components are deployed and healthy:
#   - scheduler: 2 replicas Running (HA)
#   - event-gateway: Running + Service reachable in-cluster
#   - agent_events table exists
#   - alerting capability present (alert columns + alerting module)
#
# NOTE: On the local Docker Desktop cluster there is no ingress controller, so
# "event-gateway Ingress resolves" is verified as "the event-gateway Service is
# reachable in-cluster" (the Ingress manifest is correct but disabled locally).
# NOTE: SMTP is intentionally log-only by default (SMTP_HOST unset), so we verify
# the alerting *capability* (alert_email/alert_on_failure columns + alerting.py),
# not that SMTP env vars are set.
#
# Usage: bash scripts/smoke-test-cp3-infra.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

echo "=== CP3 Infra Smoke Test ==="

# --- Scheduler HA: 2 replicas Running ---
SCHED=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=scheduler --no-headers 2>/dev/null | grep -c Running || echo 0)
if [ "${SCHED:-0}" -ge 2 ]; then pass "scheduler HA — ${SCHED} replicas Running"; else fail "scheduler — expected >=2 Running, got ${SCHED}"; fi

# --- Event Gateway Running ---
EG=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=event-gateway --no-headers 2>/dev/null | grep -c Running || echo 0)
if [ "${EG:-0}" -ge 1 ]; then pass "event-gateway — ${EG} replica(s) Running"; else fail "event-gateway — no Running pods"; fi

# --- Event Gateway Service reachable in-cluster (stands in for Ingress locally) ---
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
try:
    r = httpx.get('http://agentshield-event-gateway:8091/health', timeout=8)
    print('health', r.status_code, r.json())
    sys.exit(0 if r.status_code == 200 else 1)
except Exception as e:
    print('unreachable:', e); sys.exit(1)
" 2>/dev/null | grep -q "health 200" && pass "event-gateway Service reachable (/health 200)" || fail "event-gateway Service unreachable"

# --- agent_events table exists ---
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio
from db import AsyncSessionLocal
from sqlalchemy import text
async def m():
    async with AsyncSessionLocal() as s:
        t=(await s.execute(text(\"select to_regclass('public.agent_events')\"))).scalar()
        print('agent_events', t)
asyncio.run(m())
" 2>/dev/null | grep -q "agent_events agent_events" && pass "agent_events table exists" || fail "agent_events table missing"

# --- Alerting capability present (alert columns + alerting module) ---
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio, importlib
from db import AsyncSessionLocal
from sqlalchemy import text
async def m():
    async with AsyncSessionLocal() as s:
        cols=(await s.execute(text(
            \"select column_name from information_schema.columns \"
            \"where table_name='agent_triggers' and column_name in ('alert_email','alert_on_failure')\"
        ))).scalars().all()
        assert sorted(cols)==['alert_email','alert_on_failure'], cols
    importlib.import_module('alerting').dispatch_failure_alert  # module + fn present
    print('alerting OK')
asyncio.run(m())
" 2>/dev/null | grep -q "alerting OK" && pass "alerting capability present (columns + alerting.py)" || fail "alerting capability missing"

echo ""
echo "==> CP3 Infra Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
