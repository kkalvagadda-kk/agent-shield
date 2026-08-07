#!/usr/bin/env bash
# scripts/e2e/lib/e2e-auth.sh — ONE definition of how a bash e2e suite authenticates.
#
# WHY THIS EXISTS
# ---------------
# Commit 76b3570 (webhook-application-identity) put `Depends(require_user)` on
# `create_trigger` (routers/triggers.py) and `create_workflow_trigger`
# (routers/composite_workflows.py). Every bash suite created triggers with
# `X-User-Sub` headers alone — that header is an audit stamp, never an
# authentication — so from that commit onward FIFTEEN suites died at setup with
#
#     401 {"detail":"Authentication required"}
#
# and the whole trigger / schedule / webhook / daemon / alerting e2e layer went
# dark: suites 19, 21, 22, 26, 27, 28, 31, 32, 33, 34, 66, 70, 71, 75, 77.
#
# It stayed dark because the failures did not name their cause. suite-28 printed
# an EMPTY setup diagnostic and simply stopped; suite-66 reported "a production
# trigger did not fire a completed run", which points at the scheduler — three
# layers below the actual problem. Silence and a misdirected message are the same
# defect class this repo keeps paying for.
#
# WHY A SHARED FILE AND NOT A SNIPPET PER SUITE
# ---------------------------------------------
# The obvious repair is to paste a token fetch into each of the fifteen drivers.
# That is fifteen copies of one decision, which is precisely the pattern
# `routers/webhook_clients.py` and `agent_endpoints.py` both carry postmortems
# about: two (here, fifteen) parallel paths, one gets fixed, the others silently
# do not. One definition, fifteen callers.
#
# USAGE
#   source "$(dirname "${BASH_SOURCE[0]}")/lib/e2e-auth.sh"
#   TOKEN="$(e2e_token "$NAMESPACE" "$API_POD")"
#
# then interpolate it into the in-pod driver the same way suites already
# interpolate agent names:
#
#   H = {"X-User-Sub": ADMIN, "X-User-Team": "platform",
#        "Authorization": "Bearer ${TOKEN}"}
#
# The token is fetched INSIDE the pod (cluster-internal Keycloak Service DNS),
# so it needs no port-forward and no host-side Keycloak reachability.

# Resolve THIS library's directory ONCE, at source time. Do not compute it inside
# a function from `${BASH_SOURCE[0]}`: the value there is not reliably this file
# when the function is invoked from a sourcing script, and the failure mode is a
# path silently rooted at the caller's cwd (`<repo>/e2e_auth.py missing`) rather
# than an obvious error.
E2E_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Created by the platform itself — registry-api's `bootstrap_admin.ensure_platform_admin`
# writes this user, its realm role and its `user_team_assignments` row on every start
# (R0 / Decision 40). The realm-init Job no longer creates ANY user.
E2E_KC_USER="${E2E_KC_USER:-platform-admin}"
E2E_KC_PASS="${E2E_KC_PASS:-PlatformAdmin2024}"
E2E_KC_CLIENT="${E2E_KC_CLIENT:-agentshield-studio}"
E2E_KC_URL="${E2E_KC_URL:-http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token}"

# The NON-ADMIN persona. Four suites (76, 78, 82, 83) need a second real Keycloak
# identity to prove caller-scoping and the 403 path, and `agent-reviewer` has always
# been it. It used to arrive with the chart; since R0 (FR-9) the realm-init Job creates
# no users at all, so the suites create it themselves — see `e2e_ensure_reviewer`.
E2E_REVIEWER_USER="${E2E_REVIEWER_USER:-agent-reviewer}"
E2E_REVIEWER_PASS="${E2E_REVIEWER_PASS:-Reviewer2024}"

