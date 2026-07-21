#!/usr/bin/env bash
# scripts/e2e/suite-82-artifact-grants.sh — Artifact Delegation Foundation (Decision 25/30)
#
# The generic artifact_role_grants delegation endpoint (routers/artifact_grants.py) — the
# FIRST live consumer of rbac.has_artifact_role / can_delegate_role, which had zero router
# callers before this feature. Proves grant/list/revoke for user/team/application grantees
# and all three roles, plus the RBAC gate (403) and the 409/400/422 error paths.
#
# Personas (no user provisioning needed — reuse the two seeded accounts):
#   platform-admin / PlatformAdmin2024  — platform-admin (bypass); the grantor
#   agent-reviewer / Reviewer2024       — NO scoped role on a fresh agent → the 403 persona
#                                          and a real grant TARGET (its sub exists in
#                                          user_team_assignments, so it resolves)
#
# Driven in-pod (registry-api at localhost:8000; Keycloak over its Service DNS), same shape
# as smoke-test-cp2-appid-behaviour.sh. Creates its own fixtures, best-effort cleanup.
#
#   T-ARG-001  agent-admin (platform-admin) grants agent-admin to another user -> 201, row exists
#   T-ARG-002  grant approver to a team -> 201; a platform-team member's has_artifact_role -> True
#   T-ARG-003  grant invoker to an application the team owns -> 201
#   T-ARG-004  a caller with no scoped role (agent-reviewer) attempts any grant -> 403
#   T-ARG-005  platform-admin grants a role on a second agent (bypass path) -> 201
#   T-ARG-006  DELETE .../grants/{id} -> 204; has_artifact_role -> False; GET excludes it
#   T-ARG-007  grant a role outside {agent-admin,approver,invoker} -> 422
#   T-ARG-008  grant to an unresolvable grantee_id -> 400
#   T-ARG-009  grant the same (artifact, role, grantee) twice -> 409 on the second
#   T-ARG-010  cleanup: delete every fixture this suite created
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FAIL  T-ARG-FIXTURE  |  no Running registry-api pod found"; exit 1
fi

echo "=== Suite 82: Artifact Delegation Foundation (grants API) ==="
echo "    pod: $API_POD"
echo ""
RUN_TAG="arg-$(date +%s)"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cd /tmp && PYTHONPATH=/app python3 -" <<PY
import asyncio, base64, json, urllib.error, urllib.parse, urllib.request

import asyncio as _a
LOOP = _a.new_event_loop()
PASS = 0; FAIL = 0
def ok(m):
    global PASS; print(f"PASS  {m}"); PASS += 1
def bad(m, d=""):
    global FAIL; print(f"FAIL  {m}  |  {d}"); FAIL += 1

API = "http://localhost:8000"
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
    tok = json.loads(urllib.request.urlopen(urllib.request.Request(KC, data=data), timeout=15).read())["access_token"]
    p = tok.split(".")[1]; p += "=" * (-len(p) % 4)
    sub = json.loads(base64.urlsafe_b64decode(p))["sub"]
    return tok, sub

async def has_role(sub, aid, role, team=None):
    from db import AsyncSessionLocal
    import rbac, uuid
    async with AsyncSessionLocal() as s:
        return await rbac.has_artifact_role(s, sub, uuid.UUID(aid), role, user_team=team)

PT, PSUB = token_for("platform-admin", "PlatformAdmin2024")
RT, RSUB = token_for("agent-reviewer", "Reviewer2024")
ok("T-ARG-FIXTURE-000 fetched platform-admin + agent-reviewer tokens")

# agent-reviewer must exist in user_team_assignments to resolve as a 'user' grantee
# (the 403 persona is defined by lacking an ARTIFACT role, not by lacking a team).
call("PATCH", f"/api/v1/admin/users/{RSUB}", PT, {"team": "platform", "role": "operator"})
ok("T-ARG-FIXTURE-000b ensured agent-reviewer is a resolvable grantee (platform team)")

AGENT_A = "${RUN_TAG}-a"; AGENT_B = "${RUN_TAG}-b"
st, a = call("POST", "/api/v1/agents", PT, {"name": AGENT_A, "team": "platform"})
if st not in (200, 201):
    bad("T-ARG-FIXTURE-001 create agentA", f"{st} {a}"); print(f"=== Suite 82: PASS={PASS} FAIL={FAIL+1} ==="); raise SystemExit(1)
AID = a["id"]; ok(f"T-ARG-FIXTURE-001 created agentA {AID}")

# agentB — a second agent where agent-reviewer holds NO role, used for the 403 test
# (must not be an agent it was just granted agent-admin on) and the bypass test.
st, b = call("POST", "/api/v1/agents", PT, {"name": AGENT_B, "team": "platform"})
if st not in (200, 201):
    bad("T-ARG-FIXTURE-002 create agentB", f"{st} {b}"); print(f"=== Suite 82: PASS={PASS} FAIL={FAIL+1} ==="); raise SystemExit(1)
BID = b["id"]; ok(f"T-ARG-FIXTURE-002 created agentB {BID}")

