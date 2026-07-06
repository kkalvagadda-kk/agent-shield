#!/usr/bin/env bash
# scripts/e2e/suite-16-create-agent.sh
#
# E2E Suite 16: Create Agent Flow (auto-team, tool binding)
# Tests T-S16-001 through T-S16-006.
#
# What this proves:
#   T-S16-001 — GET /api/v1/me returns user's team from user_team_assignments
#   T-S16-002 — GET /api/v1/me without auth → 401
#   T-S16-003 — POST /agents/ with tools list → agent created + tools bound
#   T-S16-004 — GET /agents/{name}/tools → bound tools returned
#   T-S16-005 — POST /agents/ with unknown tool name → agent created, unknown tool skipped
#   T-S16-006 — POST /agents/ with agent_type=declarative → agent created correctly
#
# Usage:
#   bash scripts/e2e/suite-16-create-agent.sh
#   NAMESPACE=my-ns bash scripts/e2e/suite-16-create-agent.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

cleanup() {
  echo ""
  echo "==> Cleanup..."
  kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import urllib.request
for name in ['${AGENT_NAME}', '${AGENT_DECL}']:
    try:
        urllib.request.urlopen(urllib.request.Request('http://localhost:8000/api/v1/agents/' + name, method='DELETE'), timeout=5)
    except Exception: pass
" 2>/dev/null || true
}
trap cleanup EXIT

PASS=0
FAIL=0
MANUAL=0

AGENT_NAME="s16-create-test-agent"
AGENT_DECL="s16-declarative-agent"
TEST_TOOL="lookup_order"

run_test() {
  local desc="$1"
  shift
  if kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "$@" 2>/dev/null; then
    echo "  PASS: $desc"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $desc"
    FAIL=$((FAIL + 1))
  fi
}

check_manual() {
  local test_id="$1"
  local desc="$2"
  shift 2
  echo ""
  echo "  MANUAL [${test_id}]: ${desc}"
  MANUAL=$((MANUAL + 1))
}

echo "=== Suite 16: Create Agent Flow (auto-team, tool binding) ==="
echo ""

# ---------------------------------------------------------------------------
# Setup: clean up any leftover test agents from a prior run
# ---------------------------------------------------------------------------
echo "--- Setup: removing any leftover test agents ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def cleanup():
    async with AsyncSessionLocal() as db:
        for name in ['${AGENT_NAME}', '${AGENT_DECL}']:
            # Hard-delete agent_tools first (FK), then the agent row
            await db.execute(text(
                'DELETE FROM agent_tools WHERE agent_id IN '
                '(SELECT id FROM agents WHERE name = :name)'
            ), {'name': name})
            result = await db.execute(text('DELETE FROM agents WHERE name = :name'), {'name': name})
            print(f'  hard-deleted {name} ({result.rowcount} rows)')
        await db.commit()

asyncio.run(cleanup())
" 2>/dev/null || true
echo ""

# ---------------------------------------------------------------------------
# T-S16-001: GET /me returns team
# ---------------------------------------------------------------------------
echo "--- T-S16-001: GET /me returns user's team ---"
run_test "T-S16-001 — /me returns team for assigned user" "
import httpx, asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
from jose import jwt as jose_jwt

# Get a real user token from Keycloak
KC_URL = 'http://agentshield-keycloak'
r = httpx.post(f'{KC_URL}/realms/agentshield/protocol/openid-connect/token', data={
    'grant_type': 'password',
    'client_id': 'agentshield-studio',
    'username': 'platform-admin',
    'password': 'PlatformAdmin2024',
}, timeout=10)
assert r.status_code == 200, f'Token request failed: {r.status_code}'
token = r.json()['access_token']
claims = jose_jwt.get_unverified_claims(token)
user_sub = claims['sub']

# Ensure user_team_assignments has a row for this user
async def setup():
    async with AsyncSessionLocal() as db:
        await db.execute(text(
            \"INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_at) \"
            \"VALUES (:sub, 'platform', 'operator', now()) \"
            \"ON CONFLICT (user_sub) DO UPDATE SET team_name='platform'\"
        ), {'sub': user_sub})
        await db.commit()
asyncio.run(setup())

# Call /me with real Bearer token
r2 = httpx.get('http://localhost:8000/api/v1/me', headers={'Authorization': f'Bearer {token}'})
assert r2.status_code == 200, f'Expected 200, got {r2.status_code}: {r2.text}'
data = r2.json()
assert data['team'] == 'platform', f'Expected platform, got {data[\"team\"]}'
assert data['role'] == 'operator', f'Expected operator, got {data[\"role\"]}'
assert data['sub'] == user_sub
"

