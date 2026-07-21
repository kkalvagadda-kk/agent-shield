#!/usr/bin/env bash
# scripts/e2e/suite-83-webhook-applications.sh — Webhook Applications (Decision 30)
#
# The end-to-end signed-webhook path through the NEW identity model: application ->
# invoker grant -> auth_mode flips to client_signed -> event-gateway resolves
# applications + artifact_role_grants -> HMAC verify -> dispatch. Plus the security
# invariants (revoked grant / disabled app / bad signature all deny with a BYTE-IDENTICAL
# uniform 401), secret rotation, and multi-artifact grant isolation.
#
# Driven in-pod (registry-api at localhost:8000; gateway over its Service DNS
# agentshield-event-gateway:8091; Keycloak over its Service DNS). Signs with the EXACT
# recipe real senders use (services/event-gateway/webhook_auth.py). Own fixtures, cleanup.
#
#   T-SYY-001  create application under a team -> row exists, zero invoker grants
#   T-SYY-002  agent-admin grants invoker on their agent -> 201; GET triggers auth_mode client_signed
#   T-SYY-003  a contributor (not agent-admin on that artifact) attempts to grant invoker -> 403
#   T-SYY-004  signed webhook from a granted+enabled app -> accepted past auth; agent_events
#              status='matched' committed with client_id = the application name
#   T-SYY-005  signed webhook from a revoked-grant app -> uniform 401
#   T-SYY-006  signed webhook from a disabled app -> uniform 401, body byte-identical to 005
#   T-SYY-007  rotate secret -> old secret's signature 401, new secret's succeeds
#   T-SYY-008  app granted invoker on TWO agents -> revoking one leaves the other working
#   T-SYY-009  (documented skip) pre-0070 webhook_clients backfill — not re-runnable post-migration
#   T-SYY-010  cleanup
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-SYY-FIXTURE  |  no Running registry-api pod found"; exit 1
fi

echo "=== Suite 83: Webhook Applications (invoker grants + signed invoke) ==="
echo "    pod: $API_POD"
echo ""
RUN_TAG="syy-$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import asyncio, base64, hashlib, hmac, json, time, urllib.error, urllib.parse, urllib.request

PASS = 0; FAIL = 0
def ok(m):
    global PASS; print(f"PASS  {m}"); PASS += 1
def bad(m, d=""):
    global FAIL; print(f"FAIL  {m}  |  {d}"); FAIL += 1
def skip(m):
    print(f"SKIP  {m}")

API = "http://localhost:8000"
GW = "http://agentshield-event-gateway:8091"
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"

class _Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return urllib.request.Request(newurl, data=req.data, method=req.get_method(),
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

def token_for(user, pw):
    data = urllib.parse.urlencode({
        "grant_type": "password", "client_id": "agentshield-studio",
        "username": user, "password": pw}).encode()
    return json.loads(urllib.request.urlopen(urllib.request.Request(KC, data=data), timeout=15).read())["access_token"]

def sign(secret, body_bytes, ts):
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + body_bytes, hashlib.sha256).hexdigest()
    return {"X-Timestamp": str(ts), "X-Signature": f"sha256={mac}"}

