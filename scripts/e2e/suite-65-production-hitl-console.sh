#!/usr/bin/env bash
# scripts/e2e/suite-65-production-hitl-console.sh
#
# E2E Suite 65: PRODUCTION reviewer-console HITL (P2). NO fakes.
#
# The one genuinely production-specific path (sandbox/playground uses INLINE
# self-service approval; production uses the authority-gated reviewer CONSOLE).
# Drives a real high-risk member through: park -> production approval -> console
# approve by an authority holder -> resume -> advance -> complete.
#
#   create a high-risk WORKER (bound to the risk=high refund_action tool; instructed
#   to call it ONLY on a refund request so the eval stays clean) + a normal follow-on
#   member -> deploy SANDBOX -> REAL eval-runner Jobs so eval_passed auto-sets ->
#   ATTEST adversarial_eval_passed on the worker version (PATCH — the product's real
#   sign-off gate for high-risk tools; there is no automated adversarial-eval Job) ->
#   deploy PRODUCTION -> durable workflow [worker, final] -> POST /internal/runs/start
#   (context=production) with a refund request -> the worker calls refund_action ->
#   PARKS with a context=production approval -> approve via /approvals/ console list +
#   PATCH as a platform_admin (authority) -> _resume_and_advance resumes the worker at
#   its PRODUCTION pod + advances the workflow -> poll parent to terminal.
#
# What it proves:
#   T-S65-001 — the high-risk worker reaches production only AFTER both gates:
#               eval_passed (real eval-run) AND adversarial_eval_passed (attested)
#   T-S65-002 — a production-triggered run parks with a context=production, risk=high
#               approval for refund_action (reviewer-console routed, NOT inline)
#   T-S65-003 — a platform_admin SEES that approval via the authority-scoped console
#               list and approves it (PATCH -> 200)
#   T-S65-004 — after the console decision the worker RESUMES (at its production pod),
#               the workflow ADVANCES to the next member, and the parent reaches
#               'completed' with context=production (both members completed)
#
# Real pods + real LLM + real eval Jobs + prod deploy + HITL -> slow. Driver runs
# DETACHED in-pod (PYTHONPATH=/app -> result file); this script polls with short execs.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
echo "=== Suite 65: PRODUCTION reviewer-console HITL (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

