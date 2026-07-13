#!/usr/bin/env bash
# scripts/e2e/suite-56-workflow-durable-modes.sh
#
# E2E Suite 56: WS-1 T5 — workflow D3 all-four-mode durable resume.
#
# Proves that conditional / handoff / supervisor now durably park→resume→advance→
# complete (previously only sequential did; the others "halted correctly but
# completed with member output"). The orchestrator runs IN registry-api as a
# background task and dispatches members to their deployed pods — which don't exist
# in this cluster — so, exactly like suite-36 and suite-55, this drives the
# workflow_orchestrator functions directly in-pod with a FAKED `_run_step`
# (scripts each member's outcome) and a FAKED `resolve_edge_graph` (supplies the
# adjacency). That isolates the T5 logic under test: the mode-specific cursor is
# checkpointed on park, and resume_orchestration re-enters per mode from that
# cursor and advances to completion.
#
#   T-S56-001 — conditional: park writes cursor {mode,node,visited_count}
#   T-S56-002 — conditional: resume routes node→next (Markovian) → advances → completes
#   T-S56-003 — handoff:     park writes cursor {mode,node,visited_count}
#   T-S56-004 — handoff:     resume follows the hop → advances → completes
#   T-S56-005 — supervisor:  park CHECKPOINTS THE ACCUMULATOR (worker_outputs + iteration
#                            + phase) — proves prior worker output survives the pause
#   T-S56-006 — supervisor:  resume reconstructs the accumulator → completes on DONE
#
# The live-pod leg (a real durable member pod parking on an OPA gate, a reviewer
# deciding in the console, the member pod re-driving) is the same agent-pod fixture
# boundary the other bash suites accept — recorded in the gap ledger + the manual
# UI e2e plan. This suite proves the registry/orchestrator-level invariant.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 56: workflow durable modes — D3 all-four-mode resume (WS-1 T5) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, uuid
from db import AsyncSessionLocal
from sqlalchemy import select, text
from models import AgentRun, CompositeWorkflow
import workflow_orchestrator as wo

TEAM='platform'

# ---- fixtures -------------------------------------------------------------
async def mk_wf(mode):
    async with AsyncSessionLocal() as s:
        w=CompositeWorkflow(name='s56-'+mode+'-'+uuid.uuid4().hex[:8], team=TEAM,
                            orchestration=mode, execution_shape='durable')
        s.add(w); await s.commit(); await s.refresh(w)
        return str(w.id)

async def mk_parent(wf_id):
    async with AsyncSessionLocal() as s:
        r=AgentRun(agent_name='s56-parent', status='running', context='production',
                   trigger_type='workflow', team=TEAM, workflow_id=uuid.UUID(wf_id),
                   input='start')
        s.add(r); await s.commit(); await s.refresh(r)
        return str(r.id)

async def read(pid):
    async with AsyncSessionLocal() as s:
        r=(await s.execute(select(AgentRun).where(AgentRun.id==pid))).scalar_one_or_none()
        return dict(status=r.status, output=r.output, state=r.orchestrator_state) if r else None

# ---- fakes ----------------------------------------------------------------
# _run_step is replaced by a script: a list of (status, output, err) consumed in
# order (member pods don't exist). resolve_edge_graph returns a fixed adjacency.
SCRIPT=[]
async def fake_run_step(parent_run_id, team, agent_name, current_input):
    return SCRIPT.pop(0)

GRAPH={}
async def fake_resolve_edge_graph(session, workflow_id):
    return GRAPH

wo._run_step = fake_run_step
wo.resolve_edge_graph = fake_resolve_edge_graph

