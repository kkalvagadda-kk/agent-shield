#!/usr/bin/env bash
# =============================================================================
# poc1-poc2-prod-verify.sh — LIVE production verification of context-storage
# POC-1 (shared workflow thread) + POC-2 (per-member attribution).
#
# trigger-demo-flow can't prove POC-1 (it is execution_shape=durable +
# memory_enabled=False). This creates a REACTIVE, memory-ENABLED 2-member
# workflow, deploys both members to PRODUCTION, runs it, and asserts:
#   POC-1: the scope=workflow_run shared transcript has BOTH members' turns.
#   POC-2: the run tree has 2 completed, named member children.
#
# Run it yourself (production deploy = your explicit action):
#   KUBECONFIG=~/.kube/test-cluster-kube-config.yaml bash scripts/verify/poc1-poc2-prod-verify.sh
# =============================================================================
set -euo pipefail
NS="${NS:-agentshield-platform}"
echo "=== POC-1/POC-2 PRODUCTION verification ==="

kubectl exec -i -n "$NS" deploy/agentshield-registry-api -c registry-api -- python3 - <<'PY'
import asyncio, httpx, time
BASE="http://localhost:8000/api/v1"
H={"X-User-Sub":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6","X-User-Team":"platform"}
SFX=str(int(time.time()))[-5:]
A=f"poc1prod-a-{SFX}"; B=f"poc1prod-b-{SFX}"; WF=f"poc1-shared-prod-{SFX}"

async def main():
    async with httpx.AsyncClient(timeout=120, headers=H) as c:
        pid=(await c.get(f"{BASE}/llm-providers/?team=platform")).json().get("items",[{}])[0].get("id")
        assert pid, "no LLM provider for team platform"
        print(f"provider={pid}")
        instrs={A:"You are Member A. Emit exactly one secret code word: KANGAROO. Say nothing else.",
                B:"You are Member B. Read Member A's turn from the shared conversation and repeat A's secret code word verbatim."}
        for n in (A,B):
            r=await c.post(f"{BASE}/agents/",json={"name":n,"team":"platform","agent_type":"declarative",
                "execution_shape":"reactive","memory_enabled":True,
                "metadata":{"instructions":instrs[n],"llm_provider_id":pid,"tools":[]}})
            print(f"create {n}: {r.status_code}")
            d=await c.post(f"{BASE}/agents/{n}/deploy",json={"environment":"production"})
            print(f"  deploy production {n}: {d.status_code} {d.text[:120] if d.status_code>=400 else ''}")
        rw=await c.post(f"{BASE}/workflows",json={"name":WF,"team":"platform","orchestration":"sequential",
            "execution_shape":"reactive","memory_enabled":True})
        wid=rw.json()["id"]; print(f"workflow {WF}: {rw.status_code} {wid}")
        for i,n in enumerate((A,B)):
            aid=(await c.get(f"{BASE}/agents/{n}")).json()["id"]
            m=await c.post(f"{BASE}/workflows/{wid}/members",json={"agent_id":aid,"position":i+1})
            print(f"  member {n}: {m.status_code}")

        # Retry the run until both member pods are Ready (a run dispatch-fails until then).
        print("\n--- running (retries until member pods warm) ---")
        tree=None
        for attempt in range(8):
            rr=await c.post(f"{BASE}/workflows/{wid}/runs",json={
                "input_payload":{"message":"Begin."},"trigger_type":"api",
                "run_by":"75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"})
            run_id=rr.json().get("run_id")
            for _ in range(40):
                await asyncio.sleep(2)
                tj=(await c.get(f"{BASE}/workflows/{wid}/runs/{run_id}/tree")).json()
                st=tj.get("parent",{}).get("status"); kids=tj.get("children",[])
                if st in ("completed","failed"):
                    dispatch_fail=any("dispatch failed" in (k.get("error_message") or "")
                                      or "not known" in (k.get("output") or "") for k in kids)
                    if st=="completed" and len(kids)>=2:
                        tree=(tj,run_id); break
                    if st=="failed" and dispatch_fail:
                        print(f"  attempt {attempt}: members not warm yet, retrying...")
                    else:
                        tree=(tj,run_id); break  # terminal, non-warmup outcome
                    break
            if tree: break
            await asyncio.sleep(10)

        assert tree, "no terminal run obtained"
        tj,run_id=tree
        kids=tj.get("children",[])
        print(f"\n--- RUN {run_id} parent={tj['parent'].get('status')} children={len(kids)} ---")
        for k in kids:
            print(f"   member {k.get('agent_name')}: {k.get('status')} -> {(k.get('output') or k.get('error_message') or '')[:70]!r}")

        m=await c.get(f"{BASE}/agents/{A}/memory",params={"scope":"workflow_run","thread_id":run_id})
        rows=m.json() if m.status_code==200 else []
        authors=sorted({r.get('agent_name') for r in rows})
        print(f"\n--- SHARED TRANSCRIPT (scope=workflow_run) rows={len(rows)} authors={authors} ---")
        for r in rows:
            print(f"   [{r.get('message_index')}] {r.get('agent_name')} ({r.get('message_kind')}): {(r.get('content') or '')[:55]!r}")

        # Verdicts
        poc2 = (tj['parent'].get('status')=="completed"
                and len([k for k in kids if k.get('status')=="completed"])>=2
                and len({k.get('agent_name') for k in kids if k.get('agent_name')})>=2)
        poc1 = len(authors)>=2
        print(f"\n=== VERDICT (production) ===")
        print(f"  POC-2 attribution (2 completed named members): {'PASS' if poc2 else 'FAIL'}")
        print(f"  POC-1 shared thread  (>=2 authors in one transcript): {'PASS' if poc1 else 'FAIL'}")
        print(f"  workflow={WF} members={A},{B} run={run_id}")
        if not (poc1 and poc2): raise SystemExit(1)

asyncio.run(main())
PY
echo "=== done ==="
