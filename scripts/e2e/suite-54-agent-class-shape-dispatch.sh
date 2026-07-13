#!/usr/bin/env bash
# scripts/e2e/suite-54-agent-class-shape-dispatch.sh
#
# E2E Suite 54: WS-0 — agent_class authoring + shape-aware triggered dispatch.
#
# What this proves (the three v2 collapse points, made real end-to-end):
#   * agent_class is a first-class, NOT-NULL, validated field on BOTH executables
#     (agents + workflows), authored on create and PATCH (the update_agent orphan
#     is wired), read straight from the column at deploy.
#   * a triggered production run HONORS execution_shape: durable -> the shared
#     durable-dispatch helper writes run_steps via the internal step-update
#     callback; reactive -> the synchronous /chat path (output, no run_steps).
#   * a reactive workflow can't durably park — an approval gate fails-closed (S2).
#   * PARITY: the /run POST lives in ONE shared helper both routers call (the
#     2026-07-11 HITL retro root cause was a sandbox/production copy).
#
#   T-S54-001 — create agent w/o class -> user_delegated (explicit default)
#   T-S54-002 — create agent w/ agent_class="bogus" -> rejected (422/validation)
#   T-S54-003 — PATCH {agent_class:daemon} -> reload -> daemon (orphan wired)
#   T-S54-004 — create workflow agent_class=daemon -> reload -> daemon
#   T-S54-005 — reactive workflow w/ high-risk-tool member -> warnings non-empty (S2 producer)
#   T-S54-006 — durable step-update callback -> RunStep written + run completed
#   T-S54-007 — _dispatch_and_complete(shape=durable) -> shared helper + fail-closed on dispatch failure
#   T-S54-008 — _dispatch_and_complete(shape=reactive) -> /chat output, NO run_steps
#   T-S54-009 — _park_or_fail(shape=reactive) -> parent failed w/ "set shape=durable" (fail-closed)
#   T-S54-010 — PARITY grep: dispatch_durable_run called from both routers; no raw /run POST in routers
#
# Usage:
#   bash scripts/e2e/suite-54-agent-class-shape-dispatch.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
REPO_ROOT=$(cd "$SCRIPT_DIR/../.." && pwd)

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 54: agent_class authoring + shape-aware dispatch (WS-0) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, uuid, types
from db import AsyncSessionLocal
from sqlalchemy import select, text
from models import Agent, CompositeWorkflow, WorkflowMember, Tool, AgentTool, AgentRun, RunStep
from schemas import AgentCreate, AgentUpdate, CompositeWorkflowCreate
import routers.agents as agents_r
import routers.composite_workflows as wf_r
import routers.internal as internal
import workflow_orchestrator as wo
import durable_dispatch

TEAM='platform'
SFX=uuid.uuid4().hex[:8]
SUB='s54-'+SFX
AG='s54-agent-'+SFX
AG2='s54-member-'+SFX
WF='s54-wf-'+SFX
WF2='s54-wfwarn-'+SFX
TOOL='s54-risky-'+SFX

