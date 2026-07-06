#!/usr/bin/env bash
# Suite 33: Composable agent filter (?composable=true)
# Tests T-S33-001 through T-S33-003
#
# Proves the GET /api/v1/agents/?composable=true filter:
#   - Unfiltered list includes both a plain agent AND one with an enabled schedule trigger
#   - ?composable=true INCLUDES the plain agent (no enabled triggers)
#   - ?composable=true EXCLUDES the agent that has an enabled schedule trigger
#
# Usage: bash scripts/e2e/suite-33-composable-agents.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
PLAIN_AGENT="s33-plain-${TS}"
TRIGGERED_AGENT="s33-triggered-${TS}"
TEAM="platform"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agents..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
c=httpx.Client(base_url='http://localhost:8000/api/v1', timeout=10, headers={'X-User-Sub':'system'})
for n in ['${PLAIN_AGENT}','${TRIGGERED_AGENT}']:
    try: c.delete('/agents/'+n)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 33: Composable Agent Filter ==="

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
B='http://localhost:8000/api/v1'; H={'X-User-Sub':'system'}
c=httpx.Client(base_url=B, timeout=30)
P=0; F=0
def ok(n):
    global P; P+=1; print('  PASS:', n)
def bad(n,d=''):
    global F; F+=1; print('  FAIL:', n, d)

# Setup: create plain agent (no triggers)
r=c.post('/agents/', json={'name':'${PLAIN_AGENT}','team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive'}, headers=H)
assert r.status_code==201, 'setup plain agent: '+r.text

# Setup: create triggered agent and attach an enabled schedule trigger
r=c.post('/agents/', json={'name':'${TRIGGERED_AGENT}','team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive'}, headers=H)
assert r.status_code==201, 'setup triggered agent: '+r.text
r=c.post('/agents/${TRIGGERED_AGENT}/triggers', json={'trigger_type':'schedule','cron_expression':'0 9 * * 1','timezone':'UTC'}, headers=H)
assert r.status_code==201, 'setup schedule trigger: '+r.text

# T-S33-001: unfiltered list includes BOTH agents
lst=c.get('/agents/', params={'team':'${TEAM}'}, headers=H).json()
names={a['name'] for a in lst.get('items',[])}
if '${PLAIN_AGENT}' in names and '${TRIGGERED_AGENT}' in names:
    ok('T-S33-001 unfiltered list includes both agents')
else:
    bad('T-S33-001', 'found: '+str(names))

# T-S33-002: composable=true includes the plain agent
lst2=c.get('/agents/', params={'team':'${TEAM}','composable':'true'}, headers=H).json()
cnames={a['name'] for a in lst2.get('items',[])}
ok('T-S33-002 composable list includes plain agent') if '${PLAIN_AGENT}' in cnames else bad('T-S33-002', 'not found in composable set: '+str(cnames))

# T-S33-003: composable=true EXCLUDES the agent with an enabled schedule trigger
ok('T-S33-003 composable list excludes scheduled-trigger agent') if '${TRIGGERED_AGENT}' not in cnames else bad('T-S33-003', 'triggered agent unexpectedly in composable list')

print('__RESULT__', P, F)
sys.exit(0 if F==0 else 1)
" 2>&1 | grep -v "Defaulted container" | tee /tmp/s33_out.txt

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s33_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 33 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
