#!/usr/bin/env bash
# Suite 38: Deployment Overview — deployment-scoped stats + runs (Slice 1)
# Proves the unified-artifact-deployment-navigation mental model at the API
# layer: a deployment has a name, and playground runs are isolated to the
# sandbox deployment that produced them (agent_runs.sandbox_deployment_id).
# Tests T-S38-001 through T-S38-006.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0

TS=$(date +%s)
AGENT="dep-ovw-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "${API_POD:-}" ]; then
  echo "FATAL: Registry API pod not found in $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
try:
    req = urllib.request.Request('http://localhost:8000/api/v1/agents/${AGENT}', method='DELETE')
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "==> Suite 38: Deployment Overview"
echo ""

# T-S38-001 — Deploy a sandbox deployment; it gets an auto-generated name.
echo "--- T-S38-001: sandbox deployment has an auto-generated name ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys

r = httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${AGENT}', 'team': 'default', 'agent_type': 'declarative',
    'metadata': {'instructions': 'dep overview test'},
})
if r.status_code != 201:
    print(f'FAIL: create agent {r.status_code}: {r.text}'); sys.exit(1)

v = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/versions', json={
    'image_tag': 'registry.internal/agentshield/noop:latest', 'eval_passed': True,
})
if v.status_code != 201:
    print(f'FAIL: create version {v.status_code}: {v.text}'); sys.exit(1)
version_id = v.json()['id']

d = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/deploy', json={
    'version_id': version_id, 'environment': 'sandbox',
})
if d.status_code != 201:
    print(f'FAIL: deploy {d.status_code}: {d.text}'); sys.exit(1)
dep = d.json()
name = dep.get('name')
if not name or not name.startswith('${AGENT}-'):
    print(f'FAIL: deployment name not auto-generated: {name}'); sys.exit(1)

# Save → reload → assert: name survives a fresh read from the backend.
lst = httpx.get('http://localhost:8000/api/v1/agents/${AGENT}/deployments')
match = [x for x in lst.json() if x['id'] == dep['id']]
if not match or match[0].get('name') != name:
    print(f'FAIL: deployment name did not persist on reload'); sys.exit(1)

with open('/tmp/s38_dep.txt', 'w') as f:
    f.write(dep['id'])
print('OK ' + name)
" && pass "T-S38-001 — deployment auto-named + name persists on reload" || fail "T-S38-001"

DEP_ID=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- cat /tmp/s38_dep.txt 2>/dev/null || true)

# T-S38-002 — Playground run scoped to the deployment shows in /runs (round-trip).
echo "--- T-S38-002: playground run scoped by sandbox_deployment_id ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
dep_id = '${DEP_ID}'
ar = httpx.post('http://localhost:8000/api/v1/agent-runs', json={
    'agent_name': '${AGENT}', 'context': 'playground', 'trigger_type': 'api',
    'input': 'hello sandbox', 'run_by': 's38', 'team': 'default',
    'sandbox_deployment_id': dep_id,
})
if ar.status_code != 201:
    print(f'FAIL: create run {ar.status_code}: {ar.text}'); sys.exit(1)
if ar.json().get('sandbox_deployment_id') != dep_id:
    print(f'FAIL: run not scoped: {ar.json().get(\"sandbox_deployment_id\")}'); sys.exit(1)

# Reload from the deployment-scoped endpoint.
r = httpx.get(f'http://localhost:8000/api/v1/deployments/{dep_id}/runs', params={'context': 'playground'})
if r.status_code != 200:
    print(f'FAIL: runs endpoint {r.status_code}: {r.text}'); sys.exit(1)
runs = r.json()
if len(runs) < 1 or runs[0]['sandbox_deployment_id'] != dep_id:
    print(f'FAIL: expected >=1 scoped run, got {len(runs)}'); sys.exit(1)
print('OK')
" && pass "T-S38-002 — run round-trips through /deployments/{id}/runs" || fail "T-S38-002"

# T-S38-003 — Deployment-scoped stats reflect the run.
echo "--- T-S38-003: deployment stats run_count >= 1 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/deployments/${DEP_ID}/stats', params={'context': 'playground'})
if r.status_code != 200:
    print(f'FAIL: stats {r.status_code}: {r.text}'); sys.exit(1)
data = r.json()
if data.get('run_count', 0) < 1:
    print(f'FAIL: run_count expected >=1, got {data.get(\"run_count\")}'); sys.exit(1)
if 'error_rate' not in data:
    print('FAIL: missing error_rate'); sys.exit(1)
print('OK')
" && pass "T-S38-003 — deployment stats reflect scoped run" || fail "T-S38-003"

# T-S38-004 — Context isolation: a sandbox id is unknown to the production table.
echo "--- T-S38-004: production context does not resolve a sandbox id (404) ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/deployments/${DEP_ID}/runs', params={'context': 'production'})
if r.status_code != 404:
    print(f'FAIL: expected 404 for cross-context, got {r.status_code}'); sys.exit(1)
print('OK')
" && pass "T-S38-004 — explicit context prevents cross-table leakage" || fail "T-S38-004"

# T-S38-005 — Invalid context is rejected.
echo "--- T-S38-005: invalid context -> 422 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/deployments/${DEP_ID}/stats', params={'context': 'bogus'})
if r.status_code != 422:
    print(f'FAIL: expected 422, got {r.status_code}'); sys.exit(1)
print('OK')
" && pass "T-S38-005 — invalid context rejected" || fail "T-S38-005"

# T-S38-006 — Unknown deployment id -> 404.
echo "--- T-S38-006: unknown deployment id -> 404 ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.get('http://localhost:8000/api/v1/deployments/00000000-0000-0000-0000-000000000000/stats', params={'context': 'playground'})
if r.status_code != 404:
    print(f'FAIL: expected 404, got {r.status_code}'); sys.exit(1)
print('OK')
" && pass "T-S38-006 — unknown deployment 404" || fail "T-S38-006"

echo ""
echo "==> Suite 38 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