async def main():
    out={}
    wid=None; wid2=None

    # T-S54-001: create w/o class -> user_delegated
    async with AsyncSessionLocal() as db:
        r=await agents_r.create_agent(AgentCreate(name=AG, team=TEAM, agent_type='declarative', execution_shape='durable'), x_user_sub=SUB, user=None, db=db)
        await db.commit()
        out['t1_class']=r.agent_class

    # T-S54-002: bogus class rejected at the schema boundary (422 path)
    try:
        AgentCreate(name='x', team=TEAM, agent_class='bogus'); out['t2_bogus']='accepted'
    except Exception:
        out['t2_bogus']='rejected'

    # T-S54-003: PATCH daemon -> reload daemon (update_agent orphan wired)
    async with AsyncSessionLocal() as db:
        await agents_r.update_agent(AG, AgentUpdate(agent_class='daemon'), db=db)
        await db.commit()
    async with AsyncSessionLocal() as db:
        a=(await db.execute(select(Agent).where(Agent.name==AG))).scalar_one()
        out['t3_patch_class']=a.agent_class

    # T-S54-004: workflow create daemon -> reload daemon
    async with AsyncSessionLocal() as db:
        w=await wf_r.create_workflow(CompositeWorkflowCreate(name=WF, team=TEAM, orchestration='sequential', execution_shape='durable', agent_class='daemon'), x_user_sub=SUB, user=None, db=db)
        wid=w.id
    async with AsyncSessionLocal() as db:
        ww=(await db.execute(select(CompositeWorkflow).where(CompositeWorkflow.id==wid))).scalar_one()
        out['t4_wf_class']=ww.agent_class

    # T-S54-005: reactive workflow + high-risk-tool member -> warnings (S2 producer)
    async with AsyncSessionLocal() as db:
        m=Agent(name=AG2, team=TEAM, agent_type='declarative', status='active', agent_class='user_delegated'); db.add(m); await db.flush()
        t=Tool(name=TOOL, type='python', risk_level='high'); db.add(t); await db.flush()
        db.add(AgentTool(agent_id=m.id, tool_id=t.id, added_by='system'))
        w2=await wf_r.create_workflow(CompositeWorkflowCreate(name=WF2, team=TEAM, orchestration='sequential', execution_shape='reactive', agent_class='user_delegated'), x_user_sub=SUB, user=None, db=db)
        wid2=w2.id
        db.add(WorkflowMember(workflow_id=wid2, agent_id=m.id, position=1))
        await db.commit()
    async with AsyncSessionLocal() as db:
        got=await wf_r.get_workflow(wid2, db=db)
        out['t5_warnings']=bool(got.warnings)

    # T-S54-006: durable step-update callback -> RunStep + run completed
    async with AsyncSessionLocal() as db:
        r=AgentRun(agent_name=AG, team=TEAM, status='running', context='production'); db.add(r); await db.flush(); rid=str(r.id); await db.commit()
    async with AsyncSessionLocal() as db:
        await internal.internal_step_update(rid, {'step_number':1,'step_name':'agent_execution','status':'completed','output_text':'done','run_completed':True}, db=db)
    async with AsyncSessionLocal() as db:
        steps=(await db.execute(select(RunStep).where(RunStep.run_id==uuid.UUID(rid)))).scalars().all()
        rr=(await db.execute(select(AgentRun).where(AgentRun.id==uuid.UUID(rid)))).scalar_one()
        out['t6_steps']=len(steps); out['t6_run_status']=rr.status; out['t6_output']=rr.output

    # T-S54-007: durable dispatch routing + fail-closed (stub the shared helper to fail)
    _orig=durable_dispatch.dispatch_durable_run
    async def _stub_fail(**k): return (False,'stub-unreachable')
    durable_dispatch.dispatch_durable_run=_stub_fail
    async with AsyncSessionLocal() as db:
        r=AgentRun(agent_name=AG, team=TEAM, status='running', context='production'); db.add(r); await db.flush(); rid7=str(r.id); await db.commit()
    await internal._dispatch_and_complete(rid7, AG, TEAM, 'msg', 'durable', {}, None)
    durable_dispatch.dispatch_durable_run=_orig
    async with AsyncSessionLocal() as db:
        rr=(await db.execute(select(AgentRun).where(AgentRun.id==uuid.UUID(rid7)))).scalar_one()
        steps=(await db.execute(select(RunStep).where(RunStep.run_id==uuid.UUID(rid7)))).scalars().all()
        out['t7_durable_status']=rr.status; out['t7_durable_steps']=len(steps)

    # T-S54-008: reactive dispatch routing (stub httpx) -> /chat output, no run_steps
    _realhttp=internal.httpx
    class _Resp:
        status_code=200
        def json(self): return {'output':'reactive-out'}
    class _Cli:
        def __init__(self,*a,**k): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*a): return False
        async def post(self,url,json=None): return _Resp()
    internal.httpx=types.SimpleNamespace(AsyncClient=_Cli)
    async with AsyncSessionLocal() as db:
        r=AgentRun(agent_name=AG, team=TEAM, status='running', context='production'); db.add(r); await db.flush(); rid8=str(r.id); await db.commit()
    await internal._dispatch_and_complete(rid8, AG, TEAM, 'msg', 'reactive', None, None)
    internal.httpx=_realhttp
    async with AsyncSessionLocal() as db:
        rr=(await db.execute(select(AgentRun).where(AgentRun.id==uuid.UUID(rid8)))).scalar_one()
        steps=(await db.execute(select(RunStep).where(RunStep.run_id==uuid.UUID(rid8)))).scalars().all()
        out['t8_reactive_status']=rr.status; out['t8_reactive_output']=rr.output; out['t8_reactive_steps']=len(steps)

    # T-S54-009: reactive workflow approval gate -> fail-closed
    async with AsyncSessionLocal() as db:
        p=AgentRun(agent_name=WF, team=TEAM, status='running', context='production', workflow_id=wid); db.add(p); await db.flush(); pid=str(p.id); await db.commit()
    await wo._park_or_fail(pid, 'sequential', TEAM, str(wid), 'reactive')
    async with AsyncSessionLocal() as db:
        pp=(await db.execute(select(AgentRun).where(AgentRun.id==uuid.UUID(pid)))).scalar_one()
        out['t9_park_status']=pp.status
        out['t9_park_msg_ok']=bool(pp.error_message and 'set shape=durable' in pp.error_message)

    # cleanup (best-effort — uniquely-suffixed rows; a cleanup FK snag must not fail the asserts)
    try:
        async with AsyncSessionLocal() as db:
            for wnm in (WF, WF2, AG, AG2):
                for rr in (await db.execute(select(AgentRun).where(AgentRun.agent_name==wnm))).scalars().all():
                    await db.execute(text('DELETE FROM run_steps WHERE run_id=:i'), {'i':rr.id})
                    await db.delete(rr)
            for nm in (AG, AG2):
                a=(await db.execute(select(Agent).where(Agent.name==nm))).scalar_one_or_none()
                if a:
                    await db.execute(text('DELETE FROM agent_tools WHERE agent_id=:i'), {'i':a.id})
                    await db.delete(a)
            for w_id in (wid, wid2):
                if w_id is None: continue
                w=(await db.execute(select(CompositeWorkflow).where(CompositeWorkflow.id==w_id))).scalar_one_or_none()
                if w:
                    await db.execute(text('DELETE FROM workflow_members WHERE workflow_id=:i'), {'i':w_id})
                    await db.delete(w)
            t=(await db.execute(select(Tool).where(Tool.name==TOOL))).scalar_one_or_none()
            if t: await db.delete(t)
            await db.commit()
    except Exception as e:
        out['cleanup_err']=str(e)[:100]

    print('RESULT', out)

