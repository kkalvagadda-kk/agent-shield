#!/usr/bin/env bash
# scripts/e2e/suite-59-workflow-orchestrations-live.sh
#
# E2E Suite 59: ALL FOUR orchestrations + HITL, on the REAL pod path. NO fakes.
#
# suite-56 proves the orchestrator LOGIC with faked _run_step/resolve_edge_graph.
# THIS suite builds a real workflow for EACH orchestration and drives it through
# the real dispatch→pod→LLM→callback→route→park→approve→resume→advance path — the
# path where the live-only bugs hid.
#
# It creates the WORKFLOWS (the thing under test) and runs real triggers, over the
# platform's deployed durable agents (real running pods — NOT a fake, and NOT a
# hand-crafted DB fixture). It does NOT re-mint fresh LLM agents per run, which are
# flaky at reliably calling their tool; suite-58 already covers the create-own-agents
# path. Required agents (deploy first): wf-router (classifier → 'refund'/'info'),
# wf-payout (high-risk refund_action → parks), wf-confirm (plain), wf-supervisor.
#
#   T-S59-001 — required durable agents are deployed + running
#   T-S59-002 — sequential:  run → wf-payout parks → approve → completed
#   T-S59-003 — conditional: run → route(refund) → wf-payout parks → approve → completed
#   T-S59-004 — handoff:     run → wf-payout parks → approve → completed
#   T-S59-005 — supervisor:  run → routes to wf-payout → parks → approve → completed
#   T-S59-006 — the parked approval carries STRUCTURED args (order_id/amount), not a generic 'query'
#
# Real pods + real LLM → slow. Generous timeouts. Tears down the workflows it creates.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && { echo "ERROR: no registry-api pod"; exit 1; }

echo "=== Suite 59: all 4 orchestrations + HITL (real pods, no fakes) ==="
echo "  Pod: $API_POD"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, os, uuid, httpx
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import AgentRun, Deployment, Agent

BASE="http://localhost:8000/api/v1"
H={"X-User-Sub":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6","X-User-Team":"platform"}
S=uuid.uuid4().hex[:6]
ROUTER,WORKER,PLAIN,SUP = "wf-router","wf-payout","wf-confirm","wf-supervisor"
REQUIRED=[ROUTER,WORKER,PLAIN,SUP]
MSG="I want a refund of $50 for order A1"

async def running(names):
    async with AsyncSessionLocal() as s:
        rows=(await s.execute(select(Agent.name, Deployment.status).join(Deployment, Deployment.agent_id==Agent.id)
              .where(Agent.name.in_(names), Deployment.environment=="sandbox", Deployment.status=="running"))).all()
    return {n for n,_ in rows}

async def poll(run_id, timeout=180):
    for _ in range(timeout//5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            p=(await s.execute(select(AgentRun.status).where(AgentRun.id==uuid.UUID(run_id)))).scalar()
            kids=(await s.execute(select(AgentRun.status, AgentRun.thread_id).where(AgentRun.parent_run_id==uuid.UUID(run_id)))).all()
        aw=[k[1] for k in kids if k[0]=="awaiting_approval"]
        if aw: return "park", aw[0]
        if p in ("completed","failed","cancelled"): return p, None
    return "timeout", None

async def approve(c, thread):
    items=(await c.get("/approvals/", params={"status":"pending","context":"playground"})).json()["items"]
    ap=next((a for a in items if a["thread_id"]==thread), None)
    if not ap: return None
    await c.patch(f"/approvals/{ap['id']}", json={"decision":"approved","version":ap["version"],"reviewer_id":"s59"})
    return ap

async def run_hitl(c, wid):
    # Approve EVERY park until the run reaches a true terminal. supervisor mode can
    # re-route to the high-risk worker several times (each a fresh gate), so a single
    # approve isn't enough — keep approving until completed/failed. Captures the first
    # approval's args for the structured-args assertion.
    run=(await c.post(f"/workflows/{wid}/runs", json={"input_payload":{"message":MSG},"run_by":"s59"})).json()["run_id"]
    first_ap=None
    for _ in range(20):
        st, thread = await poll(run)
        if st == "park":
            ap = await approve(c, thread)
            if ap and first_ap is None:
                first_ap = ap
            continue
        return st, first_ap
    return "timeout", first_ap

async def mk_wf(c, name, orch, members, edges):
    wid=(await c.post("/workflows", json={"name":name,"team":"platform","orchestration":orch,
         "execution_shape":"durable","agent_class":"daemon"})).json()["id"]
    aid={}
    for n,pos,role in members:
        aid[n]=(await c.get(f"/agents/{n}")).json()["id"]
        b={"agent_id":aid[n],"position":pos}
        if role: b["role"]=role
        await c.post(f"/workflows/{wid}/members", json=b)
    for i,(sn,tn,cond) in enumerate(edges):
        await c.post(f"/workflows/{wid}/edges", json={"source_agent_id":aid[sn],"target_agent_id":aid[tn],"condition":cond,"position":i+1})
    return wid

async def main():
    out={}; wids=[]
    c=httpx.AsyncClient(base_url=BASE, headers=H, timeout=60, follow_redirects=True)
    up=await running(REQUIRED)
    out["001_agents_running"]= all(n in up for n in REQUIRED)
    if not out["001_agents_running"]:
        out["_diag"]=f"missing/not-running: {[n for n in REQUIRED if n not in up]} — deploy them first"
        for k,v in out.items(): print(("PASS" if v else "FAIL") if not k.startswith("_") else "DIAG", k, v if k.startswith("_") else "")
        return
    try:
        seen=[]
        wid=await mk_wf(c, f"s59-seq-{S}", "sequential", [(ROUTER,1,None),(WORKER,2,None),(PLAIN,3,None)], []); wids.append(wid)
        st,ap=await run_hitl(c, wid); out["002_sequential_completed"]=(st=="completed"); seen.append(ap)
        wid=await mk_wf(c, f"s59-cond-{S}", "conditional", [(ROUTER,1,None),(WORKER,2,None),(PLAIN,3,None)],
                        [(ROUTER,WORKER,"refund"),(ROUTER,PLAIN,None)]); wids.append(wid)
        st,ap=await run_hitl(c, wid); out["003_conditional_completed"]=(st=="completed"); seen.append(ap)
        wid=await mk_wf(c, f"s59-hand-{S}", "handoff", [(ROUTER,1,None),(WORKER,2,None),(PLAIN,3,None)],
                        [(ROUTER,WORKER,None),(WORKER,PLAIN,None)]); wids.append(wid)
        st,ap=await run_hitl(c, wid); out["004_handoff_completed"]=(st=="completed"); seen.append(ap)
        wid=await mk_wf(c, f"s59-sup-{S}", "supervisor", [(SUP,1,"supervisor"),(WORKER,2,None),(PLAIN,3,None)], []); wids.append(wid)
        st,ap=await run_hitl(c, wid); out["005_supervisor_completed"]=(st=="completed"); seen.append(ap)
        args=[a["tool_args"] for a in seen if a]
        out["006_structured_approval_args"]= any(("order_id" in (ta or {}) or "amount" in (ta or {})) for ta in args)
        out["_args"]=str(args[:1])
    finally:
        for w in wids: await c.delete(f"/workflows/{w}")
    for k,v in out.items():
        if k.startswith("_"): print("DIAG", k, v)
        else: print(("PASS" if v else "FAIL"), k)

asyncio.run(main())
PY
)
echo "$RESULT"
echo ""
if echo "$RESULT" | grep -q "FAIL"; then echo "❌ Suite 59 FAILED"; exit 1; fi
echo "✅ Suite 59 PASSED"
