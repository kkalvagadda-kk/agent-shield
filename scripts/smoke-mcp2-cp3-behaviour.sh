#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster + the Keycloak confidential client (quickstart.md) +
# an agents-platform namespace with a usable SA (for the data-plane token).
# =============================================================================
# CP3c — MCP Phase 2 (WS-C internal identity): behaviour smoke.
#
# Drives the data-plane /internal/tools/call for each identity_mode:
#   - none            → byte-identical to Phase 1: echo returns the input VERBATIM
#                       (no minted bearer, no x-user-sub needed) [T-S85-021 slice]
#   - service_identity→ tools/call succeeds (a minted bearer is presented upstream;
#                       the stub ignores auth, so success is the available proof — a
#                       full header capture needs a header-echoing upstream) [T-S85-022]
#   - on_behalf_of, NO x-user-sub  → 200 is_error "requires a user identity" [T-S85-023]
#   - on_behalf_of, WITH x-user-sub→ 200 is_error "blocked on Decision 29"  [T-S85-024]
#   - x-user-sub emission [T-S85-027]: a deployed fixture agent pod carries
#                       AGENTSHIELD_USER_SUB (the executor sends x-user-sub only when
#                       non-empty). Gated — SKIPs if no sdk-0.2.4 fixture agent is deployed.
#
# The OBO assertions do NOT depend on Keycloak (authz own-team passes, then identity
# raises before any upstream call); only service_identity needs the mint.
# jq assertions. Exit 0 on full pass, non-zero on the first failure. Ends `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP3: identity behaviour smoke (none/service_identity/OBO/x-user-sub) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
MCP_NS="${MCP_NS:-agentshield-mcp}"
PROXY_AUDIENCE="${PROXY_AUDIENCE:-agentshield-mcp-proxy}"
AGENT_NS="${AGENT_NS:-agents-platform}"     # own-team caller namespace (team=platform)
AGENT_SA="${AGENT_SA:-default}"
SUFFIX="$(date +%s | tail -c 7)"

fail() { echo "FAIL: $1" >&2; exit 1; }

PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  proxy=$PROXY_POD api=$API_POD agent-ns=$AGENT_NS"

# ── Start the fixture in the proxy pod ────────────────────────────────────────
echo "--- starting stub MCP fixture inside the proxy pod ---"
kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
  sh -c 'setsid nohup python3 /app/fixtures/stub_mcp_server.py >/tmp/stub.log 2>&1 &' || true
READY=""
for _ in $(seq 1 20); do
  if kubectl exec -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- python3 -c \
       "import socket; s=socket.socket(); s.settimeout(2); s.connect(('127.0.0.1',9999)); s.close()" \
       2>/dev/null; then READY=1; break; fi
  sleep 2
done
[ -n "$READY" ] || fail "stub fixture did not start listening on 127.0.0.1:9999"
echo "  OK: fixture listening"

# ── Register one server per identity_mode (all → the fixture) ─────────────────
echo "--- registering none / service_identity / on_behalf_of servers ---"
REG_OUT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx, sys
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
async def reg(c, name, mode, extra=None):
    body = {"name": name, "description": f"cp3 {mode}",
            "server_url": "http://127.0.0.1:9999/mcp", "transport": "streamable_http",
            "owner_team": "platform", "is_external": False,
            "identity_mode": mode, "scan_results": True}
    if extra: body.update(extra)
    r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json=body)
    if r.status_code != 201:
        print("REGFAIL", name, r.status_code, r.text[:200]); sys.exit(1)
    return r.json()["id"]
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        n = await reg(c, f"cp3-none-{SUFFIX}", "none")
        si = await reg(c, f"cp3-si-{SUFFIX}", "service_identity",
                       {"transport_config": {"identity_audience": f"aud-{SUFFIX}"}})
        obo = await reg(c, f"cp3-obo-{SUFFIX}", "on_behalf_of")
        print("NONE", n)
        print("SI", si)
        print("OBO", obo)
asyncio.run(main())
PY
)" || { echo "$REG_OUT"; fail "server registration failed"; }
echo "$REG_OUT"
NONE_ID="$(echo "$REG_OUT" | sed -n 's/^NONE //p' | tr -d '[:space:]')"
SI_ID="$(echo "$REG_OUT" | sed -n 's/^SI //p' | tr -d '[:space:]')"
OBO_ID="$(echo "$REG_OUT" | sed -n 's/^OBO //p' | tr -d '[:space:]')"
[ -n "$NONE_ID" ] && [ -n "$SI_ID" ] && [ -n "$OBO_ID" ] || fail "could not capture all three server ids"
cleanup() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env IDS="$NONE_ID $SI_ID $OBO_ID" python3 - <<'PY' 2>/dev/null || true
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        for sid in os.environ["IDS"].split():
            await c.delete(f"http://localhost:8000/api/v1/mcp-servers/{sid}",
                           headers={"X-User-Sub": "platform-admin"})
asyncio.run(main())
PY
}
trap cleanup EXIT

# ── Mint the data-plane agent SA token (own-team: agents-platform) ────────────
echo "--- minting agent SA token (${AGENT_NS}/${AGENT_SA}, audience ${PROXY_AUDIENCE}) ---"
AGENT_TOKEN="$(kubectl create token "$AGENT_SA" -n "$AGENT_NS" \
  --audience "$PROXY_AUDIENCE" --duration 10m 2>/dev/null || true)"
[ -n "$AGENT_TOKEN" ] \
  || fail "could not mint an agent SA token in ${AGENT_NS} (does the namespace + SA exist? it's the own-team data-plane caller)"

