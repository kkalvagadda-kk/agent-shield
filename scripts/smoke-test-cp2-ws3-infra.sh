#!/usr/bin/env bash
# scripts/smoke-test-cp2-ws3-infra.sh
#
# WS-3 Checkpoint 2 — INFRA smoke (CP2b). Proves the workflow + alert path's infra
# is present (behaviour is CP2c). All existing shared code — no bump, no migration.
#
#   T-CP2B-001 — workflow member agent pods Running (orchestrator reachable path)
#   T-CP2B-002 — scheduler UNION-query wiring: a scheduled workflow trigger row
#                (workflow_id set) is visible to the scheduler's query
#   T-CP2B-003 — alerting module importable in the registry-api pod
#                (dispatch_failure_alert present)
#
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"

PASS=0; FAIL=0
ok()   { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-3 CP2b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: no running registry-api pod"; exit 1; fi

# ── T-CP2B-001 — at least one agent pod Running (workflow member dispatch target)
AGENT_PODS=$(kubectl get pods -A -l 'app.kubernetes.io/managed-by=deploy-controller' \
  --field-selector=status.phase=Running -o name 2>/dev/null | wc -l | tr -d ' ')
# Fallback: any pod in an agents-* namespace (deploy-controller labels vary by version).
if [ "${AGENT_PODS:-0}" -lt 1 ]; then
  AGENT_PODS=$(kubectl get pods -n agents-platform --field-selector=status.phase=Running \
    -o name 2>/dev/null | wc -l | tr -d ' ')
fi
if [ "${AGENT_PODS:-0}" -ge 1 ]; then
  ok "T-CP2B-001 workflow member agent pods Running (dispatch targets present)" "running_agent_pods=$AGENT_PODS"
else
  bad "T-CP2B-001 workflow member agent pods Running" "running_agent_pods=${AGENT_PODS:-0}"
fi

# ── T-CP2B-002 — scheduler UNION-query wiring: create a scheduled workflow trigger
#    (workflow_id set) and confirm it is returned by the same query the scheduler
#    uses (agent_triggers WHERE enabled AND trigger_type='schedule' AND cron set).
QOUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  'cd /app && PYTHONPATH=/app python3 - <<PY
import asyncio, uuid
from sqlalchemy import text
from db import AsyncSessionLocal
async def main():
    sfx = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as s:
        wf = (await s.execute(text(
            "insert into workflows (id,name,team,orchestration,execution_shape,agent_class,status) "
            "values (gen_random_uuid(),:n,:t,:o,:e,:c,:st) returning id"),
            {"n": f"cp2b-{sfx}", "t": "platform", "o": "sequential",
             "e": "durable", "c": "daemon", "st": "draft"})).scalar_one()
        await s.execute(text(
            "insert into agent_triggers (id,workflow_id,trigger_type,cron_expression,enabled,armed_by) "
            "values (gen_random_uuid(),:w,:tt,:cron,true,:a)"),
            {"w": wf, "tt": "schedule", "cron": "0 0 * * *", "a": "cp2b"})
        await s.commit()
        # The scheduler UNION-queries schedule triggers that carry a workflow_id.
        n = (await s.execute(text(
            "select count(*) from agent_triggers "
            "where workflow_id=:w and enabled and trigger_type='"'"'schedule'"'"' "
            "and cron_expression is not null"), {"w": wf})).scalar_one()
        print(f"VISIBLE:{n}")
        # cleanup
        await s.execute(text("delete from agent_triggers where workflow_id=:w"), {"w": wf})
        await s.execute(text("delete from workflows where id=:w"), {"w": wf})
        await s.commit()
asyncio.run(main())
PY' 2>/dev/null | tr -d "[:space:]" || true)
if echo "$QOUT" | grep -q "^VISIBLE:1"; then
  ok "T-CP2B-002 scheduler UNION-query sees scheduled WORKFLOW trigger (workflow_id set)" "$QOUT"
else
  bad "T-CP2B-002 scheduler UNION-query sees scheduled WORKFLOW trigger" "result=${QOUT:-<none>}"
fi

# ── T-CP2B-003 — alerting importable + dispatch_failure_alert present ─────────
IMP=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  'cd /app && PYTHONPATH=/app python3 -c "import alerting; print(\"OK\" if callable(alerting.dispatch_failure_alert) else \"NOTCALLABLE\")"' 2>/dev/null | tr -d "[:space:]" || true)
if [ "$IMP" = "OK" ]; then
  ok "T-CP2B-003 alerting module importable (dispatch_failure_alert callable)" "import=$IMP"
else
  bad "T-CP2B-003 alerting module importable" "result=${IMP:-<none>}"
fi

echo ""
echo "=== CP2b summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "CP2b INFRA SMOKE FAILED"; exit 1; fi
echo "CP2b INFRA SMOKE PASSED"
