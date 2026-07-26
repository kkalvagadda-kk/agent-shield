#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP3c — MCP Phase 4 (WS-2 end-to-end OAuth tool call): behaviour smoke.
#
# Proves the full runtime path — a real upstream bearer presented by the proxy — plus the
# two fail-closed arms:
#   1. Start the stub OAuth AS + bearer-gated MCP fixture in the PROXY pod, bound to
#      0.0.0.0 and advertising http://<proxy-pod-ip>:9100 so BOTH registry-api (discovery /
#      code-exchange / refresh) and the proxy (the upstream /mcp dial) reach the SAME
#      origin. Register it as an External+OAuth server.
#   2. Drive authorize→callback (registry-api ↔ fixture, cross-pod) → grant authorized;
#      the callback's discover-as-user pulls a token via the proxy and materializes the
#      `echo` Tool. Grant `echo` to team platform + mint an agents-platform SA token.
#   3. Agent-SA POST /internal/tools/call WITH x-user-sub → 200 result echoes the input
#      (the proxy pulled a fresh access token from registry-api and presented it upstream —
#      the fixture's bearer-gate accepted it).
#   4. NO x-user-sub → 200 is_error "user identity" (resolve_headers fail-closed, no dial).
#   5. After DELETE …/oauth (revoke) → 200 is_error "(re-)authorize" (registry-api returns
#      needs_auth; the proxy never downgrades to an unauthenticated call).
#
# NOTE (reachability): registry-api must reach the proxy pod IP:9100. If a NetworkPolicy
# blocks that cross-pod hop, run the fixture behind a ClusterIP Service both can resolve and
# set FIXTURE_BASE to it. Cleans up the server/grant/SA/tool + fixture on exit.
# Exit 0 on full pass, non-zero on first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP4-CP3: end-to-end OAuth tool call + fail-closed (revoke/no-user) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NS="${AGENTS_NS:-agents-platform}"
SUFFIX="$(date +%s | tail -c 7)"
SERVER_NAME="cp3c-oauth-${SUFFIX}"
USER_SUB="cp3c-user-${SUFFIX}"
AGENT_SA="cp3c-agent-${SUFFIX}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FIXTURE="${SCRIPT_DIR}/e2e/fixtures/oauth_mcp_server.py"

fail() { echo "FAIL: $1" >&2; exit 1; }

# Free TCP 9100 in the proxy pod ROBUSTLY. `pkill -f oauth_mcp_server.py` is not enough: a
# fixture left over from an interrupted run can present an EMPTY /proc/<pid>/cmdline (so
# pkill -f never matches it) while still holding the LISTEN socket — which then makes the
# "fixture listening" probe below a false positive against the STALE process. So also find
# whoever owns the 9100 (0x238C) LISTEN socket via its inode and kill by PID.
free_fixture_port() {
  kubectl exec -n "$NAMESPACE" "$1" -c mcp-proxy -- sh -c '
    pkill -9 -f oauth_mcp_server.py 2>/dev/null || true
    inode=$(grep -i "238C 00000000:0000 0A" /proc/net/tcp 2>/dev/null | awk "{print \$10}" | head -1)
    if [ -n "$inode" ]; then
      for p in $(ls /proc 2>/dev/null | grep -E "^[0-9]+$"); do
        ls -l /proc/$p/fd 2>/dev/null | grep -q "socket:\[$inode\]" && kill -9 "$p" 2>/dev/null
      done
    fi
    sleep 1' >/dev/null 2>&1 || true
}

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -f "$FIXTURE" ] || fail "fixture not found at $FIXTURE"
PROXY_IP="$(kubectl get pod "$PROXY_POD" -n "$NAMESPACE" -o jsonpath='{.status.podIP}' 2>/dev/null || true)"
[ -n "$PROXY_IP" ] || fail "could not resolve proxy pod IP"
FIXTURE_BASE="${FIXTURE_BASE:-http://${PROXY_IP}:9100}"
echo "  api=$API_POD proxy=$PROXY_POD proxy_ip=$PROXY_IP base=$FIXTURE_BASE"

