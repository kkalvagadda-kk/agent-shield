#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP1c — MCP Phase 4 (WS-1 CredentialProvider seam): behaviour smoke.
#
# Proves the core WS-1 contract: the seam changed the PLUMBING, not the BYTES.
#   1. Create an AuthConfig WITH credentials through the REAL API — this routes the
#      value through get_provider().put() and stamps a `pg-fernet://…` credential_ref
#      (plus the transition dual-write to the retained credentials_encrypted column).
#   2. Bind an MCP server to it and materialize the per-server K8s Secret via the REAL
#      mcp_secrets.materialize_server_secret (which takes the PROVIDER branch because the
#      row carries a credential_ref). Read the composed `auth_headers` back OUT of the
#      live Secret `agentshield-mcp-server-{id}` (agentshield-mcp) and jq it.
#   3. BYTE-IDENTITY: the value that flowed through the provider (the materialized Secret)
#      is byte-for-byte equal to composing the headers DIRECTLY from
#      crypto.decrypt_json(credentials_encrypted) — same Fernet key, same door, same
#      _compose_auth_headers — so the seam is behaviour-neutral on pg-fernet. Triangulated
#      against an in-pod provider-get compose, and asserted non-empty (rules out the
#      trivial {}=={} match).
#   4. suite-84 stays GREEN — the rewired call sites are behaviour-neutral for Phase 1.
#
# jq/kubectl/in-pod-python assertions. Exit 0 on full pass, non-zero on the first
# failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP1: byte-identity behaviour smoke (provider == legacy compose) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
SUFFIX="$(date +%s | tail -c 7)"
TOKEN="cp1c-secret-${SUFFIX}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() { echo "FAIL: $1" >&2; exit 1; }

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  api=$API_POD  mcp-ns=$MCP_NS"

# ── 1+2. Create AuthConfig (API) → bind + materialize (provider path), in-pod ──
echo "--- create AuthConfig (provider put + pg-fernet ref) → bind server → materialize Secret ---"
INPOD="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" TOKEN="$TOKEN" python3 - <<'PY'
import os, asyncio, httpx, json, sys
SUFFIX = os.environ["SUFFIX"]
TOKEN = os.environ["TOKEN"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin", "X-User-Team": "platform"}

async def main():
    from db import AsyncSessionLocal
    from models import AuthConfig, MCPServer
    from crypto import decrypt_json
    from credential_provider import get_provider, CredentialRef
    from mcp_secrets import _compose_auth_headers, materialize_server_secret
    from sqlalchemy import select

    # 1. Create the AuthConfig through the REAL API → get_provider().put() + credential_ref.
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/auth-configs/", headers=HDR, json={
            "name": f"cp1c-ac-{SUFFIX}", "type": "bearer",
            "credentials": {"token": TOKEN}, "owner_team": "platform"})
        if r.status_code != 201:
            print("ACFAIL", r.status_code, r.text[:200]); sys.exit(1)
        acid = r.json()["id"]
    print("AUTH_CONFIG_ID", acid)

    # 2. Bind an MCP server to it and materialize the per-server Secret (provider branch).
    async with AsyncSessionLocal() as s:
        srv = MCPServer(name=f"cp1c-srv-{SUFFIX}",
                        server_url="http://unreachable.invalid:9999/mcp",
                        transport="streamable_http", owner_team="platform",
                        status="error", identity_mode="none", is_external=False,
                        scan_results=True, auth_config_id=acid)
        s.add(srv); await s.commit(); sid = srv.id
    print("SERVER_ID", sid)

    async with AsyncSessionLocal() as s:
        srv = (await s.execute(select(MCPServer).where(MCPServer.id == sid))).scalar_one()
        ac = (await s.execute(select(AuthConfig).where(AuthConfig.id == acid))).scalar_one()
        # The pointer that proves the value now lives BEHIND the provider.
        print("CREDENTIAL_REF", ac.credential_ref)
        # Provider path (exactly what materialize_server_secret resolves through).
        prov_creds = await get_provider().get(CredentialRef.parse(ac.credential_ref))
        print("PROVIDER_HEADERS", json.dumps(_compose_auth_headers(ac.type, prov_creds)))
        # Legacy path: compose DIRECTLY from the retained Fernet column (the pre-seam bytes).
        legacy_creds = decrypt_json(ac.credentials_encrypted)
        print("LEGACY_HEADERS", json.dumps(_compose_auth_headers(ac.type, legacy_creds)))
        # Write the REAL per-server Secret through the provider seam.
        await materialize_server_secret(s, srv)
    print("MATERIALIZED ok")

asyncio.run(main())
PY
)" || { echo "$INPOD"; fail "in-pod create/materialize block errored"; }
echo "$INPOD"

