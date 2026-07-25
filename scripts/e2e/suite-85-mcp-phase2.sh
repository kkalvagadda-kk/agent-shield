#!/usr/bin/env bash
# scripts/e2e/suite-85-mcp-phase2.sh
#
# E2E Suite 85: MCP as a Tool Source (Phase 2) — registry-api API surface.
#
# Runs in-pod (kubectl exec into registry-api) against the real routers + real
# Postgres, mirroring suite-84's template. The API-testable slice of Phase 2's three
# workstreams: WS-A (health loop threshold/recovery + last_synced_at invariant),
# WS-B (the /internal/mcp/list-changed re-sync endpoint's coalesce/unknown/malformed
# behaviour), and WS-C (identity_mode/identity_audience carried in the per-server
# Secret). The proxy-gated /internal/health auth check + the RUNTIME dispatch (proxy
# subscriber → fixture → re-sync, service-identity minting, OBO stub) are proven by
# the CP1/CP2/CP3 smoke scripts, not here — a kubectl-exec API suite structurally
# cannot drive the proxy's live sessions (same boundary suite-84 accepts).
#
# The three probe/discovery/secret seams are monkeypatched IN-POD so the suite tests
# the registry-api LOGIC deterministically without a live mcp-proxy or a real upstream
# MCP server (identical philosophy to suite-84 seeding via ORM to avoid the proxy):
#   - mcp_proxy_client.health_check_server → forced ok=false / ok=true (WS-A state machine)
#   - mcp_discovery._materialize_and_discover → no-op counters (WS-B endpoint logic)
#   - mcp_secrets.upsert_secret → capture the Secret payload (WS-C connection JSON)
#
#   T-S85-004 — WS-A: three failing probes flip status→error ONLY on the 3rd
#               (threshold), consecutive_failures==3, last_error set.
#   T-S85-005 — WS-A: a success probe recovers status→connected + consecutive_failures 0;
#               last_synced_at is UNCHANGED across every health write (health != discovery).
#   T-S85-011 — WS-B: POST /internal/mcp/list-changed unknown server → 200 ok=false
#               reason=server_not_found (a stale subscription is a normal answer, not 4xx).
#   T-S85-014 — WS-B: two re-syncs in a row for a real server → 2nd is coalesced:true.
#   T-S85-015 — WS-B: malformed body (no server_id) → 422.
#   T-S85-020 — WS-C: materialize_server_secret for identity_mode="service_identity"
#               writes identity_mode + identity_audience into the Secret's connection JSON.
#   T-S85-007 — proxy /internal/health with NO token → 401. [proxy-gated → SKIP if proxy unreachable]
#
# Usage:
#   bash scripts/e2e/suite-85-mcp-phase2.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 7)"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && API_POD=$(kubectl get pods -n "$NAMESPACE" --no-headers 2>/dev/null | grep registry-api | grep Running | awk '{print $1}' | head -1)
[ -z "$API_POD" ] && { echo "FATAL: no running registry-api pod"; exit 1; }

echo "=== Suite 85: MCP as a Tool Source Phase 2 (health / list_changed / identity) ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, httpx, uuid, json
from datetime import datetime, timezone
SUFFIX = os.environ["SUFFIX"]
BASE = "http://localhost:8000/api/v1"
TEAM = "platform"
fails = []

def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond:
        fails.append(tid)

def skip(tid, msg):
    print(f"RESULT {tid} SKIP {msg}")

