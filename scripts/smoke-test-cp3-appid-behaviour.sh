#!/usr/bin/env bash
# scripts/smoke-test-cp3-appid-behaviour.sh — Webhook Application Identity (Decision 30)
# Checkpoint 3 behaviour gate (CP3c) — UI-flow proxy for the Studio invoke-access surface.
#
# CP3 proves the Studio UX (Phases 6-8). This behaviour gate re-runs, via real HTTP, the
# exact backend flow InvokeAccessPanel (T016/T018/T019) depends on: create a team
# application -> grant it the `invoker` role on an agent -> the agent's webhook trigger
# auth_mode flips to 'client_signed'. That flip is what the trigger card's auth_mode badge
# renders, and what ApplicationsPage + the grant picker are for. The full signed-webhook
# e2e (revoked/disabled 401s) is CP2c's job — this is the narrower UI-contract proxy that
# the Playwright specs (T031) then prove in a real browser.
#
# Driven in-pod (no external port-forward): registry-api at localhost:8000, same pattern
# as smoke-test-cp2-appid-behaviour.sh. Creates its own fixtures, best-effort cleanup.
#
#   T-CP3C-APPID-001  a fresh webhook trigger is born auth_mode='token'
#   T-CP3C-APPID-002  create team application -> 201 with a whsec_ secret shown once
#   T-CP3C-APPID-003  grant the application invoker on the agent -> 201
#   T-CP3C-APPID-004  the invoker grant flips the webhook trigger auth_mode -> 'client_signed'
#   T-CP3C-APPID-005  list grants shows the invoker grant with grantee_label = app name
#                     (what ArtifactGrantsList/InvokeAccessPanel render)
#   T-CP3C-APPID-006  revoke the grant -> 204; it no longer appears in the active grants list
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-CP3C-APPID-FIXTURE  |  no Running registry-api pod found"
  exit 1
fi

echo "=== CP3c: Webhook Application Identity behaviour gate (UI-flow proxy) ==="
echo "    pod: $API_POD"
echo ""

RUN_TAG="cp3appid-$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import json, urllib.error, urllib.parse, urllib.request

PASS = 0
FAIL = 0
def ok(m):
    global PASS; print(f"PASS  {m}"); PASS += 1
def bad(m, d=""):
    global FAIL; print(f"FAIL  {m}  |  {d}"); FAIL += 1

API = "http://localhost:8000"

# FastAPI redirect_slashes answers a missing/extra trailing slash with a 307 on POST;
# stock urllib won't re-issue method+body, so follow one hop ourselves.
class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(
            newurl, data=req.data, method=req.get_method(),
            headers={k: v for k, v in req.header_items()})
_OPENER = urllib.request.build_opener(_Redirect)

def call(method, path, token=None, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = _OPENER.open(req, timeout=15)
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}

# --- fixture: platform-admin bearer token -------------------------------------------
data = urllib.parse.urlencode({
    "grant_type": "password", "client_id": "agentshield-studio",
    "username": "platform-admin", "password": "PlatformAdmin2024",
}).encode()
req = urllib.request.Request(
    "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token", data=data)
TOKEN = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
ok("T-CP3C-APPID-FIXTURE-000 fetched platform-admin bearer token")

AGENT_NAME = "${RUN_TAG}-agent"
status, agent = call("POST", "/api/v1/agents", TOKEN, {"name": AGENT_NAME, "team": "platform"})
if status not in (200, 201):
    bad("T-CP3C-APPID-FIXTURE-001 create fixture agent", f"status={status} body={agent}")
    print(f"=== CP3c summary: PASS={PASS} FAIL={FAIL+1} ===")
    raise SystemExit(1)
AGENT_ID = agent["id"]
ok(f"T-CP3C-APPID-FIXTURE-001 created fixture agent {AGENT_ID}")

APP_ID = GRANT_ID = None
APP_NAME = "${RUN_TAG}-app"
try:
    status, trig = call("POST", f"/api/v1/agents/{AGENT_NAME}/triggers", TOKEN,
                        {"trigger_type": "webhook", "enabled": True})
    if status not in (200, 201) or not trig.get("token"):
        bad("T-CP3C-APPID-FIXTURE-002 create webhook trigger", f"status={status} body={trig}")
        raise SystemExit(1)

    if trig.get("auth_mode") == "token":
        ok("T-CP3C-APPID-001 webhook trigger is born auth_mode='token'")
    else:
        bad("T-CP3C-APPID-001 trigger born auth_mode='token'", f"auth_mode={trig.get('auth_mode')}")

    status, a = call("POST", "/api/v1/teams/platform/applications", TOKEN, {"name": APP_NAME})
    if status == 201 and str(a.get("secret", "")).startswith("whsec_"):
        APP_ID = a["id"]
        ok("T-CP3C-APPID-002 created application -> 201, secret shown once (whsec_)")
    else:
        bad("T-CP3C-APPID-002 create application", f"status={status} body={a}"); raise SystemExit(1)

    status, g = call("POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
                     {"grantee_type": "application", "grantee_id": APP_ID, "role": "invoker"})
    if status == 201:
        GRANT_ID = g["id"]; ok("T-CP3C-APPID-003 granted application invoker -> 201")
    else:
        bad("T-CP3C-APPID-003 grant invoker", f"status={status} body={g}"); raise SystemExit(1)

    status, trigs = call("GET", f"/api/v1/agents/{AGENT_NAME}/triggers", TOKEN)
    modes = [t.get("auth_mode") for t in trigs] if isinstance(trigs, list) else []
    if modes and all(m == "client_signed" for m in modes):
        ok("T-CP3C-APPID-004 invoker grant flipped webhook trigger auth_mode -> 'client_signed'")
    else:
        bad("T-CP3C-APPID-004 auth_mode flip to client_signed", f"modes={modes}")

    status, grants = call("GET", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN)
    inv = [x for x in grants if x.get("role") == "invoker"] if isinstance(grants, list) else []
    if inv and inv[0].get("grantee_label") == APP_NAME:
        ok("T-CP3C-APPID-005 grants list shows invoker grant with grantee_label = app name")
    else:
        bad("T-CP3C-APPID-005 grant list label", f"grants={grants}")

    status, _ = call("DELETE", f"/api/v1/artifacts/agent/{AGENT_ID}/grants/{GRANT_ID}", TOKEN)
    if status in (200, 204):
        status, grants = call("GET", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN)
        still = [x for x in grants if x.get("id") == GRANT_ID] if isinstance(grants, list) else []
        if not still:
            ok("T-CP3C-APPID-006 revoke -> 204; grant gone from active list")
            GRANT_ID = None
        else:
            bad("T-CP3C-APPID-006 revoke removes grant", f"still present: {still}")
    else:
        bad("T-CP3C-APPID-006 revoke grant", f"status={status}")

finally:
    # best-effort cleanup
    if GRANT_ID:
        call("DELETE", f"/api/v1/artifacts/agent/{AGENT_ID}/grants/{GRANT_ID}", TOKEN)
    if APP_ID:
        call("DELETE", f"/api/v1/teams/platform/applications/{APP_ID}", TOKEN)
    call("DELETE", f"/api/v1/agents/{AGENT_NAME}", TOKEN)

print(f"=== CP3c summary: PASS={PASS} FAIL={FAIL} ===")
raise SystemExit(0 if FAIL == 0 else 1)
PY