# e2e_token <namespace> <pod> [container]
# Echoes a raw access token, or echoes nothing and returns 1.
# FAILS LOUD by design: a suite that silently proceeds without a token produces
# the 401-at-setup confusion this file exists to end.
e2e_token() {
  local ns="$1" pod="$2" container="${3:-registry-api}" out
  out=$(kubectl exec -n "$ns" "$pod" -c "$container" -- python3 -c "
import json, sys, urllib.parse, urllib.request
data = urllib.parse.urlencode({
    'grant_type': 'password', 'client_id': '${E2E_KC_CLIENT}',
    'username': '${E2E_KC_USER}', 'password': '${E2E_KC_PASS}'}).encode()
try:
    req = urllib.request.Request('${E2E_KC_URL}', data=data)
    print(json.loads(urllib.request.urlopen(req, timeout=15).read())['access_token'])
except Exception as exc:
    print('E2E_TOKEN_ERROR: %s' % exc, file=sys.stderr)
    sys.exit(1)
" 2>/dev/null) || { echo "" ; return 1; }
  [ -n "$out" ] || return 1
  printf '%s' "$out"
}

# e2e_install_pyauth <namespace> <pod> [container]
# Copy lib/e2e_auth.py into the pod at /tmp/e2e_auth.py so a detached driver can
# `from e2e_auth import BearerAuth` and refresh its own token.
#
# REQUIRED for any suite that runs longer than the 300s token lifespan — i.e.
# every detached-driver suite (66, 70, 71, 75, 77). A statically-interpolated
# token is fine ONLY for the short inline suites that finish inside 5 minutes.
# Getting this wrong does not fail loudly at the start; it fails 20 minutes in,
# on whichever case happens to run last, and looks like a feature bug.
e2e_install_pyauth() {
  local ns="$1" pod="$2" container="${3:-registry-api}" lib="$E2E_LIB_DIR/e2e_auth.py"
  [ -f "$lib" ] || { echo "FATAL: $lib missing" >&2; exit 1; }
  kubectl exec -i -n "$ns" "$pod" -c "$container" -- bash -c 'cat > /tmp/e2e_auth.py' < "$lib" \
    || { echo "FATAL: could not install e2e_auth.py into $pod" >&2; exit 1; }
  # VERIFY the module actually landed and imports. A silent no-op here does not
  # fail now — it fails twenty minutes later inside the driver, as a
  # ModuleNotFoundError that never reaches the result file, and the suite reports
  # "driver did not finish" with no output at all. Check while it is still cheap.
  kubectl exec -n "$ns" "$pod" -c "$container" -- \
    python3 -c 'import sys; sys.path.insert(0, "/tmp"); import e2e_auth' >/dev/null 2>&1 \
    || { echo "FATAL: /tmp/e2e_auth.py did not import inside $pod" >&2; exit 1; }
}

# e2e_require_token <namespace> <pod> [container]
# Aborts the suite with a message that NAMES THE CAUSE. Use this in any suite whose
# assertions depend on trigger CRUD — a missing token must read as "could not
# authenticate", never as "the feature is broken".
#
# CALL IT BARE, NOT IN A COMMAND SUBSTITUTION, when you rely on the abort:
#
#     e2e_set_token "$NAMESPACE" "$API_POD"     # good: exit propagates
#     TOK="$(e2e_require_token ...)"            # the `exit 1` below only kills the
#                                               # SUBSHELL; the suite carries on with
#                                               # TOK empty and every later call 401s
#
# That subshell subtlety is why `e2e_set_token` exists and is what the suites use.
e2e_require_token() {
  local tok
  tok=$(e2e_token "$@") || {
    echo "FATAL: could not obtain a Keycloak token for ${E2E_KC_USER} (client ${E2E_KC_CLIENT})." >&2
    echo "       Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401." >&2
    echo "       ${E2E_KC_USER} is created by registry-api's LIFESPAN BOOTSTRAP" >&2
    echo "       (services/registry-api/bootstrap_admin.py), NOT by the realm-init Job — since" >&2
    echo "       R0/FR-9 that Job creates no users at all, so 'check the Job' is a dead end." >&2
    echo "       Check GET /ready: it stays 503 {\"status\":\"bootstrapping\",\"detail\":…} until the" >&2
    echo "       bootstrap succeeds, and its detail names the cause (a Keycloak outage, or an" >&2
    echo "       empty PLATFORM_ADMIN_PASSWORD from the keycloak-user-passwords Secret)." >&2
    echo "       Then GET /api/v1/admin/identity-audit for a Keycloak/DB divergence." >&2
    exit 1
  }
  printf '%s' "$tok"
}

# e2e_set_token <namespace> <pod> [container]
# Sets E2E_TOKEN in the CALLER'S shell and aborts the suite if it cannot.
#
# Assigns rather than echoes precisely so the failure path works: `exit 1` inside a
# command substitution exits only that subshell, so `E2E_TOKEN="$(e2e_require_token
# ...)"` silently yields an EMPTY token and the suite then fails later with a wall of
# 401s — the misdirected-failure mode this whole helper exists to end.
e2e_set_token() {
  E2E_TOKEN="$(e2e_token "$@")" || true
  if [ -z "${E2E_TOKEN:-}" ]; then
    echo "FATAL: could not obtain a Keycloak token for ${E2E_KC_USER} (client ${E2E_KC_CLIENT})." >&2
    echo "       Trigger CRUD requires a real JWT since 76b3570 — X-User-Sub alone returns 401." >&2
    echo "       ${E2E_KC_USER} is created by registry-api's LIFESPAN BOOTSTRAP" >&2
    echo "       (services/registry-api/bootstrap_admin.py), NOT by the realm-init Job — since" >&2
    echo "       R0/FR-9 that Job creates no users at all, so 'check the Job' is a dead end." >&2
    echo "       Check GET /ready: it stays 503 {\"status\":\"bootstrapping\",\"detail\":…} until the" >&2
    echo "       bootstrap succeeds, and its detail names the cause (a Keycloak outage, or an" >&2
    echo "       empty PLATFORM_ADMIN_PASSWORD from the keycloak-user-passwords Secret)." >&2
    echo "       Then GET /api/v1/admin/identity-audit for a Keycloak/DB divergence." >&2
    exit 1
  fi
}

# e2e_ensure_reviewer <namespace> <pod> [container]
# Idempotently ensure the `agent-reviewer` persona exists and can actually log in.
#
# WHY IT EXISTS
#   R0/FR-9 took every `kcadm.sh create users` out of
#   charts/agentshield/templates/realm-init-job.yaml. `platform-admin` is fine — the
#   platform now writes it from code — but `agent-reviewer` has no owner, so on the
#   next fresh install suites 76, 78, 82 and 83 would each get a null token and either
#   SKIP their real cases or 401 somewhere downstream. A fixture whose dependency
#   silently vanished is exactly the "went dark and did not name its cause" failure
#   this file was written about. The suite that needs the persona creates the persona.
#
# WHY THE REAL API AND NOT kcadm / a direct INSERT
#   `POST /api/v1/admin/users` is the ONE path that creates the Keycloak user, its
#   realm role and its `user_team_assignments` row atomically (FR-8). Provisioning the
#   fixture any other way would build a persona the product cannot build, and would
#   stop exercising the very endpoint the fixture depends on.
#
# WHY THE ADMIN TOKEN AND NOT AN ANONYMOUS CALL
#   `/api/v1/admin/*` is unauthenticated today, but R2 puts
#   `require_global_role("platform-admin")` on it. Authenticating now means R2 does not
#   have to come back and rewrite four suites' fixtures.
#
# WHY THE reset-password CALL IS NOT OPTIONAL
#   `keycloak_client.create_user` writes `"temporary": True` and
#   `requiredActions: ["UPDATE_PASSWORD"]`. Keycloak refuses a `password` grant for
#   such a user with "Account is not fully set up" — a 401 that names nothing. So the
#   create is always followed by `POST /{kc_id}/reset-password {"temporary": false}`,
#   which also re-asserts the credential if someone rotated it.
#
# WHY IT VERIFIES INSTEAD OF RETURNING OPTIMISTICALLY
#   Every failure mode above surfaces as "token is None" inside a driver 20 minutes
#   later, on whichever case happens to run last, and reads as a feature bug. So the
#   helper performs the `password` grant itself and aborts naming the cause.
#
# CALL IT BARE, NOT IN A COMMAND SUBSTITUTION — same subshell subtlety documented on
# `e2e_require_token` above: the `exit 1` here must reach the suite, not a subshell.
#
#     source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
#     e2e_ensure_reviewer "$NAMESPACE" "$API_POD"
#
# Idempotent: 409 from the create means the user already exists, which is SUCCESS —
# the kc_id is re-resolved from `GET /api/v1/admin/users` and the run continues.
# ── Role personas for RBAC testing ────────────────────────────────────────────
# WHY THIS EXISTS
# --------------
# 61 suites mint a token and every one of them authenticates as platform-admin. Four use
# `agent-reviewer`, which is itself created as a *contributor*. No suite has ever logged in
# as a `consumer`, and suite-42-rbac — the RBAC suite — contains ZERO 403 assertions: its
# seven cases are structural (table exists, creator auto-grant, /me shape, normalization).
#
# That is survivable for R1, which only asks "is there a valid token". It is NOT survivable
# for R2: R2 flips rbac.py's ENFORCE and wires require_global_role("platform-admin"), and if
# every caller IS platform-admin then every test passes whether enforcement works or not.
# The suite would go green over a completely broken authorization layer — the same
# can't-fail-guard defect this repo keeps paying for (see G-R0-9, a test that was red from
# birth and never once ran).
#
# e2e_ensure_persona creates/repairs a user at a GIVEN global role and echoes a token for
# it. Idempotent: 409 means it already exists, and the role is re-pinned if it drifted —
# a persona whose role is wrong silently inverts every assertion built on it.
E2E_PERSONA_PASS="${E2E_PERSONA_PASS:-Persona2024!}"

# e2e_ensure_persona <ns> <pod> <username> <global-role> [container]
#   echoes: a Keycloak access token for that user (empty + non-zero on failure)
e2e_ensure_persona() {
  local ns="$1" pod="$2" uname="$3" role="$4" container="${5:-registry-api}" admin_tok tok
  admin_tok="$(e2e_token "$ns" "$pod" "$container")" || {
    echo "FATAL: e2e_ensure_persona could not obtain a ${E2E_KC_USER} token; cannot create '${uname}'." >&2
    return 1
  }
  tok=$(kubectl exec -i -n "$ns" "$pod" -c "$container" -- env \
      P_ADMIN_TOKEN="$admin_tok" P_USER="$uname" P_ROLE="$role" \
      P_PASS="$E2E_PERSONA_PASS" P_CLIENT="$E2E_KC_CLIENT" P_KC="$E2E_KC_URL" \
      python3 - <<'PY_PERSONA'
import json, os, urllib.error, urllib.parse, urllib.request
BASE="http://localhost:8000/api/v1/admin/users"
U, ROLE, PW = os.environ["P_USER"], os.environ["P_ROLE"], os.environ["P_PASS"]
H={"Authorization":"Bearer "+os.environ["P_ADMIN_TOKEN"],"Content-Type":"application/json"}

def call(method, url, body=None):
    req=urllib.request.Request(url, method=method,
        data=json.dumps(body).encode() if body is not None else None, headers=H)
    try:
        with urllib.request.urlopen(req, timeout=20) as r: return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e: return e.code, (e.read() or b"").decode()[:200]

# @example.com, never @agentshield.local: UserCreate.email is EmailStr and email-validator
# rejects .local as an RFC 6762 special-use TLD, so the API answers 422.
code, body = call("POST", BASE, {"username":U,"email":f"{U}@example.com","first_name":"E2E",
                                 "last_name":"Persona","temp_password":PW,"team":"platform","role":ROLE})
kc_id = body.get("kc_id") if code == 201 and isinstance(body, dict) else None
if code == 409:                      # already exists — success, then re-pin the role
    st, users = call("GET", BASE)
    match = [u for u in (users or []) if u.get("username") == U] if st == 200 else []
    if not match: raise SystemExit(f"FATAL: {U} reported 409 but is not listed")
    kc_id = match[0]["kc_id"]
    if match[0].get("role") != ROLE:  # a drifted persona inverts every assertion on it
        call("PATCH", f"{BASE}/{kc_id}", {"role": ROLE, "team": "platform"})
elif code != 201:
    raise SystemExit(f"FATAL: create {U} -> {code} {body}")

# Non-temporary password: create_user sets requiredActions=["UPDATE_PASSWORD"], and
# Keycloak refuses a password grant for such a user with "Account is not fully set up".
call("POST", f"{BASE}/{kc_id}/reset-password", {"new_password": PW, "temporary": False})

data=urllib.parse.urlencode({"grant_type":"password","client_id":os.environ["P_CLIENT"],
                             "username":U,"password":PW}).encode()
try:
    # P_KC is the FULL token URL already (E2E_KC_URL, :57) — appending the realm path
    # to it doubles the path and 404s.
    with urllib.request.urlopen(os.environ["P_KC"], data=data, timeout=20) as r:
        print(json.loads(r.read())["access_token"])
except Exception as exc:
    raise SystemExit(f"FATAL: {U} exists but cannot obtain a token: {exc}")
PY_PERSONA
) || { echo "FATAL: e2e_ensure_persona failed for '${uname}' (role=${role})" >&2; return 1; }
  printf '%s' "$tok"
}

# e2e_refresh_token <ns> <pod> [container]
# Re-mint E2E_TOKEN. Call this before any authenticated work that happens LATE in a long
# suite — cleanup traps above all.
#
# WHY: Keycloak issues these with a 300-SECOND lifetime (measured 2026-08-07). Any suite
# that runs longer than five minutes and then makes an authenticated call is holding an
# expired token. That did not matter while cleanup was anonymous; R1-R3 made agent
# DELETE/PATCH/publish require a credential, so every long suite's cleanup became a
# time-bomb. suite-5 is where it surfaced: its cleanup DELETE returned non-204 while the
# identical call with a fresh token returned 204.
#
# This is a REFRESH, not a second source of truth — it overwrites the same E2E_TOKEN the
# suite already uses, so there is still one variable and one way to authenticate.
e2e_refresh_token() {
  local ns="$1" pod="$2" container="${3:-registry-api}"
  e2e_set_token "$ns" "$pod" "$container"
}

e2e_ensure_reviewer() {
  local ns="$1" pod="$2" container="${3:-registry-api}" admin_tok out
  admin_tok="$(e2e_token "$ns" "$pod" "$container")" || {
    echo "FATAL: e2e_ensure_reviewer could not obtain a ${E2E_KC_USER} token, so it cannot" >&2
    echo "       call POST /api/v1/admin/users to create ${E2E_REVIEWER_USER}." >&2
    echo "       registry-api's bootstrap writes ${E2E_KC_USER} on start — check /ready is 200" >&2
    echo "       (503 {\"status\":\"bootstrapping\"} means it has not succeeded yet)." >&2
    exit 1
  }

  # Values go in as ENV, not interpolated into the payload: a JWT and a password have
  # no business being spliced into a script body, and a quoted heredoc keeps the Python
  # readable as Python.
  out=$(kubectl exec -i -n "$ns" "$pod" -c "$container" -- env \
      REVIEWER_ADMIN_TOKEN="$admin_tok" \
      REVIEWER_USER="$E2E_REVIEWER_USER" \
      REVIEWER_PASS="$E2E_REVIEWER_PASS" \
      REVIEWER_KC_CLIENT="$E2E_KC_CLIENT" \
      REVIEWER_KC_URL="$E2E_KC_URL" \
      python3 - <<'PY'
import json, os, sys, urllib.error, urllib.parse, urllib.request

BASE = "http://localhost:8000/api/v1/admin/users"
USER = os.environ["REVIEWER_USER"]
PASSWORD = os.environ["REVIEWER_PASS"]
TEAM = "platform"
# 'contributor' is the canonical stated role. The persona's job is to be a caller with
# no ARTIFACT role (suite-82 T-ARG-004, suite-83 T-SYY-003); its GLOBAL role is
# irrelevant to that and must not be the legacy 'operator' the R0 work is removing.
ROLE = "contributor"
HEADERS = {
    "Authorization": "Bearer %s" % os.environ["REVIEWER_ADMIN_TOKEN"],
    "Content-Type": "application/json",
}


def api(method, path, body=None):
    """Returns (status, parsed_body). A 4xx/5xx is a RESULT here, not an exception —
    409 is a success case and the others have to be reported with their body."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, headers=HEADERS, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except Exception:
            return exc.code, raw.decode("utf-8", "replace")[:300]


def die(msg):
    print("FATAL: %s" % msg)
    sys.exit(1)


status, body = api("POST", "", {
    "username": USER,
    "email": "%s@example.com" % USER,
    "first_name": "Agent",
    "last_name": "Reviewer",
    "temp_password": PASSWORD,
    "team": TEAM,
    "role": ROLE,
})

if status in (200, 201):
    kc_id, state = body["kc_id"], "created"
elif status == 409:
    # ALREADY EXISTS IS SUCCESS. `_kc_error` maps Keycloak's 409 to
    # "Username or email already exists" (admin_users.py:152-153) and nothing was
    # written, so re-resolve the id from the list rather than treating it as a failure.
    st, users = api("GET", "")
    if st != 200 or not isinstance(users, list):
        die("e2e_ensure_reviewer: POST said 409 but GET /api/v1/admin/users -> %s %s" % (st, users))
    match = [u for u in users if u.get("username") == USER]
    if not match:
        die("e2e_ensure_reviewer: POST said %r already exists, but it is not in "
            "GET /api/v1/admin/users — the realm and this API disagree." % USER)
    kc_id, state = match[0]["kc_id"], "existing"
    # The 409 only proves the KEYCLOAK half. A user left over from the old chart (or
    # from a run whose row was cleaned up) can exist with no assignment row at all, and
    # after FR-5 that persona cannot call anything — every route resolving a global
    # role answers 403 no_platform_role. Re-pin through the same real API.
    if match[0].get("team") != TEAM or match[0].get("role") != ROLE:
        st, patched = api("PATCH", "/%s" % kc_id, {"team": TEAM, "role": ROLE})
        if st != 200:
            die("e2e_ensure_reviewer: PATCH /api/v1/admin/users/%s -> %s %s" % (kc_id, st, patched))
        state = "existing+repinned"
else:
    die("e2e_ensure_reviewer: POST /api/v1/admin/users -> %s %s" % (status, body))

# REQUIRED, not a nicety — see the header comment. Without it the account still carries
# requiredActions=["UPDATE_PASSWORD"] and the grant below fails "Account is not fully set up".
st, resp = api("POST", "/%s/reset-password" % kc_id, {"new_password": PASSWORD, "temporary": False})
if st not in (200, 204):
    die("e2e_ensure_reviewer: POST /api/v1/admin/users/%s/reset-password -> %s %s" % (kc_id, st, resp))

# VERIFY. Do the exact grant the suites will do, so a broken persona fails HERE.
grant = urllib.parse.urlencode({
    "grant_type": "password", "client_id": os.environ["REVIEWER_KC_CLIENT"],
    "username": USER, "password": PASSWORD}).encode()
try:
    token = json.loads(urllib.request.urlopen(
        urllib.request.Request(os.environ["REVIEWER_KC_URL"], data=grant), timeout=20).read())
except Exception as exc:
    detail = ""
    try:
        detail = exc.read().decode("utf-8", "replace")[:300]
    except Exception:
        pass
    die("%s exists (kc_id=%s, %s) but cannot obtain a token from Keycloak: %s %s"
        % (USER, kc_id, state, exc, detail))
if not token.get("access_token"):
    die("%s exists (kc_id=%s, %s) but cannot obtain a token: the grant returned no "
        "access_token (%s)" % (USER, kc_id, state, sorted(token)))

print("E2E_REVIEWER_OK %s kc_id=%s (%s)" % (USER, kc_id, state))
PY
  ) || {
    if [ -n "$out" ]; then echo "$out" >&2; fi
    echo "FATAL: e2e_ensure_reviewer could not provision ${E2E_REVIEWER_USER} in $pod." >&2
    echo "       This suite uses it as its non-admin persona; the chart stopped creating" >&2
    echo "       users in R0 (FR-9), so the fixture is the suite's own responsibility." >&2
    exit 1
  }
  echo "  $out"
}