# Per-invocation paths (the suite-74 lesson): a fixed /tmp/s65_out.txt lets two
# overlapping invocations (a retry, a second operator, a CI re-run against the same pod)
# share a result file and silently read each OTHER's results.
RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s65_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s65_out_${RUN_TAG}.txt"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, AgentRun, EvalRun, Approval
BASE="http://localhost:8000/api/v1"
H={"X-User-Sub":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6","X-User-Team":"platform"}
SFX=uuid.uuid4().hex[:6]
WORK=f"s65-work-{SFX}"; FINAL=f"s65-final-{SFX}"
WORK_INSTR=("You answer factual questions. Reply with ONLY the answer — no preamble. "
            "ONLY when the user explicitly asks to process a refund, call the refund_action tool.")
FINAL_INSTR="You answer factual questions. Reply with ONLY the answer — no preamble."
ITEMS=[{"input":"What is the capital of France?","expected_output":"Paris"},
       {"input":"What is 2+2?","expected_output":"4"},
       {"input":"What color is a clear daytime sky?","expected_output":"Blue"}]
async def prov(c): return (await c.get("/llm-providers/", params={"team":"platform"})).json()["items"][0]["id"]
async def wait_run(name, env, tries=70):
    for _ in range(tries):
        async with AsyncSessionLocal() as s:
            a=(await s.execute(select(Agent).where(Agent.name==name))).scalars().first()
            d=(await s.execute(select(Deployment).where(Deployment.agent_id==a.id, Deployment.environment==env)
                               .order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
        if d and d.status=="running": return d.id
        await asyncio.sleep(3)
    return None
async def latest_version(name):
    async with AsyncSessionLocal() as s:
        a=(await s.execute(select(Agent).where(Agent.name==name))).scalars().first()
        v=(await s.execute(select(AgentVersion).where(AgentVersion.agent_id==a.id).order_by(desc(AgentVersion.created_at)).limit(1))).scalars().first()
    return str(v.id)
async def main():
    out={}; wid=None
    c=httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid=await prov(c)
    try:
        specs=[(WORK, WORK_INSTR, ["refund_action"]), (FINAL, FINAL_INSTR, [])]
        for n,instr,tools in specs:
            await c.post("/agents/", json={"name":n,"team":"platform","agent_type":"declarative","execution_shape":"durable","agent_class":"daemon","metadata":{"instructions":instr,"llm_provider_id":pid,"tools":tools}})
            await c.post(f"/agents/{n}/deploy", json={"environment":"sandbox"})
        sbx={n: await wait_run(n,"sandbox") for n,_,_ in specs}
        if not all(sbx.values()): print("SKIP sandbox deploy did not run"); return
        # eval both -> eval_passed
        ok_eval=True
        for n,_,_ in specs:
            ds=(await c.post("/playground/datasets", json={"name":f"s65-ds-{n}","mode":"reactive","items":ITEMS})).json()["id"]
            er=await c.post("/playground/eval-runs", json={"dataset_id":ds,"agent_name":n,"sandbox_deployment_id":str(sbx[n])})
            if er.status_code!=201: print(f"SKIP eval-run {n} not launched"); return
            rid=er.json()["id"]; run=None
            for _ in range(75):
                await asyncio.sleep(4)
                async with AsyncSessionLocal() as s: run=(await s.execute(select(EvalRun).where(EvalRun.id==uuid.UUID(rid)))).scalar_one_or_none()
                if run and run.status in ("completed","failed"): break
            if not run or run.status!="completed": print(f"SKIP eval-run {n} did not complete"); return
            async with AsyncSessionLocal() as s:
                a=(await s.execute(select(Agent).where(Agent.name==n))).scalars().first()
                ok_eval = ok_eval and any(getattr(v,'eval_passed',None) is True for v in (await s.execute(select(AgentVersion).where(AgentVersion.agent_id==a.id))).scalars().all())
        # attest adversarial gate on the WORKER (high-risk tool)
        vid=await latest_version(WORK)
        adv=await c.patch(f"/agents/{WORK}/versions/{vid}", json={"adversarial_eval_passed": True})
        # deploy production
        for n,_,_ in specs: await c.post(f"/agents/{n}/deploy", json={"environment":"production"})
        prod={n: await wait_run(n,"production") for n,_,_ in specs}
        out["T-S65-001 both_gates_then_production"]= bool(ok_eval and adv.status_code==200 and all(prod.values()))

        # workflow + PRODUCTION trigger with a refund request
        r=await c.post("/workflows", json={"name":f"s65-wf-{SFX}","team":"platform","orchestration":"sequential","execution_shape":"durable","agent_class":"daemon"})
        wid=r.json()["id"]
        for i,n in enumerate([WORK,FINAL]):
            aid=(await c.get(f"/agents/{n}")).json()["id"]
            await c.post(f"/workflows/{wid}/members", json={"agent_id":aid,"position":i+1})
        tr=await c.post("/internal/runs/start", json={"workflow_id":wid,"trigger_type":"manual","run_by":"suite-65","trigger_payload":{"message":"Please process a refund of $50 for order 1234."}})
        prun=tr.json().get("id") if tr.status_code==201 else None
        # wait for the park + production approval
        appr=None
        if prun:
            for _ in range(40):
                await asyncio.sleep(3)
                async with AsyncSessionLocal() as s:
                    p=(await s.execute(select(AgentRun).where(AgentRun.id==prun))).scalar_one_or_none()
                    kids=(await s.execute(select(AgentRun).where(AgentRun.parent_run_id==prun))).scalars().all()
                    for k in kids:
                        a=(await s.execute(select(Approval).where(Approval.thread_id==str(k.id), Approval.status=="pending"))).scalars().first()
                        if a: appr=a; break
                if appr or (p and p.status in ("completed","failed")): break
        out["T-S65-002 parks_production_approval_high_risk"]= bool(appr and appr.context=="production" and appr.tool_name=="refund_action" and getattr(appr,'risk_level',None)=="high")
        # console approve as platform_admin (authority-scoped list + PATCH)
        decided=False
        if appr:
            items=(await c.get("/approvals/", params={"status":"pending","context":"production"})).json().get("items",[])
            mine=[x for x in items if x["id"]==str(appr.id)]
            if mine:
                pr=await c.patch(f"/approvals/{appr.id}", json={"decision":"approved","version":mine[0]["version"],"reviewer_id":"suite-65-admin"})
                decided = pr.status_code==200
        out["T-S65-003 console_authority_sees_and_approves"]= decided
        # resume -> advance -> complete
        status=ctx=None; kids=[]
        if decided:
            for _ in range(40):
                await asyncio.sleep(3)
                async with AsyncSessionLocal() as s:
                    p=(await s.execute(select(AgentRun).where(AgentRun.id==prun))).scalar_one_or_none()
                    if p.status in ("completed","failed","cancelled"):
                        status,ctx=p.status,p.context
                        kids=(await s.execute(select(AgentRun).where(AgentRun.parent_run_id==prun))).scalars().all()
                        break
        out["T-S65-004 resume_advance_completed_production"]= bool(status=="completed" and ctx=="production" and len(kids)>=2 and all(k.status=="completed" for k in kids))
        if status!="completed":
            out["_diag"]=f"status={status} ctx={ctx} kids=" + "; ".join(f"{k.agent_name}:{k.status}" for k in kids)
    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson). Without this, a bare try/finally records only
        # the cases reached BEFORE the crash and the bash summary (PASS>0, FAIL==0) reports
        # the suite GREEN while silently dropping every remaining case — a partial run must
        # never look like a pass, least of all one gating PRODUCTION HITL.
        import traceback
        out["T-S65-999 driver ran every case without crashing"]=False
        out["_diag_crash"]=(f"driver CRASHED mid-run — cases after this point never ran: "
                            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # write results BEFORE cleanup (the suite-69 lesson), then tear down. Cleanup can
        # itself hang or raise; results recorded up to this point must survive it. This is
        # also what prints SUITE-65-DONE on the SKIP paths (which `return` from the try).
        for k,v in out.items():
            if k.startswith("_"): print("DIAG",k,v)
            else: print(("PASS" if v else "FAIL"), k)
        print("SUITE-65-DONE", flush=True)
        try:
            if wid: await c.delete(f"/workflows/{wid}")
            await c.delete(f"/agents/{WORK}"); await c.delete(f"/agents/{FINAL}")
        except Exception: pass
        await c.aclose()
asyncio.run(main())
PY

kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "rm -f $OUTFILE; cd /app && PYTHONPATH=/app nohup python3 $DRIVER > $OUTFILE 2>&1 & echo launched pid \$!"
echo "  driving production HITL lifecycle (detached in-pod)..."
DONE=""
for i in $(seq 1 120); do
  sleep 10
  if kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- grep -q "SUITE-65-DONE" "$OUTFILE" 2>/dev/null; then DONE=1; break; fi
done
RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null | grep -vE "Internal error occurred|langfuse.com" || true)
echo "$RESULT"; echo ""
if [ -z "$DONE" ]; then echo "❌ Suite 65 INCONCLUSIVE (driver did not finish in the poll window)"; exit 1; fi
# SKIP is an env limit, not a half-run: the driver returned before recording ANY case, so
# it is checked BEFORE the census (which would otherwise report all four cases missing).
if echo "$RESULT" | grep -q "^SKIP"; then echo "⚠️  Suite 65 SKIPPED (env limit — eval-runner Job / prod deploy unavailable). Not a pass."; exit 0; fi

# grep -c exits 1 on zero matches, which `set -e` would treat as a suite error.
PASS=$(echo "$RESULT" | grep -c "^PASS" || true)
FAIL=$(echo "$RESULT" | grep -c "^FAIL" || true)

# Completeness gate (the suite-74 lesson): a suite that silently stops early must NEVER
# read as green. FAIL=0 is only a pass if every gate assertion actually RAN — an exception,
# an early return, or a truncated result file otherwise produces "0 failures" on a half-run
# gate, and this gate is what says "production HITL is proven". REQUIRED_IDS is the ONE
# source of truth for "did the gate run in full"; a hardcoded case COUNT drifted immediately
# in suite-74 and cannot say WHICH case vanished. Add a case here and nowhere else.
REQUIRED_IDS="001 002 003 004"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RESULT" | grep -q "T-S65-$id" || MISSING="$MISSING T-S65-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S65-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$OUTFILE" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S65-COMPLETE every gate assertion ran (001-004, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" 2>/dev/null || true

echo ""
echo "=== suite-65 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "❌ Suite 65 FAILED (a real production-HITL assertion failed)"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "❌ Suite 65 INCONCLUSIVE (no assertions ran)"; exit 1; fi
if ! echo "$RESULT" | grep -q "PASS T-S65-004"; then echo "❌ Suite 65 INCONCLUSIVE"; exit 1; fi
echo "✅ Suite 65 PASSED — production reviewer-console HITL proven end-to-end"
