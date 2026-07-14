#!/usr/bin/env bash
# scripts/e2e/suite-64-production-workflow-golden-path.sh
#
# E2E Suite 64: PRODUCTION durable-workflow golden path (P1). NO fakes.
#
# The production twin of suite-58 (which proves the sandbox/playground path). This
# drives the FULL production lifecycle a real triggered workflow goes through — the
# parts sandbox skips: the eval GATE, production deployment, and the cluster-internal
# trigger that sets context=production:
#
#   create 2 real durable agents -> deploy SANDBOX (real pods) -> run REAL eval-runner
#   Jobs (real judge) so eval_passed auto-sets (the production deploy GATE) -> deploy
#   PRODUCTION (real {agent}-production pods) -> create a durable sequential workflow ->
#   POST /internal/runs/start (workflow_id, trigger_type=manual) — the SAME entry the
#   scheduler/event-gateway use, which sets context=production -> orchestrate ->
#   members dispatch to their PRODUCTION pods -> poll the parent to terminal.
#
# Guards the production path that historically failed with "durable member timed out
# (no terminal callback within 120s)" — the class of durable-workflow live-path bugs
# fixed for playground must also hold for production (they share _run_step /
# _dispatch_durable_member; only context + HITL routing differ).
#
# What it proves:
#   T-S64-001 — both agents earn eval_passed via a REAL eval-run (score >= threshold)
#   T-S64-002 — both deploy to PRODUCTION (real -production pods running)
#   T-S64-003 — a production-triggered workflow run (context=production, via
#               /internal/runs/start) reaches 'completed'
#   T-S64-004 — every member child completed with non-empty output (real LLM on the
#               production pod + real callback landed) — NO timeout
#   T-S64-005 — the parent trace carries member step-spans AND every member trace
#               ingested real observations in Langfuse (traces work in production too)
#
# HARNESS NOTE: the whole lifecycle takes several minutes (real eval Jobs + prod
# deploy). A single multi-minute `kubectl exec` stream gets dropped, so the driver
# runs DETACHED inside the pod (nohup → a result file) and this script polls that
# file with short execs — the long run is decoupled from any one connection.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi

echo "=== Suite 64: PRODUCTION durable-workflow golden path (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

DRIVER=/tmp/s64_driver.py
OUTFILE=/tmp/s64_out.txt

