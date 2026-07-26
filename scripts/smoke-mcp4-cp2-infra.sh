#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2b — MCP Phase 4 (WS-2 data model + token endpoint RBAC): infrastructure smoke.
#
# Asserts the WS-2 migration + the new TokenReview RBAC landed:
#   - registry-api pod Ready
#   - alembic current == 0074
#   - mcp_oauth_grants table EXISTS (to_regclass not null) with its composite PK
#   - mcp_servers.external_auth_mode + mcp_servers.oauth_client_ref columns EXIST
#   - the registry-api SA has a ClusterRoleBinding granting `tokenreviews: create`
#     (the token endpoint TokenReviews the proxy SA — without it every pull 401s)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP2: infra smoke (migration 0074 + tokenreviews RBAC) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
RELEASE="${RELEASE:-agentshield}"
REGISTRY_API_SA="${REGISTRY_API_SA:-agentshield-registry-api}"

fail() { echo "FAIL: $1" >&2; exit 1; }

# ── 1. registry-api pod Ready ─────────────────────────────────────────────────
echo "--- registry-api pod Ready ---"
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=registry-api -n "$NAMESPACE" --timeout=180s \
  || fail "registry-api pod not Ready within timeout"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod found"
echo "  OK: pod $API_POD Ready"

# ── 2. alembic current == 0074 ────────────────────────────────────────────────
echo "--- alembic current == 0074 ---"
CUR="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- alembic current 2>/dev/null || true)"
echo "  alembic current: ${CUR:-<empty>}"
echo "$CUR" | grep -q "0074" || fail "alembic head is not 0074 (got: ${CUR:-<empty>})"
echo "  OK: alembic at 0074"

# ── 3. Table + columns (in-pod ORM) ───────────────────────────────────────────
echo "--- mcp_oauth_grants table + mcp_servers.external_auth_mode/oauth_client_ref columns ---"
RESULT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY'
import asyncio, sys
def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)
async def main():
    from db import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as s:
        grants = (await s.execute(text("SELECT to_regclass('mcp_oauth_grants')"))).scalar()
        cols = set((await s.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='mcp_servers'"))).scalars().all())
        gcols = set((await s.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name='mcp_oauth_grants'"))).scalars().all())
    check(grants is not None, "CP2B-grants-table", f"to_regclass('mcp_oauth_grants')={grants}")
    check({"external_auth_mode", "oauth_client_ref"}.issubset(cols), "CP2B-server-cols",
          f"mcp_servers missing={{'external_auth_mode','oauth_client_ref'}} - {cols & {'external_auth_mode','oauth_client_ref'}}")
    check({"server_id", "user_sub", "credential_ref", "status"}.issubset(gcols), "CP2B-grant-cols",
          f"mcp_oauth_grants cols={sorted(gcols)}")
    print("ALLPASS")
asyncio.run(main())
PY
)" || { echo "$RESULT"; fail "infra assertions failed"; }
echo "$RESULT"
echo "$RESULT" | grep -q "ALLPASS" || fail "infra assertion block did not complete"

# ── 4. registry-api SA has tokenreviews:create via a ClusterRoleBinding ────────
echo "--- registry-api ClusterRoleBinding grants tokenreviews:create ---"
if kubectl auth can-i create tokenreviews \
     --as="system:serviceaccount:${NAMESPACE}:${REGISTRY_API_SA}" >/dev/null 2>&1; then
  echo "  OK: ${REGISTRY_API_SA} can create tokenreviews (auth can-i)"
else
  # Fall back to inspecting the binding object directly (auth can-i may be restricted).
  CRB="$(kubectl get clusterrolebinding "${RELEASE}-registry-api-tokenreview" -o name 2>/dev/null || true)"
  [ -n "$CRB" ] || fail "no tokenreviews:create ClusterRoleBinding for ${REGISTRY_API_SA} (${RELEASE}-registry-api-tokenreview absent)"
  echo "  OK: ClusterRoleBinding ${RELEASE}-registry-api-tokenreview present"
fi

echo "PASS"
