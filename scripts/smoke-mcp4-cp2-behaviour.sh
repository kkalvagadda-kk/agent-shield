#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2c — MCP Phase 4 (WS-2 authorize flow + refresh/rotation): behaviour smoke.
#
# Proves the registry-api OAuth dance + the internal token endpoint end-to-end against
# the in-pod stub OAuth AS (no proxy needed):
#   1. Start scripts/e2e/fixtures/oauth_mcp_server.py on 127.0.0.1:9100 in the registry-api
#      pod; register an External+OAuth server (server_url=http://127.0.0.1:9100/mcp).
#   2. Drive authorize → follow the authorization_url to the stub /authorize → capture the
#      code+state → GET /mcp-servers/oauth/callback → assert grant status='authorized' with
#      a pg-fernet …/mcp-oauth-refresh/… credential_ref (the refresh token is behind the
#      provider, NEVER in a column).
#   3. POST /internal/mcp/oauth/access-token with the PROXY SA token (audience
#      agentshield-registry-api, minted via `kubectl create token`) → 200 authorized + an
#      access_token; a NON-proxy SA → 403; no bearer → 401.
#   4. Force refresh-token ROTATION: a second pull makes the stub rotate the refresh token;
#      assert the stored provider blob's value changed (a stale RT after rotation is a
#      lockout — RFC 9700).
#
# Cleans up the server + grant + provider blobs + fixture no matter how it exits.
# jq/kubectl/in-pod-python assertions. Exit 0 on full pass, non-zero on first failure.
# Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP2: authorize flow + refresh/rotation behaviour smoke ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AUD="${MCP_PROXY_SA_AUDIENCE:-agentshield-registry-api}"
PROXY_SA="${PROXY_SA:-agentshield-mcp-proxy}"
API_SA="${API_SA:-agentshield-registry-api}"
SUFFIX="$(date +%s | tail -c 7)"
SERVER_NAME="cp2c-oauth-${SUFFIX}"
USER_SUB="cp2c-user-${SUFFIX}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="${SCRIPT_DIR}/e2e/fixtures/oauth_mcp_server.py"

fail() { echo "FAIL: $1" >&2; exit 1; }

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
[ -f "$FIXTURE" ] || fail "fixture not found at $FIXTURE"
echo "  api=$API_POD"

# ── Start the stub OAuth AS fixture in the registry-api pod ────────────────────
echo "--- starting stub OAuth AS fixture on 127.0.0.1:9100 ---"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  sh -c 'cat > /tmp/oauth_mcp_server.py' < "$FIXTURE"
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  sh -c 'setsid nohup python3 /tmp/oauth_mcp_server.py --port 9100 >/tmp/oauth_stub.log 2>&1 &' || true
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9100)); s.close()" \
       2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || fail "stub OAuth fixture did not start on 127.0.0.1:9100"
echo "  OK: fixture listening"

SERVER_ID=""
cleanup() {
  kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    sh -c "pkill -f oauth_mcp_server.py" >/dev/null 2>&1 || true
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SRVNAME="$SERVER_NAME" python3 - <<'PY' 2>/dev/null || true
import os, asyncio
async def main():
    from db import AsyncSessionLocal
    from sqlalchemy import text
    async with AsyncSessionLocal() as s:
        row = (await s.execute(text("SELECT id FROM mcp_servers WHERE name = :n"),
                               {"n": os.environ["SRVNAME"]})).first()
        if row:
            sid = str(row[0])
            await s.execute(text("DELETE FROM tools WHERE mcp_server_id = :i"), {"i": sid})
            await s.execute(text("DELETE FROM mcp_oauth_grants WHERE server_id = :i"), {"i": sid})
            await s.execute(text("DELETE FROM credential_blobs WHERE path LIKE :p"),
                            {"p": f"mcp-oauth-refresh/{sid}/%"})
            await s.execute(text("DELETE FROM credential_blobs WHERE path = :p"),
                            {"p": f"mcp-oauth-client/{sid}"})
            await s.execute(text("DELETE FROM mcp_servers WHERE id = :i"), {"i": sid})
        await s.commit()
asyncio.run(main())
PY
}
trap cleanup EXIT