# Helper: POST /internal/tools/call from inside the proxy pod. Prints "HTTP <code> <body>".
call_tool() {  # $1 = server_id, $2 = user_sub ("" to omit)
  kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
    env SID="$1" USUB="$2" TOKEN="$AGENT_TOKEN" python3 - <<'PY'
import os, json, urllib.request, urllib.error
URL = "http://localhost:8080/internal/tools/call"
body = json.dumps({
    "server_id": os.environ["SID"], "mcp_tool_name": "echo",
    "arguments": {"text": "cp3-roundtrip"},
    "session_id": "cp3", "agent_name": "cp3"}).encode()
h = {"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["TOKEN"]}
usub = os.environ.get("USUB", "")
if usub:
    h["x-user-sub"] = usub
req = urllib.request.Request(URL, data=body, headers=h, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=60)
    print("HTTP", r.getcode(), r.read().decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode())
except Exception as e:  # noqa: BLE001
    print("HTTP ERR", e)
PY
}

# ── 1. none → echo verbatim (byte-identical to Phase 1) ───────────────────────
echo "--- none server tools/call → echo verbatim ---"
NONE_OUT="$(call_tool "$NONE_ID" "")"
echo "  $NONE_OUT"
echo "$NONE_OUT" | grep -q "^HTTP 200" || fail "none tools/call HTTP != 200 ($NONE_OUT)"
NONE_JSON="${NONE_OUT#HTTP 200 }"
echo "$NONE_JSON" | jq -e '.is_error == false and (.result | test("cp3-roundtrip"))' >/dev/null \
  || fail "none tools/call did not echo the input verbatim ($NONE_JSON)"
echo "  OK: none → verbatim echo, is_error=false"

# ── 2. service_identity → tools/call succeeds (minted bearer path) ────────────
echo "--- service_identity server tools/call → succeeds (minted bearer) ---"
SI_OUT="$(call_tool "$SI_ID" "")"
echo "  $SI_OUT"
echo "$SI_OUT" | grep -q "^HTTP 200" || fail "service_identity tools/call HTTP != 200 ($SI_OUT)"
SI_JSON="${SI_OUT#HTTP 200 }"
echo "$SI_JSON" | jq -e '.is_error == false' >/dev/null \
  || fail "service_identity tools/call is_error=true — minted-bearer path failed ($SI_JSON). Keycloak client provisioned?"
echo "  OK: service_identity → call succeeded (bearer minted + presented; header-capture needs an echoing upstream)"

# ── 3. on_behalf_of, NO x-user-sub → 200 is_error 'requires a user identity' ──
echo "--- on_behalf_of (no x-user-sub) → 200 is_error requires-a-user-identity ---"
OBO1_OUT="$(call_tool "$OBO_ID" "")"
echo "  $OBO1_OUT"
echo "$OBO1_OUT" | grep -q "^HTTP 200" || fail "OBO(no-sub) HTTP != 200 ($OBO1_OUT)"
OBO1_JSON="${OBO1_OUT#HTTP 200 }"
echo "$OBO1_JSON" | jq -e '.is_error == true and (.error | test("requires a user identity"))' >/dev/null \
  || fail "OBO(no-sub) did not fail-closed with 'requires a user identity' ($OBO1_JSON)"
echo "  OK: OBO without x-user-sub → fail-closed (requires a user identity)"

# ── 4. on_behalf_of, WITH x-user-sub → 200 is_error 'blocked on Decision 29' ──
echo "--- on_behalf_of (with x-user-sub) → 200 is_error blocked-on-Decision-29 (STUB) ---"
OBO2_OUT="$(call_tool "$OBO_ID" "cp3-user-sub")"
echo "  $OBO2_OUT"
echo "$OBO2_OUT" | grep -q "^HTTP 200" || fail "OBO(with-sub) HTTP != 200 ($OBO2_OUT)"
OBO2_JSON="${OBO2_OUT#HTTP 200 }"
echo "$OBO2_JSON" | jq -e '.is_error == true and (.error | test("Decision 29"))' >/dev/null \
  || fail "OBO(with-sub) did not return the STUB error 'blocked on Decision 29' ($OBO2_JSON)"
echo "  OK: OBO with x-user-sub → STUB error (blocked on Decision 29) — NOT a working exchange"

# ── 5. x-user-sub emission (T-S85-027) — gated on a deployed fixture agent ─────
echo "--- x-user-sub emission: a deployed fixture agent pod carries AGENTSHIELD_USER_SUB ---"
AGENT_POD="$(kubectl get pods -n "$AGENT_NS" --no-headers 2>/dev/null | grep Running | awk '{print $1}' | head -1 || true)"
if [ -z "$AGENT_POD" ]; then
  echo "  SKIP: no Running pod in ${AGENT_NS} — deploy an sdk-0.2.4 fixture agent to prove x-user-sub emission"
else
  USUB_ENV="$(kubectl exec -n "$AGENT_NS" "$AGENT_POD" -- printenv AGENTSHIELD_USER_SUB 2>/dev/null || true)"
  if [ -n "$USUB_ENV" ]; then
    echo "  OK: ${AGENT_POD} has AGENTSHIELD_USER_SUB set (=${USUB_ENV}) — the executor sends x-user-sub when non-empty"
  else
    echo "  SKIP: ${AGENT_POD} has no AGENTSHIELD_USER_SUB set — header emission is covered by the SDK/runner unit tests"
  fi
fi

echo "PASS"