# ── Start the fixture in the PROXY pod, bound 0.0.0.0, advertising the pod IP ───
echo "--- starting stub OAuth+MCP fixture in the proxy pod (base=$FIXTURE_BASE) ---"
free_fixture_port "$PROXY_POD"   # ensure a clean 9100 (kill any stale/zombie listener)
kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c 'cat > /tmp/oauth_mcp_server.py' < "$FIXTURE"
kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c "setsid nohup python3 /tmp/oauth_mcp_server.py --host 0.0.0.0 --port 9100 --base '$FIXTURE_BASE' >/tmp/oauth_stub.log 2>&1 &" || true
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9100)); s.close()" \
       2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || fail "stub OAuth fixture did not start in the proxy pod"
# Assert OUR fixture actually bound — a stale listener freed above must not have raced back,
# and a bind failure (address already in use) must fail loudly, not read as "listening".
if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
     sh -c 'grep -q "address already in use" /tmp/oauth_stub.log' 2>/dev/null; then
  fail "fixture could not bind 9100 (address already in use) — stale listener not cleared"
fi
echo "  OK: fixture listening"

SERVER_ID=""
cleanup() {
  free_fixture_port "$PROXY_POD"   # robust: also kills an empty-cmdline zombie holding 9100
  kubectl delete sa "$AGENT_SA" -n "$AGENTS_NS" --ignore-not-found >/dev/null 2>&1 || true
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
            await s.execute(text("DELETE FROM asset_grants WHERE granted_by = 'auto:cp3c'"))
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

# ── 0. Mint a real Keycloak JWT (ROPC) once ───────────────────────────────────
# Both OAuth control-plane endpoints this smoke drives — POST …/oauth/authorize and
# DELETE …/oauth (revoke) — anchor to a VERIFIED jwt.sub (require_user), so a spoofable
# X-User-Sub is correctly rejected there. Authenticate the way real Studio does: a genuine
# Keycloak token via the seeded admin + the `agentshield-studio` public client (same
# identity the Playwright e2e uses). Its `sub` becomes the grant owner, so the data-plane
# tool calls below carry that same sub as x-user-sub.
echo "--- mint a real Keycloak JWT (platform-admin, agentshield-studio client) ---"
JWT_OUT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY'
import httpx, json, base64
t = httpx.post("http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
    data={"grant_type": "password", "client_id": "agentshield-studio",
          "username": "platform-admin", "password": "PlatformAdmin2024"}, timeout=15)
at = t.json()["access_token"]
pl = at.split(".")[1]
sub = json.loads(base64.urlsafe_b64decode(pl + "=" * (-len(pl) % 4)))["sub"]
print("ACCESS_TOKEN", at)
print("SUB", sub)
PY
)"
ACCESS_TOKEN="$(echo "$JWT_OUT" | sed -n 's/^ACCESS_TOKEN //p' | tr -d '[:space:]')"
USER_SUB="$(echo "$JWT_OUT" | sed -n 's/^SUB //p' | tr -d '[:space:]')"
[ -n "$ACCESS_TOKEN" ] || fail "could not mint a Keycloak JWT (ROPC — is platform-admin seeded?)"
[ -n "$USER_SUB" ] || fail "could not resolve the JWT subject"
echo "  OK: JWT minted; authorizing user_sub=$USER_SUB"

# ── 1+2. Register → authorize → callback → grant authorized + tool discovered ──
echo "--- register + authorize→callback (discover-as-user materializes echo) ---"
INPOD="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SERVER_NAME="$SERVER_NAME" USER_SUB="$USER_SUB" ACCESS_TOKEN="$ACCESS_TOKEN" FIXTURE_BASE="$FIXTURE_BASE" python3 - <<'PY'
import os, asyncio, httpx, sys
from urllib.parse import urlparse, parse_qs
SERVER_NAME = os.environ["SERVER_NAME"]
USER_SUB = os.environ["USER_SUB"]
BASEURL = os.environ["FIXTURE_BASE"]
BASE = "http://localhost:8000/api/v1"
ADMIN = {"X-User-Sub": "platform-admin", "X-User-Team": "platform"}
# register/list honour the X-User-Sub dev header (ADMIN); the OAuth authorize sub-route
# requires the verified bearer.
BEARER = {"Authorization": f"Bearer {os.environ['ACCESS_TOKEN']}"}