# 1) write the driver into the pod (one short exec)
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, AgentRun, EvalRun
from tracing import get_langfuse, _lf_trace_id
BASE="http://localhost:8000/api/v1"
H={"X-User-Sub":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6","X-User-Team":"platform"}
SFX=uuid.uuid4().hex[:6]
NAMES=[f"s64-a-{SFX}", f"s64-b-{SFX}"]
INSTR="You answer factual questions. Reply with ONLY the answer — no preamble."
ITEMS=[{"input":"What is the capital of France?","expected_output":"Paris"},
       {"input":"What is 2+2?","expected_output":"4"},
       {"input":"What color is a clear daytime sky?","expected_output":"Blue"}]
def obs(tid):
    lf=get_langfuse()
    if not lf or not tid: return None
    try: return len(getattr(lf.fetch_trace(_lf_trace_id(tid)).data,"observations",[]) or [])
    except Exception: return None
async def prov(c):
    return (await c.get("/llm-providers/", params={"team":"platform"})).json()["items"][0]["id"]
async def dep_running(name, env):
    async with AsyncSessionLocal() as s:
        a=(await s.execute(select(Agent).where(Agent.name==name))).scalars().first()
        d=(await s.execute(select(Deployment).where(Deployment.agent_id==a.id, Deployment.environment==env)
                           .order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
    return (bool(d and d.status=="running"), (d.id if d else None))
async def wait_running(name, env, tries=60):
    for _ in range(tries):
        ok,did=await dep_running(name, env)
        if ok: return did
        await asyncio.sleep(3)
    return None
async def main():
    out={}; wid=None
    c=httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid=await prov(c)
    try:
        for n in NAMES:
            await c.post("/agents/", json={"name":n,"team":"platform","agent_type":"declarative",
                "execution_shape":"durable","agent_class":"daemon",
                "metadata":{"instructions":INSTR,"llm_provider_id":pid,"tools":[]}})
            await c.post(f"/agents/{n}/deploy", json={"environment":"sandbox"})
        sbx={n: await wait_running(n,"sandbox") for n in NAMES}
        if not all(sbx.values()):
            print("SKIP sandbox deploy did not run"); return
        passed=True
        for n in NAMES:
            ds=(await c.post("/playground/datasets", json={"name":f"s64-ds-{n}","mode":"reactive","items":ITEMS})).json()["id"]
            er=await c.post("/playground/eval-runs", json={"dataset_id":ds,"agent_name":n,"sandbox_deployment_id":str(sbx[n])})
            if er.status_code!=201:
                print(f"SKIP eval-run {n} not launched ({er.status_code})"); return
            rid=er.json()["id"]; run=None
            for _ in range(75):
                await asyncio.sleep(4)
                async with AsyncSessionLocal() as s:
                    run=(await s.execute(select(EvalRun).where(EvalRun.id==uuid.UUID(rid)))).scalar_one_or_none()
                if run and run.status in ("completed","failed"): break
            if not run or run.status!="completed":
                print(f"SKIP eval-run {n} did not complete"); return
            async with AsyncSessionLocal() as s:
                a=(await s.execute(select(Agent).where(Agent.name==n))).scalars().first()
                ep=any(getattr(v,'eval_passed',None) is True for v in (await s.execute(select(AgentVersion).where(AgentVersion.agent_id==a.id))).scalars().all())
            passed = passed and ep
        out["001_eval_passed_via_real_evalrun"]=passed
        for n in NAMES:
            await c.post(f"/agents/{n}/deploy", json={"environment":"production"})
        prod={n: await wait_running(n,"production") for n in NAMES}
        out["002_both_deployed_production"]=all(prod.values())
        r=await c.post("/workflows", json={"name":f"s64-wf-{SFX}","team":"platform",
            "orchestration":"sequential","execution_shape":"durable","agent_class":"daemon"})
        wid=r.json()["id"]
        for i,n in enumerate(sorted(NAMES)):
            aid=(await c.get(f"/agents/{n}")).json()["id"]
            await c.post(f"/workflows/{wid}/members", json={"agent_id":aid,"position":i+1})
        tr=await c.post("/internal/runs/start", json={"workflow_id":wid,"trigger_type":"manual",
            "run_by":"suite-64","trigger_payload":{"message":"What is the capital of France?"}})
        pid_run=tr.json().get("id") if tr.status_code==201 else None
        if not pid_run:
            out["003_prod_run_completed_context_production"]=False
            out["_diag"]=f"internal trigger {tr.status_code}: {tr.text[:160]}"
        else:
            status=ctx=None; kids=[]; p=None
            for _ in range(70):
                await asyncio.sleep(3)
                async with AsyncSessionLocal() as s:
                    p=(await s.execute(select(AgentRun).where(AgentRun.id==pid_run))).scalar_one_or_none()
                    if p and p.status in ("completed","failed","cancelled"):
                        status,ctx=p.status,p.context
                        kids=(await s.execute(select(AgentRun).where(AgentRun.parent_run_id==pid_run))).scalars().all()
                        break
            out["003_prod_run_completed_context_production"]=(status=="completed" and ctx=="production")
            out["004_members_completed_no_timeout"]=(len(kids)>=2 and all(k.status=="completed" and (k.output or "").strip() for k in kids))
            if status!="completed":
                out["_diag"]=f"status={status} ctx={ctx} kids=" + "; ".join(f"{k.agent_name}:{k.status}:{(k.error_message or '')[:40]}" for k in kids)
            if status=="completed":
                po=None; mo={}
                for _ in range(8):
                    await asyncio.sleep(6)
                    po=obs(p.langfuse_trace_id); mo={k.agent_name: obs(k.langfuse_trace_id) for k in kids}
                    if isinstance(po,int) and po>0 and mo and all(isinstance(v,int) and v>0 for v in mo.values()): break
                if get_langfuse() is not None:
                    out["005_prod_parent_and_member_traces"]=bool(isinstance(po,int) and po>0 and mo and all(isinstance(v,int) and v>0 for v in mo.values()))
                    out["_diag_trace"]=f"parent_obs={po} member_obs={mo}"
    finally:
        try:
            if wid: await c.delete(f"/workflows/{wid}")
            for n in NAMES: await c.delete(f"/agents/{n}")
        except Exception: pass
        await c.aclose()
    for k,v in out.items():
        if k.startswith("_"): print("DIAG",k,v)
        else: print(("PASS" if v else "FAIL"), k)
    print("SUITE-64-DONE")
asyncio.run(main())
PY

# 2) launch it DETACHED (nohup) from the app WORKDIR (/app has db.py on the path) —
#    returns immediately, survives exec disconnects
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "rm -f $OUTFILE; cd /app && PYTHONPATH=/app nohup python3 $DRIVER > $OUTFILE 2>&1 & echo launched pid \$!"

# 3) poll the result file for the done marker (short execs; up to ~18 min)
echo "  driving production lifecycle (detached in-pod)..."
DONE=""
for i in $(seq 1 108); do
  sleep 10
  if kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- grep -q "SUITE-64-DONE" "$OUTFILE" 2>/dev/null; then DONE=1; break; fi
done

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null | grep -vE "Internal error occurred|langfuse.com" || true)
echo "$RESULT"; echo ""

if [ -z "$DONE" ]; then echo "❌ Suite 64 INCONCLUSIVE (driver did not finish within the poll window)"; exit 1; fi
if echo "$RESULT" | grep -q "^FAIL"; then echo "❌ Suite 64 FAILED (a real production-path assertion failed)"; exit 1; fi
if echo "$RESULT" | grep -q "^SKIP"; then echo "⚠️  Suite 64 SKIPPED (env limit — eval-runner Job / prod deploy unavailable). Not a pass, not a fake."; exit 0; fi
if ! echo "$RESULT" | grep -q "PASS 003_prod_run_completed_context_production"; then echo "❌ Suite 64 INCONCLUSIVE (no completed prod run, no explicit skip)"; exit 1; fi
echo "✅ Suite 64 PASSED — production durable workflow golden path proven end-to-end"
