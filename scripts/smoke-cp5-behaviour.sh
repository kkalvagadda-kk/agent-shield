#!/usr/bin/env bash
# =============================================================================
# DEFERRED — written but NOT executed this run; run after deploying.
# The Vitest+typecheck half runs LOCALLY (needs studio/node_modules); the curl
# half requires a live cluster.
# =============================================================================
# CP5c — MCP as a Tool Source (Phase 1): Studio UI behaviour smoke.
#
#   1. LOCAL — the MCP-touching frontend suites are green + typecheck clean:
#        cd studio && npm run test -- <the 4 suites> && npm run typecheck
#   2. CLUSTER — curl the register->detail path the UI drives:
#        POST /api/v1/mcp-servers/  (register)                 -> 201
#        GET  /api/v1/mcp-servers/{id}  (detail the UI reads)  -> 200, tools[]
#        DELETE /api/v1/mcp-servers/{id}  (cleanup)            -> 204
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint CP5: studio behaviour smoke (Vitest + register->detail) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. LOCAL — the 4 MCP-touching Vitest suites + typecheck ───────────────────
echo "--- Vitest: MCP Servers + Detail + ToolsPage + ToolsPicker ---"
( cd studio && npm run test -- \
    src/pages/McpServersPage.test.tsx \
    src/pages/McpServerDetailPage.test.tsx \
    src/pages/ToolsPage.test.tsx \
    src/components/agent/ToolsPicker.test.tsx ) || fail "MCP Vitest suites failed"

echo "--- typecheck ---"
( cd studio && npm run typecheck ) || fail "tsc --noEmit reported errors"

# ── 2. CLUSTER — register -> detail (the path the UI drives) ───────────────────
echo "--- register -> detail via the registry-api (in-pod curl) ---"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"

RESULT="$(kubectl exec -n "$NAMESPACE" "$API_POD" -- python3 -c "
import httpx, time
base = 'http://localhost:8000/api/v1'
name = 'cp5-smoke-' + str(int(time.time()))
h = {'X-User-Sub': 'cp5-smoke', 'X-User-Team': 'platform'}
# register (unreachable URL -> 201 with status='error' is fine; the API path is
# what this proves, not upstream reachability)
r = httpx.post(base + '/mcp-servers/', headers=h, timeout=30, json={
    'name': name,
    'server_url': 'http://cp5-fixture.agentshield-mcp.svc.cluster.local:9999/mcp',
    'transport': 'streamable_http',
    'is_external': True,
})
assert r.status_code == 201, f'register -> {r.status_code}: {r.text[:200]}'
sid = r.json()['id']
# detail — the page's read; must be 200 with a tools[] array present
d = httpx.get(base + '/mcp-servers/' + sid, headers=h, timeout=10)
assert d.status_code == 200, f'detail -> {d.status_code}'
assert isinstance(d.json().get('tools'), list), 'detail body missing tools[]'
# cleanup (unbound -> 204)
x = httpx.delete(base + '/mcp-servers/' + sid, headers=h, timeout=10)
assert x.status_code == 204, f'delete -> {x.status_code}'
print('OK register=201 detail=200 delete=204')
" 2>&1 || true)"
echo "  $RESULT"
echo "$RESULT" | grep -q '^OK ' || fail "register->detail->delete path did not pass"

echo "PASS"