async def main():
    from db import AsyncSessionLocal
    from models import MCPOAuthGrant, Tool
    from sqlalchemy import select
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/mcp-servers/", headers=ADMIN, json={
            "name": SERVER_NAME, "server_url": f"{BASEURL}/mcp",
            "transport": "streamable_http", "owner_team": "platform",
            "is_external": True, "external_auth_mode": "oauth"})
        if r.status_code != 201:
            print("REGFAIL", r.status_code, r.text[:200]); sys.exit(1)
        sid = r.json()["id"]; print("SERVER_ID", sid)
        a = await c.post(f"{BASE}/mcp-servers/{sid}/oauth/authorize", headers=BEARER)
        if a.status_code != 200:
            print("AUTHZFAIL", a.status_code, a.text[:200]); sys.exit(1)
        rr = await c.get(a.json()["authorization_url"], follow_redirects=False)
        q = parse_qs(urlparse(rr.headers.get("location", "")).query)
        params = {"code": (q.get("code") or [None])[0], "state": (q.get("state") or [None])[0]}
        if q.get("iss"):
            params["iss"] = q["iss"][0]
        cb = await c.get(f"{BASE}/mcp-servers/oauth/callback", params=params, follow_redirects=False)
        print("OUTCOME", (parse_qs(urlparse(cb.headers.get("location", "")).query).get("oauth") or [""])[0])
    async with AsyncSessionLocal() as s:
        g = (await s.execute(select(MCPOAuthGrant).where(
            MCPOAuthGrant.server_id == sid, MCPOAuthGrant.user_sub == USER_SUB))).scalar_one_or_none()
        tool = (await s.execute(select(Tool).where(
            Tool.mcp_server_id == sid, Tool.mcp_tool_name == "echo"))).scalar_one_or_none()
    print("GRANT_STATUS", g.status if g else "none")
    print("ECHO_TOOL", "yes" if tool else "no")
asyncio.run(main())
PY
)" || { echo "$INPOD"; fail "register/authorize block errored"; }
echo "$INPOD"
SERVER_ID="$(echo "$INPOD" | sed -n 's/^SERVER_ID //p' | tr -d '[:space:]')"
[ "$(echo "$INPOD" | sed -n 's/^OUTCOME //p' | tr -d '[:space:]')" = "connected" ] || fail "dance outcome != connected"
[ "$(echo "$INPOD" | sed -n 's/^GRANT_STATUS //p' | tr -d '[:space:]')" = "authorized" ] || fail "grant not authorized"
echo "$INPOD" | grep -q "ECHO_TOOL yes" || fail "discover-as-user did not materialize the echo tool"
echo "  OK: authorized + echo tool discovered"

# ── Grant echo to team platform + create an agents-platform SA + mint its token ─
echo "--- grant echo to platform + mint an agents-platform SA token ---"
kubectl get ns "$AGENTS_NS" >/dev/null 2>&1 || fail "namespace $AGENTS_NS absent (needed for the agent-SA tool call)"
kubectl create sa "$AGENT_SA" -n "$AGENTS_NS" >/dev/null 2>&1 || fail "could not create SA $AGENT_SA in $AGENTS_NS"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SID="$SERVER_ID" python3 - >/dev/null 2>&1 <<'PY' || true
import os, asyncio
async def main():
    from db import AsyncSessionLocal
    from models import Tool, AssetGrant
    from sqlalchemy import select
    async with AsyncSessionLocal() as s:
        t = (await s.execute(select(Tool).where(
            Tool.mcp_server_id == os.environ["SID"], Tool.mcp_tool_name == "echo"))).scalar_one_or_none()
        if t is not None:
            s.add(AssetGrant(asset_type="tool", asset_id=t.id, grantee_team="platform",
                             granted_by="auto:cp3c"))
            await s.commit()