async def main():
    from db import AsyncSessionLocal
    from models import MCPServer
    from sqlalchemy import text as sqltext
    from config import settings
    import mcp_health, mcp_proxy_client, mcp_discovery, mcp_secrets

    h_name = f"s85-health-{SUFFIX}"
    lc_name = f"s85-lc-{SUFFIX}"
    id_name = f"s85-ident-{SUFFIX}"
    health_id = lc_id = None

    # ════════════════════════════════════════════════════════════════════════════
    # WS-A — health loop threshold / recovery / last_synced_at invariant
    #   T-S85-004 / T-S85-005
    # ════════════════════════════════════════════════════════════════════════════
    mcp_health._backoff_skip.clear()
    FIXED = datetime(2020, 1, 1, tzinfo=timezone.utc)  # a distinctive last_synced_at

    async with AsyncSessionLocal() as s:
        srv = MCPServer(name=h_name, server_url="http://unreachable.invalid:9999/mcp",
                        transport="streamable_http", owner_team=TEAM, status="connected",
                        identity_mode="none", is_external=False, scan_results=True,
                        last_synced_at=FIXED, health_detail={"consecutive_failures": 0})
        s.add(srv); await s.flush(); health_id = srv.id; await s.commit()

    # Capture last_synced_at AS STORED (same DB representation we'll compare against).
    async with AsyncSessionLocal() as s:
        before_synced = (await s.get(MCPServer, health_id)).last_synced_at

    threshold = settings.mcp_health_failure_threshold  # default 3

    async def fake_fail(server_id):
        return {"ok": False, "health_detail": "simulated probe failure"}

    async def fake_ok(server_id):
        return {"ok": True}

    # `_probe_and_apply` does `from mcp_proxy_client import health_check_server` at call
    # time, so patching the module attribute takes effect on the next call.
    mcp_proxy_client.health_check_server = fake_fail
    statuses, cfs, last_error = [], [], None
    for _ in range(threshold):
        async with AsyncSessionLocal() as s:
            srv = await s.get(MCPServer, health_id)
            await mcp_health._probe_and_apply(s, srv)
            await s.commit()
            statuses.append(srv.status)
            cfs.append(int((srv.health_detail or {}).get("consecutive_failures") or 0))
            last_error = (srv.health_detail or {}).get("last_error")

    below = statuses[:threshold - 1]
    check(all(st == "connected" for st in below)
          and statuses[threshold - 1] == "error"
          and cfs == list(range(1, threshold + 1))
          and bool(last_error),
          "T-S85-004",
          f"statuses={statuses} cfs={cfs} last_error={bool(last_error)} "
          f"(flip only on failure #{threshold}, cf reaches {threshold})")

    # A single good probe recovers the server.
    mcp_proxy_client.health_check_server = fake_ok
    async with AsyncSessionLocal() as s:
        srv = await s.get(MCPServer, health_id)
        await mcp_health._probe_and_apply(s, srv)
        await s.commit()
        rec_status = srv.status
        rec_cf = int((srv.health_detail or {}).get("consecutive_failures") or 0)

    async with AsyncSessionLocal() as s:
        after_synced = (await s.get(MCPServer, health_id)).last_synced_at

    check(rec_status == "connected" and rec_cf == 0 and after_synced == before_synced,
          "T-S85-005",
          f"recovered_status={rec_status} cf={rec_cf} "
          f"last_synced_unchanged={after_synced == before_synced} "
          f"(before={before_synced} after={after_synced})")

    # ════════════════════════════════════════════════════════════════════════════
    # WS-B — /internal/mcp/list-changed: unknown / malformed / coalesce
    #   T-S85-011 / T-S85-015 / T-S85-014
    # ════════════════════════════════════════════════════════════════════════════
    async with AsyncSessionLocal() as s:
        lsrv = MCPServer(name=lc_name, server_url="http://unreachable.invalid:9999/mcp",
                         transport="streamable_http", owner_team=TEAM, status="connected",
                         identity_mode="none", is_external=False, scan_results=True)
        s.add(lsrv); await s.flush(); lc_id = lsrv.id; await s.commit()

    # Stub the SHARED discovery core so the endpoint's coalesce guard / server_not_found
    # / 422 handling is what's under test here (not the proxy hop). The endpoint resolves
    # `mcp_discovery._materialize_and_discover` off the module at call time.
    async def fake_materialize(db, server, acknowledge_schema_drift=False):
        server.status = "connected"
        return {"tools_added": 0, "tools_updated": 0, "tools_inactivated": 0}

    mcp_discovery._materialize_and_discover = fake_materialize
    mcp_discovery._last_resync.pop(str(lc_id), None)  # ensure the 1st call is not coalesced

    lc = f"{BASE}/internal/mcp/list-changed"
    async with httpx.AsyncClient(timeout=30) as c:
        unk = await c.post(lc, json={"server_id": str(uuid.uuid4())})
        unk_body = unk.json() if unk.status_code == 200 else {}
        check(unk.status_code == 200 and unk_body.get("ok") is False
              and unk_body.get("reason") == "server_not_found",
              "T-S85-011",
              f"unknown={unk.status_code}/{unk_body} (want 200 ok=false server_not_found)")

        bad = await c.post(lc, json={})  # no server_id
        check(bad.status_code == 422, "T-S85-015", f"malformed={bad.status_code} (want 422)")

        r1 = await c.post(lc, json={"server_id": str(lc_id)})
        r2 = await c.post(lc, json={"server_id": str(lc_id)})
        r2_body = r2.json() if r2.status_code == 200 else {}
        check(r1.status_code == 200 and r2.status_code == 200 and r2_body.get("coalesced") is True,
              "T-S85-014",
              f"first={r1.status_code} second={r2.status_code}/{r2_body} "
              f"(2nd within {settings.mcp_list_changed_min_resync_interval_seconds}s → coalesced)")

    # ════════════════════════════════════════════════════════════════════════════
    # WS-C — per-server Secret carries identity_mode + identity_audience
    #   T-S85-020
    # ════════════════════════════════════════════════════════════════════════════
    captured = {}

    async def fake_upsert(name, namespace, data):
        captured["name"] = name
        captured["namespace"] = namespace
        captured["data"] = data

    # materialize_server_secret calls `upsert_secret(...)` off the mcp_secrets module
    # globals — patch there.
    mcp_secrets.upsert_secret = fake_upsert

    async with AsyncSessionLocal() as s:
        isrv = MCPServer(name=id_name, server_url="https://ext.example.com/mcp",
                         transport="streamable_http", owner_team=TEAM, status="connected",
                         identity_mode="service_identity", is_external=False, scan_results=True,
                         transport_config={"identity_audience": f"aud-{SUFFIX}"})
        s.add(isrv); await s.flush()
        await mcp_secrets.materialize_server_secret(s, isrv)
        await s.commit()

    conn = {}
    try:
        conn = json.loads(captured.get("data", {}).get("connection", "{}"))
    except Exception as e:
        print("secret-parse-err", e)
    check(conn.get("identity_mode") == "service_identity"
          and conn.get("identity_audience") == f"aud-{SUFFIX}",
          "T-S85-020",
          f"secret.connection identity_mode={conn.get('identity_mode')} "
          f"identity_audience={conn.get('identity_audience')}")

    # ── cleanup (best-effort, uniquely suffixed) ────────────────────────────────
    try:
        async with AsyncSessionLocal() as s:
            await s.execute(sqltext("DELETE FROM tools WHERE name LIKE :p"), {"p": f"s85-%{SUFFIX}%"})
            await s.execute(sqltext("DELETE FROM mcp_servers WHERE name LIKE :p"), {"p": f"s85-%{SUFFIX}%"})
            await s.commit()
    except Exception as e:
        print("cleanup-err", str(e)[:120])

    print("FAILS", ",".join(fails) if fails else "NONE")

