#!/usr/bin/env bash
# scripts/e2e/suite-86-credential-provider.sh
#
# E2E Suite 86: MCP Phase 4 WS-1 — the pluggable CredentialProvider seam (Decision 31).
#
# Runs in-pod (kubectl exec into registry-api) against the real credential_provider +
# real Postgres + the real AuthConfig write path, mirroring suite-84's template. The
# API-testable slice of WS-1: the provider round-trip, the migration (0073) surface, the
# pg-fernet BYTE-IDENTITY invariant (the value that flows through get_provider() composes
# the SAME auth_headers bytes as the retained-column decrypt), and the legacy DUAL-READ
# (a null-credential_ref row still resolves via credentials_encrypted). The AWS Secrets
# Manager backend (T-S86-008) needs a live ASM + IRSA, so it is SKIPPED unless the pod is
# actually configured with CREDENTIAL_PROVIDER_BACKEND=aws-sm.
#
#   T-S86-001 — provider put→get round-trip: get_provider().get() returns exactly what put() stored.
#   T-S86-002 — provider rotate: pg-fernet rotates IN PLACE (same ref) and get() returns the new value.
#   T-S86-003 — provider delete: a subsequent get() raises CredentialNotFound; a 2nd delete is a no-op.
#   T-S86-004 — schema: credential_blobs table exists; auth_configs.credential_ref column exists (migration 0073).
#   T-S86-005 — AuthConfig create via the REAL API stamps a pg-fernet:// credential_ref; provider.get resolves it.
#   T-S86-006 — BYTE-IDENTITY: provider-path composed auth_headers == legacy-column composed auth_headers (non-empty).
#   T-S86-007 — legacy DUAL-READ: a null-ref row (only credentials_encrypted) still materializes through the real seam.
#   T-S86-008 — aws-sm backend round-trip. [backend-gated → SKIP unless CREDENTIAL_PROVIDER_BACKEND=aws-sm]
#
# Usage:
#   bash scripts/e2e/suite-86-credential-provider.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && API_POD=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep registry-api | grep Running | awk '{print $1}' | head -1)
[ -z "$API_POD" ] && { echo "FATAL: no running registry-api pod"; exit 1; }

# R1/FR-11: POST /api/v1/auth-configs/ (credential-bearing, routers/auth_configs.py) now
# requires a real JWT — it is the ONE HTTP call this suite makes into R1's ten routers;
# everything else runs in-pod against the ORM and the provider seam. The driver is a
# QUOTED heredoc, so the token travels as an env var beside SUFFIX. Call e2e_set_token
# BARE — a command substitution swallows its abort (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

echo "=== Suite 86: CredentialProvider seam (WS-1 / Decision 31) ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" E2E_TOKEN="$E2E_TOKEN" python3 - <<'PY'
import os, asyncio, httpx, json
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
TEAM = "platform"
# X-User-* stay AUDIT STAMPS; the Bearer is the R1 authentication for
# POST /auth-configs/ (credential-bearing, protected router-wide except secret-ref).
ADMIN = {"X-User-Sub": "platform-admin", "X-User-Team": TEAM,
         "Authorization": "Bearer " + os.environ["E2E_TOKEN"]}
TOKEN = f"s86-secret-{SUFFIX}"
fails = []

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

def skip(tid, msg):
    print(f"RESULT {tid} SKIP {msg}")

