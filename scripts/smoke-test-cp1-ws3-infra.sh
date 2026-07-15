#!/usr/bin/env bash
# scripts/smoke-test-cp1-ws3-infra.sh
#
# WS-3 Checkpoint 1 — INFRA smoke (CP1b). Proves the scheduled-agent gate's infra
# is in place (no behaviour — that's CP1c). WS-3 has NO new migration and NO
# backend bump: these assertions confirm the already-deployed WS-0/1/2 backend.
#
#   T-CP1B-001 — registry-api pods Running (running≥1, crashloop=0)
#   T-CP1B-002 — scheduler Deployment ready (2 replicas, APScheduler + PG advisory-lock HA)
#   T-CP1B-003 — alembic head = 0062 (NO new WS-3 migration)
#   T-CP1B-004 — agent_triggers has armed_by / alert_email / alert_on_failure columns
#
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

PASS=0; FAIL=0
ok()   { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-3 CP1b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

# ── T-CP1B-001 — registry-api pods Running, no CrashLoopBackOff ───────────────
PODS_JSON=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.phase}{";"}{range .status.containerStatuses[*]}{.state.waiting.reason}{","}{end}{"\n"}{end}' 2>/dev/null || true)
RUNNING_COUNT=$(echo "$PODS_JSON" | grep -c "=Running;" || true)
CRASH_COUNT=$(echo "$PODS_JSON" | grep -c "CrashLoopBackOff" || true)
if [ "$RUNNING_COUNT" -ge 1 ] && [ "$CRASH_COUNT" -eq 0 ]; then
  ok "T-CP1B-001 registry-api pods healthy" "running=$RUNNING_COUNT crashloop=$CRASH_COUNT"
else
  bad "T-CP1B-001 registry-api pods healthy" "running=$RUNNING_COUNT crashloop=$CRASH_COUNT :: $PODS_JSON"
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ── T-CP1B-002 — scheduler Deployment ready, 2 replicas (HA) ──────────────────
SCHED=$(kubectl get deploy agentshield-scheduler -n "$NAMESPACE" \
  -o jsonpath='{.status.readyReplicas}/{.spec.replicas}' 2>/dev/null || true)
READY_R=${SCHED%%/*}; SPEC_R=${SCHED##*/}
if [ "${READY_R:-0}" -ge 2 ] && [ "${SPEC_R:-0}" -ge 2 ]; then
  ok "T-CP1B-002 scheduler ready (HA, 2 replicas — APScheduler + PG advisory-lock)" "ready=$SCHED"
else
  bad "T-CP1B-002 scheduler ready (HA, 2 replicas)" "ready=$SCHED (expected >=2/2)"
fi

# ── T-CP1B-003 — alembic head = 0062 (no new WS-3 migration) ──────────────────
if [ -z "$API_POD" ]; then
  bad "T-CP1B-003 alembic head = 0062" "no running registry-api pod to query"
else
  HEAD=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    'cd /app && alembic current 2>/dev/null' | grep -oE '^[0-9]{4}' | head -1 || true)
  if [ "$HEAD" = "0062" ]; then
    ok "T-CP1B-003 alembic head = 0062 (no new WS-3 migration)" "current=$HEAD"
  else
    bad "T-CP1B-003 alembic head = 0062" "current=${HEAD:-<none>} (expected 0062)"
  fi
fi

# ── T-CP1B-004 — agent_triggers has armed_by/alert_email/alert_on_failure ─────
if [ -z "$API_POD" ]; then
  bad "T-CP1B-004 agent_triggers alert/armed_by columns" "no running registry-api pod to query"
else
  COLS=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    'cd /app && PYTHONPATH=/app python3 - <<PY
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(
            "select column_name from information_schema.columns "
            "where table_name=:t and column_name in "
            "('"'"'armed_by'"'"','"'"'alert_email'"'"','"'"'alert_on_failure'"'"')"),
            {"t": "agent_triggers"})
        print(",".join(sorted(x[0] for x in r.fetchall())))
asyncio.run(main())
PY' 2>/dev/null | tr -d "[:space:]" || true)
  if [ "$COLS" = "alert_email,alert_on_failure,armed_by" ]; then
    ok "T-CP1B-004 agent_triggers alert/armed_by columns present" "cols=$COLS"
  else
    bad "T-CP1B-004 agent_triggers alert/armed_by columns present" "cols=${COLS:-<none>}"
  fi
fi

echo ""
echo "=== CP1b summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "CP1b INFRA SMOKE FAILED"; exit 1; fi
echo "CP1b INFRA SMOKE PASSED"
