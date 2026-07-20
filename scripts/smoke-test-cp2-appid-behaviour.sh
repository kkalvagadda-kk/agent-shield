#!/usr/bin/env bash
# scripts/smoke-test-cp2-appid-behaviour.sh — Webhook Application Identity (Decision 30)
# Checkpoint 2 behaviour gate (CP2c).
#
# The FIRST live end-to-end proof of the signed-webhook path through the NEW identity
# model: application -> invoker grant -> auth_mode flips to client_signed -> event-gateway
# resolves applications+artifact_role_grants -> HMAC verify -> dispatch. Plus the two
# security invariants the cutover must preserve: a revoked grant and a disabled
# application both deny, with a BYTE-IDENTICAL uniform 401 (no enumeration oracle).
#
# Driven via REAL HTTP in-pod (this environment has no active port-forward to the
# external ingress): registry-api at localhost:8000, the gateway over its real Service
# DNS agentshield-event-gateway:8091 — the same in-pod pattern every other
# smoke-test-cp1-*-behaviour.sh uses. Creates its OWN fixtures and deletes them at the
# end (best-effort). exit 0 only on all-pass.
#
#   T-CP2C-APPID-001  webhook trigger is born auth_mode='token'
#   T-CP2C-APPID-002  first application invoker grant flips auth_mode -> 'client_signed'
#                     (T-SYY-002; the create_grant fix in registry-api 0.2.222)
#   T-CP2C-APPID-003  valid signed webhook (granted + enabled app) is ACCEPTED past auth
#                     (202 dispatched, or 502 dispatch-unavailable — never 401; the run
#                     dispatch itself is out of scope, same boundary the bash suites accept)
#   T-CP2C-APPID-004  ...and it committed an agent_events row status='matched' with the
#                     VERIFIED client_id stamped (proves per-application identity resolved)
#   T-CP2C-APPID-005  bad signature (valid client, still granted+enabled) -> 401
#   T-CP2C-APPID-006  revoked invoker grant -> same signed request now -> 401
#   T-CP2C-APPID-007  disabled application (grant still active) -> its signed request -> 401
#   T-CP2C-APPID-008  the three 401 bodies are BYTE-IDENTICAL (uniform-401 oracle closed)
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-CP2C-APPID-FIXTURE  |  no Running registry-api pod found"
  exit 1
fi

echo "=== CP2c: Webhook Application Identity behaviour gate (signed webhook e2e) ==="
echo "    pod: $API_POD"
echo ""

RUN_TAG="cp2appid-$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import asyncio
import hashlib
import hmac
import json
import time
import urllib.error
import urllib.parse
import urllib.request

PASS = 0
FAIL = 0

def ok(msg):
    global PASS
    print(f"PASS  {msg}")
    PASS += 1

def bad(msg, detail=""):
    global FAIL
    print(f"FAIL  {msg}  |  {detail}")
    FAIL += 1

API = "http://localhost:8000"
GW = "http://agentshield-event-gateway:8091"

# FastAPI redirect_slashes answers a missing/extra trailing slash with a 307
# (POST /api/v1/agents -> /api/v1/agents/); stock urllib won't follow a 307/308 on
# POST, so re-issue method+body+headers to the target ourselves (one hop).
class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(
            newurl, data=req.data, method=req.get_method(),
            headers={k: v for k, v in req.header_items()})
_OPENER = urllib.request.build_opener(_Redirect)