asyncio.run(main())
" 2>&1 | grep -v Defaulted | grep '^RESULT' | tail -1)

echo "  $RESULT"
echo ""

PASS=0; FAIL=0
check() {  # check "<label>" "<grep-substring>"
  if echo "$RESULT" | grep -q "$2"; then echo "  PASS: $1"; PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi
}

check "T-S54-001 create w/o class -> user_delegated"          "'t1_class': 'user_delegated'"
check "T-S54-002 bogus class rejected (422)"                  "'t2_bogus': 'rejected'"
check "T-S54-003 PATCH daemon -> reload daemon (orphan)"      "'t3_patch_class': 'daemon'"
check "T-S54-004 workflow daemon persisted"                   "'t4_wf_class': 'daemon'"
check "T-S54-005 reactive wf high-risk member -> warnings"    "'t5_warnings': True"
check "T-S54-006 step-update writes RunStep + completes"      "'t6_steps': 1"
check "T-S54-006 step-update run completed w/ output"         "'t6_run_status': 'completed'"
check "T-S54-007 durable dispatch fail-closed"                "'t7_durable_status': 'failed'"
check "T-S54-007 durable dispatch wrote no run_steps"         "'t7_durable_steps': 0"
check "T-S54-008 reactive dispatch -> /chat output"           "'t8_reactive_output': 'reactive-out'"
check "T-S54-008 reactive dispatch -> NO run_steps"           "'t8_reactive_steps': 0"
check "T-S54-009 reactive workflow gate fail-closed"          "'t9_park_status': 'failed'"
check "T-S54-009 fail message names set shape=durable"        "'t9_park_msg_ok': True"

# T-S54-010: PARITY grep (host filesystem) — the /run POST literal lives ONLY in the
# shared helper; both routers call dispatch_durable_run; neither router POSTs /run raw.
PG_R="$REPO_ROOT/services/registry-api"
if grep -q "dispatch_durable_run" "$PG_R/routers/playground.py" \
   && grep -q "dispatch_durable_run" "$PG_R/routers/internal.py" \
   && ! grep -q '"/run"' "$PG_R/routers/playground.py" "$PG_R/routers/internal.py"; then
  echo "  PASS: T-S54-010 parity — single /run POST helper, both routers call it, no router copy"
  PASS=$((PASS+1))
else
  echo "  FAIL: T-S54-010 parity — a router has a divergent /run POST or misses the shared helper"
  FAIL=$((FAIL+1))
fi

echo ""
echo "=== Suite 54 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
