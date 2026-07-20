#!/usr/bin/env bash
# scripts/smoke-test-cp1-appid-behaviour.sh — Webhook Application Identity (Decision 30) CP1
# behaviour gate (CP1c).
#
# First-ever live proof that artifact_role_grants delegation works at all (design doc §3):
# grant + application happy path, plus one failure case each, driven via REAL HTTP against
# the RUNNING registry-api — in-pod (this environment has no active port-forward for the
# external nip.io ingress right now; localhost:8000 in-pod is the same pattern every other
# smoke-test-cp1-*-behaviour.sh in this repo already relies on, per the
# "context-storage-local-deploy" memory note on in-pod auth).
#
# Creates its OWN fixture agent/application/grant and deletes them at the end (best-effort),
# matching this repo's e2e-suite fixture-hygiene convention. exit 0 only on all-pass.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-CP1C-APPID-FIXTURE  |  no Running registry-api pod found"
  exit 1
fi

echo "=== CP1c: Webhook Application Identity behaviour gate ==="
echo "    pod: $API_POD"
echo ""

RUN_TAG="cp1appid-$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import json
import urllib.error
import urllib.request

PASS = 0
FAIL = 0

# FastAPI's redirect_slashes answers a missing/extra trailing slash with a 307
# (e.g. POST /api/v1/agents -> /api/v1/agents/). Stock urllib REFUSES to follow a
# 307/308 on POST (raises HTTPError), so re-issue the same method+body+headers to
# the redirect target ourselves. Bounded to one hop (FastAPI redirects once).
class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(
            newurl, data=req.data, method=req.get_method(),
            headers={k: v for k, v in req.header_items()})
_OPENER = urllib.request.build_opener(_Redirect)

def ok(msg):
    global PASS
    print(f"PASS  {msg}")
    PASS += 1

def bad(msg, detail=""):
    global FAIL
    print(f"FAIL  {msg}  |  {detail}")
    FAIL += 1

def call(method, path, token=None, body=None):
    url = f"http://localhost:8000{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = _OPENER.open(req)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}

# --- fixture: platform-admin bearer token -------------------------------------------
import urllib.parse
data = urllib.parse.urlencode({
    "grant_type": "password", "client_id": "agentshield-studio",
    "username": "platform-admin", "password": "PlatformAdmin2024",
}).encode()
req = urllib.request.Request(
    "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token", data=data
)
TOKEN = json.loads(urllib.request.urlopen(req).read())["access_token"]
ok("T-CP1C-APPID-FIXTURE-000 fetched platform-admin bearer token")

# --- fixture: throwaway agent on team 'platform' -------------------------------------
status, agent = call("POST", "/api/v1/agents", TOKEN, {"name": "${RUN_TAG}-agent", "team": "platform"})
if status not in (200, 201):
    bad("T-CP1C-APPID-FIXTURE-001 create fixture agent", f"status={status} body={agent}")
    print(f"=== CP1c summary: PASS={PASS} FAIL={FAIL+1} ===")
    raise SystemExit(1)
AGENT_ID = agent["id"]
ok(f"T-CP1C-APPID-FIXTURE-001 created fixture agent {AGENT_ID}")

APP_ID = None
GRANT_ID = None
try:
    # --- T-CP1C-APPID-001: create application → 201, secret starts with whsec_ ------
    status, app = call("POST", "/api/v1/teams/platform/applications", TOKEN, {"name": "${RUN_TAG}-app"})
    if status == 201 and str(app.get("secret", "")).startswith("whsec_"):
        ok("T-CP1C-APPID-001 POST /teams/platform/applications -> 201, secret starts with whsec_")
        APP_ID = app["id"]
    else:
        bad("T-CP1C-APPID-001 create application", f"status={status} body={app}")

    # --- T-CP1C-APPID-002: grant invoker on the fixture agent → 201 -----------------
    if APP_ID:
        status, grant = call(
            "POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
            {"grantee_type": "application", "grantee_id": APP_ID, "role": "invoker"},
        )
        if status == 201:
            ok("T-CP1C-APPID-002 POST /artifacts/agent/{id}/grants (invoker) -> 201")
            GRANT_ID = grant["id"]
        else:
            bad("T-CP1C-APPID-002 grant invoker", f"status={status} body={grant}")

    # --- T-CP1C-APPID-003: duplicate active grant -> 409 -----------------------------
    if APP_ID:
        status, dup = call(
            "POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
            {"grantee_type": "application", "grantee_id": APP_ID, "role": "invoker"},
        )
        if status == 409:
            ok("T-CP1C-APPID-003 duplicate active grant -> 409")
        else:
            bad("T-CP1C-APPID-003 duplicate active grant", f"status={status} body={dup}")

    # --- T-CP1C-APPID-004: unresolvable grantee_id -> 400 -----------------------------
    status, unresolved = call(
        "POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
        {"grantee_type": "user", "grantee_id": "nonexistent-sub-xyz", "role": "agent-admin"},
    )
    if status == 400:
        ok("T-CP1C-APPID-004 unresolvable grantee_id (user) -> 400")
    else:
        bad("T-CP1C-APPID-004 unresolvable grantee_id", f"status={status} body={unresolved}")

finally:
    # --- cleanup (best-effort) -------------------------------------------------------
    if GRANT_ID:
        call("DELETE", f"/api/v1/artifacts/agent/{AGENT_ID}/grants/{GRANT_ID}", TOKEN)
    if APP_ID:
        call("DELETE", f"/api/v1/teams/platform/applications/{APP_ID}", TOKEN)
    call("DELETE", f"/api/v1/agents/{AGENT_ID}", TOKEN)
    print(f"    (cleanup attempted: grant={GRANT_ID} app={APP_ID} agent={AGENT_ID})")

print(f"=== CP1c summary: PASS={PASS} FAIL={FAIL} ===")
if FAIL:
    raise SystemExit(1)
PY