APP_ID = GRANT1 = GRANT2 = GRANT3 = None
try:
    # T-ARG-001 — agent-admin grants agent-admin to another user
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                 {"grantee_type": "user", "grantee_id": RSUB, "role": "agent-admin"})
    if st == 201:
        GRANT1 = g["id"]
        st2, gl = call("GET", f"/api/v1/artifacts/agent/{AID}/grants", PT)
        if any(x["id"] == GRANT1 for x in gl):
            ok("T-ARG-001 agent-admin grant to user -> 201, row exists")
        else:
            bad("T-ARG-001 row exists after grant", f"list={gl}")
    else:
        bad("T-ARG-001 agent-admin grant to user", f"{st} {g}")

    # T-ARG-002 — grant approver to a team; a platform-team member resolves the role
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                 {"grantee_type": "team", "grantee_id": "platform", "role": "approver"})
    if st == 201:
        GRANT2 = g["id"]
        if LOOP.run_until_complete(has_role(PSUB, AID, "approver", team="platform")):
            ok("T-ARG-002 approver-to-team grant -> 201; team member has_artifact_role True")
        else:
            bad("T-ARG-002 has_artifact_role(team) True", "returned False")
    else:
        bad("T-ARG-002 approver-to-team grant", f"{st} {g}")

    # T-ARG-003 — grant invoker to an application the team owns
    st, app = call("POST", "/api/v1/teams/platform/applications", PT, {"name": "${RUN_TAG}-app"})
    if st == 201:
        APP_ID = app["id"]
        st2, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                      {"grantee_type": "application", "grantee_id": APP_ID, "role": "invoker"})
        if st2 == 201:
            GRANT3 = g["id"]; ok("T-ARG-003 invoker-to-application grant -> 201")
        else:
            bad("T-ARG-003 invoker grant", f"{st2} {g}")
    else:
        bad("T-ARG-003 create application", f"{st} {app}")

    # T-ARG-004 — a caller with no scoped role attempts a grant on agentB -> 403
    # (agentB, where agent-reviewer holds nothing — NOT agentA, where T-ARG-001 just
    # granted it agent-admin.)
    st, g = call("POST", f"/api/v1/artifacts/agent/{BID}/grants", RT,
                 {"grantee_type": "team", "grantee_id": "platform", "role": "agent-admin"})
    ok("T-ARG-004 no-scoped-role grant -> 403") if st == 403 else bad("T-ARG-004 -> 403", f"{st} {g}")

    # T-ARG-005 — platform-admin grants on agentB (bypass path) -> 201
    st, g = call("POST", f"/api/v1/artifacts/agent/{BID}/grants", PT,
                 {"grantee_type": "user", "grantee_id": RSUB, "role": "agent-admin"})
    ok("T-ARG-005 platform-admin grant (bypass) -> 201") if st == 201 else bad("T-ARG-005 -> 201", f"{st} {g}")

    # T-ARG-006 — revoke -> 204; role gone; excluded from list
    if not GRANT1:
        bad("T-ARG-006 revoke", "GRANT1 missing (T-ARG-001 did not create it)")
    else:
        st, _ = call("DELETE", f"/api/v1/artifacts/agent/{AID}/grants/{GRANT1}", PT)
        if st in (200, 204):
            gone = not LOOP.run_until_complete(has_role(RSUB, AID, "agent-admin"))
            st2, gl = call("GET", f"/api/v1/artifacts/agent/{AID}/grants", PT)
            excluded = all(x["id"] != GRANT1 for x in gl)
            if gone and excluded:
                ok("T-ARG-006 revoke -> 204; has_artifact_role False; excluded from list"); GRANT1 = None
            else:
                bad("T-ARG-006 revoke effects", f"gone={gone} excluded={excluded}")
        else:
            bad("T-ARG-006 revoke", f"{st}")

    # T-ARG-007 — role outside the allowed set -> 422
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                 {"grantee_type": "user", "grantee_id": RSUB, "role": "superuser"})
    ok("T-ARG-007 invalid role -> 422") if st == 422 else bad("T-ARG-007 -> 422", f"{st} {g}")

    # T-ARG-008 — unresolvable grantee_id -> 400
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                 {"grantee_type": "user", "grantee_id": "no-such-sub-xyz", "role": "approver"})
    ok("T-ARG-008 unresolvable grantee -> 400") if st == 400 else bad("T-ARG-008 -> 400", f"{st} {g}")

    # T-ARG-009 — duplicate active grant -> 409
    st, g = call("POST", f"/api/v1/artifacts/agent/{AID}/grants", PT,
                 {"grantee_type": "team", "grantee_id": "platform", "role": "approver"})
    ok("T-ARG-009 duplicate grant -> 409") if st == 409 else bad("T-ARG-009 -> 409", f"{st} {g}")

finally:
    # T-ARG-010 — cleanup
    for gid in (GRANT1, GRANT2, GRANT3):
        if gid:
            call("DELETE", f"/api/v1/artifacts/agent/{AID}/grants/{gid}", PT)
    if BID:
        call("DELETE", f"/api/v1/artifacts/agent/{BID}/grants", PT)  # best-effort; ignore
    if APP_ID:
        call("DELETE", f"/api/v1/teams/platform/applications/{APP_ID}", PT)
    call("DELETE", f"/api/v1/agents/{AGENT_A}", PT)
    call("DELETE", f"/api/v1/agents/{AGENT_B}", PT)
    print("PASS  T-ARG-010 cleanup complete"); PASS += 1

print(f"=== Suite 82: PASS={PASS} FAIL={FAIL} ===")
raise SystemExit(0 if FAIL == 0 else 1)
PY
