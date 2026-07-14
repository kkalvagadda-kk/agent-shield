#!/usr/bin/env bash
# scripts/e2e/suite-63-echo-endpoint.sh
#
# E2E Suite 63: in-cluster /echo endpoint — the httpbin.org replacement.
#
# httpbin.org (an external service) backed several demo/test HTTP tools
# (http_echo seed, refund_action on wf-payout, suite-18 OPA tools). When it went
# down (503 / timeout) a governed tool call CRASHED the durable member and failed
# the workflow (docs/debugging/011). registry-api now hosts an in-cluster /echo
# that reflects the request — no external dependency can ever fail those tools.
#
# What it proves (real HTTP calls against the running registry-api pod — /echo is
# unauthenticated like /health, so no auth setup needed):
#   T-S63-001 — GET  /echo                 -> 200, {ok:true, method:"GET"}
#   T-S63-002 — POST /echo {json body}     -> 200, body reflected under "json"
#   T-S63-003 — GET  /echo/a/b?x=1&y=2      -> 200, path + query args reflected
#   T-S63-004 — POST /echo accepts a body-less POST (what a schema-driven refund
#               tool sends) -> 200 (the exact call that 503'd against httpbin)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 63: in-cluster /echo endpoint (httpbin.org replacement) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import httpx
BASE = "http://localhost:8000"
out = {}
with httpx.Client(base_url=BASE, timeout=10) as c:
    # 001 — GET /echo
    r = c.get("/echo")
    j = r.json() if r.status_code == 200 else {}
    out["001_get_echo_200"] = (r.status_code == 200 and j.get("ok") is True and j.get("method") == "GET")

    # 002 — POST /echo with a JSON body reflected under "json"
    r = c.post("/echo", json={"amount": 50.0, "order_id": "1234"})
    j = r.json() if r.status_code == 200 else {}
    out["002_post_echo_reflects_body"] = (
        r.status_code == 200 and j.get("method") == "POST"
        and isinstance(j.get("json"), dict) and j["json"].get("order_id") == "1234"
    )

    # 003 — path + query reflected
    r = c.get("/echo/a/b", params={"x": "1", "y": "2"})
    j = r.json() if r.status_code == 200 else {}
    out["003_path_and_query_reflected"] = (
        r.status_code == 200 and j.get("path") == "/a/b"
        and j.get("args", {}).get("x") == "1" and j.get("args", {}).get("y") == "2"
    )

    # 004 — body-less POST (a schema-driven governed tool with no body still gets 200;
    #        this is the exact shape that 503'd against httpbin.org and crashed wf-payout)
    r = c.post("/echo")
    out["004_bodyless_post_200"] = (r.status_code == 200 and r.json().get("ok") is True)

for k, v in out.items():
    print(("PASS" if v else "FAIL"), k)
PY
)

echo "$RESULT"
echo ""
if echo "$RESULT" | grep -q "^FAIL"; then
  echo "❌ Suite 63 FAILED (the /echo endpoint contract broke — httpbin replacement not working)"
  exit 1
fi
if [ "$(echo "$RESULT" | grep -c '^PASS')" -lt 4 ]; then
  echo "❌ Suite 63 INCONCLUSIVE (expected 4 PASS lines)"
  exit 1
fi
echo "✅ Suite 63 PASSED — in-cluster /echo works for GET/POST/path/query + body-less POST"
