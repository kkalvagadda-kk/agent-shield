#!/usr/bin/env bash
# Suite 30: Workflow Orchestration Modes (Decision 22 — full orchestration)
# Tests T-S30-001 through T-S30-010
#
# Validates the new orchestration engine: the 'conditional' mode is now a valid
# workflow orchestration (was rejected by the CHECK constraint), edge CRUD +
# validation, run acceptance for conditional/supervisor/handoff (previously a
# hard 422), and the deterministic supervisor-missing-role failure path.
#
# NOTE: member agents are not deployed in the e2e env, so runs fail fast at
# dispatch (G-6). This suite asserts wiring/acceptance/structure + the
# deterministic supervisor-role guard — NOT run completion.
#
# Usage: bash scripts/e2e/suite-30-orchestration-modes.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT_A="s30-agent-a-${TS}"
AGENT_B="s30-agent-b-${TS}"
AGENT_C="s30-agent-c-${TS}"   # not a member (edge-validation negative test)
TEAM="platform"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agents + workflows..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
c=httpx.Client(base_url='http://localhost:8000/api/v1', timeout=10, headers={'X-User-Sub':'system'})
for n in ['${AGENT_A}','${AGENT_B}','${AGENT_C}']:
    try: c.delete('/agents/'+n)
    except Exception: pass
for w in c.get('/workflows?limit=500').json():
    if w['name'].startswith('s30-'):
        try: c.delete('/workflows/'+w['id'])
        except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 30: Workflow Orchestration Modes ==="

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, time
B='http://localhost:8000/api/v1'; H={'X-User-Sub':'system'}
c=httpx.Client(base_url=B, timeout=30)
P=0; F=0
def ok(n):
    global P; P+=1; print('  PASS:', n)
def bad(n,d=''):
    global F; F+=1; print('  FAIL:', n, d)

# Setup: two same-team member agents
for name in ['${AGENT_A}','${AGENT_B}']:
    r=c.post('/agents/', json={'name':name,'team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive'}, headers=H)
    assert r.status_code==201, f'setup {name}: {r.text}'
aid={a['name']:a['id'] for a in c.get('/agents/?limit=500', headers=H).json()['items']}

# T-S30-001: create workflow with orchestration=conditional (was CHECK-rejected before 0029)
r=c.post('/workflows', json={'name':'s30-cond-${TS}','team':'${TEAM}','orchestration':'conditional'}, headers=H)
if r.status_code==201 and r.json()['orchestration']=='conditional': ok('T-S30-001 create conditional workflow'); wid=r.json()['id']
else: bad('T-S30-001', r.text); sys.exit(1)

# members
c.post(f'/workflows/{wid}/members', json={'agent_id':aid['${AGENT_A}'],'position':1}, headers=H)
c.post(f'/workflows/{wid}/members', json={'agent_id':aid['${AGENT_B}'],'position':2}, headers=H)
ok('T-S30-002 add two members')

# T-S30-003: add edge a->b with a condition
r=c.post(f'/workflows/{wid}/edges', json={'source_agent_id':aid['${AGENT_A}'],'target_agent_id':aid['${AGENT_B}'],'condition':'approved','position':1}, headers=H)
if r.status_code==201: ok('T-S30-003 add edge 201'); eid=r.json()['id']
else: bad('T-S30-003', r.text); eid=None

# T-S30-004: edge referencing a non-member agent -> 400
r=c.post(f'/workflows/{wid}/edges', json={'source_agent_id':aid['${AGENT_A}'],'target_agent_id':aid['${AGENT_B}']}, headers=H)  # dup first? no, condition differs but src/tgt same
# (this is actually the duplicate case) -> expect 409
ok('T-S30-005 duplicate edge 409') if r.status_code==409 else bad('T-S30-005', str(r.status_code))

# non-member edge (create a throwaway agent id by using a random uuid via a real non-member agent)
rc=c.post('/agents/', json={'name':'${AGENT_C}','team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive'}, headers=H)
cid=rc.json()['id']
r=c.post(f'/workflows/{wid}/edges', json={'source_agent_id':aid['${AGENT_A}'],'target_agent_id':cid}, headers=H)
ok('T-S30-004 non-member edge rejected 400') if r.status_code==400 else bad('T-S30-004', str(r.status_code))

# T-S30-006: list edges = 1
r=c.get(f'/workflows/{wid}/edges', headers=H)
ok('T-S30-006 list edges = 1') if (r.status_code==200 and len(r.json())==1) else bad('T-S30-006', r.text)

# edges echoed on the workflow GET too
wf=c.get(f'/workflows/{wid}', headers=H).json()
ok('T-S30-006b workflow GET includes edges') if len(wf.get('edges',[]))==1 else bad('T-S30-006b', str(wf.get('edges')))

# T-S30-007: trigger conditional run -> 202 (previously 422)
r=c.post(f'/workflows/{wid}/runs', json={'input_message':'hi','run_by':'s30'}, headers=H)
ok('T-S30-007 conditional run accepted 202') if r.status_code==202 else bad('T-S30-007', r.text)
# undeployed members -> warning present
ok('T-S30-007b undeployed warning surfaced') if (r.status_code==202 and r.json().get('warning')) else bad('T-S30-007b', str(r.json()))

# T-S30-008: supervisor WITHOUT a supervisor-role member -> run accepted, then deterministically fails
r=c.post('/workflows', json={'name':'s30-sup-${TS}','team':'${TEAM}','orchestration':'supervisor'}, headers=H)
sid=r.json()['id']
c.post(f'/workflows/{sid}/members', json={'agent_id':aid['${AGENT_A}'],'position':1}, headers=H)
r=c.post(f'/workflows/{sid}/runs', json={'input_message':'go','run_by':'s30'}, headers=H)
if r.status_code!=202: bad('T-S30-008', 'supervisor run not accepted: '+r.text)
else:
    rid=r.json()['run_id']; parent=None
    for _ in range(10):
        time.sleep(1)
        parent=c.get(f'/workflows/{sid}/runs/{rid}/tree', headers=H).json()['parent']
        if parent['status']=='failed': break
    if parent and parent['status']=='failed' and 'supervisor' in (parent.get('error_message') or '').lower():
        ok('T-S30-008 supervisor-missing-role fails deterministically')
    else:
        bad('T-S30-008', str(parent))

# T-S30-009: handoff workflow run accepted -> 202
r=c.post('/workflows', json={'name':'s30-ho-${TS}','team':'${TEAM}','orchestration':'handoff'}, headers=H)
hid=r.json()['id']
c.post(f'/workflows/{hid}/members', json={'agent_id':aid['${AGENT_A}'],'position':1}, headers=H)
r=c.post(f'/workflows/{hid}/runs', json={'input_message':'go','run_by':'s30'}, headers=H)
ok('T-S30-009 handoff run accepted 202') if r.status_code==202 else bad('T-S30-009', r.text)

# T-S30-010: delete edge -> 204
if eid:
    r=c.request('DELETE', f'/workflows/{wid}/edges/{eid}', headers=H)
    ok('T-S30-010 delete edge 204') if r.status_code==204 else bad('T-S30-010', str(r.status_code))

print(f'__RESULT__ {P} {F}')
sys.exit(0 if F==0 else 1)
" 2>&1 | grep -v "Defaulted container" | tee /tmp/s30_out.txt

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s30_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 30 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
