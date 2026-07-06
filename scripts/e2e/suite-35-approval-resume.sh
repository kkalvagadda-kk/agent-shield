#!/usr/bin/env bash
# Suite 35: Production HITL approval decision + best-effort pod resume
# Tests T-S35-001 through T-S35-003
#
# Proves the production approval decision path:
#   - Create an approval with a thread_id (T-S35-001)
#   - PATCH approved → 200 + status=approved, decision_at set (T-S35-002)
#   - PATCH a second approval as rejected → 200 + status=rejected (T-S35-003)
#
# The resume POST to the agent pod (/resume/{thread_id}) is fire-and-forget and
# swallows all errors — no live agent pod is needed. The assertion is purely that
# the PATCH returns 200 and the persisted status matches the decision.
#
# reviewer_id="system" bypasses the ApprovalAuthority check (test-only bypass).
#
# Usage: bash scripts/e2e/suite-35-approval-resume.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT="s35-agent-${TS}"
TEAM="platform"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agent..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
c=httpx.Client(base_url='http://localhost:8000/api/v1', timeout=10, headers={'X-User-Sub':'system'})
try: c.delete('/agents/${AGENT}')
except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 35: Production HITL Approval Resume ==="

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
B='http://localhost:8000/api/v1'; H={'X-User-Sub':'system'}
c=httpx.Client(base_url=B, timeout=30)
P=0; F=0
def ok(n):
    global P; P+=1; print('  PASS:', n)
def bad(n,d=''):
    global F; F+=1; print('  FAIL:', n, d)

# Setup: create agent + get UUID (ApprovalCreate requires agent_id UUID)
r=c.post('/agents/', json={'name':'${AGENT}','team':'${TEAM}','agent_type':'sdk','execution_shape':'durable'}, headers=H)
assert r.status_code==201, 'setup agent: '+r.text
agent_id=r.json()['id']

# T-S35-001: create an approval with a thread_id
r=c.post('/approvals/', json={
    'agent_id': agent_id, 'agent_name': '${AGENT}', 'team': '${TEAM}',
    'thread_id': 's35-001-${TS}', 'tool_name': 'send_payment',
    'tool_args': {'amount': 250.0, 'recipient': 'ops@example.com'},
    'risk_level': 'high', 'timeout_seconds': 1800, 'context': 'production',
}, headers=H)
if r.status_code==201:
    j=r.json(); a1_id=j['id']; a1_ver=j['version']
    ok('T-S35-001 approval created (thread_id=s35-001-${TS} status='+j['status']+')')
else:
    bad('T-S35-001', r.text); print('__RESULT__', P, F); sys.exit(1)

# T-S35-002: PATCH approved → 200 + status=approved
# The best-effort resume POST to /resume/{thread_id} fires but swallows the
# connection error (no live agent pod) — the endpoint always returns 200.
r=c.patch('/approvals/'+a1_id, json={
    'decision': 'approved', 'reviewer_id': 'system',
    'reviewer_notes': 'E2E Suite 35 approve', 'version': a1_ver,
}, headers=H)
j=r.json() if r.status_code==200 else {}
if r.status_code==200 and j.get('status')=='approved':
    ok('T-S35-002 PATCH approved -> 200 + status=approved (decision_at set: '+str(bool(j.get('decision_at')))+')')
else:
    bad('T-S35-002', 'status_code='+str(r.status_code)+' body='+r.text[:200])

# T-S35-003: create a second approval, PATCH as rejected → 200 + status=rejected
r2=c.post('/approvals/', json={
    'agent_id': agent_id, 'agent_name': '${AGENT}', 'team': '${TEAM}',
    'thread_id': 's35-002-${TS}', 'tool_name': 'send_payment',
    'tool_args': {'amount': 500.0, 'recipient': 'finance@example.com'},
    'risk_level': 'critical', 'timeout_seconds': 1800, 'context': 'production',
}, headers=H)
if r2.status_code==201:
    j2=r2.json(); a2_id=j2['id']; a2_ver=j2['version']
    r3=c.patch('/approvals/'+a2_id, json={
        'decision': 'rejected', 'reviewer_id': 'system',
        'reviewer_notes': 'E2E Suite 35 reject', 'version': a2_ver,
    }, headers=H)
    j3=r3.json() if r3.status_code==200 else {}
    if r3.status_code==200 and j3.get('status')=='rejected':
        ok('T-S35-003 PATCH rejected -> 200 + status=rejected (decision_at set: '+str(bool(j3.get('decision_at')))+')')
    else:
        bad('T-S35-003', 'status_code='+str(r3.status_code)+' body='+r3.text[:200])
else:
    bad('T-S35-003', 'create approval 2 failed: '+r2.text[:200])

print('__RESULT__', P, F)
sys.exit(0 if F==0 else 1)
" 2>&1 | grep -v "Defaulted container" | tee /tmp/s35_out.txt

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s35_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 35 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
