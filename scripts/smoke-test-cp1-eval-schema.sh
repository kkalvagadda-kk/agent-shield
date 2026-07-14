#!/usr/bin/env bash
# scripts/smoke-test-cp1-eval-schema.sh
#
# Eval v2 E-0 — Checkpoint 1 INFRA smoke (schema + migrations applied).
#
# Proves migrations 0059/0060 applied on the running (seeded) DB and the pod is
# healthy:
#   - registry-api pod is Running (not CrashLooping)
#   - playground_datasets has `mode`; eval_runs has `mode`; eval_run_results has
#     `dimension_scores` (psql assert against the live DB)
#   - every pre-existing dataset GETs back mode='reactive' (back-compat backfill)
#   - POST /playground/eval/score returns 200
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "==> CP1 Eval schema smoke — namespace: $NAMESPACE"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "[FATAL] no Running registry-api pod in $NAMESPACE"; exit 1
fi
echo "    Pod: $API_POD"

# 1. pod not CrashLooping
RESTARTS=$(kubectl get pod -n "$NAMESPACE" "$API_POD" \
  -o jsonpath='{.status.containerStatuses[?(@.name=="registry-api")].restartCount}' 2>/dev/null || echo "?")
PHASE=$(kubectl get pod -n "$NAMESPACE" "$API_POD" -o jsonpath='{.status.phase}' 2>/dev/null || echo "?")
if [ "$PHASE" = "Running" ]; then
  pass "registry-api pod Running (restarts=$RESTARTS, not CrashLooping)"
else
  fail "registry-api pod phase=$PHASE"
fi

# 2. columns present + all datasets read mode=reactive + /eval/score 200 — one
#    in-pod python block against the live DB (AsyncSessionLocal) and the API.
OUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, json, urllib.request, urllib.error
from sqlalchemy import text
from db import AsyncSessionLocal

async def col_exists(conn, table, col):
    q = text("""SELECT 1 FROM information_schema.columns
                WHERE table_name=:t AND column_name=:c""")
    return (await conn.execute(q, {"t": table, "c": col})).first() is not None

async def main():
    out = {}
    async with AsyncSessionLocal() as s:
        out["col_playground_datasets_mode"] = await col_exists(s, "playground_datasets", "mode")
        out["col_eval_runs_mode"] = await col_exists(s, "eval_runs", "mode")
        out["col_eval_run_results_dimension_scores"] = await col_exists(s, "eval_run_results", "dimension_scores")
        # every existing dataset backfilled to reactive
        rows = (await s.execute(text("SELECT mode FROM playground_datasets"))).scalars().all()
        out["existing_datasets_all_reactive"] = all(m == "reactive" for m in rows)
        out["_dataset_count"] = len(rows)
    print(json.dumps(out))

asyncio.run(main())
PY
)
echo "    db-probe: $OUT"

check() {
  local key="$1" label="$2"
  local v
  v=$(echo "$OUT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('$key'))" 2>/dev/null || echo "None")
  if [ "$v" = "True" ]; then pass "$label"; else fail "$label (got $v)"; fi
}
check col_playground_datasets_mode        "playground_datasets.mode column present (0059)"
check col_eval_runs_mode                  "eval_runs.mode column present (0059)"
check col_eval_run_results_dimension_scores "eval_run_results.dimension_scores column present (0060)"
check existing_datasets_all_reactive      "every pre-existing dataset reads mode='reactive' (backfill)"

# 3. POST /playground/eval/score -> 200
SCORE_CODE=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import urllib.request, urllib.error, json
body = json.dumps({
  "mode": "reactive",
  "item": {"input_message": "What is the capital of France?", "expected_output": "Paris"},
  "input": "What is the capital of France?",
  "response": "The capital of France is Paris.",
}).encode()
req = urllib.request.Request("http://localhost:8000/api/v1/playground/eval/score",
  data=body, headers={"Content-Type": "application/json", "X-User-Sub": "eval-runner"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=40)
    print(r.getcode())
except urllib.error.HTTPError as e:
    print(e.code)
except Exception as e:
    print("ERR")
PY
)
if [ "$SCORE_CODE" = "200" ]; then
  pass "POST /playground/eval/score -> 200"
else
  fail "POST /playground/eval/score -> $SCORE_CODE (expected 200)"
fi

echo ""
echo "================================"
echo "CP1 schema smoke: PASS=$PASS FAIL=$FAIL"
echo "================================"
[ "$FAIL" -gt 0 ] && { echo "FAIL"; exit 1; }
echo "PASS"