asyncio.run(main())
PY
AGENT_TOKEN="$(kubectl create token "$AGENT_SA" -n "$AGENTS_NS" --audience=agentshield-mcp-proxy 2>/dev/null || true)"
[ -n "$AGENT_TOKEN" ] || fail "could not mint an agents-platform SA token (kubectl create token)"
echo "  OK: agent SA token minted"

tools_call() {  # $1 = extra header spec ("" or "x-user-sub: <sub>"); prints CODE/ISERR/MSG/RESULT
  local extra="$1"
  kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
    env AT="$AGENT_TOKEN" SID="$SERVER_ID" EXTRA="$extra" python3 - <<'PY'
import os, httpx
headers = {"Authorization": f"Bearer {os.environ['AT']}"}
extra = os.environ.get("EXTRA") or ""
if ":" in extra:
    k, _, v = extra.partition(":")
    headers[k.strip()] = v.strip()
# 75s > the proxy's worst case on the revoke arm: the first call_tool blocks on the
# fixture's 401 until MCP_CONNECT_TIMEOUT_SECONDS (30s), then the proxy evicts + re-pulls
# and returns 200 is_error. Steps 3/4 answer in well under a second; only step 5 waits.
r = httpx.post("http://localhost:8080/internal/tools/call",
    json={"server_id": os.environ["SID"], "mcp_tool_name": "echo",
          "arguments": {"text": "cp3c-hi"}, "session_id": "cp3c", "agent_name": "cp3c"},
    headers=headers, timeout=75)
b = r.json() if r.status_code == 200 else {}
print("CODE", r.status_code, "ISERR", b.get("is_error"),
      "RESULT", (b.get("result") or "")[:40], "MSG", (b.get("error") or "")[:80])
PY
}

# ── 3. WITH x-user-sub → 200 real result (bearer presented upstream) ──────────
echo "--- tools/call WITH x-user-sub → real echo result ---"
R="$(tools_call "x-user-sub: $USER_SUB")"
echo "  $R"
echo "$R" | grep -q "CODE 200 ISERR False" || fail "authorized tools/call was not a 200 success: $R"
echo "$R" | grep -q "cp3c-hi" || fail "echo did not round-trip the input (bearer not presented upstream?): $R"
echo "  OK: real upstream result (fresh access token presented)"

# ── 4. NO x-user-sub → 200 is_error user identity ─────────────────────────────
echo "--- tools/call with NO x-user-sub → 200 is_error (user identity) ---"
R="$(tools_call "")"
echo "  $R"
echo "$R" | grep -q "CODE 200 ISERR True" || fail "no-user tools/call was not 200 is_error: $R"
echo "$R" | grep -qiE "user identity|OAuth" || fail "no-user error text unexpected: $R"
echo "  OK: fail-closed on missing user identity"

# ── 5. Revoke → 200 is_error re-authorize ─────────────────────────────────────
echo "--- DELETE …/oauth (revoke) → tools/call → 200 is_error (re-authorize) ---"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SID="$SERVER_ID" AT="$ACCESS_TOKEN" python3 - >/dev/null 2>&1 <<'PY' || true
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=20) as c:
        # revoke is a require_user OAuth endpoint → present the verified bearer, not a header.
        await c.delete(f"http://localhost:8000/api/v1/mcp-servers/{os.environ['SID']}/oauth",
                       headers={"Authorization": f"Bearer {os.environ['AT']}"})
asyncio.run(main())
PY
R="$(tools_call "x-user-sub: $USER_SUB")"
echo "  $R"
echo "$R" | grep -q "CODE 200 ISERR True" || fail "revoked tools/call was not 200 is_error: $R"
# The proxy's retry path re-pulls after the revoked-token 401 and surfaces the grant's
# state; the exact wording is "grant status is 'needs_auth'" (the re-authorize signal —
# needs_auth is precisely "the user must authorize again"). Accept either phrasing.
echo "$R" | grep -qiE "needs_auth|authoriz|re-authoriz" || fail "revoked error text did not signal needs-auth/re-authorize: $R"
echo "  OK: fail-closed on a revoked grant (needs_auth / re-authorize)"

echo "PASS"
