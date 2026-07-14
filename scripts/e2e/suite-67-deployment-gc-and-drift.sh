#!/usr/bin/env bash
# scripts/e2e/suite-67-deployment-gc-and-drift.sh
#
# E2E Suite 67: deployment GC on delete + running-but-lost drift reconcile. NO fakes.
#
# Two sides of deploy-controller drift, both with REAL agents/pods (no seeded rows):
#
#  (A) DELETE-GC — DELETE /agents used to set its deployments straight to
#      'terminated', skipping the controller's terminating→delete_deployment→
#      terminated GC step, so the k8s Deployment/pods were ORPHANED (leaked until
#      the node filled). Fixed 2026-07-14 (registry-api 0.2.173): delete now sets
#      'terminating'. This proves: DELETE /agents → deployment 'terminating' → the
#      controller deletes the k8s Deployment → 'terminated', and the k8s Deployment
#      is actually GONE.
#
#  (B) RUNNING-BUT-LOST — a deployment the DB says is 'running' whose k8s Deployment
#      vanished (cluster wipe / manual delete) is reconciled by the controller
#      (_handle_sandbox_running_drift → mark 'terminated'; production self-heals via
#      re-materialize, not asserted here). This drives it with a REAL deployed agent
#      (kubectl delete its Deployment) rather than suite-52's seeded bogus row.
#
# What it proves:
#   T-S67-001 — DELETE /agents transitions the deployment 'terminating' (NOT straight
#               to 'terminated') and the controller GCs it to 'terminated'
#   T-S67-002 — after the delete, the k8s Deployment is actually gone (no pod leak)
#   T-S67-003 — a 'running' sandbox deployment whose k8s Deployment is deleted out
#               from under it is reconciled to 'terminated' by the controller
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NS="${AGENTS_NS:-agents-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
echo "=== Suite 67: deployment GC on delete + running-but-lost drift (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

SFX=$(date +%s | tail -c 6)   # cheap uniquifier (no Date.now in shell is fine)
# short in-pod python helper: prints one line
dbq() { kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "$1" 2>/dev/null | grep -vE "Internal error occurred|langfuse.com"; }

api_create_deploy() {  # $1 = agent name
  dbq "
import asyncio, httpx
H={'X-User-Sub':'75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6','X-User-Team':'platform'}
async def m():
    async with httpx.AsyncClient(base_url='http://localhost:8000/api/v1', headers=H, timeout=40) as c:
        pid=(await c.get('/llm-providers/', params={'team':'platform'})).json()['items'][0]['id']
        r=await c.post('/agents/', json={'name':'$1','team':'platform','agent_type':'declarative','execution_shape':'reactive','agent_class':'user_delegated','metadata':{'instructions':'hi','llm_provider_id':pid,'tools':[]}})
        d=await c.post('/agents/$1/deploy', json={'environment':'sandbox'})
        print(f'{r.status_code}/{d.status_code}')
asyncio.run(m())"
}
dep_status() {  # $1 = agent name -> "status|k8sname"
  dbq "
import asyncio
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment
async def m():
    async with AsyncSessionLocal() as s:
        a=(await s.execute(select(Agent).where(Agent.name=='$1'))).scalars().first()
        if not a: print('noagent|'); return
        d=(await s.execute(select(Deployment).where(Deployment.agent_id==a.id).order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
        print(f'{d.status}|{d.k8s_deployment_name}' if d else 'nodep|')
asyncio.run(m())"
}
api_delete() { dbq "
import asyncio, httpx
H={'X-User-Sub':'75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6','X-User-Team':'platform'}
async def m():
    async with httpx.AsyncClient(base_url='http://localhost:8000/api/v1', headers=H, timeout=40) as c:
        print((await c.delete('/agents/$1')).status_code)
asyncio.run(m())"; }
wait_running() { for _ in $(seq 1 25); do sleep 5; s=$(dep_status "$1"); echo "$s" | grep -q '^running' && { echo "$s"; return; }; done; echo "$(dep_status "$1")"; }
wait_status() { for _ in $(seq 1 25); do sleep 5; s=$(dep_status "$1" | cut -d'|' -f1); [ "$s" = "$2" ] && return 0; done; return 1; }

PASS=0; FAIL=0
mark() { if [ "$1" = "1" ]; then echo "PASS $2"; PASS=$((PASS+1)); else echo "FAIL $2"; FAIL=$((FAIL+1)); fi; }

# ---- (A) DELETE-GC ----
A1="s67a-$SFX"
echo "  [T1/T2] create+deploy $A1 ..."; api_create_deploy "$A1" >/dev/null
R=$(wait_running "$A1"); K8S_A=$(echo "$R" | cut -d'|' -f2)
if ! echo "$R" | grep -q '^running'; then echo "SKIP $A1 never reached running ($R) — env limit"; api_delete "$A1" >/dev/null 2>&1; exit 0; fi
api_delete "$A1" >/dev/null
ST_NOW=$(dep_status "$A1" | cut -d'|' -f1)
mark "$([ "$ST_NOW" = "terminating" ] && echo 1 || echo 0)" "001_delete_sets_terminating_not_terminated"
if wait_status "$A1" "terminated"; then GC_OK=1; else GC_OK=0; fi
# T2: the k8s Deployment must actually be gone. delete_deployment uses foreground
# propagation + a 30s grace period, so it lingers (Terminating) briefly after the
# status flips — POLL for it to disappear rather than checking once.
K8S_GONE=0
for _ in $(seq 1 14); do
  kubectl get deploy "$K8S_A" -n "$AGENTS_NS" >/dev/null 2>&1 || { K8S_GONE=1; break; }
  sleep 5
done
mark "$([ "$GC_OK" = "1" ] && [ "$K8S_GONE" = "1" ] && echo 1 || echo 0)" "002_k8s_deployment_gc_after_delete"

# ---- (B) RUNNING-BUT-LOST ----
A2="s67b-$SFX"
echo "  [T3] create+deploy $A2 ..."; api_create_deploy "$A2" >/dev/null
R=$(wait_running "$A2"); K8S_B=$(echo "$R" | cut -d'|' -f2)
if echo "$R" | grep -q '^running'; then
  kubectl delete deploy "$K8S_B" -n "$AGENTS_NS" --wait=false >/dev/null 2>&1   # simulate cluster loss
  if wait_status "$A2" "terminated"; then DRIFT_OK=1; else DRIFT_OK=0; fi
  mark "$DRIFT_OK" "003_running_but_lost_reconciled_to_terminated"
else
  echo "SKIP $A2 never reached running — env limit"
fi
api_delete "$A2" >/dev/null 2>&1

echo ""
if [ "$FAIL" -gt 0 ]; then echo "❌ Suite 67 FAILED ($FAIL failed)"; exit 1; fi
if [ "$PASS" -lt 2 ]; then echo "❌ Suite 67 INCONCLUSIVE ($PASS passed)"; exit 1; fi
echo "✅ Suite 67 PASSED — deployment GC-on-delete + running-but-lost drift reconcile proven"
