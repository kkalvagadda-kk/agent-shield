#!/usr/bin/env bash
# Suite 29: Composite Workflow (Decision 22)
# Tests T-S29-001 through T-S29-010
#
# Validates the composite-workflow executable: CRUD, member management (same/
# cross team), sequential run → run tree (parent workflow_id + child
# parent_run_id), member removal, and archive-then-run rejection.
#
# Usage: bash scripts/e2e/suite-29-workflow-composite.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
WF_NAME="s29-wf-${TS}"
AGENT_A="s29-agent-a-${TS}"
AGENT_B="s29-agent-b-${TS}"
AGENT_X="s29-agent-x-${TS}"   # different team (for the 422 test)
TEAM="platform"
OTHER_TEAM="default"

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agents..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for n in ['${AGENT_A}', '${AGENT_B}', '${AGENT_X}']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + n, method='DELETE'), timeout=5)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 29: Composite Workflow (Decision 22) ==="

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, time, uuid
B='http://localhost:8000/api/v1'
H={'X-User-Sub':'system'}
c=httpx.Client(base_url=B, timeout=30)
P=0; F=0
def ok(n):
    global P; P+=1; print('  PASS:', n)
def bad(n,d=''):
    global F; F+=1; print('  FAIL:', n, d)

# Setup: two same-team agents + one different-team agent
for name, team in [('${AGENT_A}','${TEAM}'), ('${AGENT_B}','${TEAM}'), ('${AGENT_X}','${OTHER_TEAM}')]:
    r=c.post('/agents/', json={'name':name,'team':team,'agent_type':'declarative','execution_shape':'reactive'}, headers=H)
    assert r.status_code==201, f'setup agent {name}: {r.status_code} {r.text}'
aid = {a['name']:a['id'] for a in c.get('/agents/?limit=500', headers=H).json()['items']}

# T-S29-001: create composite workflow (happy path CRUD)
r=c.post('/workflows', json={'name':'${WF_NAME}','team':'${TEAM}','orchestration':'sequential'}, headers=H)
if r.status_code==201 and r.json()['member_count']==0: ok('T-S29-001 create workflow'); wid=r.json()['id']
else: bad('T-S29-001', r.text); sys.exit(1)

# T-S29-002: duplicate name+team → 409
r=c.post('/workflows', json={'name':'${WF_NAME}','team':'${TEAM}'}, headers=H)
ok('T-S29-002 duplicate 409') if r.status_code==409 else bad('T-S29-002', str(r.status_code))

# T-S29-003: add member same team → 201
r=c.post(f'/workflows/{wid}/members', json={'agent_id':aid['${AGENT_A}'],'position':1}, headers=H)
ok('T-S29-003 add same-team member 201') if r.status_code==201 else bad('T-S29-003', r.text)
c.post(f'/workflows/{wid}/members', json={'agent_id':aid['${AGENT_B}'],'position':2}, headers=H)

# T-S29-004: add member different team → 422 (validation) or 400
r=c.post(f'/workflows/{wid}/members', json={'agent_id':aid['${AGENT_X}'],'position':3}, headers=H)
ok('T-S29-004 cross-team member rejected') if r.status_code in (400,422) else bad('T-S29-004', str(r.status_code))

# member_count now 2
mc=c.get(f'/workflows/{wid}', headers=H).json()['member_count']
ok('T-S29-009a member_count=2') if mc==2 else bad('T-S29-009a', str(mc))

# T-S29-005: trigger sequential run → 202
r=c.post(f'/workflows/{wid}/runs', json={'input_message':'s29 run','run_by':'s29'}, headers=H)
if r.status_code==202: ok('T-S29-005 trigger run 202'); run_id=r.json()['run_id']
else: bad('T-S29-005', r.text); run_id=None

# T-S29-006/007/008: run tree — parent workflow_id set, children parent_run_id set + workflow_id NULL
if run_id:
    tree=None
    for _ in range(15):
        time.sleep(2)
        tree=c.get(f'/workflows/{wid}/runs/{run_id}/tree', headers=H).json()
        if tree['children']: break
    if tree and tree['parent']['workflow_id']==wid: ok('T-S29-008 parent workflow_id set')
    else: bad('T-S29-008', str(tree['parent'] if tree else None))
    if tree and tree['children'] and all(ch['parent_run_id']==run_id for ch in tree['children']):
        ok('T-S29-006 children parent_run_id -> parent')
    else: bad('T-S29-006', 'no/mismatched children')
    if tree and tree['children'] and all(ch['workflow_id'] is None for ch in tree['children']):
        ok('T-S29-007 child workflow_id NULL')
    else: bad('T-S29-007', 'child workflow_id not NULL')

# T-S29-009: remove member decrements member_count
r=c.request('DELETE', f'/workflows/{wid}/members/'+aid['${AGENT_B}'], headers=H)
mc=c.get(f'/workflows/{wid}', headers=H).json()['member_count']
ok('T-S29-009 remove member -> count 1') if (r.status_code==204 and mc==1) else bad('T-S29-009', str((r.status_code,mc)))

# T-S29-010: archive then run → rejected (422)
c.request('DELETE', f'/workflows/{wid}', headers=H)
r=c.post(f'/workflows/{wid}/runs', json={'input_message':'x','run_by':'s29'}, headers=H)
ok('T-S29-010 run archived workflow rejected') if r.status_code in (404,422) else bad('T-S29-010', str(r.status_code))

print(f'__RESULT__ {P} {F}')
sys.exit(0 if F==0 else 1)
" 2>&1 | grep -v "Defaulted container" | tee /tmp/s29_out.txt

# roll up counts from the python result line
RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s29_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 29 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