# ── 1+2. Register → authorize → callback → assert authorized + refresh ref ─────
echo "--- register External+OAuth server → drive authorize→callback ---"
INPOD="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SERVER_NAME="$SERVER_NAME" USER_SUB="$USER_SUB" python3 - <<'PY'
import os, asyncio, httpx, sys
from urllib.parse import urlparse, parse_qs
SERVER_NAME = os.environ["SERVER_NAME"]
USER_SUB = os.environ["USER_SUB"]
BASE = "http://localhost:8000/api/v1"
FIX = "http://127.0.0.1:9100"
ADMIN = {"X-User-Sub": "platform-admin", "X-User-Team": "platform"}
HDR = {"X-User-Sub": USER_SUB, "X-User-Team": "platform"}

async def main():
    from db import AsyncSessionLocal
    from credential_provider import CredentialRef, get_provider
    from models import MCPOAuthGrant
    from sqlalchemy import select
    async with httpx.AsyncClient(timeout=40) as c:
        r = await c.post(f"{BASE}/mcp-servers/", headers=ADMIN, json={
            "name": SERVER_NAME, "server_url": f"{FIX}/mcp",
            "transport": "streamable_http", "owner_team": "platform",
            "is_external": True, "external_auth_mode": "oauth"})
        if r.status_code != 201:
            print("REGFAIL", r.status_code, r.text[:200]); sys.exit(1)
        sid = r.json()["id"]
        print("SERVER_ID", sid)
        a = await c.post(f"{BASE}/mcp-servers/{sid}/oauth/authorize", headers=HDR)
        if a.status_code != 200:
            print("AUTHZFAIL", a.status_code, a.text[:200]); sys.exit(1)
        auth_url = a.json()["authorization_url"]
        rr = await c.get(auth_url, follow_redirects=False)
        q = parse_qs(urlparse(rr.headers.get("location", "")).query)
        code = (q.get("code") or [None])[0]
        state = (q.get("state") or [None])[0]
        iss = (q.get("iss") or [None])[0]
        params = {"code": code, "state": state}
        if iss:
            params["iss"] = iss
        cb = await c.get(f"{BASE}/mcp-servers/oauth/callback", params=params, follow_redirects=False)
        outcome = (parse_qs(urlparse(cb.headers.get("location", "")).query).get("oauth") or [""])[0]
        print("OUTCOME", outcome)
    async with AsyncSessionLocal() as s:
        g = (await s.execute(select(MCPOAuthGrant).where(
            MCPOAuthGrant.server_id == sid, MCPOAuthGrant.user_sub == USER_SUB))).scalar_one_or_none()
    if g is None:
        print("GRANT none"); sys.exit(1)
    print("GRANT_STATUS", g.status)
    print("CREDENTIAL_REF", g.credential_ref)
    stored = await get_provider().get(CredentialRef.parse(g.credential_ref)) if g.credential_ref else {}
    print("REFRESH_BEFORE", (stored or {}).get("refresh_token", ""))
asyncio.run(main())
PY
)" || { echo "$INPOD"; fail "authorize/callback block errored"; }
echo "$INPOD"

SERVER_ID="$(echo "$INPOD" | sed -n 's/^SERVER_ID //p' | tr -d '[:space:]')"
OUTCOME="$(echo "$INPOD" | sed -n 's/^OUTCOME //p' | tr -d '[:space:]')"
GRANT_STATUS="$(echo "$INPOD" | sed -n 's/^GRANT_STATUS //p' | tr -d '[:space:]')"
CREDENTIAL_REF="$(echo "$INPOD" | sed -n 's/^CREDENTIAL_REF //p' | head -1 | tr -d '[:space:]')"
REFRESH_BEFORE="$(echo "$INPOD" | sed -n 's/^REFRESH_BEFORE //p' | tr -d '[:space:]')"

[ "$OUTCOME" = "connected" ] || fail "callback outcome=$OUTCOME (want connected)"
[ "$GRANT_STATUS" = "authorized" ] || fail "grant status=$GRANT_STATUS (want authorized)"
case "$CREDENTIAL_REF" in
  pg-fernet://*mcp-oauth-refresh*) echo "  OK: grant authorized with a pg-fernet refresh credential_ref";;
  *) fail "credential_ref is not a pg-fernet refresh ref: '$CREDENTIAL_REF'";;
esac
[ -n "$REFRESH_BEFORE" ] || fail "no refresh token stored behind the provider"

