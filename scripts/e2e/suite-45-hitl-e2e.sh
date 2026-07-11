#!/usr/bin/env bash
# suite-45-hitl-e2e.sh — Full HITL end-to-end test (sandbox + production)
#
# Tests the complete 7-step HITL approval flow:
#   1. Agent with high-risk tool triggers OPA require_approval
#   2. Chat → approval_requested SSE → approval record created
#   3. Approve path: tool executes, real results returned
#   4. Deny path: agent responds with own knowledge
#   5. Approvals visible to team members
#   6. Audit trail: decision_at, reviewer_id on all records
#   7. Production: authority-gated approval + resume-stream
#
# Requires: serper-agent-4 deployed with web_search (risk=high),
#           Serper API key seeded, OPA bundle propagated.

set -euo pipefail

PASS=0; FAIL=0; SKIP=0
AGENT="serper-agent-4"
KALYAN="643b0e62-b437-40f8-8104-57c34203624b"
ADMIN="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"

POD=$(kubectl get pods -n agentshield-platform -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$POD" ]; then
  echo "FATAL: registry-api pod not found"
  exit 1
fi

run_test() {
  local test_id="$1" desc="$2"
  shift 2
  echo -n "  $test_id — $desc ... "
}

pass() { echo "PASS"; PASS=$((PASS + 1)); }
fail() { echo "FAIL${1:+ ($1)}"; FAIL=$((FAIL + 1)); }

api() {
  kubectl exec -n agentshield-platform "$POD" -c registry-api -- \
    python3 -c "$1" 2>&1
}

# ===========================================================================
echo ""
echo "=== Suite 45: HITL End-to-End (sandbox + production) ==="
echo ""

# ---------------------------------------------------------------------------
# T-S45-001: Pre-flight — bundle has agent with high-risk tool
# ---------------------------------------------------------------------------
run_test "T-S45-001" "OPA bundle has serper-agent-4 with web_search risk=high"
BUNDLE_CHECK=$(api "
import asyncio, json
async def check():
    from bundle_generator import generate_bundle_data
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        data = await generate_bundle_data(db)
        for key, val in data.get('agents', {}).items():
            if 'serper-agent-4' in key:
                tools = val.get('tools', [])
                ws = [t for t in tools if t.get('name') == 'web_search' and t.get('risk') == 'high']
                if ws:
                    print('OK')
                else:
                    print(f'WRONG_RISK: {tools}')
                return
        print('NOT_IN_BUNDLE')
asyncio.run(check())
")
if echo "$BUNDLE_CHECK" | grep -q "^OK$"; then pass; else fail "$BUNDLE_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-002: ApprovalAuthority auto-granted for team
# ---------------------------------------------------------------------------
run_test "T-S45-002" "ApprovalAuthority exists for both team members"
AUTH_CHECK=$(api "
import asyncio
async def check():
    from db import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as db:
        r = await db.execute(text(\"SELECT COUNT(*) FROM approval_authority WHERE resource_id='web_search' AND revoked_at IS NULL\"))
        count = r.scalar()
        print(f'OK:{count}' if count >= 2 else f'MISSING:{count}')
asyncio.run(check())
")
if echo "$AUTH_CHECK" | grep -q "^OK:"; then pass; else fail "$AUTH_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-003: Sandbox APPROVE path — chat → approval_requested → approve → real results
# ---------------------------------------------------------------------------
run_test "T-S45-003" "Sandbox approve: chat → HITL → approve → tool executes"
APPROVE_RESULT=$(api "
import asyncio, json, httpx

async def test():
    base = 'http://localhost:8000/api/v1'
    h = {'X-User-Sub': '$KALYAN', 'X-User-Team': 'platform'}

    # Create run
    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f'{base}/playground/runs', json={
            'agent_name': '$AGENT',
            'input_message': 'What is the current weather in Austin Texas right now?',
        }, headers=h)
        if r.status_code != 201:
            print(f'CREATE_FAIL:{r.status_code}:{r.text[:100]}')
            return
        run_id = r.json().get('run_id')

    # Stream — expect approval_requested
    approval_id = None
    async with httpx.AsyncClient(timeout=90.0) as c:
        async with c.stream('GET', f'{base}/playground/runs/{run_id}/stream', headers=h) as resp:
            async for line in resp.aiter_lines():
                if 'approval_requested' in line:
                    try:
                        ev = json.loads(line[6:].strip()) if line.startswith('data: ') else json.loads(line)
                        approval_id = ev.get('approval_id')
                    except:
                        pass
                    break
                if '\"done\"' in line or '\"error\"' in line:
                    break

    if not approval_id:
        print('NO_APPROVAL_REQUESTED')
        return

    # Check approval record
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f'{base}/approvals/{approval_id}', headers=h)
        if r.status_code == 200:
            appr = r.json()
            if appr.get('tool_name') != 'web_search' or appr.get('status') != 'pending':
                print(f'BAD_APPROVAL: tool={appr.get(\"tool_name\")} status={appr.get(\"status\")}')
                return

    # Approve
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f'{base}/playground/approvals/{approval_id}/decide',
            json={'decision': 'approved'}, headers=h)
        if r.status_code != 200:
            print(f'DECIDE_FAIL:{r.status_code}:{r.text[:100]}')
            return
        thread_id = r.json().get('thread_id')

    # Resume stream — expect tool execution + real answer
    resume_text = ''
    tool_executed = False
    async with httpx.AsyncClient(timeout=90.0) as c:
        async with c.stream('GET', f'{base}/playground/runs/{run_id}/resume-stream', headers=h) as resp:
            async for line in resp.aiter_lines():
                if line.startswith('data: '):
                    try:
                        ev = json.loads(line[6:].strip())
                        if ev.get('event') == 'tool_call_end':
                            tool_executed = True
                        if ev.get('event') == 'text_delta':
                            resume_text += ev.get('content', '')
                        if ev.get('event') in ('done', 'error'):
                            break
                    except:
                        pass

    if not tool_executed:
        print(f'TOOL_NOT_EXECUTED: response={resume_text[:100]}')
        return
    if len(resume_text) < 20:
        print(f'RESPONSE_TOO_SHORT: {resume_text[:100]}')
        return

    # Verify audit trail
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f'{base}/approvals/{approval_id}', headers=h)
        if r.status_code == 200:
            appr = r.json()
            if appr.get('status') != 'approved' or not appr.get('decision_at'):
                print(f'AUDIT_FAIL: status={appr.get(\"status\")} decision_at={appr.get(\"decision_at\")}')
                return

    print('OK')

asyncio.run(test())
")
if echo "$APPROVE_RESULT" | grep -q "^OK$"; then pass; else fail "$APPROVE_RESULT"; fi

# ---------------------------------------------------------------------------
# T-S45-004: Sandbox DENY path — chat → approval_requested → deny → agent responds
# ---------------------------------------------------------------------------
run_test "T-S45-004" "Sandbox deny: chat → HITL → deny → agent uses own knowledge"
DENY_RESULT=$(api "
import asyncio, json, httpx

async def test():
    base = 'http://localhost:8000/api/v1'
    h = {'X-User-Sub': '$KALYAN', 'X-User-Team': 'platform'}

    async with httpx.AsyncClient(timeout=60.0) as c:
        r = await c.post(f'{base}/playground/runs', json={
            'agent_name': '$AGENT',
            'input_message': 'What time is it in Tokyo Japan right now?',
        }, headers=h)
        if r.status_code != 201:
            print(f'CREATE_FAIL:{r.status_code}')
            return
        run_id = r.json().get('run_id')

    approval_id = None
    async with httpx.AsyncClient(timeout=90.0) as c:
        async with c.stream('GET', f'{base}/playground/runs/{run_id}/stream', headers=h) as resp:
            async for line in resp.aiter_lines():
                if 'approval_requested' in line:
                    try:
                        ev = json.loads(line[6:].strip()) if line.startswith('data: ') else json.loads(line)
                        approval_id = ev.get('approval_id')
                    except:
                        pass
                    break
                if '\"done\"' in line or '\"error\"' in line:
                    break

    if not approval_id:
        print('NO_APPROVAL_REQUESTED')
        return

    # Deny
    async with httpx.AsyncClient(timeout=30.0) as c:
        r = await c.post(f'{base}/playground/approvals/{approval_id}/decide',
            json={'decision': 'denied'}, headers=h)
        if r.status_code != 200:
            print(f'DECIDE_FAIL:{r.status_code}:{r.text[:100]}')
            return

    # Resume — expect agent response without tool
    deny_text = ''
    async with httpx.AsyncClient(timeout=90.0) as c:
        async with c.stream('GET', f'{base}/playground/runs/{run_id}/resume-stream', headers=h) as resp:
            async for line in resp.aiter_lines():
                if line.startswith('data: '):
                    try:
                        ev = json.loads(line[6:].strip())
                        if ev.get('event') == 'text_delta':
                            deny_text += ev.get('content', '')
                        if ev.get('event') in ('done', 'error'):
                            break
                    except:
                        pass

    if len(deny_text) < 10:
        print(f'NO_RESPONSE: {deny_text[:100]}')
        return

    # Verify audit trail — status should be 'rejected' (denied maps to rejected)
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f'{base}/approvals/{approval_id}', headers=h)
        if r.status_code == 200:
            appr = r.json()
            if appr.get('status') != 'rejected':
                print(f'WRONG_STATUS: {appr.get(\"status\")}')
                return

    print('OK')

asyncio.run(test())
")
if echo "$DENY_RESULT" | grep -q "^OK$"; then pass; else fail "$DENY_RESULT"; fi

# ---------------------------------------------------------------------------
# T-S45-005: Both team members see approvals
# ---------------------------------------------------------------------------
run_test "T-S45-005" "Both team members can list approvals"
VISIBILITY_CHECK=$(api "
import asyncio, httpx

async def test():
    base = 'http://localhost:8000/api/v1'
    async with httpx.AsyncClient(timeout=10.0) as c:
        r1 = await c.get(f'{base}/approvals/', headers={
            'X-User-Sub': '$KALYAN', 'X-User-Team': 'platform'})
        r2 = await c.get(f'{base}/approvals/', headers={
            'X-User-Sub': '$ADMIN', 'X-User-Team': 'platform'})

    c1 = r1.json().get('total', 0) if r1.status_code == 200 else 0
    c2 = r2.json().get('total', 0) if r2.status_code == 200 else 0
    if c1 > 0 and c2 > 0:
        print(f'OK:kalyan={c1},admin={c2}')
    else:
        print(f'FAIL:kalyan={c1},admin={c2}')

asyncio.run(test())
")
if echo "$VISIBILITY_CHECK" | grep -q "^OK:"; then pass; else fail "$VISIBILITY_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-006: Production resume-stream endpoint exists
# ---------------------------------------------------------------------------
run_test "T-S45-006" "Production resume-stream endpoint responds (404 for non-existent run)"
RESUME_CHECK=$(api "
import asyncio, httpx

async def test():
    base = 'http://localhost:8000/api/v1'
    async with httpx.AsyncClient(timeout=10.0) as c:
        r = await c.get(f'{base}/agents/$AGENT/chat/00000000-0000-0000-0000-000000000000/resume-stream',
            headers={'Authorization': 'Bearer test'})
    # 401 or 404 both prove the endpoint exists (not 405 Method Not Allowed)
    if r.status_code in (401, 403, 404):
        print(f'OK:{r.status_code}')
    else:
        print(f'UNEXPECTED:{r.status_code}')

asyncio.run(test())
")
if echo "$RESUME_CHECK" | grep -q "^OK:"; then pass; else fail "$RESUME_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-007: Deployment chat records deployment_id + approval-status endpoint
#            returns the pending approval scoped to the run owner.
# ---------------------------------------------------------------------------
run_test "T-S45-007" "Deployment chat → approval-status endpoint reflects pending approval"
STATUS_CHECK=$(api "
import asyncio, json, base64, httpx

def sub_of(tok):
    p = tok.split('.')[1]; p += '=' * (4 - len(p) % 4)
    return json.loads(base64.urlsafe_b64decode(p)).get('sub')

async def test():
    base = 'http://localhost:8000/api/v1'
    # The deployment-chat + approval-status endpoints require a real JWT
    # (require_user) — the X-User-Sub header shortcut only works on playground
    # endpoints. Fetch a token the way the browser does.
    tok_resp = httpx.post(
        'http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
        data={'grant_type': 'password', 'client_id': 'agentshield-studio',
              'username': 'platform-admin', 'password': 'PlatformAdmin2024'}, timeout=10)
    if tok_resp.status_code != 200:
        print(f'SKIP:no-token={tok_resp.status_code}'); return
    token = tok_resp.json()['access_token']
    owner = sub_of(token)
    auth = {'Authorization': f'Bearer {token}'}

    async with httpx.AsyncClient(timeout=30.0) as c:
        dr = await c.get(f'{base}/agents/$AGENT/deployments', headers=auth)
        _b = dr.json() if dr.status_code == 200 else []
        deps = _b.get('items', []) if isinstance(_b, dict) else _b
        running = [d for d in deps if d.get('status') == 'running']
        if not running:
            print('SKIP:no-running-deployment'); return
        dep_id = running[0]['id']

        r = await c.post(f'{base}/agents/$AGENT/deployments/{dep_id}/chat',
            json={'message': 'What is the weather in Austin TX right now?'}, headers=auth)
        if r.status_code != 200:
            print(f'FAIL:start={r.status_code}'); return
        run_id = r.json()['run_id']

    # Stream until approval_requested so the approval record exists.
    async with httpx.AsyncClient(timeout=90.0) as c:
        async with c.stream('GET', f'{base}/agents/$AGENT/deployments/{dep_id}/chat/{run_id}/stream', headers=auth) as resp:
            async for line in resp.aiter_lines():
                if 'approval_requested' in line:
                    break

    # Requester-scoped approval-status endpoint reflects the pending approval.
    async with httpx.AsyncClient(timeout=15.0) as c:
        s = await c.get(f'{base}/agents/$AGENT/chat/{run_id}/approval-status', headers=auth)
        if s.status_code != 200:
            print(f'FAIL:status={s.status_code}'); return
        body = s.json()
        if body.get('status') == 'pending' and body.get('tool') == 'web_search':
            print(f'OK:owner={owner[:8]},run={run_id}')
        else:
            print(f'FAIL:body={json.dumps(body)}')

try:
    asyncio.run(test())
except Exception as e:
    print(f'FAIL:exc={e!r}')
")
if echo "$STATUS_CHECK" | grep -q "^OK:"; then pass
elif echo "$STATUS_CHECK" | grep -q "^SKIP:"; then echo "SKIP"; SKIP=$((SKIP + 1))
else fail "$STATUS_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-008: HITL console list surfaces provenance (requested_by + deployment).
# ---------------------------------------------------------------------------
run_test "T-S45-008" "list_approvals enriches requested_by + deployment_name + environment"
PROV_CHECK=$(api "
import asyncio, json, httpx

async def test():
    base = 'http://localhost:8000/api/v1'
    h = {'X-User-Sub': '$KALYAN', 'X-User-Team': 'platform'}
    async with httpx.AsyncClient(timeout=20.0) as c:
        # Deployment-chat approvals now live in the 'sandbox' context (they left
        # the production queue), so provenance enrichment is checked there.
        r = await c.get(f'{base}/approvals/', params={'status': 'pending', 'context': 'sandbox'}, headers=h)
        if r.status_code != 200:
            print(f'FAIL:{r.status_code}'); return
        items = r.json().get('items', [])
        # At least one sandbox deployment-chat approval should carry provenance
        # (requested_by populated via thread_id -> run join).
        enriched = [i for i in items if i.get('requested_by')]
        if enriched:
            ex = enriched[0]
            print(f'OK:requested_by={bool(ex.get(\"requested_by\"))},dep={ex.get(\"deployment_name\")},env={ex.get(\"environment\")}')
        else:
            # No enriched row yet is acceptable only if there are zero deployment-chat approvals.
            print(f'FAIL:no-enriched-of-{len(items)}')

asyncio.run(test())
")
if echo "$PROV_CHECK" | grep -q "^OK:"; then pass; else fail "$PROV_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-009: Sandbox deployment chat approval is context='sandbox' (out of the
# production queue) with username+team provenance and a session-scoped list.
# ---------------------------------------------------------------------------
run_test "T-S45-009" "Sandbox deployment approval → context=sandbox + username/team + session list"
SANDBOX_CHECK=$(api "
import asyncio, json, httpx
def sub_of(t):
    import base64
    p=t.split('.')[1]; p+='='*(4-len(p)%4)
    return json.loads(base64.urlsafe_b64decode(p)).get('sub')
async def test():
    base='http://localhost:8000/api/v1'
    tr=httpx.post('http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token',
        data={'grant_type':'password','client_id':'agentshield-studio','username':'platform-admin','password':'PlatformAdmin2024'},timeout=10)
    if tr.status_code!=200: print(f'SKIP:no-token={tr.status_code}'); return
    tok=tr.json()['access_token']; auth={'Authorization':f'Bearer {tok}'}
    sess='suite45-009'
    async with httpx.AsyncClient(timeout=30) as c:
        dr=await c.get(f'{base}/agents/$AGENT/deployments',headers=auth)
        deps=dr.json(); deps=deps.get('items',[]) if isinstance(deps,dict) else deps
        running=[d for d in deps if d.get('status')=='running']
        if not running: print('SKIP:no-running'); return
        dep=running[0]['id']
        r=await c.post(f'{base}/agents/$AGENT/deployments/{dep}/chat',
            json={'message':'What is the weather in Austin TX?','session_id':sess},headers=auth)
        if r.status_code!=200: print(f'FAIL:start={r.status_code}'); return
        run_id=r.json()['run_id']
    aid=None
    async with httpx.AsyncClient(timeout=90) as c:
        async with c.stream('GET',f'{base}/agents/$AGENT/deployments/{dep}/chat/{run_id}/stream',headers=auth) as resp:
            async for line in resp.aiter_lines():
                if 'approval_requested' in line:
                    aid=json.loads(line[6:]).get('approval_id'); break
    async with httpx.AsyncClient(timeout=15) as c:
        # session-scoped list (feeds the sandbox panel)
        s=await c.get(f'{base}/agents/$AGENT/chat/session/{sess}/approvals',headers=auth)
        sess_ok = any(a['context']=='sandbox' and a['approval_id']==aid for a in s.json().get('approvals',[]))
        # sandbox context list carries username+team+deployment provenance
        sb=await c.get(f'{base}/approvals/',params={'context':'sandbox','status':'pending'},headers=auth)
        mine=[i for i in sb.json().get('items',[]) if i.get('id')==aid]
        # must NOT be in the production queue
        pr=await c.get(f'{base}/approvals/',params={'status':'pending'},headers=auth)
        in_prod=any(i.get('id')==aid for i in pr.json().get('items',[]))
    if mine and sess_ok and not in_prod:
        m=mine[0]
        uname_ok = m.get('requested_by') and '-' not in str(m.get('requested_by'))[:8]  # username, not a raw sub
        if m['context']=='sandbox' and m.get('requested_by_team') and m.get('deployment_name'):
            print(f\"OK:ctx={m['context']},user={m['requested_by']},team={m['requested_by_team']},dep={m['deployment_name']}\")
        else:
            print(f'FAIL:prov={json.dumps(m)[:200]}')
    else:
        print(f'FAIL:mine={bool(mine)},sess={sess_ok},in_prod={in_prod}')
try: asyncio.run(test())
except Exception as e: print(f'FAIL:exc={e!r}')
")
if echo "$SANDBOX_CHECK" | grep -q "^OK:"; then pass
elif echo "$SANDBOX_CHECK" | grep -q "^SKIP:"; then echo 'SKIP'; SKIP=$((SKIP + 1))
else fail "$SANDBOX_CHECK"; fi

# ---------------------------------------------------------------------------
# T-S45-010: Batch eval (eval-runner identity) auto-approves — HITL is skipped,
# the tool executes, the run does not hang. A REAL user still gets HITL.
# ---------------------------------------------------------------------------
run_test "T-S45-010" "Batch eval auto-approves HITL; real user still gated"
EVAL_CHECK=$(api "
import asyncio, json, httpx
async def stream_saw_approval(base, headers, msg):
    async with httpx.AsyncClient(timeout=30) as c:
        r=await c.post(f'{base}/playground/runs',json={'agent_name':'$AGENT','input_message':msg},headers=headers)
        run_id=r.json()['run_id']
    saw=False; ended=False
    async with httpx.AsyncClient(timeout=180) as c:
        async with c.stream('GET',f'{base}/playground/runs/{run_id}/stream',headers=headers) as resp:
            async for line in resp.aiter_lines():
                if 'approval_requested' in line: saw=True; break
                if '\"done\"' in line: ended=True; break
    # 'ended' = stream reached done without HITL (auto-approved run completed).
    return saw, ended
async def test():
    base='http://localhost:8000/api/v1'
    # eval-runner identity → auto-approve (no interrupt, run completes)
    ev_saw, ev_done = await stream_saw_approval(base, {'X-User-Sub':'eval-runner'}, 'weather in Austin TX?')
    # real user → HITL still fires
    u_saw, _ = await stream_saw_approval(base, {'X-User-Sub':'643b0e62-b437-40f8-8104-57c34203624b','X-User-Team':'platform'}, 'weather in Austin TX?')
    if (not ev_saw) and ev_done and u_saw:
        print('OK:eval-auto-approved,user-gated')
    else:
        print(f'FAIL:eval_saw={ev_saw},eval_done={ev_done},user_saw={u_saw}')
try: asyncio.run(test())
except Exception as e: print(f'FAIL:exc={e!r}')
")
if echo "$EVAL_CHECK" | grep -q "^OK:"; then pass; else fail "$EVAL_CHECK"; fi

# ===========================================================================
echo ""
echo "=== Suite 45 Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped ==="
echo ""
exit "$FAIL"
