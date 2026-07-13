#!/usr/bin/env bash
# scripts/e2e/suite-55-durable-engine.sh
#
# E2E Suite 55: WS-1 durable engine — park → resume routing (the extend-not-alter guard).
#
# The shared harness (agentshield_sdk/durable.py) is unit-proven (6/6, real steps /
# interrupt-park / fail-closed / resume / crash-fail). This suite proves the REGISTRY
# side of T4: approvals._resume_and_advance resumes a durable /run run THROUGH the
# harness (passes run_id + callback_url so the pod re-drives + emits steps), while a
# chat run and a workflow-member run keep the UNCHANGED synchronous resume — the
# safety-critical parity property (a durable change must not alter reactive-chat HITL).
#
# Discriminator (reasoned from the running code): a durable /run run parks its AgentRun
# at id == thread_id AND writes RunStep rows; chat runs do neither; workflow members
# have parent_run_id set.
#
#   T-S55-001 — durable run (RunStep rows, id==thread_id, no parent) → resume body
#               carries run_id + the /internal/runs/{id}/step-update callback (durable path)
#   T-S55-002 — chat run (thread_id is not a run id, no RunStep) → resume body has NO
#               run_id/callback_url (reactive /chat resume UNCHANGED — the regression guard)
#   T-S55-003 — workflow member (parent_run_id set, has steps) → NOT single-agent durable
#               (no run_id in body) → the existing workflow re-entry path handles it
#
# Live-pod leg (park→approve→resume→complete through a real durable agent pod, and
# kill-pod→resume) is covered by the durable.py unit tests + a manual step in the UI
# e2e plan — it needs a deployed durable agent with a high-risk tool (the same
# agent-pod fixture boundary the other bash suites accept). Documented in the gap ledger.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 55: durable engine — park/resume routing (WS-1 T4) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, uuid, types
from db import AsyncSessionLocal
from sqlalchemy import select, text
from models import AgentRun, RunStep
import routers.approvals as approvals

TEAM='platform'; AG='s55-agent-'+uuid.uuid4().hex[:8]

async def _mk_run(db, *, with_step, parent=None):
    r=AgentRun(agent_name=AG, team=TEAM, status='awaiting_approval', context='production',
               parent_run_id=parent)
    db.add(r); await db.flush()
    if with_step:
        db.add(RunStep(run_id=r.id, step_number=1, name='tool:wire', status='awaiting_approval'))
    await db.commit()
    return r.id

async def _capture_resume(thread_id):
    '''Call _resume_and_advance with httpx mocked; return the POSTed resume body.'''
    box={}
    class _Resp:
        status_code=200
        def json(self): return {'response':'resumed'}
    class _Cli:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def post(self, url, json=None): box['url']=url; box['body']=json or {}; return _Resp()
    real=approvals.httpx
    approvals.httpx=types.SimpleNamespace(AsyncClient=_Cli)
    try:
        await approvals._resume_and_advance(AG, TEAM, str(thread_id), 'approved', 'rev-1', None)
    finally:
        approvals.httpx=real
    return box.get('body', {})

async def main():
    out={}
    async with AsyncSessionLocal() as db:
        durable_id = await _mk_run(db, with_step=True)                 # 001
        member_parent = await _mk_run(db, with_step=False)             # a parent for 003
        member_id = await _mk_run(db, with_step=True, parent=member_parent)  # 003

    # 001 durable → body carries run_id + step-update callback
    b1 = await _capture_resume(durable_id)
    out['t1_run_id'] = b1.get('run_id')
    out['t1_cb'] = ('/internal/runs/%s/step-update' % durable_id) in (b1.get('callback_url') or '')

    # 002 chat → a fresh uuid that is NOT any run id, no RunStep → no durable body
    b2 = await _capture_resume(uuid.uuid4())
    out['t2_has_run_id'] = 'run_id' in b2
    out['t2_decision'] = b2.get('decision')   # chat body still carries the decision

    # 003 workflow member (parent set) → not single-agent durable
    b3 = await _capture_resume(member_id)
    out['t3_has_run_id'] = 'run_id' in b3

    print('RESULT', out)

    # cleanup (best-effort — RESULT is already emitted, so a cleanup hiccup can't
    # abort the capture pipeline under set -o pipefail)
    try:
        async with AsyncSessionLocal() as db:
            for rid in (durable_id, member_parent, member_id):
                await db.execute(text('DELETE FROM run_steps WHERE run_id=:i'), {'i':rid})
                r=(await db.execute(select(AgentRun).where(AgentRun.id==rid))).scalar_one_or_none()
                if r: await db.delete(r)
            await db.commit()
    except Exception:
        pass

asyncio.run(main())
" 2>&1 | grep -v Defaulted | grep '^RESULT' | tail -1 || true)

echo "  $RESULT"
echo ""

PASS=0; FAIL=0
check() { if echo "$RESULT" | grep -q "$2"; then echo "  PASS: $1"; PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

check "T-S55-001 durable run → resume body carries run_id"        "'t1_run_id': '"
check "T-S55-001 durable run → step-update callback in body"      "'t1_cb': True"
check "T-S55-002 chat run → NO durable run_id (resume unchanged)" "'t2_has_run_id': False"
check "T-S55-002 chat run → decision still forwarded"             "'t2_decision': 'approved'"
check "T-S55-003 workflow member → NOT single-agent durable"     "'t3_has_run_id': False"

echo ""
echo "=== Suite 55 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
