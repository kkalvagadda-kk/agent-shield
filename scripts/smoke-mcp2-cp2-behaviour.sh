#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2c — MCP Phase 2 (WS-B list_changed): behaviour smoke.
#
# Proves the upstream-tool-change → auto re-sync → persisted Tool round-trip:
#   1. Start the stub fixture in the proxy pod (base toolset — no dynamic_echo);
#      register it; assert dynamic_echo is ABSENT.
#   2. simulate_tool_change("add") mutates the fixture; the proxy subscriber SHOULD
#      auto-POST /internal/mcp/list-changed (best-effort cross-session — observed via
#      proxy logs, informational). The DETERMINISTIC proof is a direct
#      POST /internal/mcp/list-changed → GET /mcp-servers/{id} shows the namespaced
#      dynamic_echo tool (save → reload → assert).
#   3. simulate_tool_change("remove") + re-sync → the tool goes INACTIVE, not deleted
#      (FR-MCP-04 — the row survives).
#   4. Coalesce: with a fresh window, 3 re-syncs fired in a burst → only the 1st runs
#      (coalesced:false); the 2nd/3rd within the min-resync window → coalesced:true.
#
# Best-effort auto-notification is ledgered (docs/testing/manual-ui-e2e-test-plan.md,
# MCP Phase 2). The direct POST is the deterministic driver, exactly as ledgered.
# jq/SQL assertions. Exit 0 on full pass, non-zero on the first failure. Ends `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP2: list_changed behaviour smoke (add/remove/coalesce) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
# Clear window > MCP_LIST_CHANGED_DEBOUNCE_SECONDS (5) + mcp_list_changed_min_resync_interval_seconds (10).
WINDOW="${WINDOW:-18}"

fail() { echo "FAIL: $1" >&2; exit 1; }

PROXY_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$PROXY_POD" ] || fail "no Running mcp-proxy pod"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  proxy=$PROXY_POD api=$API_POD"

# ── Start the fixture (base toolset) inside the proxy pod ─────────────────────
echo "--- starting stub MCP fixture (base toolset) inside the proxy pod ---"
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

# ── 1. Register the fixture; capture id + name; assert dynamic_echo absent ────
SERVER_NAME="cp2-lc-${SUFFIX}"
REG_OUT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SERVER_NAME="$SERVER_NAME" python3 - <<'PY'
import os, asyncio, httpx, sys
NAME = os.environ["SERVER_NAME"]
BASE = "http://localhost:8000/api/v1"
HDR = {"X-User-Sub": "platform-admin"}
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/mcp-servers/", headers=HDR, json={
            "name": NAME, "description": "cp2 list_changed fixture",
            "server_url": "http://127.0.0.1:9999/mcp", "transport": "streamable_http",
            "owner_team": "platform", "is_external": False,
            "identity_mode": "none", "scan_results": True})
        if r.status_code != 201:
            print("REGFAIL", r.status_code, r.text[:200]); sys.exit(1)
        sid = r.json()["id"]
        print("SERVER_ID", sid)
        d = await c.get(f"{BASE}/mcp-servers/{sid}", headers=HDR)
        names = {t.get("mcp_tool_name") for t in d.json().get("tools", [])}
        print("HAS_DYNAMIC", "yes" if "dynamic_echo" in names else "no")
asyncio.run(main())
PY
)" || { echo "$REG_OUT"; fail "register failed"; }
echo "$REG_OUT"
SERVER_ID="$(echo "$REG_OUT" | sed -n 's/^SERVER_ID //p' | tr -d '[:space:]')"
[ -n "$SERVER_ID" ] || fail "could not capture SERVER_ID"
echo "$REG_OUT" | grep -q "HAS_DYNAMIC no" || fail "dynamic_echo unexpectedly present before simulate_tool_change('add')"
echo "  OK: server $SERVER_ID registered; dynamic_echo absent"
cleanup() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="$SERVER_ID" python3 - <<'PY' 2>/dev/null || true
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        await c.delete(f"http://localhost:8000/api/v1/mcp-servers/{os.environ['SID']}",
                       headers={"X-User-Sub": "platform-admin"})
asyncio.run(main())
PY
}
trap cleanup EXIT