# ---------------------------------------------------------------------------
# T-S16-002: GET /me without auth → 401
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S16-002: GET /me without auth returns 401 ---"
run_test "T-S16-002 — /me rejects unauthenticated requests" "
import httpx
r = httpx.get('http://localhost:8000/api/v1/me')
assert r.status_code == 401, f'Expected 401, got {r.status_code}'
"

# ---------------------------------------------------------------------------
# T-S16-003: POST /agents/ with tools list → tools bound
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S16-003: Create agent with tools binding ---"
run_test "T-S16-003 — POST /agents with tools creates agent and binds tools" "
import httpx, sys
# Ensure test tool exists
tool_body = {
    'name': '${TEST_TOOL}',
    'type': 'http',
    'http_url': 'http://mock-service:8080/orders/{{order_id}}',
    'http_method': 'GET',
    'risk_level': 'low',
    'description': 'Look up an order by ID',
}
r = httpx.post('http://localhost:8000/api/v1/tools/', json=tool_body)
assert r.status_code in (201, 409), f'Tool create failed: {r.status_code} {r.text}'

# Create agent with tools
agent_body = {
    'name': '${AGENT_NAME}',
    'team': 'platform-team',
    'description': 'Test agent for suite 16',
    'agent_type': 'sdk',
    'tools': ['${TEST_TOOL}'],
}
r = httpx.post('http://localhost:8000/api/v1/agents/', json=agent_body,
               headers={'X-User-Sub': 'test-user-s16'})
assert r.status_code == 201, f'Agent create failed: {r.status_code} {r.text}'
data = r.json()
assert data['name'] == '${AGENT_NAME}'
assert data['agent_type'] == 'sdk'
"

# ---------------------------------------------------------------------------
# T-S16-004: GET /agents/{name}/tools → bound tools returned
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S16-004: Verify tools are bound to agent ---"
run_test "T-S16-004 — GET /agents/{name}/tools returns bound tools" "
import httpx
r = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_NAME}/tools')
assert r.status_code == 200, f'Expected 200, got {r.status_code}'
data = r.json()
items = data.get('items', data) if isinstance(data, dict) else data
tool_names = [t['name'] for t in items]
assert '${TEST_TOOL}' in tool_names, f'Expected ${TEST_TOOL} in {tool_names}'
"

# ---------------------------------------------------------------------------
# T-S16-005: POST /agents/ with unknown tool → agent created, tool skipped
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S16-005: Unknown tool name is skipped gracefully ---"
run_test "T-S16-005 — Unknown tool in list does not block agent creation" "
import httpx
agent_body = {
    'name': '${AGENT_DECL}',
    'team': 'platform-team',
    'description': 'Declarative test',
    'agent_type': 'declarative',
    'tools': ['nonexistent_tool_xyz'],
}
r = httpx.post('http://localhost:8000/api/v1/agents/', json=agent_body,
               headers={'X-User-Sub': 'test-user-s16'})
assert r.status_code == 201, f'Expected 201, got {r.status_code} {r.text}'
"

# ---------------------------------------------------------------------------
# T-S16-006: Declarative agent type
# ---------------------------------------------------------------------------
echo ""
echo "--- T-S16-006: Declarative agent created correctly ---"
run_test "T-S16-006 — agent_type=declarative stored correctly" "
import httpx
r = httpx.get('http://localhost:8000/api/v1/agents/${AGENT_DECL}')
assert r.status_code == 200, f'Expected 200, got {r.status_code}'
data = r.json()
assert data['agent_type'] == 'declarative', f'Expected declarative, got {data[\"agent_type\"]}'
"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
echo ""
echo "--- Cleanup ---"
kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal

async def cleanup():
    async with AsyncSessionLocal() as db:
        for name in ['${AGENT_NAME}', '${AGENT_DECL}']:
            await db.execute(text(
                'DELETE FROM agent_tools WHERE agent_id IN '
                '(SELECT id FROM agents WHERE name = :name)'
            ), {'name': name})
            await db.execute(text('DELETE FROM agents WHERE name = :name'), {'name': name})
        await db.execute(text(\"DELETE FROM user_team_assignments WHERE user_sub = 'test-user-s16'\"))
        await db.commit()

asyncio.run(cleanup())
print('  cleanup done')
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "=== Suite 16 Results ==="
echo "  PASS: $PASS"
echo "  FAIL: $FAIL"
echo "  MANUAL: $MANUAL"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "SUITE 16 FAILED ($FAIL failures)"
  exit 1
else
  echo "SUITE 16 PASSED"
fi