def call(method, path, token=None, body=None):
    """JSON call to registry-api. Returns (status, parsed_json)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        resp = _OPENER.open(req, timeout=15)
        return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw)
        except Exception:
            return e.code, {"raw": raw.decode(errors="replace")}

def sign(secret, body_bytes, ts):
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body_bytes, hashlib.sha256).hexdigest()
    return {"X-Timestamp": str(ts), "X-Signature": f"sha256={mac}"}

def gw_post(agent_name, path_token, client_id, headers_extra, body_bytes):
    """Raw POST to the gateway hook. Returns (status, raw_bytes)."""
    req = urllib.request.Request(
        f"{GW}/hooks/{agent_name}/{path_token}", data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Client-Id", client_id)
    for k, v in headers_extra.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

async def matched_event_count(agent_name, client_id):
    from sqlalchemy import text
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        return (await s.execute(text(
            "SELECT count(*) FROM agent_events "
            "WHERE agent_name = :an AND client_id = :cid AND status = 'matched'"
        ), {"an": agent_name, "cid": client_id})).scalar()

# --- fixture: platform-admin bearer token -------------------------------------------
data = urllib.parse.urlencode({
    "grant_type": "password", "client_id": "agentshield-studio",
    "username": "platform-admin", "password": "PlatformAdmin2024",
}).encode()
req = urllib.request.Request(
    "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token", data=data)
TOKEN = json.loads(urllib.request.urlopen(req, timeout=15).read())["access_token"]
ok("T-CP2C-APPID-FIXTURE-000 fetched platform-admin bearer token")

# --- fixture: agent + webhook trigger on team 'platform' ----------------------------
AGENT_NAME = "${RUN_TAG}-agent"
status, agent = call("POST", "/api/v1/agents", TOKEN, {"name": AGENT_NAME, "team": "platform"})
if status not in (200, 201):
    bad("T-CP2C-APPID-FIXTURE-001 create fixture agent", f"status={status} body={agent}")
    print(f"=== CP2c summary: PASS={PASS} FAIL={FAIL+1} ===")
    raise SystemExit(1)
AGENT_ID = agent["id"]
ok(f"T-CP2C-APPID-FIXTURE-001 created fixture agent {AGENT_ID}")

APP1_ID = APP2_ID = GRANT1_ID = GRANT2_ID = None
APP1_NAME = "${RUN_TAG}-app1"
APP2_NAME = "${RUN_TAG}-app2"
try:
    status, trig = call("POST", f"/api/v1/agents/{AGENT_NAME}/triggers", TOKEN,
                        {"trigger_type": "webhook", "enabled": True})
    if status not in (200, 201) or not trig.get("token"):
        bad("T-CP2C-APPID-FIXTURE-002 create webhook trigger", f"status={status} body={trig}")
        raise SystemExit(1)
    WHT = trig["token"]

    # T-CP2C-APPID-001: born 'token'
    if trig.get("auth_mode") == "token":
        ok("T-CP2C-APPID-001 webhook trigger is born auth_mode='token'")
    else:
        bad("T-CP2C-APPID-001 trigger born auth_mode='token'", f"auth_mode={trig.get('auth_mode')}")

    # --- create two applications -----------------------------------------------------
    status, a1 = call("POST", "/api/v1/teams/platform/applications", TOKEN, {"name": APP1_NAME})
    if status == 201 and str(a1.get("secret", "")).startswith("whsec_"):
        APP1_ID, SECRET1 = a1["id"], a1["secret"]
        ok("T-CP2C-APPID-FIXTURE-003 created application app1 (secret whsec_)")
    else:
        bad("T-CP2C-APPID-FIXTURE-003 create app1", f"status={status} body={a1}"); raise SystemExit(1)

    status, a2 = call("POST", "/api/v1/teams/platform/applications", TOKEN, {"name": APP2_NAME})
    if status == 201 and str(a2.get("secret", "")).startswith("whsec_"):
        APP2_ID, SECRET2 = a2["id"], a2["secret"]
        ok("T-CP2C-APPID-FIXTURE-004 created application app2 (secret whsec_)")
    else:
        bad("T-CP2C-APPID-FIXTURE-004 create app2", f"status={status} body={a2}"); raise SystemExit(1)

    # --- grant both apps invoker on the agent (first grant flips auth_mode) ----------
    status, g1 = call("POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
                      {"grantee_type": "application", "grantee_id": APP1_ID, "role": "invoker"})
    if status == 201:
        GRANT1_ID = g1["id"]; ok("T-CP2C-APPID-FIXTURE-005 granted app1 invoker (201)")
    else:
        bad("T-CP2C-APPID-FIXTURE-005 grant app1 invoker", f"status={status} body={g1}"); raise SystemExit(1)

    status, g2 = call("POST", f"/api/v1/artifacts/agent/{AGENT_ID}/grants", TOKEN,
                      {"grantee_type": "application", "grantee_id": APP2_ID, "role": "invoker"})
    if status == 201:
        GRANT2_ID = g2["id"]; ok("T-CP2C-APPID-FIXTURE-006 granted app2 invoker (201)")
    else:
        bad("T-CP2C-APPID-FIXTURE-006 grant app2 invoker", f"status={status} body={g2}"); raise SystemExit(1)

    # --- T-CP2C-APPID-002: auth_mode flipped to client_signed ------------------------
    status, trigs = call("GET", f"/api/v1/agents/{AGENT_NAME}/triggers", TOKEN)
    modes = [t.get("auth_mode") for t in trigs] if isinstance(trigs, list) else []
    if modes and all(m == "client_signed" for m in modes):
        ok("T-CP2C-APPID-002 invoker grant flipped webhook trigger auth_mode -> 'client_signed' (T-SYY-002)")
    else:
        bad("T-CP2C-APPID-002 auth_mode flip to client_signed", f"modes={modes}")

    # --- T-CP2C-APPID-003: valid signed webhook is accepted past auth ----------------
    body = json.dumps({"event_type": "cp2.ping"}).encode()
    ts = int(time.time())
    st, raw = gw_post(AGENT_NAME, WHT, APP1_NAME, sign(SECRET1, body, ts), body)
    if st in (202, 502):
        ok(f"T-CP2C-APPID-003 valid signed webhook accepted past auth (HTTP {st}, not 401)")
    else:
        bad("T-CP2C-APPID-003 valid signed webhook accepted", f"status={st} body={raw[:200]!r}")

    # --- T-CP2C-APPID-004: agent_events matched row with verified client_id ----------
    cnt = asyncio.run(matched_event_count(AGENT_NAME, APP1_NAME))
    if cnt and cnt >= 1:
        ok(f"T-CP2C-APPID-004 agent_events status='matched' client_id='{APP1_NAME}' committed ({cnt})")
    else:
        bad("T-CP2C-APPID-004 agent_events matched row with client_id", f"count={cnt}")

    # --- T-CP2C-APPID-005: bad signature -> 401 --------------------------------------
    ts = int(time.time())
    bad_headers = {"X-Timestamp": str(ts), "X-Signature": "sha256=" + "0" * 64}
    st_bad, body_bad = gw_post(AGENT_NAME, WHT, APP1_NAME, bad_headers, body)
    if st_bad == 401:
        ok("T-CP2C-APPID-005 bad signature -> 401")
    else:
        bad("T-CP2C-APPID-005 bad signature -> 401", f"status={st_bad} body={body_bad[:200]!r}")

    # --- T-CP2C-APPID-006: revoked grant -> 401 --------------------------------------
    call("DELETE", f"/api/v1/artifacts/agent/{AGENT_ID}/grants/{GRANT1_ID}", TOKEN)
    ts = int(time.time())
    st_rev, body_rev = gw_post(AGENT_NAME, WHT, APP1_NAME, sign(SECRET1, body, ts), body)
    if st_rev == 401:
        ok("T-CP2C-APPID-006 revoked invoker grant -> 401")
    else:
        bad("T-CP2C-APPID-006 revoked grant -> 401", f"status={st_rev} body={body_rev[:200]!r}")

    # --- T-CP2C-APPID-007: disabled application -> 401 -------------------------------
    call("PATCH", f"/api/v1/teams/platform/applications/{APP2_ID}", TOKEN, {"enabled": False})
    ts = int(time.time())
    st_dis, body_dis = gw_post(AGENT_NAME, WHT, APP2_NAME, sign(SECRET2, body, ts), body)
    if st_dis == 401:
        ok("T-CP2C-APPID-007 disabled application -> 401")
    else:
        bad("T-CP2C-APPID-007 disabled application -> 401", f"status={st_dis} body={body_dis[:200]!r}")

    # --- T-CP2C-APPID-008: the three 401 bodies are byte-identical -------------------
    if st_bad == st_rev == st_dis == 401 and body_bad == body_rev == body_dis:
        ok("T-CP2C-APPID-008 bad-sig / revoked-grant / disabled-app 401 bodies are BYTE-IDENTICAL (uniform-401 oracle closed)")
    else:
        bad("T-CP2C-APPID-008 uniform-401 byte-identity",
            f"bad={body_bad!r} rev={body_rev!r} dis={body_dis!r}")

finally:
    # --- cleanup (best-effort) -------------------------------------------------------
    if GRANT2_ID:
        call("DELETE", f"/api/v1/artifacts/agent/{AGENT_ID}/grants/{GRANT2_ID}", TOKEN)
    if APP1_ID:
        call("DELETE", f"/api/v1/teams/platform/applications/{APP1_ID}", TOKEN)
    if APP2_ID:
        call("DELETE", f"/api/v1/teams/platform/applications/{APP2_ID}", TOKEN)
    call("DELETE", f"/api/v1/agents/{AGENT_ID}", TOKEN)
    print(f"    (cleanup attempted: app1={APP1_ID} app2={APP2_ID} agent={AGENT_ID})")

print(f"=== CP2c summary: PASS={PASS} FAIL={FAIL} ===")
if FAIL:
    raise SystemExit(1)
PY
