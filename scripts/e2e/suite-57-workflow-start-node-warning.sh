#!/usr/bin/env bash
# scripts/e2e/suite-57-workflow-start-node-warning.sh
#
# E2E Suite 57: multiple-start-node save-time validation.
#
# The conditional/handoff engine walks a SINGLE cursor from ONE start node
# (workflow_orchestrator.find_start_node = first member, by position, with no
# incoming edge). If the edge graph has >1 such root, only the first runs and the
# rest are silently orphaned. compute_start_node_warnings surfaces that at save
# time (composed into the workflow's warnings → the builder toasts it). This suite
# drives the helper directly in-pod against real DB fixtures.
#
#   T-S57-001 — conditional + 2 roots (no edge into the 2nd) → warning fires, names both roots
#   T-S57-002 — conditional single-entry fork (router→a, router→b) → NO warning
#   T-S57-003 — handoff + 2 roots → warning fires
#   T-S57-004 — supervisor with no edges → NO warning (exempt: routes dynamically by role)
#   T-S57-005 — sequential with 2 roots → NO warning (exempt: runs by member order, no start node)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 57: multiple-start-node save-time warning ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import asyncio, uuid
from db import AsyncSessionLocal
from models import Agent, CompositeWorkflow, WorkflowMember, WorkflowEdge
from routers.composite_workflows import compute_start_node_warnings

TEAM='platform'

async def mk_agent(tag):
    async with AsyncSessionLocal() as s:
        a=Agent(name='s57-'+tag+'-'+uuid.uuid4().hex[:8], team=TEAM, agent_type='declarative',
                execution_shape='durable', agent_class='daemon', created_by='s57')
        s.add(a); await s.commit(); await s.refresh(a)
        return a.id

async def mk_wf(mode, agent_ids, edges, roles=None):
    async with AsyncSessionLocal() as s:
        w=CompositeWorkflow(name='s57-'+mode+'-'+uuid.uuid4().hex[:8], team=TEAM,
                            orchestration=mode, execution_shape='durable', created_by='s57')
        s.add(w); await s.commit(); await s.refresh(w)
        for i,aid in enumerate(agent_ids):
            role=(roles or {}).get(i)
            s.add(WorkflowMember(workflow_id=w.id, agent_id=aid, position=i+1, role=role))
        for si,ti,cond in edges:
            s.add(WorkflowEdge(workflow_id=w.id, source_agent_id=agent_ids[si],
                               target_agent_id=agent_ids[ti], condition=cond, position=1))
        await s.commit()
        return w.id

async def main():
    r=await mk_agent('r'); a=await mk_agent('a'); b=await mk_agent('b')
    out={}

    # 001 conditional, 2 roots (no edges) -> warn
    wid=await mk_wf('conditional',[r,a,b],[])   # a,b,r all roots
    async with AsyncSessionLocal() as s:
        warns=await compute_start_node_warnings(s, wid, 'conditional')
    out['001_conditional_multiroot']= bool(warns) and 'Multiple start nodes' in warns[0]

    # 002 conditional single-entry fork: r->a, r->b  (only r is a root) -> no warn
    wid=await mk_wf('conditional',[r,a,b],[(0,1,'x'),(0,2,None)])
    async with AsyncSessionLocal() as s:
        warns=await compute_start_node_warnings(s, wid, 'conditional')
    out['002_conditional_fork_ok']= (warns==[])

    # 003 handoff, 2 roots -> warn
    wid=await mk_wf('handoff',[r,a,b],[(0,1,None)])   # b has no incoming -> r,b roots
    async with AsyncSessionLocal() as s:
        warns=await compute_start_node_warnings(s, wid, 'handoff')
    out['003_handoff_multiroot']= bool(warns)

    # 004 supervisor, no edges -> exempt
    wid=await mk_wf('supervisor',[r,a,b],[],roles={0:'supervisor'})
    async with AsyncSessionLocal() as s:
        warns=await compute_start_node_warnings(s, wid, 'supervisor')
    out['004_supervisor_exempt']= (warns==[])

    # 005 sequential, 2 roots -> exempt
    wid=await mk_wf('sequential',[r,a,b],[])
    async with AsyncSessionLocal() as s:
        warns=await compute_start_node_warnings(s, wid, 'sequential')
    out['005_sequential_exempt']= (warns==[])

    for k,v in out.items():
        print(('PASS' if v else 'FAIL'), k)

asyncio.run(main())
")

echo "$RESULT"
echo ""
if echo "$RESULT" | grep -q "FAIL"; then
  echo "❌ Suite 57 FAILED"
  exit 1
fi
echo "✅ Suite 57 PASSED (5/5)"
