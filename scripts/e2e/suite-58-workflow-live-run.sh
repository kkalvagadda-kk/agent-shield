#!/usr/bin/env bash
# scripts/e2e/suite-58-workflow-live-run.sh
#
# E2E Suite 58: REAL durable-workflow run — NO fakes.
#
# Unlike suites 36/55/56 (which monkeypatch _run_step / resolve_edge_graph / httpx
# and never dispatch a real pod), this suite exercises the ACTUAL live path a user
# hits from the builder's "Run Workflow":
#
#   create agents → DEPLOY them (real pods) → create a durable workflow →
#   POST /workflows/{id}/runs (the real trigger) → members dispatch to their real
#   pods → each pod runs its LLM and POSTs a real step-update CALLBACK → the
#   orchestrator advances → the run COMPLETES.
#
# This is the path that hid three production bugs the faked suites could never
# catch (all found only by running it for real):
#   - the durable-member callback URL pointed at a non-existent Service (DNS fail,
#     120s timeout);
#   - Bedrock message content is a LIST of content blocks, not a str → the callback
#     500'd writing to a text column;
#   - a workflow run was hardcoded context=production so its approval never went inline.
#
#   T-S58-001 — both freshly-created agents deploy to a running state (real pods)
#   T-S58-002 — a real workflow run reaches 'completed' (real dispatch→callback→advance)
#   T-S58-003 — every member child completed with non-empty output (real LLM + callback landed)
#   T-S58-004 — the interactive builder run is context=playground (children inherit it)
#   T-S58-005 — the PARENT workflow trace carries member step-spans in Langfuse
#               (orchestrator authors a span per step — no more empty envelope)
#   T-S58-006 — EVERY member trace ingested real observations in Langfuse (guards
#               the DNS/OTLP export failure that made member traces 404 — docs/debugging/011)
#
# It creates ALL its own resources up front and tears them down. Slower than the
# logic suites (real deploy + real LLM) — that is the point.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 58: REAL durable-workflow run (no fakes) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, os, uuid
import httpx
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import AgentRun, CompositeWorkflow, Deployment, Agent

BASE="http://localhost:8000/api/v1"
SUB="system"
H={"X-User-Sub":SUB,"X-User-Team":"platform"}
SUFFIX=uuid.uuid4().hex[:8]
NAMES=[f"s58-a-{SUFFIX}", f"s58-b-{SUFFIX}"]
# Resolve a real LLM provider for team platform (don't hardcode an id).
INSTR="Reply with a single short sentence acknowledging the request. Do not ask any questions."

async def provider_id(c):
    r=await c.get("/llm-providers/", params={"team":"platform"})
    if r.status_code>=300: return None
    items=r.json()
    items=items if isinstance(items,list) else items.get("items",[])
    return items[0]["id"] if items else None

async def wait_deploy_running(names, timeout=180):
    for _ in range(timeout//5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            rows=(await s.execute(
                select(Agent.name, Deployment.status)
                .join(Deployment, Deployment.agent_id==Agent.id)
                .where(Agent.name.in_(names), Deployment.environment=="sandbox")
            )).all()
        by={n:st for (n,st) in rows}
        if all(by.get(n)=="running" for n in names):
            return True, by
        if any(by.get(n)=="failed" for n in names):
            return False, by
    return False, by

