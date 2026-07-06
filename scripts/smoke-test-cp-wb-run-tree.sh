#!/usr/bin/env bash
# Checkpoint CP-Wb — Run-Tree Smoke (Decision 22, phase W3)
# Proves: POST /workflows/{id}/runs creates a PARENT AgentRun (workflow_id set),
# and orchestration creates CHILD runs (parent_run_id → parent). The /tree
# endpoint returns the hierarchy. Child dispatch may fail if member pods are not
# running — that's fine; this checkpoint verifies the run-TREE structure, not
# agent execution.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
echo "=== Checkpoint CP-Wb: Run-Tree Smoke ==="

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, time, uuid
B = 'http://localhost:8000/api/v1'
H = {'X-User-Sub': 'system'}
c = httpx.Client(base_url=B, timeout=30)

# Need a registered agent to use as a member.
agents = c.get('/agents/?limit=5', headers=H).json().get('items', [])
if not agents:
    print('SKIP: no agents registered — cannot test run tree'); sys.exit(0)
member = agents[0]; team = member['team']
name = 'cp-wb-wf-' + uuid.uuid4().hex[:8]

# Create workflow + add the member.
wf = c.post('/workflows', json={'name': name, 'team': team, 'orchestration': 'sequential'}, headers=H).json()
wid = wf['id']
r = c.post(f'/workflows/{wid}/members', json={'agent_id': member['id'], 'position': 1}, headers=H)
assert r.status_code == 201, f'add member -> {r.status_code}: {r.text}'
print('  ok: workflow + member created', wid)

# Start a run.
r = c.post(f'/workflows/{wid}/runs', json={'input_message': 'hello workflow', 'run_by': 'cp-wb-smoke'}, headers=H)
assert r.status_code == 202, f'start run -> {r.status_code}: {r.text}'
run_id = r.json()['run_id']
assert r.json()['workflow_id'] == wid
print('  ok: POST /runs 202 run_id=', run_id)

# Poll the tree until a child appears (background orchestration) — up to ~30s.
tree = None
for _ in range(15):
    time.sleep(2)
    r = c.get(f'/workflows/{wid}/runs/{run_id}/tree', headers=H)
    assert r.status_code == 200, f'tree -> {r.status_code}: {r.text}'
    tree = r.json()
    if tree['children']:
        break

assert tree is not None, 'no tree'
# Parent carries workflow_id.
assert tree['parent']['workflow_id'] == wid, f'parent.workflow_id mismatch: {tree[\"parent\"]}'
print('  ok: parent run has workflow_id set')
# At least one child, each linked to the parent.
assert tree['children'], f'no child runs created (parent status={tree[\"parent\"][\"status\"]})'
for ch in tree['children']:
    assert ch['parent_run_id'] == run_id, f'child parent_run_id mismatch: {ch}'
    assert ch['trigger_type'] == 'workflow', f'child trigger_type: {ch}'
print(f'  ok: {len(tree[\"children\"])} child run(s), all parent_run_id -> parent')

# /runs list includes this run.
runs = c.get(f'/workflows/{wid}/runs', headers=H).json()
assert any(x['id'] == run_id for x in runs), 'run missing from /runs list'
print('  ok: GET /workflows/{id}/runs lists the run')

# Cleanup: archive the workflow.
c.delete(f'/workflows/{wid}', headers=H)
print('=== CP-Wb ALL PASS ===')
" 2>&1 | grep -v "Defaulted container"

echo "PASS"