# ── 3. Internal token endpoint: proxy SA → 200; non-proxy → 403; no bearer → 401 ─
echo "--- internal token endpoint auth matrix (proxy 200 / non-proxy 403 / no-bearer 401) ---"
PROXY_TOKEN="$(kubectl create token "$PROXY_SA" -n "$NAMESPACE" --audience="$AUD" 2>/dev/null || true)"
NONPROXY_TOKEN="$(kubectl create token "$API_SA" -n "$NAMESPACE" --audience="$AUD" 2>/dev/null || true)"
[ -n "$PROXY_TOKEN" ] || fail "could not mint a proxy SA token (kubectl create token / SA missing)"

MATRIX="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env PT="$PROXY_TOKEN" NT="$NONPROXY_TOKEN" SID="$SERVER_ID" US="$USER_SUB" python3 - <<'PY'
import os, asyncio, httpx
async def main():
    url = "http://localhost:8000/api/v1/internal/mcp/oauth/access-token"
    body = {"server_id": os.environ["SID"], "user_sub": os.environ["US"]}
    async with httpx.AsyncClient(timeout=25) as c:
        rp = await c.post(url, json=body, headers={"Authorization": f"Bearer {os.environ['PT']}"})
        bp = rp.json() if rp.status_code == 200 else {}
        print("PROXY", rp.status_code, "STATUS", bp.get("status"),
              "HASTOKEN", "yes" if bp.get("access_token") else "no")
        rn = await c.post(url, json=body, headers={"Authorization": f"Bearer {os.environ['NT']}"})
        print("NONPROXY", rn.status_code)
        ra = await c.post(url, json=body)
        print("NOAUTH", ra.status_code)
asyncio.run(main())
PY
)" || { echo "$MATRIX"; fail "token endpoint matrix block errored"; }
echo "$MATRIX"
echo "$MATRIX" | grep -q "PROXY 200 STATUS authorized HASTOKEN yes" || fail "proxy SA pull did not return 200 authorized + token"
echo "$MATRIX" | grep -q "NONPROXY 403" || fail "non-proxy SA was not 403"
echo "$MATRIX" | grep -q "NOAUTH 401" || fail "no-bearer call was not 401"
echo "  OK: token endpoint auth matrix (200 / 403 / 401)"

# ── 4. Rotation: a second pull rotates the stored refresh token ────────────────
echo "--- force refresh-token rotation (second pull) → stored ref value changed ---"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env PT="$PROXY_TOKEN" SID="$SERVER_ID" US="$USER_SUB" python3 - >/dev/null 2>&1 <<'PY' || true
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=25) as c:
        await c.post("http://localhost:8000/api/v1/internal/mcp/oauth/access-token",
                     json={"server_id": os.environ["SID"], "user_sub": os.environ["US"]},
                     headers={"Authorization": f"Bearer {os.environ['PT']}"})
asyncio.run(main())
PY
AFTER="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SID="$SERVER_ID" US="$USER_SUB" python3 - <<'PY'
import os, asyncio
async def main():
    from db import AsyncSessionLocal
    from credential_provider import CredentialRef, get_provider, CredentialNotFound
    from models import MCPOAuthGrant
    from sqlalchemy import select
    async with AsyncSessionLocal() as s:
        g = (await s.execute(select(MCPOAuthGrant).where(
            MCPOAuthGrant.server_id == os.environ["SID"],
            MCPOAuthGrant.user_sub == os.environ["US"]))).scalar_one_or_none()
    if g is None or not g.credential_ref:
        print("VALUE", ""); return
    try:
        v = await get_provider().get(CredentialRef.parse(g.credential_ref))
        print("VALUE", (v or {}).get("refresh_token", ""))
    except CredentialNotFound:
        print("VALUE", "")
asyncio.run(main())
PY
)"
AFTER_VAL="$(echo "$AFTER" | sed -n 's/^VALUE //p' | tr -d '[:space:]')"
[ -n "$AFTER_VAL" ] || fail "could not read the rotated refresh token"
[ "$AFTER_VAL" != "$REFRESH_BEFORE" ] \
  || fail "refresh token did NOT rotate (before==after==$REFRESH_BEFORE)"
echo "  OK: stored refresh token rotated (before != after)"

echo "PASS"
