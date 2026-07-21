#!/usr/bin/env bash
# scripts/e2e/suite-76-webhook-client-signing.sh
#
# E2E Suite 76: WS-4 → Decision 30 CUTOVER gate.
#
# WS-4's per-trigger webhook_clients registration is RETIRED (Decision 30). The reusable
# webhook identity is now an `application` granted the `invoker` role on an artifact; the
# full signed-invoke path (real HMAC → real event-gateway → 202/matched row, plus
# revoked-grant / disabled-app / bad-signature uniform-401, rotate, and multi-artifact
# isolation) is proven by **suite-83-webhook-applications.sh**. This suite keeps the two
# things that are specific to the CUTOVER and NOT covered by suite-83:
#
#   T-S76-000 — PARITY GREP (the anti-drift assertion, the repo's #1 bug class): still
#               exactly ONE `def verify_webhook_auth`, TWO call sites, ZERO per-handler
#               copies, ZERO stale-timestamp oracle in main.py. Cheap, cluster-free.
#   T-S76-010 — the RETIREMENT is live: the write endpoints
#               POST/PATCH/DELETE /api/v1/triggers/{id}/clients return 410 Gone with the
#               redirect message, while GET /api/v1/triggers/{id}/clients is UNTOUCHED
#               (still 200) — exactly the T011 contract, so no caller silently 201s into
#               a dead end after the gateway cutover.
#
# (History: T-S76-001..009 tested client registration + signing against the old model.
# Those claims live on, retargeted to applications + invoker grants, as suite-83's
# T-SYY-001..010 — the one-generic-surface replacement, not a second parallel path.)
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

NAMESPACE="${NAMESPACE:-agentshield-platform}"

echo "=== Suite 76: WS-4 → Decision 30 cutover gate ==="

PASS=0; FAIL=0
bpass() { echo "PASS  $1"; PASS=$((PASS+1)); }
bfail() { echo "FAIL  $1"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------------------
# T-S76-000 — PARITY GREP. Runs first: cheap, needs no cluster, catches the failure
# mode WS-4 exists to prevent (two hook handlers drifting apart).
# ---------------------------------------------------------------------------
GW_MAIN="services/event-gateway/main.py"
GW_AUTH="services/event-gateway/webhook_auth.py"
N_DEF=$(grep -c "def verify_webhook_auth" "$GW_AUTH" || true)
N_CALL=$(grep -c "verify_webhook_auth(" "$GW_MAIN" || true)
N_COPY=$(grep -c "def verify_webhook_auth" "$GW_MAIN" || true)
N_ORACLE=$(grep -c "stale webhook timestamp" "$GW_MAIN" || true)
if [ "$N_DEF" = "1" ] && [ "$N_CALL" = "2" ] && [ "$N_COPY" = "0" ] && [ "$N_ORACLE" = "0" ]; then
  bpass "T-S76-000 parity: 1 def / 2 call sites / 0 per-handler copies / 0 stale-ts oracle"
else
  bfail "T-S76-000 parity VIOLATED  |  def=$N_DEF (want 1) calls=$N_CALL (want 2) copies=$N_COPY (want 0) oracle=$N_ORACLE (want 0)"
fi

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No running registry-api pod in namespace $NAMESPACE — the gate cannot be proven"
  echo "Suite 76 FAILED (fixture unreachable — never a skip)"
  exit 1
fi
echo "  Pod: $API_POD"
echo ""

# ---------------------------------------------------------------------------
# T-S76-010 — the retirement is LIVE (in-pod, real HTTP).
# ---------------------------------------------------------------------------
RUN_TAG="s76-$(date +%s)"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import json, urllib.error, urllib.parse, urllib.request

P = 0; F = 0
def ok(m):
    global P; print(f"PASS  {m}"); P += 1
def no(m, d=""):
    global F; print(f"FAIL  {m}  |  {d}"); F += 1

API = "http://localhost:8000"
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"

class _R(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, data=req.data, method=req.get_method(),
                                      headers={k: v for k, v in req.header_items()})
_O = urllib.request.build_opener(_R)

def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        r = _O.open(req, timeout=15); raw = r.read()
        return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}

data = urllib.parse.urlencode({"grant_type": "password", "client_id": "agentshield-studio",
                               "username": "platform-admin", "password": "PlatformAdmin2024"}).encode()
TOKEN = json.loads(urllib.request.urlopen(urllib.request.Request(KC, data=data), timeout=15).read())["access_token"]

AGENT = "${RUN_TAG}-agent"
st, a = call("POST", "/api/v1/agents", TOKEN, {"name": AGENT, "team": "platform"})
if st not in (200, 201):
    no("T-S76-010 fixture agent", f"{st} {a}"); print(f"=== Suite 76 (pod leg): PASS={P} FAIL={F+1} ==="); raise SystemExit(1)
st, t = call("POST", f"/api/v1/agents/{AGENT}/triggers", TOKEN, {"trigger_type": "webhook", "enabled": True})
TID = t.get("id")
try:
    # POST /clients -> 410 with the redirect message
    st, r = call("POST", f"/api/v1/triggers/{TID}/clients", TOKEN, {"client_id": "x"})
    msg = json.dumps(r)
    if st == 410 and "retired" in msg:
        ok("T-S76-010a POST /triggers/{id}/clients -> 410 (retired, points at applications)")
    else:
        no("T-S76-010a POST /clients -> 410", f"{st} {msg[:160]}")
    # PATCH /clients/{id} -> 410
    st, r = call("PATCH", f"/api/v1/triggers/{TID}/clients/x", TOKEN, {"enabled": False})
    ok("T-S76-010b PATCH /clients -> 410") if st == 410 else no("T-S76-010b PATCH -> 410", f"{st} {r}")
    # DELETE /clients/{id} -> 410
    st, r = call("DELETE", f"/api/v1/triggers/{TID}/clients/x", TOKEN)
    ok("T-S76-010c DELETE /clients -> 410") if st == 410 else no("T-S76-010c DELETE -> 410", f"{st} {r}")
    # GET /clients -> untouched (200)
    st, r = call("GET", f"/api/v1/triggers/{TID}/clients", TOKEN)
    ok("T-S76-010d GET /clients still 200 (read path untouched by cutover)") if st == 200 else no("T-S76-010d GET -> 200", f"{st} {r}")
finally:
    call("DELETE", f"/api/v1/agents/{AGENT}", TOKEN)

print(f"=== Suite 76 (pod leg): PASS={P} FAIL={F} ===")
raise SystemExit(0 if F == 0 else 1)
PY
POD_RC=$?
[ "$POD_RC" -eq 0 ] && bpass "T-S76-010 retirement live (POST/PATCH/DELETE -> 410, GET -> 200)" \
  || bfail "T-S76-010 retirement checks failed (see pod leg above)"

echo ""
echo "=== Suite 76 summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -eq 0 ]