AUTH_CONFIG_ID="$(echo "$INPOD" | sed -n 's/^AUTH_CONFIG_ID //p' | tr -d '[:space:]')"
SERVER_ID="$(echo "$INPOD" | sed -n 's/^SERVER_ID //p' | tr -d '[:space:]')"
CREDENTIAL_REF="$(echo "$INPOD" | sed -n 's/^CREDENTIAL_REF //p' | head -1)"
PROVIDER_HEADERS="$(echo "$INPOD" | sed -n 's/^PROVIDER_HEADERS //p' | head -1)"
LEGACY_HEADERS="$(echo "$INPOD" | sed -n 's/^LEGACY_HEADERS //p' | head -1)"
[ -n "$SERVER_ID" ] || fail "could not capture SERVER_ID"
[ -n "$AUTH_CONFIG_ID" ] || fail "could not capture AUTH_CONFIG_ID"

# Clean up the test rows + the per-server Secret no matter how we exit.
cleanup() {
  [ -n "${SERVER_ID:-}" ] && kubectl delete secret "agentshield-mcp-server-${SERVER_ID}" \
    -n "$MCP_NS" --ignore-not-found >/dev/null 2>&1 || true
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="${SERVER_ID:-}" ACID="${AUTH_CONFIG_ID:-}" python3 - <<'PY' 2>/dev/null || true
import os, asyncio, httpx
async def main():
    from db import AsyncSessionLocal
    from models import MCPServer
    from sqlalchemy import delete, text
    sid = os.environ.get("SID") or ""
    acid = os.environ.get("ACID") or ""
    async with AsyncSessionLocal() as s:
        if sid:
            await s.execute(delete(MCPServer).where(MCPServer.id == sid))
        if acid:
            # Drop the pg-fernet blob the provider wrote (path == 'auth-configs/{id}').
            await s.execute(text("DELETE FROM credential_blobs WHERE path = :p"),
                            {"p": f"auth-configs/{acid}"})
        await s.commit()
    if acid:
        async with httpx.AsyncClient(timeout=30) as c:
            await c.delete(f"http://localhost:8000/api/v1/auth-configs/{acid}",
                           headers={"X-User-Sub": "platform-admin", "X-User-Team": "platform"})
asyncio.run(main())
PY
}
trap cleanup EXIT

# ── 3. Read the materialized Secret's auth_headers OUT of the cluster ──────────
echo "--- read agentshield-mcp-server-${SERVER_ID} auth_headers (provider-materialized) ---"
SECRET_HEADERS="$(kubectl get secret "agentshield-mcp-server-${SERVER_ID}" -n "$MCP_NS" \
  -o go-template='{{index .data "auth_headers" | base64decode}}' 2>/dev/null || true)"
[ -n "$SECRET_HEADERS" ] || fail "could not read auth_headers from the per-server Secret"
echo "$SECRET_HEADERS" | jq -e . >/dev/null 2>&1 || fail "secret auth_headers is not valid JSON: $SECRET_HEADERS"
echo "  secret   auth_headers: $SECRET_HEADERS"
echo "  provider auth_headers: $PROVIDER_HEADERS"
echo "  legacy   auth_headers: $LEGACY_HEADERS"
echo "  credential_ref:        $CREDENTIAL_REF"

# ── 4. Assertions ─────────────────────────────────────────────────────────────
# (a) The value came through the PROVIDER — the row carries a pg-fernet ref.
case "$CREDENTIAL_REF" in
  pg-fernet://*) echo "  OK: credential_ref is a pg-fernet ref (value resolved through the provider)";;
  *) fail "credential_ref is not a pg-fernet ref: '${CREDENTIAL_REF}'";;
esac

# (b) Non-empty guard — the header actually resolved (rules out a trivial {}=={} pass).
EXPECTED="{\"Authorization\": \"Bearer ${TOKEN}\"}"
[ "$SECRET_HEADERS" = "$EXPECTED" ] \
  || fail "materialized auth_headers != expected. got=$SECRET_HEADERS want=$EXPECTED"

# (c) CORE BYTE-IDENTITY: provider-materialized Secret == direct legacy-column compose.
if [ "$SECRET_HEADERS" = "$LEGACY_HEADERS" ]; then
  echo "  OK: provider-materialized auth_headers are BYTE-IDENTICAL to the legacy-column compose"
else
  echo "  provider(secret): $SECRET_HEADERS" >&2
  echo "  legacy(column):   $LEGACY_HEADERS" >&2
  diff <(printf '%s' "$SECRET_HEADERS") <(printf '%s' "$LEGACY_HEADERS") >&2 || true
  fail "BYTE-IDENTITY BROKEN — the credential-provider seam changed the composed auth_headers"
fi

# (d) Triangulate: the in-pod provider-get compose matches too.
[ "$SECRET_HEADERS" = "$PROVIDER_HEADERS" ] \
  || fail "provider-get compose ($PROVIDER_HEADERS) != materialized Secret ($SECRET_HEADERS)"
echo "  OK: provider-get compose == materialized Secret (triangulated)"

# ── 5. suite-84 stays green (the rewired call sites are behaviour-neutral) ─────
echo "--- suite-84 (Phase-1 MCP API surface) still green after the seam rewire ---"
NAMESPACE="$NAMESPACE" bash "${SCRIPT_DIR}/e2e/suite-84-mcp-tools.sh" \
  || fail "suite-84 regressed after the credential-provider seam"
echo "  OK: suite-84 green"

echo "PASS"