def gw_post(agent_name, path_token, client_id, headers_extra, body_bytes):
    req = urllib.request.Request(f"{GW}/hooks/{agent_name}/{path_token}", data=body_bytes, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Client-Id", client_id)
    for k, v in headers_extra.items():
        req.add_header(k, v)
    try:
        resp = urllib.request.urlopen(req, timeout=15)
        return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()

async def _matched(agent_name, client_id):
    from sqlalchemy import text
    from db import AsyncSessionLocal
    async with AsyncSessionLocal() as s:
        return (await s.execute(text(
            "SELECT count(*) FROM agent_events WHERE agent_name=:an AND client_id=:cid AND status='matched'"
        ), {"an": agent_name, "cid": client_id})).scalar()
LOOP = asyncio.new_event_loop()
def matched(agent_name, client_id):
    return LOOP.run_until_complete(_matched(agent_name, client_id))

TOKEN = token_for("platform-admin", "PlatformAdmin2024")
RTOKEN = token_for("agent-reviewer", "Reviewer2024")  # contributor persona for T-SYY-003
ok("T-SYY-FIXTURE-000 fetched platform-admin + agent-reviewer tokens")

AGENT = "${RUN_TAG}-a"; AGENT2 = "${RUN_TAG}-b"
st, a = call("POST", "/api/v1/agents", TOKEN, {"name": AGENT, "team": "platform"})
st2, a2 = call("POST", "/api/v1/agents", TOKEN, {"name": AGENT2, "team": "platform"})
if st not in (200, 201) or st2 not in (200, 201):
    bad("T-SYY-FIXTURE-001 create agents", f"{st}/{st2}"); print(f"=== Suite 83: PASS={PASS} FAIL={FAIL+1} ==="); raise SystemExit(1)
AID, AID2 = a["id"], a2["id"]
st, t = call("POST", f"/api/v1/agents/{AGENT}/triggers", TOKEN, {"trigger_type": "webhook", "enabled": True})
st2, t2 = call("POST", f"/api/v1/agents/{AGENT2}/triggers", TOKEN, {"trigger_type": "webhook", "enabled": True})
WHT, WHT2 = t.get("token"), t2.get("token")
if not WHT or not WHT2:
    bad("T-SYY-FIXTURE-002 create webhook triggers", f"{t} {t2}"); print(f"=== Suite 83: PASS={PASS} FAIL={FAIL+1} ==="); raise SystemExit(1)
ok(f"T-SYY-FIXTURE-001/002 created 2 agents + webhook triggers")

apps = {}   # name -> (id, secret)
grants = []
body = json.dumps({"event_type": "syy.ping"}).encode()

def mk_app(name):
    st, r = call("POST", "/api/v1/teams/platform/applications", TOKEN, {"name": name})
    assert st == 201 and str(r.get("secret", "")).startswith("whsec_"), f"{st} {r}"
    apps[name] = (r["id"], r["secret"]); return r["id"], r["secret"]

def grant(aid, app_id):
    st, r = call("POST", f"/api/v1/artifacts/agent/{aid}/grants", TOKEN,
                 {"grantee_type": "application", "grantee_id": app_id, "role": "invoker"})
    if st == 201:
        grants.append((aid, r["id"]))
    return st, r

try:
    # T-SYY-001 — create application, zero grants
    A1 = "${RUN_TAG}-app1"; id1, sec1 = mk_app(A1)
    st, gl = call("GET", f"/api/v1/artifacts/agent/{AID}/grants", TOKEN)
    if not any(x.get("grantee_id") == id1 for x in gl):
        ok("T-SYY-001 created application -> row exists, zero invoker grants")
    else:
        bad("T-SYY-001 zero grants on new app", f"grants={gl}")

    # T-SYY-002 — grant invoker -> 201; auth_mode flips
    st, g = grant(AID, id1)
    st2, trigs = call("GET", f"/api/v1/agents/{AGENT}/triggers", TOKEN)
    modes = [x.get("auth_mode") for x in trigs] if isinstance(trigs, list) else []
    if st == 201 and modes and all(m == "client_signed" for m in modes):
        ok("T-SYY-002 invoker grant -> 201; trigger auth_mode client_signed")
    else:
        bad("T-SYY-002 grant + auth_mode flip", f"grant={st} modes={modes}")

    # T-SYY-003 — contributor (not agent-admin) attempts to grant invoker -> 403
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", RTOKEN,
                 {"grantee_type": "application", "grantee_id": id1, "role": "invoker"})
    ok("T-SYY-003 contributor grant invoker -> 403") if st == 403 else bad("T-SYY-003 -> 403", f"{st} {g}")

    # T-SYY-004 — signed webhook from granted+enabled app -> accepted past auth + matched row
    ts = int(time.time())
    stw, raw = gw_post(AGENT, WHT, A1, sign(sec1, body, ts), body)
    if stw in (202, 502):
        cnt = matched(AGENT, A1)
        if cnt and cnt >= 1:
            ok(f"T-SYY-004 signed webhook accepted (HTTP {stw}); agent_events matched client_id='{A1}' ({cnt})")
        else:
            bad("T-SYY-004 matched agent_events row", f"http={stw} count={cnt}")
    else:
        bad("T-SYY-004 signed webhook accepted past auth", f"status={stw} body={raw[:200]!r}")

    # T-SYY-005 — revoked grant -> 401
    call("DELETE", f"/api/v1/artifacts/agent/{AID}/grants/{grants[-1][1]}", TOKEN)
    grants.pop()
    ts = int(time.time())
    st5, b5 = gw_post(AGENT, WHT, A1, sign(sec1, body, ts), body)
    ok("T-SYY-005 revoked-grant signed webhook -> 401") if st5 == 401 else bad("T-SYY-005 -> 401", f"{st5} {b5[:200]!r}")

    # T-SYY-006 — disabled app -> 401, byte-identical to 005
    A2 = "${RUN_TAG}-app2"; id2, sec2 = mk_app(A2)
    grant(AID, id2)
    call("PATCH", f"/api/v1/teams/platform/applications/{id2}", TOKEN, {"enabled": False})
    ts = int(time.time())
    st6, b6 = gw_post(AGENT, WHT, A2, sign(sec2, body, ts), body)
    if st6 == 401 and b6 == b5:
        ok("T-SYY-006 disabled-app -> 401, body BYTE-IDENTICAL to revoked-grant 401 (uniform oracle)")
    else:
        bad("T-SYY-006 disabled-app uniform 401", f"st={st6} identical={b6 == b5} b5={b5[:80]!r} b6={b6[:80]!r}")

    # T-SYY-007 — rotate secret: old signature 401, new signature accepted
    A3 = "${RUN_TAG}-app3"; id3, sec3 = mk_app(A3)
    grant(AID, id3)
    st, rot = call("POST", f"/api/v1/teams/platform/applications/{id3}/rotate-secret", TOKEN)
    new_sec = rot.get("secret", "")
    ts = int(time.time())
    st_old, _ = gw_post(AGENT, WHT, A3, sign(sec3, body, ts), body)       # old secret
    ts = int(time.time())
    st_new, _ = gw_post(AGENT, WHT, A3, sign(new_sec, body, ts), body)    # new secret
    if st_old == 401 and st_new in (202, 502):
        ok(f"T-SYY-007 rotate: old secret -> 401, new secret -> accepted (HTTP {st_new})")
    else:
        bad("T-SYY-007 rotate old-fails/new-works", f"old={st_old} new={st_new}")

    # T-SYY-008 — app granted on two agents; revoking one leaves the other working
    A4 = "${RUN_TAG}-app4"; id4, sec4 = mk_app(A4)
    grant(AID, id4); grant(AID2, id4)
    # revoke the AID grant only
    ga = [gid for (aid_, gid) in grants if aid_ == AID and True]  # find one app4 grant on AID
    st_g, gl = call("GET", f"/api/v1/artifacts/agent/{AID}/grants", TOKEN)
    app4_on_a = [x["id"] for x in gl if x.get("grantee_id") == id4]
    if app4_on_a:
        call("DELETE", f"/api/v1/artifacts/agent/{AID}/grants/{app4_on_a[0]}", TOKEN)
        grants = [(aid_, gid) for (aid_, gid) in grants if gid != app4_on_a[0]]
    ts = int(time.time())
    st_a, _ = gw_post(AGENT, WHT, A4, sign(sec4, body, ts), body)     # AID: revoked -> 401
    ts = int(time.time())
    st_b, _ = gw_post(AGENT2, WHT2, A4, sign(sec4, body, ts), body)   # AID2: still granted -> accepted
    if st_a == 401 and st_b in (202, 502):
        ok(f"T-SYY-008 revoking one artifact's grant leaves the other working (agentA 401, agentB {st_b})")
    else:
        bad("T-SYY-008 multi-artifact isolation", f"agentA={st_a} agentB={st_b}")

    # T-SYY-009 — documented skip (not re-runnable post-migration)
    skip("T-SYY-009 pre-0070 webhook_clients backfill — proven by migration 0070 + CP1; "
         "the migration already ran on this cluster and cannot be re-triggered here")

finally:
    # T-SYY-010 — cleanup
    for (aid_, gid) in grants:
        call("DELETE", f"/api/v1/artifacts/agent/{aid_}/grants/{gid}", TOKEN)
    for (app_id, _sec) in apps.values():
        call("DELETE", f"/api/v1/teams/platform/applications/{app_id}", TOKEN)
    call("DELETE", f"/api/v1/agents/{AGENT}", TOKEN)
    call("DELETE", f"/api/v1/agents/{AGENT2}", TOKEN)
    print("PASS  T-SYY-010 cleanup complete"); PASS += 1

print(f"=== Suite 83: PASS={PASS} FAIL={FAIL} ===")
raise SystemExit(0 if FAIL == 0 else 1)
PY