asyncio.run(main())
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"

# ── T-S85-007 — proxy /internal/health with no token → 401 (proxy-gated) ───────
# Mirrors suite-84's T-S84-007 block. Sends a VALID (all-zeros) UUID so the body
# validates (McpHealthRequest.server_id is a UUID — a malformed value would 422 before
# the handler's auth check runs); with the body valid, the missing bearer token is the
# only failure → a clean 401.
PROXY_FAILED=0
PROXY_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=mcp-proxy \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -n "$PROXY_POD" ]; then
  CODE=$(kubectl exec -n "$NAMESPACE" "$PROXY_POD" -- \
    python3 -c "import httpx; print(httpx.post('http://localhost:8080/internal/health', json={'server_id':'00000000-0000-0000-0000-000000000000'}).status_code)" 2>/dev/null || echo "ERR")
  if [ "$CODE" = "401" ]; then
    echo "RESULT T-S85-007 PASS health-no-token=$CODE"
  else
    echo "RESULT T-S85-007 FAIL health-no-token=$CODE (want 401)"
    PROXY_FAILED=1
  fi
else
  echo "RESULT T-S85-007 SKIP no running mcp-proxy pod (proxy-gated)"
fi

# Verdict: the in-pod block must report "FAILS NONE" AND the proxy check (if it ran)
# must not have failed.
if echo "$RESULT" | grep -q "FAILS NONE" && [ "$PROXY_FAILED" -eq 0 ]; then
  echo "=== Suite 85 PASSED ==="
  exit 0
else
  echo "=== Suite 85 FAILED ==="
  exit 1
fi