async def wait_run_terminal(run_id, timeout=150):
    for _ in range(timeout//5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            p=(await s.execute(select(AgentRun.status, AgentRun.context).where(AgentRun.id==uuid.UUID(run_id)))).first()
            kids=(await s.execute(select(AgentRun.agent_name, AgentRun.status, AgentRun.output)
                                  .where(AgentRun.parent_run_id==uuid.UUID(run_id)))).all()
        if p and p[0] in ("completed","failed","cancelled","awaiting_approval"):
            return p[0], p[1], kids
    return (p[0] if p else "timeout"), (p[1] if p else None), kids

async def main():
    out={}
    c=httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid=await provider_id(c)
    # 1. create + deploy two real durable agents
    for n in NAMES:
        await c.post("/agents/", json={"name":n,"team":"platform","agent_type":"declarative",
            "execution_shape":"durable","agent_class":"daemon",
            "metadata":{"instructions":INSTR,"llm_provider_id":pid,"tools":[]}})
        await c.post(f"/agents/{n}/deploy", json={"environment":"sandbox"})
    ok, statuses = await wait_deploy_running(NAMES)
    out["001_agents_deployed_running"]= ok

    wid=None
    try:
        if ok:
            # 2. real durable sequential workflow over the two members
            r=await c.post("/workflows", json={"name":f"s58-wf-{SUFFIX}","team":"platform",
                "orchestration":"sequential","execution_shape":"durable","agent_class":"daemon"})
            wid=r.json()["id"]
            aid={}
            for i,n in enumerate(NAMES):
                g=await c.get(f"/agents/{n}"); aid[n]=g.json()["id"]
                await c.post(f"/workflows/{wid}/members", json={"agent_id":aid[n],"position":i+1})
            # 3. REAL trigger — the exact endpoint the builder Run panel calls
            r=await c.post(f"/workflows/{wid}/runs", json={"input_payload":{"message":"process this please"},"run_by":"suite-58"})
            run_id=r.json()["run_id"]
            status, ctx, kids = await wait_run_terminal(run_id)
            out["002_run_completed"]= (status=="completed")
            out["003_members_completed_with_output"]= (len(kids)>=2 and all(k[1]=="completed" and (k[2] or "").strip() for k in kids))
            out["004_context_playground"]= (ctx=="playground")
            if status!="completed":
                out["_diag"]=f"status={status} kids=" + "; ".join(f"{k[0]}:{k[1]}" for k in kids)

            # 4. TRACE INGESTION (T-S58-005): the parent workflow trace AND every
            #    member trace must carry real observations in Langfuse. Guards the
            #    class of bug in docs/debugging/011: member spans failing to export
            #    (DNS) → member traces 404, and the parent envelope being authored
            #    with zero spans → "No span-level observations". NOT a fake: it
            #    reads the actual Langfuse backend for the real run's traces.
            if status=="completed":
                from tracing import get_langfuse, _lf_trace_id
                async with AsyncSessionLocal() as s:
                    prow=(await s.execute(select(AgentRun.langfuse_trace_id).where(AgentRun.id==uuid.UUID(run_id)))).first()
                    krows=(await s.execute(select(AgentRun.agent_name, AgentRun.langfuse_trace_id)
                                           .where(AgentRun.parent_run_id==uuid.UUID(run_id)))).all()
                lf=get_langfuse()
                def obs_count(tid):
                    if not lf or not tid: return None
                    try: return len(getattr(lf.fetch_trace(_lf_trace_id(tid)).data, "observations", []) or [])
                    except Exception: return None  # 404 / unreachable
                if lf is None:
                    out["_diag_trace"]="Langfuse client unconfigured in this env — trace check skipped (not a pass)"
                else:
                    parent_obs=None; member_obs={}
                    for _ in range(14):  # ~70s for async OTLP ingestion
                        await asyncio.sleep(5)
                        parent_obs=obs_count(prow[0] if prow else None)
                        member_obs={k[0]: obs_count(k[1]) for k in krows}
                        if (isinstance(parent_obs,int) and parent_obs>0
                                and member_obs and all(isinstance(v,int) and v>0 for v in member_obs.values())):
                            break
                    # PASS only if the parent has a step span AND every member trace ingested spans.
                    out["005_parent_trace_has_member_spans"]= bool(isinstance(parent_obs,int) and parent_obs>0)
                    out["006_every_member_trace_has_observations"]= bool(
                        member_obs and all(isinstance(v,int) and v>0 for v in member_obs.values()))
                    out["_diag_trace"]=f"parent_obs={parent_obs} member_obs={member_obs}"
        else:
            out["_diag"]=f"deploy statuses={statuses}"
    finally:
        # cleanup
        if wid: await c.delete(f"/workflows/{wid}")
        for n in NAMES: await c.delete(f"/agents/{n}")

    for k,v in out.items():
        if k.startswith("_"): print("DIAG", k, v)
        else: print(("PASS" if v else "FAIL"), k)

asyncio.run(main())
PY
)

echo "$RESULT"
echo ""
if echo "$RESULT" | grep -q "FAIL"; then
  echo "❌ Suite 58 FAILED"
  exit 1
fi
if ! echo "$RESULT" | grep -q "PASS 002_run_completed"; then
  echo "❌ Suite 58 INCONCLUSIVE (run did not complete)"
  exit 1
fi
echo "✅ Suite 58 PASSED"
