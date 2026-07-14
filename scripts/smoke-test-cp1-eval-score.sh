#!/usr/bin/env bash
# scripts/smoke-test-cp1-eval-score.sh
#
# Eval v2 E-0 — Checkpoint 1 BEHAVIOUR smoke (one scoring door + validator).
#
# Proves:
#   - reactive /playground/eval/score returns composite == dimension_scores.response
#     to the digit (the reducer is identity for a single dimension), and a
#     known-good answer scores >= 0.7
#   - a malformed non-reactive item on dataset create is rejected 422 (the
#     discriminated-union validator fires)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0
FAIL=0
pass() { echo "[PASS] $1"; PASS=$((PASS + 1)); }
fail() { echo "[FAIL] $1"; FAIL=$((FAIL + 1)); }

echo "==> CP1 Eval behaviour smoke — namespace: $NAMESPACE"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "[FATAL] no Running registry-api pod in $NAMESPACE"; exit 1
fi
echo "    Pod: $API_POD"

# 1. reactive /eval/score — composite == dimension_scores.response, and >= 0.7 for a good answer
SCORE_OUT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import urllib.request, urllib.error, json
body = json.dumps({
  "mode": "reactive",
  "item": {"input_message": "What is the capital of France?", "expected_output": "Paris"},
  "input": "What is the capital of France?",
  "response": "The capital of France is **Paris**.",
}).encode()
req = urllib.request.Request("http://localhost:8000/api/v1/playground/eval/score",
  data=body, headers={"Content-Type": "application/json", "X-User-Sub": "eval-runner"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=45)
    print(r.read().decode())
except Exception as e:
    print(json.dumps({"error": str(e)}))
PY
)
echo "    eval/score: $SCORE_OUT"
PARITY=$(echo "$SCORE_OUT" | python3 -c "
import sys, json
d = json.load(sys.stdin)
c = d.get('composite'); r = (d.get('dimension_scores') or {}).get('response')
print('ok' if (c is not None and r is not None and abs(float(c)-float(r)) < 1e-9 and float(c) >= 0.7) else 'bad')
" 2>/dev/null || echo "bad")
if [ "$PARITY" = "ok" ]; then
  pass "reactive /eval/score: composite == dimension_scores.response (to the digit) and good answer >= 0.7"
else
  fail "reactive /eval/score parity/threshold failed ($SCORE_OUT)"
fi

# 2. discriminated-union validator: an item whose explicit `kind` disagrees with
#    the dataset `mode` is an illegal {mode, item-kind} pair -> rejected 422.
VAL_CODE=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import urllib.request, urllib.error, json
# reactive dataset carrying an item explicitly tagged kind='webhook' — the
# discriminated-union validator must reject the mismatch (unrepresentable pair).
body = json.dumps({
  "name": "cp1-eval-bad-item-smoke",
  "mode": "reactive",
  "items": [{"kind": "webhook", "trigger_payload": {"x": 1}, "expected_output": "nope"}],
}).encode()
req = urllib.request.Request("http://localhost:8000/api/v1/playground/datasets",
  data=body, headers={"Content-Type": "application/json", "X-User-Sub": "cp1-eval-smoke"}, method="POST")
try:
    r = urllib.request.urlopen(req, timeout=10)
    # if it unexpectedly succeeded, clean up and report the code
    print(r.getcode())
except urllib.error.HTTPError as e:
    print(e.code)
except Exception:
    print("ERR")
PY
)
if [ "$VAL_CODE" = "422" ]; then
  pass "malformed non-reactive dataset item rejected 422 (discriminated-union validator fires)"
else
  fail "malformed non-reactive item returned $VAL_CODE (expected 422)"
fi

# best-effort cleanup of any dataset that slipped through
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null || true
import urllib.request, json
base = "http://localhost:8000/api/v1/playground/datasets"
try:
    req = urllib.request.Request(base, headers={"X-User-Sub": "cp1-eval-smoke"})
    for ds in json.loads(urllib.request.urlopen(req, timeout=5).read()):
        if ds.get("name") == "cp1-eval-bad-item-smoke":
            urllib.request.urlopen(urllib.request.Request(base + "/" + str(ds["id"]),
                headers={"X-User-Sub": "cp1-eval-smoke"}, method="DELETE"), timeout=5)
except Exception:
    pass
PY

echo ""
echo "================================"
echo "CP1 behaviour smoke: PASS=$PASS FAIL=$FAIL"
echo "================================"
[ "$FAIL" -gt 0 ] && { echo "FAIL"; exit 1; }
echo "PASS"
