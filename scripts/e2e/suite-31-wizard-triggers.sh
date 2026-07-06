#!/usr/bin/env bash
# Suite 31: Agent Wizard Triggers + Memory (WS3/P4 backend paths)
# Tests T-S31-001 through T-S31-007
#
# Validates the backend paths the create-agent wizard + Settings trigger UI rely
# on: schedule + webhook trigger creation, one-time token/webhook_url in the
# create response (and their absence from list/get), token rotation, and the
# memory_enabled round-trip.
#
# Usage: bash scripts/e2e/suite-31-wizard-triggers.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)
AGENT="s31-agent-${TS}"
MEM_AGENT="s31-mem-${TS}"
TEAM="platform"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

cleanup() {
  echo ""; echo "==> Cleanup: deleting test agents..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx
c=httpx.Client(base_url='http://localhost:8000/api/v1', timeout=10, headers={'X-User-Sub':'system'})
for n in ['${AGENT}','${MEM_AGENT}']:
    try: c.delete('/agents/'+n)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

echo "=== Suite 31: Agent Wizard Triggers + Memory ==="

kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, sys
B='http://localhost:8000/api/v1'; H={'X-User-Sub':'system'}
c=httpx.Client(base_url=B, timeout=30)
P=0; F=0
def ok(n):
    global P; P+=1; print('  PASS:', n)
def bad(n,d=''):
    global F; F+=1; print('  FAIL:', n, d)

# T-S31-001: create agent (wizard base path)
r=c.post('/agents/', json={'name':'${AGENT}','team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive'}, headers=H)
ok('T-S31-001 create agent 201') if r.status_code==201 else bad('T-S31-001', r.text)

# T-S31-002: create schedule trigger
r=c.post('/agents/${AGENT}/triggers', json={'trigger_type':'schedule','cron_expression':'0 9 * * 1','timezone':'UTC','alert_email':'oncall@example.com'}, headers=H)
if r.status_code==201 and r.json()['cron_expression']=='0 9 * * 1': ok('T-S31-002 schedule trigger created')
else: bad('T-S31-002', r.text)

# T-S31-003: create webhook trigger -> token + webhook_url returned ONCE
r=c.post('/agents/${AGENT}/triggers', json={'trigger_type':'webhook','filter_conditions':[{'field':'event_type','op':'eq','value':'payment.fail'}]}, headers=H)
j=r.json() if r.status_code==201 else {}
if r.status_code==201 and j.get('token') and j.get('webhook_url') and '/hooks/${AGENT}/' in j['webhook_url']:
    ok('T-S31-003 webhook create returns token + webhook_url'); wh_id=j['id']
else:
    bad('T-S31-003', r.text); wh_id=None

# T-S31-004: list triggers -> 2, and token/webhook_url NOT present in list
lst=c.get('/agents/${AGENT}/triggers', headers=H).json()
tokens_absent = all(not t.get('token') and not t.get('webhook_url') for t in lst)
ok('T-S31-004 list = 2, token/url absent') if (len(lst)==2 and tokens_absent) else bad('T-S31-004', str(lst))

# T-S31-005: rotate token -> new token + webhook_url
if wh_id:
    r=c.post(f'/agents/${AGENT}/triggers/{wh_id}/rotate-token', headers=H)
    j=r.json() if r.status_code==200 else {}
    ok('T-S31-005 rotate returns new token + url') if (r.status_code==200 and j.get('token') and j.get('webhook_url')) else bad('T-S31-005', r.text)

# T-S31-006: schedule trigger requires cron (422 without it)
r=c.post('/agents/${AGENT}/triggers', json={'trigger_type':'schedule'}, headers=H)
ok('T-S31-006 schedule w/o cron rejected') if r.status_code in (400,422) else bad('T-S31-006', str(r.status_code))

# T-S31-007: memory_enabled round-trip (create + PATCH)
r=c.post('/agents/', json={'name':'${MEM_AGENT}','team':'${TEAM}','agent_type':'declarative','execution_shape':'reactive','memory_enabled':True}, headers=H)
created_mem = r.status_code==201 and r.json().get('memory_enabled') is True
r2=c.put('/agents/${MEM_AGENT}', json={'memory_enabled':False}, headers=H)
toggled = r2.status_code==200 and c.get('/agents/${MEM_AGENT}', headers=H).json().get('memory_enabled') is False
ok('T-S31-007 memory_enabled create + toggle') if (created_mem and toggled) else bad('T-S31-007', str((r.text[:80], r2.status_code)))

print(f'__RESULT__ {P} {F}')
sys.exit(0 if F==0 else 1)
" 2>&1 | grep -v "Defaulted container" | tee /tmp/s31_out.txt

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s31_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}'); FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 31 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
