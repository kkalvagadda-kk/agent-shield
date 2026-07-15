#!/usr/bin/env bash
# scripts/smoke-test-cp3-ws3-infra.sh
#
# WS-3 Checkpoint 3 — INFRA smoke (CP3b). Proves the studio deploy landed and the
# scheduled operate surface's read producer is live at the infra layer:
#
#   T-CP3B-001 — studio pod Running on image tag 0.1.136, none in CrashLoopBackOff
#   T-CP3B-002 — GET /agents/{name}/health returns HTTP 200 with mode=scheduled +
#                a next_fire_at field (created against a throwaway scheduled agent
#                in-pod, JSON-shape checked, then cleaned up)
#   T-CP3B-003 — suite-71 is registered in scripts/e2e/run-all.sh
#   T-CP3B-004 — studio tag 0.1.136 present in BOTH scripts/deploy-cpe2e.sh and
#                charts/agentshield/values.yaml
#
# REAL kubectl / in-pod httpx / repo greps. exit 0 only if every assertion passes.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXPECT_TAG="0.1.136"
ADMIN_SUB="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-3 CP3b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

# ── T-CP3B-001 — studio pod Running on 0.1.136, no CrashLoopBackOff ────────────
STUDIO_JSON=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=studio \
  -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.phase}{"|"}{range .spec.containers[*]}{.image}{","}{end}{"|"}{range .status.containerStatuses[*]}{.state.waiting.reason}{","}{end}{"\n"}{end}' 2>/dev/null || true)
if [ -z "$STUDIO_JSON" ]; then
  # Fall back to the common label used by the chart deployment.
  STUDIO_JSON=$(kubectl get pods -n "$NAMESPACE" -l app=agentshield-studio \
    -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.phase}{"|"}{range .spec.containers[*]}{.image}{","}{end}{"|"}{range .status.containerStatuses[*]}{.state.waiting.reason}{","}{end}{"\n"}{end}' 2>/dev/null || true)
fi
RUNNING=$(echo "$STUDIO_JSON" | grep -c "=Running|" || true)
CRASH=$(echo "$STUDIO_JSON" | grep -c "CrashLoopBackOff" || true)
ON_TAG=$(echo "$STUDIO_JSON" | grep -c "studio:${EXPECT_TAG}" || true)
if [ "$RUNNING" -ge 1 ] && [ "$CRASH" -eq 0 ] && [ "$ON_TAG" -ge 1 ]; then
  ok "T-CP3B-001 studio pod Running on ${EXPECT_TAG}" "running=$RUNNING crashloop=$CRASH on_tag=$ON_TAG"
else
  bad "T-CP3B-001 studio pod Running on ${EXPECT_TAG}" "running=$RUNNING crashloop=$CRASH on_tag=$ON_TAG :: $STUDIO_JSON"
fi

# ── T-CP3B-002 — GET /agents/{name}/health 200 + mode=scheduled + next_fire_at ─
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  bad "T-CP3B-002 scheduled health endpoint shape" "no running registry-api pod to query"
else
  HEALTH=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    "export ADMIN_SUB='$ADMIN_SUB'; cd /app && PYTHONPATH=/app python3 - <<'PY'
import asyncio, os, uuid, json, httpx
from sqlalchemy import delete
from db import AsyncSessionLocal
from models import Agent, AgentTrigger

ADMIN = os.environ['ADMIN_SUB']
BASE = 'http://localhost:8000/api/v1'
HDR = {'X-User-Sub': ADMIN, 'X-User-Team': 'platform'}
NAME = f'cp3b-health-{uuid.uuid4().hex[:6]}'

async def main():
    # Minimal fixture: an agent with an ENABLED schedule trigger => mode=scheduled,
    # next_fire_at = croniter over the cron. No deploy / version needed for health.
    async with AsyncSessionLocal() as s:
        ag = Agent(name=NAME, team='platform', agent_type='declarative', created_by=ADMIN)
        s.add(ag); await s.flush()
        s.add(AgentTrigger(agent_id=ag.id, trigger_type='schedule',
                           cron_expression='0 9 * * *', enabled=True))
        await s.commit()
        aid = ag.id
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=30.0) as c:
            r = await c.get(f'/agents/{NAME}/health')
        body = {}
        try: body = r.json()
        except Exception: pass
        print(json.dumps({'status': r.status_code, 'mode': body.get('mode'),
                          'has_next_fire': 'next_fire_at' in body,
                          'next_fire_at': body.get('next_fire_at')}))
    finally:
        async with AsyncSessionLocal() as s:
            await s.execute(delete(AgentTrigger).where(AgentTrigger.agent_id == aid))
            await s.execute(delete(Agent).where(Agent.id == aid))
            await s.commit()

asyncio.run(main())
PY" 2>/dev/null || true)
  H_STATUS=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status',''))" 2>/dev/null || true)
  H_MODE=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('mode',''))" 2>/dev/null || true)
  H_HASNF=$(echo "$HEALTH" | python3 -c "import sys,json;print(json.load(sys.stdin).get('has_next_fire',''))" 2>/dev/null || true)
  if [ "$H_STATUS" = "200" ] && [ "$H_MODE" = "scheduled" ] && [ "$H_HASNF" = "True" ]; then
    ok "T-CP3B-002 scheduled health endpoint shape" "status=$H_STATUS mode=$H_MODE next_fire_at present"
  else
    bad "T-CP3B-002 scheduled health endpoint shape" "status=$H_STATUS mode=$H_MODE has_next_fire=$H_HASNF :: $HEALTH"
  fi
fi

# ── T-CP3B-003 — suite-71 registered in run-all.sh ────────────────────────────
if grep -q "suite-71" "$REPO_ROOT/scripts/e2e/run-all.sh"; then
  ok "T-CP3B-003 suite-71 registered in run-all.sh" "$(grep -n 'suite-71' "$REPO_ROOT/scripts/e2e/run-all.sh" | head -1)"
else
  bad "T-CP3B-003 suite-71 registered in run-all.sh" "no 'suite-71' reference in scripts/e2e/run-all.sh"
fi

# ── T-CP3B-004 — studio tag 0.1.136 in BOTH deploy files ──────────────────────
IN_DEPLOY=$(grep -c "STUDIO_TAG=\"${EXPECT_TAG}\"" "$REPO_ROOT/scripts/deploy-cpe2e.sh" || true)
IN_VALUES=$(grep -c "tag: \"${EXPECT_TAG}\"" "$REPO_ROOT/charts/agentshield/values.yaml" || true)
if [ "$IN_DEPLOY" -ge 1 ] && [ "$IN_VALUES" -ge 1 ]; then
  ok "T-CP3B-004 studio ${EXPECT_TAG} in BOTH files" "deploy-cpe2e.sh=$IN_DEPLOY values.yaml=$IN_VALUES"
else
  bad "T-CP3B-004 studio ${EXPECT_TAG} in BOTH files" "deploy-cpe2e.sh=$IN_DEPLOY values.yaml=$IN_VALUES (both must be >=1)"
fi

echo ""
echo "=== CP3b infra smoke: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ] || exit 1
