#!/usr/bin/env bash
# suite-79-workflow-hitl.sh — Reactive-workflow inline HITL: park → inline approve → resume → complete.
#
# Guards the regression the browser found: a reactive workflow member's high-risk tool
# must park the workflow with an INLINE (playground) approval — NOT a production-console
# one — and deciding it inline must resume the member pod and ADVANCE the orchestration to
# completion. Covers the four backend fixes:
#   - _derive_context matches the member child by thread_id column (was id-only → production)
#   - reactive workflows park+resume (the execution-models-v2 fail-closed was reverted)
#   - the member resume posts to the agent's ACTUAL env pod (was -production DNS-fail)
#   - the playground decide triggers _resume_and_advance for workflow members
#
# Fixture: WF_ID (default research-summarize: reactive, sequential, members researcher-agent
# [web_search risk=high] + summarization-agent). Its members need a running sandbox deployment
# + the serper credential. If the model doesn't call web_search (Ollama non-determinism) the
# HITL cases SKIP (same best-effort boundary the other UI/agent suites accept).
set -euo pipefail

WF_ID="${WF_ID:-ab263904-9a32-4528-a72b-4f38b0066a93}"
PASS=0; FAIL=0; SKIP=0

POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$POD" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

echo ""
echo "=== Suite 79: Reactive-Workflow inline HITL (park → approve → resume → complete) ==="
echo ""

# One driver proves the whole journey and prints PASS/SKIP/FAIL lines the harness greps.
RESULT=$(kubectl exec -i -n agentshield-platform "$POD" -c registry-api -- python3 - "$WF_ID" <<'PY'
import asyncio, json, sys, time, httpx
WID=sys.argv[1]
async def main():
    base="http://localhost:8000/api/v1"
    tr=httpx.post('http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
        data={'grant_type':'password','client_id':'agentshield-studio','username':'platform-admin','password':'PlatformAdmin2024'},timeout=10)
    if tr.status_code!=200:
        print(f"SKIP T-S79-001 no-token={tr.status_code}"); return
    auth={'Authorization':f"Bearer {tr.json()['access_token']}"}
    appr=None; run_id=None
    async with httpx.AsyncClient(timeout=150) as c:
        try:
            async with c.stream('POST', f"{base}/workflows/{WID}/runs/stream",
                    json={"message":"Search the web for the current weather in Austin, Texas.","session_id":"s79-hitl"}, headers=auth) as resp:
                if resp.status_code!=200:
                    print(f"SKIP T-S79-001 stream={resp.status_code}"); return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"): continue
                    try: ev=json.loads(line[5:].strip())
                    except: continue
                    if ev.get("type")=="approval_requested": appr=ev
                    if ev.get("type")=="done": run_id=ev.get("run_id"); break
        except Exception as e:
            print(f"SKIP T-S79-001 stream-exc={e!r}"); return
    if not appr:
        print("SKIP T-S79-001 no-approval (model did not call web_search)"); return
    aid=appr["approval_id"]
    c=httpx.Client(base_url=base, headers=auth, timeout=20)

    # T-S79-001: the member approval is INLINE (playground), NOT production console.
    ctx=c.get(f"/approvals/{aid}").json().get("context")
    print("PASS T-S79-001" if ctx in ("playground","sandbox") else f"FAIL T-S79-001 context={ctx} (want playground/sandbox)")

    # T-S79-002: the workflow PARKED (awaiting_approval), not failed.
    st_before=None
    if run_id:
        t=c.get(f"/workflows/{WID}/runs/{run_id}/tree")
        if t.status_code==200: st_before=t.json().get("parent",{}).get("status")
    print("PASS T-S79-002" if st_before=="awaiting_approval" else f"FAIL T-S79-002 parent={st_before} (want awaiting_approval)")

    # T-S79-003: INLINE decide (playground) → resume + advance → run COMPLETES with both members.
    d=c.post(f"/playground/approvals/{aid}/decide", json={"decision":"approved"})
    if d.status_code!=200:
        print(f"FAIL T-S79-003 decide={d.status_code}"); return
    final=None
    for _ in range(40):
        time.sleep(5)
        t=c.get(f"/workflows/{WID}/runs/{run_id}/tree")
        if t.status_code!=200: continue
        j=t.json(); s=j.get("parent",{}).get("status")
        if s in ("completed","failed"):
            final=(s,[k.get("status") for k in j.get("children",[])]); break
    if final is None:
        print("FAIL T-S79-003 resume-timeout (workflow never reached terminal after approval)")
    elif final[0]=="completed" and all(x=="completed" for x in final[1]) and len(final[1])>=2:
        print("PASS T-S79-003")
    else:
        print(f"FAIL T-S79-003 final={final} (want completed + all members completed)")
asyncio.run(main())
PY
)
echo "$RESULT"
while read -r line; do
  case "$line" in
    PASS*) PASS=$((PASS+1)); echo "  ${line}";;
    FAIL*) FAIL=$((FAIL+1)); echo "  ${line}";;
    SKIP*) SKIP=$((SKIP+1)); echo "  ${line}";;
  esac
