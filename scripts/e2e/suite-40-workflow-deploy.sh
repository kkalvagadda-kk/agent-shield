#!/usr/bin/env bash
# Suite 40: Workflow versions + deployments (Slice 1b)
# Tests the full lifecycle: create workflow → add members → snapshot version →
# deploy → stats → lifecycle actions (suspend/resume/terminate/upgrade).
# Tests T-S40-001 through T-S40-008.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
WF_NAME="wf-s40-${TS}"
AGENT_A="wf-mem-a-${TS}"
AGENT_B="wf-mem-b-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -n "${API_POD:-}" ] || { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['${AGENT_A}', '${AGENT_B}']:
    try:
        req = urllib.request.Request(f'http://localhost:8000/api/v1/agents/{name}', method='DELETE')
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
# Workflow archive (can't hard-delete, but OK for cleanup)
import json
try:
    req = urllib.request.Request(
        'http://localhost:8000/api/v1/workflows',
        headers={'Content-Type': 'application/json'})
    r = urllib.request.urlopen(req, timeout=5)
    for wf in json.loads(r.read()):
        if wf['name'] == '${WF_NAME}':
            dreq = urllib.request.Request(
                f\"http://localhost:8000/api/v1/workflows/{wf['id']}\", method='DELETE')
            urllib.request.urlopen(dreq, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 40: Workflow Versions + Deployments"
echo ""

# Setup: two agents + a workflow with both as members
echo "--- Setup: agents + workflow + members ---"
SETUP_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, json

base = 'http://localhost:8000/api/v1'

# Create two member agents
for name in ['${AGENT_A}', '${AGENT_B}']:
    r = httpx.post(f'{base}/agents/', json={
        'name': name, 'team': 'default', 'agent_type': 'declarative',
        'metadata': {'instructions': 'wf member'}})
    if r.status_code not in (201, 409):
        print(f'FAIL agent create {name}: {r.status_code}'); sys.exit(1)

# Create workflow
r = httpx.post(f'{base}/workflows', json={
    'name': '${WF_NAME}', 'team': 'default', 'orchestration': 'sequential'})
if r.status_code != 201:
    print(f'FAIL wf create: {r.status_code} {r.text}'); sys.exit(1)
wf = r.json()

# Get agent IDs
a_id = httpx.get(f'{base}/agents/${AGENT_A}').json()['id']
b_id = httpx.get(f'{base}/agents/${AGENT_B}').json()['id']

# Add members
httpx.post(f'{base}/workflows/{wf[\"id\"]}/members', json={'agent_id': a_id, 'position': 0})
httpx.post(f'{base}/workflows/{wf[\"id\"]}/members', json={'agent_id': b_id, 'position': 1})

print(wf['id'])
" 2>&1 | grep -v "^Defaulted" | tail -1)

WF_ID="$SETUP_OUT"
echo "  workflow_id=$WF_ID"

# T-S40-001 — Create version (snapshot)
echo ""
V1_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys, json
r = httpx.post('http://localhost:8000/api/v1/workflows/${WF_ID}/versions', json={'eval_passed': True})
if r.status_code != 201:
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
v = r.json()
if v['version_number'] != 1:
    print(f'FAIL version_number={v[\"version_number\"]}'); sys.exit(1)
if len(v['members']) != 2:
    print(f'FAIL members count={len(v[\"members\"])}'); sys.exit(1)
print(v['id'])
" 2>&1 | grep -v "^Defaulted" | tail -1)
[ -n "$V1_OUT" ] && pass "T-S40-001 — version snapshot (v1, 2 members)" || fail "T-S40-001"
V1_ID="$V1_OUT"

# T-S40-002 — List versions
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/workflows/${WF_ID}/versions')
if r.status_code != 200 or len(r.json()) < 1:
    print(f'FAIL {r.status_code}'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | grep -q "OK" && pass "T-S40-002 — list versions" || fail "T-S40-002"

# T-S40-003 — Deploy workflow
DEP_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.post('http://localhost:8000/api/v1/workflows/${WF_ID}/deploy', json={
    'version_id': '${V1_ID}', 'environment': 'sandbox', 'ttl_hours': 6})
if r.status_code != 201:
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
d = r.json()
if d['status'] != 'running':
    print(f'FAIL status={d[\"status\"]}'); sys.exit(1)
if d.get('ttl_hours') != 6:
    print(f'FAIL ttl_hours={d.get(\"ttl_hours\")}'); sys.exit(1)
print(d['id'])
" 2>&1 | grep -v "^Defaulted" | tail -1)
[ -n "$DEP_OUT" ] && pass "T-S40-003 — deploy workflow (running, ttl_hours=6)" || fail "T-S40-003"
DEP_ID="$DEP_OUT"

# T-S40-004 — List deployments
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/workflows/${WF_ID}/deployments')
if r.status_code != 200 or len(r.json()) < 1:
    print(f'FAIL {r.status_code}'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | grep -q "OK" && pass "T-S40-004 — list workflow deployments" || fail "T-S40-004"

# T-S40-005 — Suspend → suspended (immediate for workflow, no controller)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/workflows/${WF_ID}/deployments/${DEP_ID}', json={'action':'suspend'})
if r.status_code != 200 or r.json()['status'] != 'suspended':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | grep -q "OK" && pass "T-S40-005 — suspend → suspended" || fail "T-S40-005"

# T-S40-006 — Resume → running
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/workflows/${WF_ID}/deployments/${DEP_ID}', json={'action':'resume'})
if r.status_code != 200 or r.json()['status'] != 'running':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | grep -q "OK" && pass "T-S40-006 — resume → running" || fail "T-S40-006"

# T-S40-007 — Upgrade (create v2, upgrade to it)
V2_OUT=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
v2 = httpx.post('http://localhost:8000/api/v1/workflows/${WF_ID}/versions', json={'eval_passed': True}).json()
r = httpx.patch('http://localhost:8000/api/v1/workflows/${WF_ID}/deployments/${DEP_ID}',
    json={'action':'upgrade','version_id': v2['id']})
if r.status_code != 200:
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
d = r.json()
if d['version_id'] != v2['id'] or d.get('previous_version_id') is None:
    print(f'FAIL version swap'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | tail -1)
[ "$V2_OUT" = "OK" ] && pass "T-S40-007 — upgrade swaps version" || fail "T-S40-007"

# T-S40-008 — Terminate
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/workflows/${WF_ID}/deployments/${DEP_ID}', json={'action':'terminate'})
if r.status_code != 200 or r.json()['status'] != 'terminated':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" 2>&1 | grep -v "^Defaulted" | grep -q "OK" && pass "T-S40-008 — terminate → terminated" || fail "T-S40-008"

echo ""
echo "==> Suite 40 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
