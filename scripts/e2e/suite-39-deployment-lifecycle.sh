#!/usr/bin/env bash
# Suite 39: Sandbox deployment lifecycle actions (Slice 1a)
# Suspend / Resume / Terminate / Upgrade via PATCH /agents/{name}/deployments/{id}.
# Asserts the API-set transitional status (controller reconciles async).
# Tests T-S39-001 through T-S39-006.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT="dep-life-${TS}"

pass()  { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail()  { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -n "${API_POD:-}" ] || { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
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

echo "==> Suite 39: Deployment Lifecycle"
echo ""

# Setup: agent + two versions + one sandbox deployment.
echo "--- Setup: agent + v1 + v2 + sandbox deployment ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
httpx.post('http://localhost:8000/api/v1/agents/', json={
    'name': '${AGENT}', 'team': 'default', 'agent_type': 'declarative',
    'metadata': {'instructions': 'lifecycle test'}})
v1 = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/versions', json={'eval_passed': True}).json()
v2 = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/versions', json={'eval_passed': True}).json()
d = httpx.post('http://localhost:8000/api/v1/agents/${AGENT}/deploy', json={
    'version_id': v1['id'], 'environment': 'sandbox', 'ttl_hours': 12})
if d.status_code != 201:
    print(f'FAIL setup deploy {d.status_code}: {d.text}'); sys.exit(1)
dep = d.json()
if dep.get('ttl_hours') != 12:
    print(f'FAIL ttl_hours not stored: {dep.get(\"ttl_hours\")}'); sys.exit(1)
open('/tmp/s39.txt','w').write(f\"{dep['id']} {v2['id']}\")
print('OK')
" && pass "setup — deploy stores ttl_hours" || fail "setup"

S39_LINE=$(kubectl exec -n "$NAMESPACE" "$API_POD" -- cat /tmp/s39.txt 2>/dev/null || true)
DEP_ID=$(echo "$S39_LINE" | cut -d' ' -f1)
V2_ID=$(echo "$S39_LINE" | cut -d' ' -f2)

# T-S39-001 — suspend → suspending
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/${DEP_ID}', json={'action':'suspend'})
if r.status_code != 200 or r.json().get('status') != 'suspending':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" && pass "T-S39-001 — suspend → suspending" || fail "T-S39-001"

# T-S39-002 — resume → pending (controller re-reconciles + scales up)
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/${DEP_ID}', json={'action':'resume'})
if r.status_code != 200 or r.json().get('status') != 'pending':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" && pass "T-S39-002 — resume → pending" || fail "T-S39-002"

# T-S39-003 — upgrade swaps version_id + pending
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/${DEP_ID}', json={'action':'upgrade','version_id':'${V2_ID}'})
d = r.json()
if r.status_code != 200 or d.get('version_id') != '${V2_ID}' or d.get('status') != 'pending':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
if d.get('previous_version_id') is None:
    print('FAIL previous_version_id not set'); sys.exit(1)
print('OK')
" && pass "T-S39-003 — upgrade swaps version + records previous" || fail "T-S39-003"

# T-S39-004 — upgrade without version_id → 400
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/${DEP_ID}', json={'action':'upgrade'})
if r.status_code != 400:
    print(f'FAIL expected 400, got {r.status_code}'); sys.exit(1)
print('OK')
" && pass "T-S39-004 — upgrade without version_id → 400" || fail "T-S39-004"

# T-S39-005 — terminate → terminating
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/${DEP_ID}', json={'action':'terminate'})
if r.status_code != 200 or r.json().get('status') != 'terminating':
    print(f'FAIL {r.status_code}: {r.text}'); sys.exit(1)
print('OK')
" && pass "T-S39-005 — terminate → terminating" || fail "T-S39-005"

# T-S39-006 — unknown deployment → 404
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
r = httpx.patch('http://localhost:8000/api/v1/agents/${AGENT}/deployments/00000000-0000-0000-0000-000000000000', json={'action':'suspend'})
if r.status_code != 404:
    print(f'FAIL expected 404, got {r.status_code}'); sys.exit(1)
print('OK')
" && pass "T-S39-006 — unknown deployment → 404" || fail "T-S39-006"

# T-S39-007 — every status the CATALOG deployment router writes is a value the
# production_deployments CHECK constraint actually admits.
#
# WHY THIS AND NOT A LIVE SUSPEND: the cases above drive the SANDBOX path
# (/agents/{name}/deployments -> the `deployments` table), whose constraint DOES
# admit "suspending". The catalog path writes to `production_deployments`, whose
# constraint does not — so `PATCH /catalog/{id}/deployments/{did} {"action":"suspend"}`
# raised CheckViolationError and answered 500 on every call, and this suite stayed
# green throughout because it never touched that table.
#
# Asserting the SET rather than replaying one action fixes the class: any future
# status literal that drifts from the constraint fails here, without needing a
# published artifact + production deployment fixture to exist first.
# docs/bugs/production-deployment-suspend-500.md
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio, re, sys
from sqlalchemy import text
from db import AsyncSessionLocal

# Scan the WHOLE router, not one handler: catalog.py has TWO status writers
# (update_deployment and patch_production_deployment_status) plus the initial
# ProductionDeployment(status=...) at deploy time, and every one of them is
# bound by the same constraint. An earlier version of this check pinned a single
# function name, could not find it, and failed — correctly, but for the wrong
# reason. Every ProductionDeployment status literal in this file must be legal.
src = open('/app/routers/catalog.py').read()
written = sorted(set(re.findall(r'status\s*=\s*[\"\x27]([a-z_]+)[\"\x27]', src)))

async def m():
    async with AsyncSessionLocal() as s:
        d = (await s.execute(text(
            \"SELECT pg_get_constraintdef(oid) FROM pg_constraint \"
            \"WHERE conname='production_deployments_status_check'\"))).scalar()
    if not d:
        print('FAIL constraint production_deployments_status_check not found'); sys.exit(1)
    allowed = set(re.findall(r\"'([a-z_]+)'\", d))
    bad = [w for w in written if w not in allowed]
    print(f'writes={written}')
    print(f'allowed={sorted(allowed)}')
    if not written:
        print('FAIL parsed zero status literals — this check would pass vacuously'); sys.exit(1)
    if bad:
        print(f'FAIL written but REJECTED by the constraint: {bad}'); sys.exit(1)
    print('OK')
asyncio.run(m())
" && pass "T-S39-007 — catalog deployment statuses all satisfy the production CHECK" || fail "T-S39-007"

echo ""
echo "==> Suite 39 Results: ${PASS} passed, ${FAIL} failed"
[ "$FAIL" -eq 0 ] || exit 1