async def main():
    out={}
    _cleanup=[]

    # ===== conditional =====
    global GRAPH, SCRIPT
    GRAPH={'A':[('B',None)], 'B':[]}          # A --default--> B ; B terminal
    wf=await mk_wf('conditional'); pid=await mk_parent(wf); _cleanup.append(pid)
    SCRIPT=[('awaiting_approval', None, None)]   # A parks
    await wo._run_conditional_from(pid, TEAM, wf, GRAPH, 'A', 0, 'in', 'durable')
    r=await read(pid); st=r['state'] or {}
    out['c_park']=(r['status']=='awaiting_approval' and st.get('mode')=='conditional'
                   and st.get('node')=='A' and st.get('visited_count')==1)
    SCRIPT=[('completed', 'C-FINAL', None)]      # B completes on resume
    await wo.resume_orchestration(pid, 'routed', 'completed')
    r=await read(pid)
    out['c_done']=(r['status']=='completed' and r['output']=='C-FINAL' and r['state'] is None)

    # ===== handoff =====
    GRAPH={'A':[('B',None)], 'B':[]}          # sole edge A->B (deterministic hop)
    wf=await mk_wf('handoff'); pid=await mk_parent(wf); _cleanup.append(pid)
    SCRIPT=[('awaiting_approval', None, None)]
    await wo._run_handoff_from(pid, TEAM, wf, GRAPH, 'A', 0, 'in', 'durable')
    r=await read(pid); st=r['state'] or {}
    out['h_park']=(r['status']=='awaiting_approval' and st.get('mode')=='handoff'
                   and st.get('node')=='A' and st.get('visited_count')==1)
    SCRIPT=[('completed', 'H-FINAL', None)]
    await wo.resume_orchestration(pid, 'routed', 'completed')
    r=await read(pid)
    out['h_done']=(r['status']=='completed' and r['output']=='H-FINAL' and r['state'] is None)

    # ===== supervisor (accumulator survival) =====
    GRAPH={}
    wf=await mk_wf('supervisor'); pid=await mk_parent(wf); _cleanup.append(pid)
    # iter0: sup->wk ; wk completes 'w1'  |  iter1: sup->wk ; wk PARKS
    SCRIPT=[
        ('completed', '{\"next\":\"wk\"}', None),  # sup call1
        ('completed', 'w1', None),                 # wk  call2  -> worker_outputs=['w1']
        ('completed', '{\"next\":\"wk\"}', None),  # sup call3
        ('awaiting_approval', None, None),         # wk  call4  -> PARK
    ]
    await wo._run_supervisor_from(pid, TEAM, wf, 'sup', ['wk'], 3,
                                  iteration=0, current_input='start', worker_outputs=[],
                                  shape='durable')
    r=await read(pid); st=r['state'] or {}
    out['s_park']=(r['status']=='awaiting_approval' and st.get('mode')=='supervisor'
                   and st.get('phase')=='worker' and st.get('iteration')==1
                   and st.get('worker_outputs')==['w1'])   # ACCUMULATOR SURVIVED
    # resume: worker returns 'w2'; next sup turn says DONE -> complete
    SCRIPT=[('completed', 'all DONE', None)]              # sup call5 on resume
    await wo.resume_orchestration(pid, 'w2', 'completed')
    r=await read(pid)
    out['s_done']=(r['status']=='completed' and r['state'] is None)

    print('RESULT', out)

    # cleanup AFTER RESULT is emitted (a hiccup can't abort the capture pipeline)
    try:
        async with AsyncSessionLocal() as s:
            for pid in _cleanup:
                rr=(await s.execute(select(AgentRun).where(AgentRun.id==pid))).scalar_one_or_none()
                if rr: await s.delete(rr)
            await s.execute(text(\"DELETE FROM workflows WHERE name LIKE 's56-%'\"))
            await s.commit()
    except Exception:
        pass

asyncio.run(main())
" 2>&1 | grep -v Defaulted | grep '^RESULT' | tail -1 || true)

echo "  $RESULT"
echo ""

PASS=0; FAIL=0
check() { if echo "$RESULT" | grep -q "$2"; then echo "  PASS: $1"; PASS=$((PASS+1)); else echo "  FAIL: $1"; FAIL=$((FAIL+1)); fi; }

check "T-S56-001 conditional park writes cursor {mode,node,visited_count}" "'c_park': True"
check "T-S56-002 conditional resume routes→advances→completes"            "'c_done': True"
check "T-S56-003 handoff park writes cursor {mode,node,visited_count}"     "'h_park': True"
check "T-S56-004 handoff resume follows hop→advances→completes"           "'h_done': True"
check "T-S56-005 supervisor park CHECKPOINTS ACCUMULATOR (worker_outputs)" "'s_park': True"
check "T-S56-006 supervisor resume reconstructs accumulator→completes"     "'s_done': True"

echo ""
echo "=== Suite 56 done: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
