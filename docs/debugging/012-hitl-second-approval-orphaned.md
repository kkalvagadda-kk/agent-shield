# Debugging: reactive workflow member's SECOND HITL approval is orphaned on resume

**Date:** 2026-07-18
**Symptom:** After approving an inline HITL prompt in a reactive workflow chat, the run finished but the agent's answer was just the echoed question (no tool result); a second approval sat `pending` forever.
**Resolution:** 1 root cause in `routers/approvals.py:_resume_and_advance` (asymmetric resume vs forward path). **Fixed:** registry-api `0.2.206`.
**Postmortem:** [`docs/bugs/hitl-multi-approval-resume-regression.md`](../bugs/hitl-multi-approval-resume-regression.md). **Regression test:** `scripts/e2e/suite-79-workflow-hitl.sh` T-S79-004b.

---

## 1. Expected chain (map it before touching logs)
```
member /chat/stream parks (interrupt) → Approval#A pending → inline decide (approve)
  → registry _resume_and_advance → POST pod /resume/{thread}
    → pod resumes graph → model calls web_search AGAIN → interrupt() → Approval#B pending, pod re-parks (HTTP 200)
      → registry MUST re-park the member (not advance)   ← this step was missing
        → user approves #B → resume again → no more gates → member completes with the real answer → workflow advances
```
Key insight (matches the playbook's P0): the pod returning **HTTP 200** looked like "done", but the authoritative "still parked" signal is *a pending Approval on the thread* — the message named the wrong layer.

## 2. Investigation (bottom-up, exact commands)

### 2a. The run tree — member output == input
```bash
POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
kubectl exec -i -n agentshield-platform "$POD" -c registry-api -- python3 - <<'PY'
import httpx
H={"X-User-Sub":"047fad5f-f38c-430a-bfba-6e4d9009314b","X-User-Team":"platform"}
c=httpx.Client(base_url="http://localhost:8000/api/v1",headers=H,timeout=30,follow_redirects=True)
WID="ab263904-9a32-4528-a72b-4f38b0066a93"; RID="e2a56353-552b-43ac-b6fb-3885bec6417c"
j=c.get(f"/workflows/{WID}/runs/{RID}/tree").json()
print("parent", j["parent"]["status"], "output=", j["parent"]["output"][:80])
for ch in j["children"]:
    print(ch["agent_name"], ch["status"], "in=", (ch["input"] or "")[:40], "out=", (ch["output"] or "")[:40])
PY
```
**Evidence:** `researcher-agent completed  in="what is the weather…"  out="what is the weather…"` — output identical to input (the echo). Parent `completed`.

### 2b. The approvals + run_steps for the thread (the smoking gun)
```bash
kubectl exec -i -n agentshield-platform "$POD" -c registry-api -- python3 - <<'PY'
import os, asyncio, asyncpg
url=(os.environ.get("DIRECT_DATABASE_URL") or os.environ.get("DATABASE_URL")).replace("+asyncpg","")
async def main():
    conn=await asyncpg.connect(url); TH="ed039d9a09a242ebb3dae3636e880353"; CH="4a6f848c-542c-48fb-8037-5109c305037c"
    for r in await conn.fetch("SELECT tool_name,status,left(tool_args::text,60) a FROM approvals WHERE thread_id=$1",TH): print("appr",dict(r))
    for r in await conn.fetch("SELECT name,status,left(output::text,50) o FROM run_steps WHERE run_id=$1 ORDER BY step_number",CH): print("step",dict(r))
    await conn.close()
asyncio.run(main())
PY
```
**Evidence:** two approvals — query A `approved`, query B **`pending`** — but only **one** `web_search` run_step. The second gate was created but never delivered.

### 2c. Pod logs — the pod DID re-park
```bash
kubectl logs -n agents-platform <researcher-agent-sandbox-pod> -c researcher-agent --tail=4000 \
  | grep -iE "approval record created|on_interrupt|POST /resume"
```
**Evidence:** `HITL approval record created id=8446cf3c …` (A) → `POST /resume/ed039d9a… 200 OK` → `on_interrupt` → `HITL approval record created id=41f3ca38 …` (B). The pod re-parked correctly; the registry did not.

### 2d. Confirm the asymmetry in code
- Forward pause detection: `services/registry-api/workflow_orchestrator.py` ~L616-624 — `SELECT Approval WHERE thread_id=?, status='pending'` → `status='awaiting_approval'`.
- Resume, no such check: `services/registry-api/routers/approvals.py:_resume_and_advance` — `member_status = "completed" if resp.status_code == 200 else "failed"` then closes the child + `resume_orchestration`.
- Pod resume ignores re-interrupt: `services/declarative-runner/workflow_executor.py:resume()` — single `ainvoke(Command(resume=…))`, returns `messages[-1].content`, never checks `result["__interrupt__"]`.

## 3. Root cause
See the postmortem. In one line: **the resume path trusted the pod's HTTP 200 as "completed" and never re-checked for a new pending approval, so a second HITL park was silently dropped and the run advanced.**

## 4. Fix + proof (reproduce-first)
- **Reproduce-first (RED):** added `suite-79` **T-S79-004b** — park a member, seed a 2nd `pending` approval on the thread (direct insert, distinct args), approve the 1st inline, assert the run never completes while an approval is pending. Against `0.2.205`: `FAIL T-S79-004b re-park-missing (run completed while a 2nd approval was still pending — gate orphaned)`.
- **Fix:** mirror the forward-path pause detection into `_resume_and_advance` (re-park if a pending approval remains; do not advance). registry-api `0.2.206`.
- **GREEN:** against `0.2.206`: `PASS T-S79-004b re-parked (parent=awaiting_approval, held; never completed with a pending approval)`; T-S79-001/002/003 still PASS.

## 5. Meta-lesson (fold into the playbook)
A downstream success signal from another service (an HTTP 200, an empty error) is **not** proof of the end-state you care about — re-derive the end-state from the authoritative store (here: "is a gate still pending on this thread?"). When two code paths (forward vs resume) must uphold the same invariant, verify **both** enforce it; a shared-core fix is proven per consumer, not assumed.
