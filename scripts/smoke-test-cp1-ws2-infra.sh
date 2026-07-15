#!/usr/bin/env bash
# scripts/smoke-test-cp1-ws2-infra.sh
#
# WS-2 Checkpoint 1 — INFRA smoke (CP1b). Proves the deploy landed the WS-2
# identity plumbing at the infra layer (no behaviour yet — that's CP1c):
#
#   T-CP1B-001 — registry-api pods Running, none in CrashLoopBackOff
#   T-CP1B-002 — agent_triggers.armed_by column exists (information_schema)
#   T-CP1B-003 — `opa test` on the shared policy dir exits 0 (T002/T003 green)
#
# opa test runs via the local openpolicyagent/opa:0.69.0-static image (test tooling,
# not a deploy — allowed). Everything else reads the live cluster.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPA_IMAGE="openpolicyagent/opa:0.69.0-static"

PASS=0; FAIL=0
ok()   { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad()  { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-2 CP1b: infra smoke ==="
echo "  namespace: $NAMESPACE"
echo ""

# ── T-CP1B-001 — registry-api pods Running, no CrashLoopBackOff ───────────────
PODS_JSON=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{range .items[*]}{.metadata.name}{"="}{.status.phase}{";"}{range .status.containerStatuses[*]}{.state.waiting.reason}{","}{end}{"\n"}{end}' 2>/dev/null || true)
RUNNING_COUNT=$(echo "$PODS_JSON" | grep -c "=Running;" || true)
CRASH_COUNT=$(echo "$PODS_JSON" | grep -c "CrashLoopBackOff" || true)
if [ "$RUNNING_COUNT" -ge 1 ] && [ "$CRASH_COUNT" -eq 0 ]; then
  ok "T-CP1B-001 registry-api pods healthy" "running=$RUNNING_COUNT crashloop=$CRASH_COUNT"
else
  bad "T-CP1B-001 registry-api pods healthy" "running=$RUNNING_COUNT crashloop=$CRASH_COUNT :: $PODS_JSON"
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)

# ── T-CP1B-002 — agent_triggers.armed_by column exists ────────────────────────
if [ -z "$API_POD" ]; then
  bad "T-CP1B-002 agent_triggers.armed_by column exists" "no running registry-api pod to query"
else
  COL=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
    'cd /app && PYTHONPATH=/app python3 - <<PY
import asyncio
from sqlalchemy import text
from db import AsyncSessionLocal
async def main():
    async with AsyncSessionLocal() as s:
        r = await s.execute(text(
            "select data_type from information_schema.columns "
            "where table_name=:t and column_name=:c"),
            {"t": "agent_triggers", "c": "armed_by"})
        row = r.first()
        print(f"FOUND:{row[0]}" if row else "MISSING")
asyncio.run(main())
PY' 2>/dev/null | tr -d "[:space:]" || true)
  if echo "$COL" | grep -q "^FOUND:"; then
    ok "T-CP1B-002 agent_triggers.armed_by column exists" "type=${COL#FOUND:}"
  else
    bad "T-CP1B-002 agent_triggers.armed_by column exists" "result=$COL"
  fi
fi

# ── T-CP1B-003 — opa test on the shared policy dir exits 0 ────────────────────
OPA_OUT=$(docker run --rm -v "$REPO_ROOT/services/registry-api/opa_policy:/policy:ro" \
  "$OPA_IMAGE" test /policy 2>&1) && OPA_RC=0 || OPA_RC=$?
OPA_SUMMARY=$(echo "$OPA_OUT" | grep -E "^PASS:|^FAIL:|^ERROR" | tail -1)
echo "  opa test summary: ${OPA_SUMMARY:-<none>}"
if [ "$OPA_RC" -eq 0 ]; then
  ok "T-CP1B-003 opa test green (identity floor + risk gates)" "${OPA_SUMMARY:-exit0}"
else
  bad "T-CP1B-003 opa test green (identity floor + risk gates)" "rc=$OPA_RC :: $(echo "$OPA_OUT" | tail -5 | tr '\n' ' ')"
fi

echo ""
echo "=== CP1b summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "CP1b INFRA SMOKE FAILED"; exit 1; fi
echo "CP1b INFRA SMOKE PASSED"
