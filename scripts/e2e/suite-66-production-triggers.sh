#!/usr/bin/env bash
# scripts/e2e/suite-66-production-triggers.sh
#
# E2E Suite 66: PRODUCTION triggers — webhook + scheduled (P3). NO fakes.
#
# Proves the two cluster-internal FIRERS correctly invoke POST /internal/runs/start
# (context=production) for a durable workflow — the wiring sandbox never exercises:
#
#   set up a production durable workflow (create 2 durable agents -> deploy sandbox ->
#   REAL eval-runner Jobs -> deploy PRODUCTION -> sequential workflow), then:
#
#   WEBHOOK  — create a webhook trigger (returns a one-time token) -> POST the event
#              gateway's public ingress /hooks/workflow/{name}/{token} -> the gateway
#              resolves the trigger and POSTs /internal/runs/start (trigger_type=webhook)
#              -> assert a completed production run.
#   SCHEDULE — create a per-minute schedule trigger (cron '* * * * *') -> the scheduler
#              service (APScheduler, HA advisory-lock) reloads (60s) and fires it ->
#              POSTs /internal/runs/start (trigger_type=schedule) -> assert a completed
#              production run. The trigger is DELETED immediately after (else it fires
#              every minute forever).
#
# What it proves:
#   T-S66-001 — the event-gateway fires a webhook -> completed production run (trig=webhook)
#   T-S66-002 — the scheduler fires a cron schedule -> completed production run (trig=schedule)
#
# Detached in-pod driver (PYTHONPATH=/app -> result file); polled with short execs.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
echo "=== Suite 66: PRODUCTION triggers — webhook + scheduled (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

