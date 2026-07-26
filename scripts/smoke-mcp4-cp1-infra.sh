#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1b — MCP Phase 4 (WS-1 CredentialProvider seam): infrastructure smoke.
#
# Asserts the WS-1 migration landed and the dual-read seam is intact at the DB level:
#   - registry-api pod Ready
#   - alembic current == 0073
#   - credential_blobs table EXISTS (to_regclass is not null)
#   - auth_configs.credential_ref column EXISTS
#   - the backfill RAN: every auth_configs row that has a credentials_encrypted blob now
#     also has a credential_ref (count WHERE credentials_encrypted IS NOT NULL AND
#     credential_ref IS NULL == 0)
#   - a LEGACY (null-ref) row still resolves via the credentials_encrypted column
#     fallback, end-to-end through the REAL mcp_secrets.materialize_server_secret
#     (the test row + its per-server Secret are cleaned up afterwards)
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP1: infra smoke (migration 0073 + credential provider seam) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"

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

# ── 2. alembic revision >= 0073 (WS-1 seam applied) ───────────────────────────
# The WS-1 seam is migration 0073. Assert it is APPLIED, not that it is the current
# tip: history is linear (each later migration's down_revision chains back through
# 0073), so any revision >= 0073 proves 0073 ran. A strict "== 0073" check breaks the
# moment a later migration (0074+) lands — which is exactly what happened. Parse the
# 4-digit revision and compare in base-10 (10# guards against the octal trap when a
# future revision contains an 8/9, e.g. 0088).
echo "--- alembic revision >= 0073 ---"
CUR="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- alembic current 2>/dev/null || true)"
echo "  alembic current: ${CUR:-<empty>}"
CUR_REV="$(echo "$CUR" | grep -oE '^[0-9]{4}' | head -1)"
[ -n "$CUR_REV" ] || fail "could not parse alembic revision (got: ${CUR:-<empty>})"
[ "$((10#$CUR_REV))" -ge "$((10#0073))" ] || fail "alembic revision $CUR_REV is before 0073 (WS-1 seam not applied)"
echo "  OK: alembic at $CUR_REV (>= 0073)"

# ── 3. Table + column + backfill + legacy-resolve (in-pod ORM, suite-84 style) ─
echo "--- credential_blobs table + credential_ref column + backfill + legacy resolve ---"
RESULT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, sys

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        sys.exit(1)

async def main():
    from db import AsyncSessionLocal
    from models import AuthConfig, MCPServer
    from crypto import encrypt_json, decrypt_json
    from mcp_secrets import _compose_auth_headers, materialize_server_secret, delete_server_secret
    from sqlalchemy import select, delete, text
    SUFFIX = os.environ["SUFFIX"]
    SECRET = f"cp1b-legacy-{SUFFIX}"

    # 1. credential_blobs table exists.
    async with AsyncSessionLocal() as s:
        reg = (await s.execute(text("SELECT to_regclass('credential_blobs')"))).scalar()
    check(reg is not None, "CP1B-blobs-table", f"to_regclass('credential_blobs')={reg}")

    # 2. auth_configs.credential_ref column exists.
    async with AsyncSessionLocal() as s:
        col = (await s.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='auth_configs' AND column_name='credential_ref'"))).first()
    check(col is not None, "CP1B-credref-col", f"credential_ref column={col}")

    # 3. Backfill complete: no blob-bearing row is left with a NULL ref.
    async with AsyncSessionLocal() as s:
        orphan = (await s.execute(text(
            "SELECT count(*) FROM auth_configs "
            "WHERE credentials_encrypted IS NOT NULL AND credential_ref IS NULL"))).scalar()
    check(orphan == 0, "CP1B-backfill", f"blob-bearing null-ref rows={orphan} (want 0)")

    # 4. LEGACY resolve: a freshly-inserted null-ref row (only credentials_encrypted set)
    #    still resolves via the column fallback, end-to-end through the REAL
    #    materialize_server_secret. Capture the outcome, ALWAYS clean up in `finally`,
    #    then assert (so a failed assertion never leaves a stray row/Secret behind).
    acid = sid = None
    legacy_ref_is_null = None
    legacy_headers = None
    materialized_ok = False
    try:
        async with AsyncSessionLocal() as s:
            ac = AuthConfig(name=f"cp1b-legacy-ac-{SUFFIX}", type="bearer",
                            credentials_encrypted=encrypt_json({"token": SECRET}),
                            credential_ref=None, owner_team="platform")
            s.add(ac); await s.flush(); acid = ac.id
            srv = MCPServer(name=f"cp1b-legacy-srv-{SUFFIX}",
                            server_url="http://unreachable.invalid:9999/mcp",
                            transport="streamable_http", owner_team="platform",
                            status="error", identity_mode="none", is_external=False,
                            scan_results=True, auth_config_id=acid)
            s.add(srv); await s.commit(); sid = srv.id
        async with AsyncSessionLocal() as s:
            ac = (await s.execute(select(AuthConfig).where(AuthConfig.id == acid))).scalar_one()
            srv = (await s.execute(select(MCPServer).where(MCPServer.id == sid))).scalar_one()
            legacy_ref_is_null = ac.credential_ref is None
            # The exact fallback branch mcp_secrets uses for a null-ref row.
            legacy_headers = _compose_auth_headers(ac.type, decrypt_json(ac.credentials_encrypted))
            await materialize_server_secret(s, srv)  # exercises the legacy branch; must not raise
            materialized_ok = True
    finally:
        if sid is not None:
            try:
                await delete_server_secret(sid)
            except Exception:  # noqa: BLE001
                pass
        async with AsyncSessionLocal() as s:
            if sid is not None:
                await s.execute(delete(MCPServer).where(MCPServer.id == sid))
            if acid is not None:
                await s.execute(delete(AuthConfig).where(AuthConfig.id == acid))
            await s.commit()
    check(legacy_ref_is_null is True, "CP1B-legacy-nullref",
          f"credential_ref is None={legacy_ref_is_null}")
    check(legacy_headers == {"Authorization": f"Bearer {SECRET}"}, "CP1B-legacy-resolve",
          f"headers={legacy_headers}")
    check(materialized_ok is True, "CP1B-legacy-materialize",
          f"materialize_server_secret completed={materialized_ok}")

    print("ALLPASS")

asyncio.run(main())
PY
)" || { echo "$RESULT"; fail "infra assertions failed"; }
echo "$RESULT"
echo "$RESULT" | grep -q "ALLPASS" || fail "infra assertion block did not complete"

echo "PASS"