done <<< "$RESULT"

# ─────────────────────────────────────────────────────────────────────────────
# T-S79-004b — DETERMINISTIC re-park backstop (reproduce-first for the multi-
# approval regression). Park a member on its first gate, SEED a SECOND pending
# approval on the SAME thread (direct DB insert — bypasses create_approval dedup,
# distinct tool_args), approve the 1st inline, then assert the INVARIANT:
#   never (the run is 'completed' AND an approval on its thread is 'pending').
# Against the pre-fix image the registry marks the member completed on the pod's
# HTTP 200 and advances, orphaning the seeded approval → FAIL (RED, the repro).
# After the fix (_resume_and_advance re-park) the parent holds awaiting_approval
# and never completes-with-pending → PASS. Only the FIRST park is model-driven
# (loud SKIP if Ollama doesn't call web_search); the 2nd gate is deterministic.
# ─────────────────────────────────────────────────────────────────────────────
RESULT2=$(kubectl exec -i -n agentshield-platform "$POD" -c registry-api -- python3 - "$WF_ID" <<'PY'
import asyncio, json, sys, time, httpx
WID=sys.argv[1]
async def main():
    base="http://localhost:8000/api/v1"
    tr=httpx.post('http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
        data={'grant_type':'password','client_id':'agentshield-studio','username':'platform-admin','password':'PlatformAdmin2024'},timeout=10)
    if tr.status_code!=200:
        print(f"SKIP T-S79-004b no-token={tr.status_code}"); return
    auth={'Authorization':f"Bearer {tr.json()['access_token']}"}
    appr=None; run_id=None
    async with httpx.AsyncClient(timeout=150) as c:
        try:
            async with c.stream('POST', f"{base}/workflows/{WID}/runs/stream",
                    json={"message":"Search the web for the current weather in Austin, Texas.","session_id":"s79-004b"}, headers=auth) as resp:
                if resp.status_code!=200:
                    print(f"SKIP T-S79-004b stream={resp.status_code}"); return
                async for line in resp.aiter_lines():
                    if not line.startswith("data:"): continue
                    try: ev=json.loads(line[5:].strip())
                    except: continue
                    if ev.get("type")=="approval_requested": appr=ev
                    if ev.get("type")=="done": run_id=ev.get("run_id"); break
        except Exception as e:
            print(f"SKIP T-S79-004b stream-exc={e!r}"); return
    if not appr or not run_id:
        print("SKIP T-S79-004b no-approval (model did not call web_search)"); return
    aid=appr["approval_id"]
    # Seed a SECOND pending approval on the same thread (direct insert; distinct args).
    from db import AsyncSessionLocal
    from sqlalchemy import text as sql
    seeded_id=None
    async with AsyncSessionLocal() as s:
        row=(await s.execute(sql(
            "INSERT INTO approvals (agent_id, agent_name, team, thread_id, tool_name, tool_args, "
            "risk_level, status, trace_id, session_id, expires_at, context, notify_slack, reasoning) "
            "SELECT agent_id, agent_name, team, thread_id, tool_name, "
            "'{\"query\":\"__seeded_second_gate__\"}'::jsonb, risk_level, 'pending', trace_id, "
            "session_id, expires_at, context, notify_slack, reasoning FROM approvals WHERE id = :aid "
            "RETURNING id"), {"aid": aid})).first()
        await s.commit()
        seeded_id=str(row[0]) if row else None
    if not seeded_id:
        print(f"SKIP T-S79-004b could-not-seed (approval {aid} not found)"); return
    cl=httpx.Client(base_url=base, headers=auth, timeout=20)
    d=cl.post(f"/playground/approvals/{aid}/decide", json={"decision":"approved"})
    if d.status_code!=200:
        print(f"FAIL T-S79-004b decide={d.status_code}"); return
    leak=False; parent_status=None; seeded_status=None
    for _ in range(24):
        time.sleep(5)
        async with AsyncSessionLocal() as s:
            srow=(await s.execute(sql("SELECT status FROM approvals WHERE id=:i"), {"i":seeded_id})).first()
        seeded_status=srow[0] if srow else None
        t=cl.get(f"/workflows/{WID}/runs/{run_id}/tree")
        if t.status_code==200:
            parent_status=t.json().get("parent",{}).get("status")
        if parent_status=="completed" and seeded_status=="pending":
            leak=True; break                      # the bug: advanced while a gate is pending
        if parent_status=="failed":
            break
    try: cl.post(f"/playground/approvals/{seeded_id}/decide", json={"decision":"approved"})  # drain
    except Exception: pass
    if leak:
        print("FAIL T-S79-004b re-park-missing (run completed while a 2nd approval was still pending — gate orphaned)")
    elif seeded_status=="pending" and parent_status!="completed":
        print(f"PASS T-S79-004b re-parked (parent={parent_status}, held; never completed with a pending approval)")
    else:
        print(f"FAIL T-S79-004b unexpected (parent={parent_status}, seeded={seeded_status}; want held-not-completed)")
asyncio.run(main())
PY
)
echo "$RESULT2"
while read -r line; do
  case "$line" in
    PASS*) PASS=$((PASS+1)); echo "  ${line}";;
    FAIL*) FAIL=$((FAIL+1)); echo "  ${line}";;
    SKIP*) SKIP=$((SKIP+1)); echo "  ${line}";;
  esac