# Per-invocation paths (the suite-74 lesson): a fixed /tmp/s66_out.txt lets two
# overlapping invocations (a retry, a second operator, a CI re-run against the same pod)
# share a result file and silently read each OTHER's results.
RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s66_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s66_out_${RUN_TAG}.txt"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, AgentRun, EvalRun, CompositeWorkflow
BASE="http://localhost:8000/api/v1"
H={"X-User-Sub":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6","X-User-Team":"platform"}
GW="http://agentshield-event-gateway:8091"
SFX=uuid.uuid4().hex[:6]; NAMES=[f"s66-a-{SFX}",f"s66-b-{SFX}"]; WFN=f"s66-wf-{SFX}"
INSTR="You answer factual questions. Reply with ONLY the answer — no preamble."
ITEMS=[{"input":"What is the capital of France?","expected_output":"Paris"},{"input":"What is 2+2?","expected_output":"4"},{"input":"What color is a clear daytime sky?","expected_output":"Blue"}]
async def prov(c): return (await c.get("/llm-providers/", params={"team":"platform"})).json()["items"][0]["id"]
async def wait_run(n,env,t=70):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a=(await s.execute(select(Agent).where(Agent.name==n))).scalars().first()
            d=(await s.execute(select(Deployment).where(Deployment.agent_id==a.id,Deployment.environment==env).order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
        if d and d.status=="running": return d.id
        await asyncio.sleep(3)
    return None
async def run_of(wfname, trig, tries):
    for _ in range(tries):
        await asyncio.sleep(3)
        async with AsyncSessionLocal() as s:
            p=(await s.execute(select(AgentRun).where(AgentRun.agent_name==wfname, AgentRun.trigger_type==trig).order_by(desc(AgentRun.started_at)).limit(1))).scalars().first()
        if p and p.status in ("completed","failed","cancelled"):
            async with AsyncSessionLocal() as s:
                kids=(await s.execute(select(AgentRun).where(AgentRun.parent_run_id==str(p.id)))).scalars().all()
            return p, kids
    return None, []
async def main():
    out={}; wid=None
    c=httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid=await prov(c)
    try:
        for n in NAMES:
            await c.post("/agents/", json={"name":n,"team":"platform","agent_type":"declarative","execution_shape":"durable","agent_class":"daemon","metadata":{"instructions":INSTR,"llm_provider_id":pid,"tools":[]}})
            await c.post(f"/agents/{n}/deploy", json={"environment":"sandbox"})
        sbx={n:await wait_run(n,"sandbox") for n in NAMES}
        if not all(sbx.values()): print("SKIP sandbox deploy"); return
        for n in NAMES:
            ds=(await c.post("/playground/datasets", json={"name":f"s66-ds-{n}","mode":"reactive","items":ITEMS})).json()["id"]
            er=await c.post("/playground/eval-runs", json={"dataset_id":ds,"agent_name":n,"sandbox_deployment_id":str(sbx[n])})
            if er.status_code!=201: print("SKIP eval-run not launched"); return
            rid=er.json()["id"]; run=None
            for _ in range(75):
                await asyncio.sleep(4)
                async with AsyncSessionLocal() as s: run=(await s.execute(select(EvalRun).where(EvalRun.id==uuid.UUID(rid)))).scalar_one_or_none()
                if run and run.status in ("completed","failed"): break
            if not run or run.status!="completed": print("SKIP eval-run did not complete"); return
        for n in NAMES: await c.post(f"/agents/{n}/deploy", json={"environment":"production"})
        _prod=[await wait_run(n,"production") for n in NAMES]
        if not all(_prod): out["_diag"]="prod deploy failed"
        r=await c.post("/workflows", json={"name":WFN,"team":"platform","orchestration":"sequential","execution_shape":"durable","agent_class":"daemon"})
        wid=r.json()["id"]
        for i,n in enumerate(NAMES):
            aid=(await c.get(f"/agents/{n}")).json()["id"]
            await c.post(f"/workflows/{wid}/members", json={"agent_id":aid,"position":i+1})
        # WEBHOOK
        wh=(await c.post(f"/workflows/{wid}/triggers", json={"trigger_type":"webhook","name":"s66-hook"})).json()
        token=wh.get("token")
        async with httpx.AsyncClient(timeout=30) as gwc:
            fired=await gwc.post(f"{GW}/hooks/workflow/{WFN}/{token}", json={"message":"What is the capital of France?"})
        p,kids=await run_of(WFN,"webhook",40)
        out["T-S66-001 webhook_fires_prod_run"]= bool(fired.status_code in (200,202) and p and p.status=="completed" and p.context=="production" and len(kids)>=2 and all(k.status=="completed" for k in kids))
        # SCHEDULE (every minute) — delete immediately after firing once
        sch=(await c.post(f"/workflows/{wid}/triggers", json={"trigger_type":"schedule","name":"s66-sched","cron_expression":"* * * * *","input_payload":{"message":"What is 2+2?"}})).json()
        p2,kids2=await run_of(WFN,"schedule",30)  # ~90s for reload+fire
        out["T-S66-002 scheduler_fires_prod_run"]= bool(p2 and p2.status=="completed" and p2.context=="production" and len(kids2)>=2 and all(k.status=="completed" for k in kids2))
        # CRITICAL: delete the every-minute schedule trigger so it stops firing
        try: await c.delete(f"/workflows/{wid}/triggers/{sch['id']}")
        except Exception: pass
        if not out.get("T-S66-002 scheduler_fires_prod_run"): out["_diag_sched"]=f"sched run={getattr(p2,'status',None)}"
    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson). Without this, a bare try/finally records only
        # the cases reached BEFORE the crash and the bash summary (PASS>0, FAIL==0) reports
        # the suite GREEN while silently dropping every remaining case — a partial run must
        # never look like a pass, least of all one gating PRODUCTION triggers.
        import traceback
        out["T-S66-999 driver ran every case without crashing"]=False
        out["_diag_crash"]=(f"driver CRASHED mid-run — cases after this point never ran: "
                            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # write results BEFORE cleanup (the suite-69 lesson), then tear down. Cleanup here
        # deletes the every-minute schedule trigger and can itself hang or raise; results
        # recorded up to this point must survive it. This is also what prints SUITE-66-DONE
        # on the SKIP paths (which `return` from the try).
        for k,v in out.items():
            if k.startswith("_"): print("DIAG",k,v)
            else: print(("PASS" if v else "FAIL"), k)
        print("SUITE-66-DONE", flush=True)
        try:
            if wid:
                for t in (await c.get(f"/workflows/{wid}/triggers")).json():
                    await c.delete(f"/workflows/{wid}/triggers/{t['id']}")
                await c.delete(f"/workflows/{wid}")
            for n in NAMES: await c.delete(f"/agents/{n}")
        except Exception: pass
        await c.aclose()
asyncio.run(main())
PY
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "rm -f $OUTFILE; cd /app && PYTHONPATH=/app nohup python3 $DRIVER > $OUTFILE 2>&1 & echo launched pid \$!"
echo "  driving webhook + schedule trigger lifecycle (detached in-pod)..."
DONE=""
for i in $(seq 1 120); do
  sleep 10
  if kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- grep -q "SUITE-66-DONE" "$OUTFILE" 2>/dev/null; then DONE=1; break; fi
done
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null | grep -vE "Internal error occurred|langfuse.com" || true)
echo "$RESULT"; echo ""
if [ -z "$DONE" ]; then echo "❌ Suite 66 INCONCLUSIVE (driver did not finish in the poll window)"; exit 1; fi
# SKIP is an env limit, not a half-run: the driver returned before recording ANY case, so
# it is checked BEFORE the census (which would otherwise report both cases missing).
if echo "$RESULT" | grep -q "^SKIP"; then echo "⚠️  Suite 66 SKIPPED (env limit). Not a pass."; exit 0; fi

# grep -c exits 1 on zero matches, which `set -e` would treat as a suite error.
PASS=$(echo "$RESULT" | grep -c "^PASS" || true)
FAIL=$(echo "$RESULT" | grep -c "^FAIL" || true)

# Completeness gate (the suite-74 lesson): a suite that silently stops early must NEVER
# read as green. FAIL=0 is only a pass if every gate assertion actually RAN — an exception,
# an early return, or a truncated result file otherwise produces "0 failures" on a half-run
# gate, and this gate is what says "production triggers are proven". The schedule case
# (002) runs LAST and is the one a mid-driver death drops, leaving webhook-only green.
# REQUIRED_IDS is the ONE source of truth; add a case here and nowhere else.
REQUIRED_IDS="001 002"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RESULT" | grep -q "T-S66-$id" || MISSING="$MISSING T-S66-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S66-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$OUTFILE" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S66-COMPLETE every gate assertion ran (001-002, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" 2>/dev/null || true

echo ""
echo "=== suite-66 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "❌ Suite 66 FAILED (a production trigger did not fire a completed run)"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "❌ Suite 66 INCONCLUSIVE (no assertions ran)"; exit 1; fi
if ! echo "$RESULT" | grep -q "PASS T-S66-001"; then echo "❌ Suite 66 INCONCLUSIVE"; exit 1; fi
echo "✅ Suite 66 PASSED — production webhook + scheduled triggers proven end-to-end"