async def main():
    from db import AsyncSessionLocal
    from config import settings
    from crypto import decrypt_json, encrypt_json
    from credential_provider import (
        CredentialNotFound, CredentialRef, get_provider,
    )
    from mcp_secrets import _compose_auth_headers, materialize_server_secret, delete_server_secret
    from models import AuthConfig, MCPServer
    from sqlalchemy import select, delete, text as sqltext

    provider = get_provider()

    # ── T-S86-001/002/003 — provider put/get/rotate/delete round-trip ───────────
    # A throwaway pg-fernet ref (FernetPgProvider strips the credential-blobs/ prefix →
    # PK 's86-blob-{SUFFIX}'). Never collides with an AuthConfig's canonical path.
    ref = CredentialRef.parse(f"pg-fernet://credential-blobs/s86-blob-{SUFFIX}")
    await provider.put(ref, {"token": TOKEN})
    got = await provider.get(ref)
    check(got == {"token": TOKEN}, "T-S86-001",
          f"put→get round-trip got={got} (want {{'token': '{TOKEN}'}})")

    rotated_ref = await provider.rotate(ref, {"token": f"{TOKEN}-rot"})
    got2 = await provider.get(rotated_ref)
    check(str(rotated_ref) == str(ref) and got2 == {"token": f"{TOKEN}-rot"}, "T-S86-002",
          f"rotate in-place ref_same={str(rotated_ref)==str(ref)} got={got2}")

    await provider.delete(ref)
    raised = False
    try:
        await provider.get(ref)
    except CredentialNotFound:
        raised = True
    # A second delete on an absent ref must be an idempotent no-op (not raise).
    idem = True
    try:
        await provider.delete(ref)
    except Exception as e:  # noqa: BLE001
        idem = False
        print("delete-idem-err", str(e)[:120])
    check(raised and idem, "T-S86-003",
          f"delete→get raised CredentialNotFound={raised} second-delete-noop={idem}")

    # ── T-S86-004 — migration 0073 surface (table + column) ─────────────────────
    async with AsyncSessionLocal() as s:
        blobs = (await s.execute(sqltext("SELECT to_regclass('credential_blobs')"))).scalar()
        col = (await s.execute(sqltext(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='auth_configs' AND column_name='credential_ref'"))).first()
    check(blobs is not None and col is not None, "T-S86-004",
          f"credential_blobs={blobs} auth_configs.credential_ref={col}")

    # ── T-S86-005/006 — AuthConfig create (REAL API) → provider ref + byte-identity ─
    acid = None
    async with httpx.AsyncClient(timeout=30) as c:
        r = await c.post(f"{BASE}/auth-configs/", headers=ADMIN, json={
            "name": f"s86-ac-{SUFFIX}", "type": "bearer",
            "credentials": {"token": TOKEN}, "owner_team": TEAM})
        acid = r.json().get("id") if r.status_code == 201 else None
    if acid is None:
        check(False, "T-S86-005", "AuthConfig create did not return 201/id")
        skip("T-S86-006", "no AuthConfig (create failed)")
    else:
        async with AsyncSessionLocal() as s:
            ac = (await s.execute(select(AuthConfig).where(AuthConfig.id == acid))).scalar_one()
            ref_str = ac.credential_ref
            # Provider path — exactly what materialize_server_secret resolves through.
            prov_creds = await get_provider().get(CredentialRef.parse(ref_str))
            prov_headers = _compose_auth_headers(ac.type, prov_creds)
            # Legacy path — compose DIRECTLY from the retained Fernet column (pre-seam bytes).
            legacy_headers = _compose_auth_headers(ac.type, decrypt_json(ac.credentials_encrypted))
        check(bool(ref_str) and str(ref_str).startswith("pg-fernet://")
              and prov_creds == {"token": TOKEN}, "T-S86-005",
              f"credential_ref={ref_str} provider_get={prov_creds}")
        # BYTE-IDENTITY: the JSON-serialized composed headers are byte-for-byte equal AND
        # non-empty (rules out a trivial {}=={} pass) — same Fernet key, same _compose door.
        prov_bytes = json.dumps(prov_headers, sort_keys=True)
        legacy_bytes = json.dumps(legacy_headers, sort_keys=True)
        expected = json.dumps({"Authorization": f"Bearer {TOKEN}"}, sort_keys=True)
        check(prov_bytes == legacy_bytes and prov_bytes == expected, "T-S86-006",
              f"provider={prov_bytes} legacy={legacy_bytes} expected={expected}")

    # ── T-S86-007 — legacy DUAL-READ (null-ref row resolves through the real seam) ──
    # Insert a row with ONLY credentials_encrypted set (credential_ref=None — a pre-provider
    # row), bind a throwaway MCP server, and materialize its per-server Secret through the
    # REAL mcp_secrets.materialize_server_secret. That exercises the explicit legacy branch
    # (null ref → decrypt the column). Capture the outcome, ALWAYS clean up in finally.
    LEGACY = f"s86-legacy-{SUFFIX}"
    lac_id = lsrv_id = None
    legacy_ref_null = None
    legacy_headers = None
    materialized_ok = False
    try:
        async with AsyncSessionLocal() as s:
            lac = AuthConfig(name=f"s86-legacy-ac-{SUFFIX}", type="bearer",
                             credentials_encrypted=encrypt_json({"token": LEGACY}),
                             credential_ref=None, owner_team=TEAM)
            s.add(lac); await s.flush(); lac_id = lac.id
            lsrv = MCPServer(name=f"s86-legacy-srv-{SUFFIX}",
                             server_url="http://unreachable.invalid:9999/mcp",
                             transport="streamable_http", owner_team=TEAM,
                             status="error", identity_mode="none", is_external=False,
                             scan_results=True, auth_config_id=lac_id)
            s.add(lsrv); await s.commit(); lsrv_id = lsrv.id
        async with AsyncSessionLocal() as s:
            lac = (await s.execute(select(AuthConfig).where(AuthConfig.id == lac_id))).scalar_one()
            lsrv = (await s.execute(select(MCPServer).where(MCPServer.id == lsrv_id))).scalar_one()
            legacy_ref_null = lac.credential_ref is None
            legacy_headers = _compose_auth_headers(lac.type, decrypt_json(lac.credentials_encrypted))
            await materialize_server_secret(s, lsrv)  # exercises the legacy branch; must not raise
            materialized_ok = True
    finally:
        if lsrv_id is not None:
            try:
                await delete_server_secret(lsrv_id)
            except Exception:  # noqa: BLE001
                pass
        async with AsyncSessionLocal() as s:
            if lsrv_id is not None:
                await s.execute(delete(MCPServer).where(MCPServer.id == lsrv_id))
            if lac_id is not None:
                await s.execute(delete(AuthConfig).where(AuthConfig.id == lac_id))
            await s.commit()
    check(legacy_ref_null is True and legacy_headers == {"Authorization": f"Bearer {LEGACY}"}
          and materialized_ok is True, "T-S86-007",
          f"null_ref={legacy_ref_null} legacy_headers={legacy_headers} materialized={materialized_ok}")

    # ── T-S86-008 — aws-sm backend (backend-gated) ──────────────────────────────
    backend = getattr(settings, "credential_provider_backend", "pg-fernet")
    if backend == "aws-sm":
        try:
            asm_ref = CredentialRef.parse(f"aws-sm://s86-asm-{SUFFIX}")
            await provider.put(asm_ref, {"token": TOKEN})
            asm_got = await provider.get(asm_ref)
            await provider.delete(asm_ref)
            check(asm_got == {"token": TOKEN}, "T-S86-008",
                  f"aws-sm put→get→delete got={asm_got}")
        except Exception as e:  # noqa: BLE001
            check(False, "T-S86-008", f"aws-sm round-trip errored: {str(e)[:160]}")
    else:
        skip("T-S86-008", f"CREDENTIAL_PROVIDER_BACKEND={backend!r} (not aws-sm)")

    # ── cleanup (best-effort, uniquely suffixed) ────────────────────────────────
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(sqltext("DELETE FROM credential_blobs WHERE path LIKE :p"),
                            {"p": f"s86-blob-{SUFFIX}%"})
            if acid is not None:
                await s.execute(sqltext("DELETE FROM credential_blobs WHERE path = :p"),
                                {"p": f"auth-configs/{acid}"})
                await s.execute(sqltext("DELETE FROM auth_configs WHERE id = :i"), {"i": acid})
            await s.execute(sqltext("DELETE FROM auth_configs WHERE name LIKE :p"),
                            {"p": f"s86-%{SUFFIX}"})
            await s.commit()
    except Exception as e:  # noqa: BLE001
        print("cleanup-err", str(e)[:120])

    print("FAILS", ",".join(fails) if fails else "NONE")

asyncio.run(main())
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"

if echo "$RESULT" | grep -q "FAILS NONE"; then
  echo "=== Suite 86 PASSED ==="
  exit 0
else
  echo "=== Suite 86 FAILED ==="
  exit 1
fi