done <<< "$RESULT2"

# ─────────────────────────────────────────────────────────────────────────────
# T-S79-004c — STATIC cross-context guard. The re-park fix lives in the SHARED
# _resume_and_advance (approvals.py) and is context-agnostic (its pending-approval
# query never reads `context`). Both decide endpoints schedule that SAME function:
#   decide_playground_approval (playground.py)  → sandbox / eval inline self-approve
#   decide_approval            (approvals.py)    → production / console reviewer decide
# So the re-park proven at RUNTIME by T-S79-004b (the playground path) is inherited by
# the production/console path too. This guard asserts both wirings + the re-park branch
# still exist, so a future refactor that bypasses _resume_and_advance on either path — or
# drops the re-park check — fails loudly. (A full production-context runtime case needs a
# deployed production workflow + real reviewer authority; that surface is tracked as a
# best-effort SKIP-loud follow-on in the HITL plan.)
# ─────────────────────────────────────────────────────────────────────────────
ROOT="$(git rev-parse --show-toplevel)"
PG="$ROOT/services/registry-api/routers/playground.py"
AP="$ROOT/services/registry-api/routers/approvals.py"
if grep -q "def decide_playground_approval" "$PG" \
   && grep -q "_resume_and_advance" "$PG" \
   && grep -q "def decide_approval" "$AP" \
   && grep -q "asyncio.create_task(_resume_and_advance" "$AP" \
   && grep -q "still_pending" "$AP"; then
  echo "PASS T-S79-004c both decide paths (playground + console/production) route through the re-parking _resume_and_advance"
  PASS=$((PASS+1))
else
  echo "FAIL T-S79-004c a decide path no longer schedules _resume_and_advance, or the re-park branch is gone (fix not inherited across contexts)"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== Suite 79 Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
exit "$FAIL"
