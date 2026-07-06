#!/usr/bin/env bash
# Checkpoint CP-Wa — Rename + Composite CRUD (Decision 22, phase W1)
# Proves: old canvas-graph endpoint moved to /api/v1/agent-graphs; the new
# composite /api/v1/workflows endpoint is live; create/dup/get/add-member/archive
# work; /api/v1/agent-runs/{id}/children is reachable.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
echo "=== Checkpoint CP-Wa: Rename + Composite CRUD ==="

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, uuid
B = 'http://localhost:8000/api/v1'
c = httpx.Client(base_url=B, timeout=20)

# 1. Renamed canvas router live at /agent-graphs
r = c.get('/agent-graphs/', headers={'X-User-Sub': 'system'})
assert r.status_code == 200, f'GET /agent-graphs -> {r.status_code}'
print('  ok: GET /agent-graphs 200 (renamed)')

# 2. Old /workflows path no longer serves canvas graphs — it is the composite router
r = c.get('/workflows', headers={'X-User-Sub': 'system'})
assert r.status_code == 200, f'GET /workflows -> {r.status_code}'
print('  ok: GET /workflows 200 (composite)')

# Pick an existing agent to use as a member (same team required)
agents = c.get('/agents/?limit=1', headers={'X-User-Sub': 'system'}).json().get('items', [])
assert agents, 'need at least one agent to test membership'
member = agents[0]; team = member['team']
name = 'cp-wa-wf-' + uuid.uuid4().hex[:8]

# 3. Create composite workflow
r = c.post('/workflows', json={'name': name, 'team': team, 'orchestration': 'sequential'}, headers={'X-User-Sub': 'system'})
assert r.status_code == 201, f'create -> {r.status_code}: {r.text}'
wf = r.json(); wid = wf['id']
assert wf['member_count'] == 0
print('  ok: POST /workflows 201', wid)

# 3b. Duplicate name+team -> 409
r = c.post('/workflows', json={'name': name, 'team': team}, headers={'X-User-Sub': 'system'})
assert r.status_code == 409, f'dup -> {r.status_code}'
print('  ok: duplicate -> 409')

# 4. Add member (same team)
r = c.post(f'/workflows/{wid}/members', json={'agent_id': member['id'], 'position': 1}, headers={'X-User-Sub': 'system'})
assert r.status_code == 201, f'add member -> {r.status_code}: {r.text}'
print('  ok: add member 201')

# 5. GET with members
r = c.get(f'/workflows/{wid}', headers={'X-User-Sub': 'system'})
assert r.status_code == 200 and r.json()['member_count'] == 1 and len(r.json()['members']) == 1, r.text
print('  ok: GET /workflows/{id} member_count=1')

# 6. Archive (soft delete)
r = c.delete(f'/workflows/{wid}', headers={'X-User-Sub': 'system'})
assert r.status_code == 204, f'archive -> {r.status_code}'
lst = c.get('/workflows', headers={'X-User-Sub': 'system'}).json()
assert not any(w['id'] == wid for w in lst), 'archived workflow still listed'
print('  ok: archived + hidden from list')

# 7. /children endpoint reachable (empty for a random id)
r = c.get(f'/agent-runs/{uuid.uuid4()}/children')
assert r.status_code == 200 and r.json() == [], f'children -> {r.status_code}: {r.text}'
print('  ok: GET /agent-runs/{id}/children 200 []')

print('=== CP-Wa ALL PASS ===')
" 2>&1 | grep -v "Defaulted container"

echo "PASS"
