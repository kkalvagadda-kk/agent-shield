#!/usr/bin/env bash
# =============================================================================
# Deferred — written, NOT executed; run on deploy.
# Requires a live cluster.
# =============================================================================
# CP2b — MCP Phase 2 (WS-B list_changed): infrastructure smoke.
#
# Proves the _materialize_and_discover EXTRACTION (T019/T020) was behaviour-neutral
# and the new re-sync endpoint's error contract:
#   - suite-84 still green (the whole Phase-1 MCP API surface is unperturbed)
#   - POST /api/v1/internal/mcp/list-changed  unknown server  → 200 ok=false
#     reason=server_not_found
#   - POST /api/v1/internal/mcp/list-changed  malformed body  → 422
#
# Exit 0 on full pass, non-zero on the first failure. Ends with `echo "PASS"`.
set -euo pipefail

echo "=== Checkpoint MCP2-CP2: list_changed infra smoke (suite-84 green + unknown/malformed) ==="

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

fail() { echo "FAIL: $1" >&2; exit 1; }

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || fail "no Running registry-api pod"
echo "  api=$API_POD"

# ── 1. suite-84 still green (extraction is behaviour-neutral for Phase 1) ──────
echo "--- suite-84 (Phase-1 MCP API surface) still green ---"
NAMESPACE="$NAMESPACE" bash "${SCRIPT_DIR}/e2e/suite-84-mcp-tools.sh" \
  || fail "suite-84 regressed after the _materialize_and_discover extraction"
echo "  OK: suite-84 green"

# ── 2. /internal/mcp/list-changed error contract (unknown → 200 ok=false; 422) ─
echo "--- POST /internal/mcp/list-changed unknown-server + malformed ---"
OUT="$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY'
import asyncio, httpx, uuid, json, sys
BASE = "http://localhost:8000/api/v1"
LC = f"{BASE}/internal/mcp/list-changed"
def check(cond, tid, msg):
    print(f"RESULT {tid} {'PASS' if cond else 'FAIL'} {msg}")
    if not cond: sys.exit(1)
async def main():
    async with httpx.AsyncClient(timeout=30) as c:
        unk = await c.post(LC, json={"server_id": str(uuid.uuid4())})
        body = unk.json() if unk.status_code == 200 else {}
        check(unk.status_code == 200 and body.get("ok") is False
              and body.get("reason") == "server_not_found",
              "CP2B-unknown", f"status={unk.status_code} body={body}")
        bad = await c.post(LC, json={})
        check(bad.status_code == 422, "CP2B-malformed", f"status={bad.status_code} (want 422)")
    print("ALLPASS")
asyncio.run(main())
PY
)" || { echo "$OUT"; fail "list-changed error-contract assertions failed"; }
echo "$OUT"
echo "$OUT" | grep -q "ALLPASS" || fail "list-changed error-contract block did not complete"
echo "  OK: unknown → 200 ok=false server_not_found; malformed → 422"

echo "PASS"