# Helper: call a fixture control tool via an in-pod mcp client (proxy pod → 127.0.0.1:9999).
simulate() {  # $1 = add|remove
  kubectl exec -i -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy -- \
    env ACTION="$1" python3 - <<'PY'
import os, asyncio
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
async def main():
    async with streamablehttp_client(url="http://127.0.0.1:9999/mcp") as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("simulate_tool_change", {"action": os.environ["ACTION"]})
            print("SIMULATED", os.environ["ACTION"], res)
asyncio.run(main())
PY
}

# Helper: direct re-sync POST → prints "COALESCED true|false" + counters.
resync() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="$SERVER_ID" python3 - <<'PY'
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post("http://localhost:8000/api/v1/internal/mcp/list-changed",
                         json={"server_id": os.environ["SID"]})
        b = r.json() if r.status_code == 200 else {}
        print("RESYNC", r.status_code, "COALESCED", str(b.get("coalesced")).lower(),
              "added", b.get("tools_added"), "inactivated", b.get("tools_inactivated"))
asyncio.run(main())
PY
}

# Helper: GET the server; print dynamic_echo presence + status.
dyn_state() {
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
    env SID="$SERVER_ID" python3 - <<'PY'
import os, asyncio, httpx
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        d = await c.get(f"http://localhost:8000/api/v1/mcp-servers/{os.environ['SID']}",
                        headers={"X-User-Sub": "platform-admin"})
        tool = next((t for t in d.json().get("tools", [])
                     if t.get("mcp_tool_name") == "dynamic_echo"), None)
        if tool is None:
            print("DYN absent")
        else:
            print("DYN present", tool.get("status"))
asyncio.run(main())
PY
}

# ── 2. add → auto path (informational) + deterministic direct re-sync ─────────
echo "--- simulate_tool_change('add') → re-sync → dynamic_echo present (save/reload/assert) ---"
simulate add || fail "simulate_tool_change('add') failed"
sleep "$WINDOW"  # let any auto re-sync fire + clear the coalesce window before the direct POST
# Informational: did the proxy's subscriber auto-POST? (best-effort cross-session — ledgered)
if kubectl logs -n "$NAMESPACE" "$PROXY_POD" -c mcp-proxy --tail=500 2>/dev/null \
     | grep -q "re-sync POSTed for server ${SERVER_ID}"; then
  echo "  (info) proxy subscriber auto-POSTed a re-sync"
else
  echo "  (info) no auto re-sync observed in proxy logs — relying on the direct POST (ledgered best-effort)"
fi
echo "  resync: $(resync)"
DYN="$(dyn_state)"
echo "  $DYN"
echo "$DYN" | grep -q "DYN present active" || fail "dynamic_echo not present+active after add re-sync"
echo "  OK: dynamic_echo discovered + active (persisted round-trip)"

# ── 3. remove → inactive, NOT deleted ─────────────────────────────────────────
echo "--- simulate_tool_change('remove') → re-sync → dynamic_echo inactive (row survives) ---"
simulate remove || fail "simulate_tool_change('remove') failed"
sleep "$WINDOW"
echo "  resync: $(resync)"
DYN="$(dyn_state)"
echo "  $DYN"
echo "$DYN" | grep -q "DYN present inactive" \
  || fail "dynamic_echo should be present but inactive after remove (FR-MCP-04: never hard-deleted)"
echo "  OK: dynamic_echo inactive, row preserved"

# ── 4. Coalesce: burst of 3 in a fresh window → one re-sync ───────────────────
echo "--- coalesce: 3 re-syncs in a burst → 1st runs, 2nd/3rd coalesced ---"
sleep "$WINDOW"  # clear the window so the FIRST of the burst is not coalesced
R1="$(resync)"; R2="$(resync)"; R3="$(resync)"
echo "  $R1"; echo "  $R2"; echo "  $R3"
echo "$R1" | grep -q "COALESCED false" || fail "1st burst re-sync was coalesced (expected to run)"
echo "$R2" | grep -q "COALESCED true"  || fail "2nd burst re-sync was NOT coalesced (want coalesced within window)"
echo "$R3" | grep -q "COALESCED true"  || fail "3rd burst re-sync was NOT coalesced (want coalesced within window)"
echo "  OK: burst of 3 → one re-sync (2nd/3rd coalesced)"

echo "PASS"
