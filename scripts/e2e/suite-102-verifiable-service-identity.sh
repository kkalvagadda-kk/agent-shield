#!/usr/bin/env bash
# scripts/e2e/suite-102-verifiable-service-identity.sh
#
# Identity propagation PHASE 3 — verifiable service identity.
# Design: docs/design/identity-propagation-architecture.md §4.5.
#
# THE NEGATIVE CASES ARE THE DELIVERABLE. Phase 3's whole point is that three identities
# stopped being self-asserted strings, so the tests that matter are the ones proving a
# forged identity is now REFUSED. The design calls these non-negotiable, and they are the
# reason this suite exists — the positive path was already covered by suites 8/45/93.
#
# What was true before this phase, measured on the cluster (not inferred):
#
#   POST /playground/runs      reached the handler with NO credential at all. `caller`
#                              fell back to the literal "dev" and BOTH gates below it are
#                              written `caller != "dev"`, so an anonymous request skipped
#                              the contributor role gate AND the per-agent authority check
#                              and could start a real run on any agent.
#   PATCH /approvals/{id}      resolved `x_user_sub or x_user_id or JWT.sub or
#                              body.reviewer_id` — a PLAINTEXT HEADER OUTRANKED THE
#                              VERIFIED TOKEN. Naming any platform-admin's sub in a header
#                              approved any pending HITL tool call.
#   POST /internal/runs/start  declared no auth dependency and took `run_by` from the body.
#
#   T-S102-001  internal run-start with NO credential            -> 401
#   T-S102-002  internal run-start with a FORGED body run_by     -> 401 (body is not identity)
#   T-S102-003  playground run with NO credential                -> 401 (was: reached handler)
#   T-S102-004  playground run with forged `X-User-Sub: eval-runner` -> 401 (was: service bypass)
#   T-S102-005  PATCH /approvals with a forged admin header      -> 401 (was: approved it)
#   T-S102-006  GET  /approvals with NO credential               -> 401 (was: every team's queue)
#   T-S102-007  is_trusted_service rejects a USER token (azp is agentshield-studio)
#   T-S102-008  is_trusted_service accepts a real scheduler client_credentials token
#   T-S102-009  a verified user token still starts a playground run (no false positive)
#   T-S102-010  a verified SERVICE token cannot decide an approval EVEN AS platform-admin
#   T-S102-011  GET  /approvals/{id}          with no credential -> 401 (was: full detail)
#   T-S102-012  POST /approvals/{id}/reopen   with no credential -> 401 (was: un-rejected it)
#   T-S102-013  PATCH /playground/datasets/{id} no credential    -> 401 (was: applied it)
#   T-S102-014a POST /playground/approvals/{id}/decide, no cred  -> 401 (was: decided it)
#   T-S102-014b the same route WITH a verified service token still reaches the handler
#   T-S102-015  GET  /catalog                  with no credential -> 401 (was: 200, 86 rows)
#   T-S102-016  a REVOKED asset grant does NOT confer catalog visibility
#   T-S102-017  un-revoking the SAME grant makes it visible (the filter IS the grant)
#
# T-S102-008 is the one that proves the mechanism rather than the refusal: it mints a REAL
# token with the scheduler's client secret and checks `azp` survives verification. Without
# it, every other case here would still pass if the fix were "deny everything".
# T-S102-009 and T-S102-014b are the same guard from the other side: a verified USER and a
# verified SERVICE must each still get through on the routes that are theirs.
#
# 011/012 assert 401 rather than 404 on a RANDOM uuid on purpose — the auth decision must
# happen before the row lookup, or existence leaks to anyone willing to probe IDs.
#
# 010 needs the extra platform-admin grant to mean anything. Measured on the cluster: the
# scheduler service account has NO user_team_assignments row, so a bare "service decides ->
# 403" already passes — by absence of a grant, not because the caller is a service. The
# grant makes the real question visible: is a service refused on KIND, or only when nothing
# happens to have granted it a role? Row is removed in a finally.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && { echo "ERROR: no registry-api pod in $NAMESPACE"; exit 1; }

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

# A NON-ADMIN caller for the catalog cases. platform-admin sees the whole catalog by design
# (every can_* helper in rbac.py short-circuits that role), so the grant filter can only be
# observed as somebody else.
S102_MEMBER_TOKEN="$(e2e_ensure_persona "$NAMESPACE" "$API_POD" "s102-member" "contributor")"

echo "=== Suite 102: verifiable service identity (identity P3) ==="
echo "  Pod: $API_POD"

# The scheduler's client secret comes from the SAME Secret the realm-init Job used to
# create the client, read at run time rather than hard-coded: a literal here would pass
# while the deployed client held a different secret, which is the exact drift the single
# Secret exists to prevent.
SCHED_SECRET=$(kubectl get secret agentshield-service-clients -n "$NAMESPACE" \
  -o jsonpath='{.data.scheduler}' 2>/dev/null | base64 -d || true)
[ -z "$SCHED_SECRET" ] && { echo "ERROR: agentshield-service-clients/scheduler not found — deploy has not created it"; exit 1; }

set +e
RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- env \
  S102_TOKEN="$E2E_TOKEN" S102_SUB="$E2E_SUB" S102_SCHED_SECRET="$SCHED_SECRET" \
  S102_MEMBER_TOKEN="$S102_MEMBER_TOKEN" \
  python3 - <<'PY' 2>&1
import json, os, sys, urllib.error, urllib.parse, urllib.request
import uuid

BASE = "http://localhost:8000/api/v1"
# From the ENVIRONMENT. This heredoc is quoted, so "${E2E_SUB}" would arrive as literal
# text — hygiene rule 8d exists because that shipped in eleven suites.
TOKEN = os.environ["S102_TOKEN"]
SUB = os.environ["S102_SUB"]
SCHED_SECRET = os.environ["S102_SCHED_SECRET"]
MEMBER_TOKEN = os.environ["S102_MEMBER_TOKEN"]

results = []


def rec(name, ok, detail=""):
    results.append((name, ok, detail))
    print(("PASS " if ok else "FAIL ") + name + ((" — " + detail) if detail else ""))


def call(method, path, body=None, headers=None):
    """Return (status, body_text). A refusal is a RESULT here, never an exception."""
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, r.read()[:300].decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300].decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001 — a transport error must not read as a pass
        return -1, f"{type(exc).__name__}: {exc}"


AGENT = "zzz-suite102-nonexistent"
# A NONEXISTENT agent on purpose. If auth is refused we get 401 and nothing runs; if auth
# were bypassed we would get 404 from the agent lookup, which is how the pre-fix hole was
# measured in the first place. Either way no agent is ever executed by this suite.
RUN_BODY = {"agent_name": AGENT, "input_payload": {"message": "suite-102"}}
INTERNAL_BODY = {"agent_name": AGENT, "trigger_type": "manual", "run_by": "serviceaccount:scheduler"}

# ── T-S102-001 / 002 — the internal run-start door ───────────────────────────
st, body = call("POST", "/internal/runs/start", INTERNAL_BODY)
rec("T-S102-001 internal run-start with NO credential is 401", st == 401, f"got {st} {body[:120]}")

st, body = call("POST", "/internal/runs/start", INTERNAL_BODY,
                {"X-User-Sub": SUB, "X-User-Id": SUB})
rec("T-S102-002 internal run-start with forged headers/body run_by is 401",
    st == 401, f"got {st} {body[:120]}")

# ── T-S102-003 / 004 — the playground door ───────────────────────────────────
st, body = call("POST", "/playground/runs", RUN_BODY)
rec("T-S102-003 playground run with NO credential is 401", st == 401, f"got {st} {body[:120]}")

st, body = call("POST", "/playground/runs", RUN_BODY, {"X-User-Sub": "eval-runner"})
rec("T-S102-004 playground run with forged 'X-User-Sub: eval-runner' is 401",
    st == 401, f"got {st} {body[:120]}")

# ── T-S102-005 / 006 — the approvals doors ───────────────────────────────────
# A random UUID: if identity were still forgeable this would reach the row lookup and
# answer 404. 401 means it never got that far.
st, body = call("PATCH", f"/approvals/{uuid.uuid4()}",
                {"decision": "approved", "version": 1, "reviewer_id": "studio-user"},
                {"X-User-Sub": SUB, "X-User-Id": SUB})
rec("T-S102-005 decide approval with a forged admin header is 401",
    st == 401, f"got {st} {body[:120]}")

st, body = call("GET", "/approvals?context=production&limit=1")
rec("T-S102-006 list approvals with NO credential is 401", st == 401, f"got {st} {body[:120]}")

# ── T-S102-007 / 008 — the mechanism itself ──────────────────────────────────
# Hoisted out of the try below so the cases that CONSUME a service token (010, 014b) can
# report "prereq failed" instead of dying on a NameError — a suite that crashes tells you
# less than a suite that names which prerequisite broke.
svc_token = None
svc_claims = None
sys.path.insert(0, "/app")
try:
    from auth_middleware import _decode_token, is_trusted_service
    import asyncio

    user_claims = asyncio.run(_decode_token(TOKEN))
    rec("T-S102-007 is_trusted_service rejects a USER token",
        user_claims is not None and is_trusted_service(user_claims) is None,
        f"azp={(user_claims or {}).get('azp')}")

    # Mint a REAL scheduler token with the deployed client secret.
    kc = os.environ.get("KEYCLOAK_URL", "http://agentshield-keycloak")
    realm = os.environ.get("KEYCLOAK_REALM", "agentshield")
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": "scheduler",
        "client_secret": SCHED_SECRET,
    }).encode()
    with urllib.request.urlopen(
        f"{kc}/realms/{realm}/protocol/openid-connect/token", data=form, timeout=20
    ) as r:
        svc_token = json.loads(r.read())["access_token"]
    svc_claims = asyncio.run(_decode_token(svc_token))
    name = is_trusted_service(svc_claims)
    rec("T-S102-008 is_trusted_service accepts a real scheduler client_credentials token",
        name == "scheduler", f"azp={(svc_claims or {}).get('azp')} resolved={name}")
except Exception as exc:  # noqa: BLE001
    rec("T-S102-007 is_trusted_service rejects a USER token", False, f"{type(exc).__name__}: {exc}")
    rec("T-S102-008 is_trusted_service accepts a real scheduler token", False, f"{type(exc).__name__}: {exc}")

# ── T-S102-010 — a SERVICE is not a reviewer, whatever rows point at its subject ──
# WHY THIS CASE IS SHAPED THIS WAY. A plain "scheduler token decides -> 403" assertion
# already passes on 0.2.280, but for the wrong reason: the scheduler's service-account sub
# simply has no 'user_team_assignments' row (measured: 0 rows, 21 in the table), so it is
# denied by ABSENCE OF A GRANT rather than by being a service. '_caller_roles'
# (approvals.py:289) is a bare 'SELECT role FROM user_team_assignments WHERE user_sub = :sub'
# — it cannot tell a human from a service account. So the moment anything writes a row for
# that subject, a service silently becomes a valid HITL reviewer, and with 'platform-admin'
# it takes the 'caller_is_admin' short-circuit at :855 and can decide ANY approval on the
# platform. That is authorization by accident, and this case is what makes it visible.
#
# So: grant the scheduler SA platform-admin, then decide. Today 200. Required 403 — a
# service is refused on KIND, before any role lookup happens. Row removed in the finally.
try:
    import asyncio as _aio
    from sqlalchemy import text as _text
    from db import AsyncSessionLocal

    svc_sub = (svc_claims or {}).get("sub")
    if not svc_token or not svc_sub:
        raise RuntimeError("prereq failed: no scheduler token/sub (see T-S102-008)")

    async def _s102_010():
        # 'Approval.agent_id' is a ForeignKey("agents.id") (models.py:771), so a random UUID
        # is a 500, not a fixture. Borrow any existing agent — this case is about WHO may
        # decide, not about which agent parked.
        async with AsyncSessionLocal() as s:
            agent_id = (await s.execute(_text("SELECT id FROM agents LIMIT 1"))).scalar()
        if not agent_id:
            return None, "SETUP: no agents on this cluster to hang an approval off"
        # A real pending approval. POST /approvals/ needs no credential (recorded as an
        # open gap — the SDK's governed_tool posts it from pods with no platform identity),
        # which is what lets this case build its own fixture.
        tid = f"s102-{uuid.uuid4().hex[:8]}"
        st_c, body_c = call("POST", "/approvals/", {
            "agent_id": str(agent_id), "agent_name": "s102-svc-decide",
            "team": "platform", "thread_id": tid,
            "tool_name": "refund_action", "tool_args": {"amount": 1},
            "risk_level": "high", "context": "production", "timeout_seconds": 1800,
        })
        if st_c not in (200, 201):
            return None, f"SETUP: could not create approval ({st_c} {body_c[:100]})"
        # Read the row back rather than parsing the response: 'call()' truncates bodies to
        # 300 chars so every other case can print one safely, and an ApprovalResponse is
        # longer than that. The thread_id we just chose is the key.
        async with AsyncSessionLocal() as s:
            row = (await s.execute(_text(
                "SELECT id, version FROM approvals WHERE thread_id = :t "
                "ORDER BY created_at DESC LIMIT 1"), {"t": tid})).first()
        if not row:
            return None, f"SETUP: approval created ({st_c}) but no row for thread_id={tid}"
        aid, ver = row[0], row[1]
        async with AsyncSessionLocal() as s:
            await s.execute(_text(
                "INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at) "
                "VALUES (:u, 'platform', 'platform-admin', 'suite-102', now()) "
                "ON CONFLICT (user_sub) DO UPDATE SET role = 'platform-admin'"),
                {"u": svc_sub})
            await s.commit()
        try:
            st_d, body_d = call("PATCH", f"/approvals/{aid}",
                                {"decision": "approved", "version": ver,
                                 "reviewer_id": "suite-102-service"},
                                {"Authorization": "Bearer " + svc_token})
            return st_d, f"got {st_d} {body_d[:120]}"
        finally:
            # Both halves matter. Leaving the grant behind would hand a service account
            # platform-admin permanently — a test must not widen the platform it measures.
            # Leaving the approval behind puts a stray pending/approved row in a real queue.
            async with AsyncSessionLocal() as s:
                await s.execute(_text(
                    "DELETE FROM user_team_assignments WHERE user_sub = :u AND assigned_by = 'suite-102'"),
                    {"u": svc_sub})
                await s.execute(_text("DELETE FROM approvals WHERE thread_id = :t"), {"t": tid})
                await s.commit()

    st10, d10 = _aio.run(_s102_010())
    rec("T-S102-010 a verified SERVICE token cannot decide an approval even as platform-admin",
        st10 == 403, d10)
except Exception as exc:  # noqa: BLE001
    rec("T-S102-010 a verified SERVICE token cannot decide an approval even as platform-admin",
        False, f"{type(exc).__name__}: {exc}")

# ── T-S102-011 / 012 — the two approvals doors P3 left with no identity at all ──
# 401 must beat 404: a random UUID proves the auth decision happens BEFORE the row lookup,
# so existence is not disclosed by probing IDs. Same rule the decide path already states
# (approvals.py:830-836) and the tool-unpublish path already enforces (403 over 409).
st, body = call("GET", f"/approvals/{uuid.uuid4()}")
rec("T-S102-011 read one approval with NO credential is 401 (not 404)",
    st == 401, f"got {st} {body[:120]}")

# A VALID body on purpose: 'reopen_approval' takes a required 'ReopenRequest', and an empty
# request answers 422 before it ever reaches an identity question — which would let this case
# "pass" for a reason that has nothing to do with auth.
st, body = call("POST", f"/approvals/{uuid.uuid4()}/reopen", {"timeout_seconds": 1800})
rec("T-S102-012 reopen an approval with NO credential is 401 (not 404)",
    st == 401, f"got {st} {body[:120]}")

# ── T-S102-013 — the dataset write door ──────────────────────────────────────
# '_resolve_dataset' gated ownership on 'require_owner and caller and ...' under
# get_optional_user, so no credential meant caller=None and the ONLY ownership check was
# skipped: any dataset editable/deletable by anyone. Pre-existing, not a P3 regression.
st, body = call("PATCH", f"/playground/datasets/{uuid.uuid4()}", {"name": "s102-should-not-apply"})
rec("T-S102-013 update a dataset with NO credential is 401 (not 404/200)",
    st == 401, f"got {st} {body[:120]}")

# ── T-S102-014 — the playground decide door, and NO false positive ───────────
# This route decides a HITL approval and can resume a run. It had no auth dependency and no
# ownership check; 'x_user_sub' was only an audit label. It is ALSO the one approval route a
# service legitimately calls (eval-runner, services/eval-runner/main.py:338), so refusing
# every service here would be the wrong fix — hence the second half of this case.
st, body = call("POST", f"/playground/approvals/{uuid.uuid4()}/decide",
                {"decision": "approved"})
rec("T-S102-014a playground decide with NO credential is 401",
    st == 401, f"got {st} {body[:120]}")

# A verified TRUSTED SERVICE must still reach the handler — 404 (approval not found) is the
# success signal, exactly as T-S102-009 uses it for a user. Without this, "deny everything"
# would pass 014a and break batch eval.
if svc_token:
    st, body = call("POST", f"/playground/approvals/{uuid.uuid4()}/decide",
                    {"decision": "approved"},
                    {"Authorization": "Bearer " + svc_token})
    rec("T-S102-014b a verified service token still reaches the playground decide handler",
        st == 404, f"got {st} {body[:120]}")
else:
    rec("T-S102-014b a verified service token still reaches the playground decide handler",
        False, "prereq failed: no scheduler token (see T-S102-008)")

# ── T-S102-015/016/017 — the marketplace catalog: grant filter + revoked grants ──
# GET /api/v1/catalog required NO credential and scoped by 'X-User-Team', a header defaulting
# to "". Two defects, and the FIRST WAS ALREADY LIVE: the Studio client never sends that header
# (zero occurrences in studio/src), so 'if x_user_team:' was skipped on every real request and
# the marketplace listed every team's published artifacts to anyone who asked. Measured before
# the fix: 200 with 86 artifacts and no credential. Only one team publishes today, which is why
# nothing looked wrong; it would have leaked the moment a second team did.
st, body = call("GET", "/catalog")
rec("T-S102-015 catalog with NO credential is 401 (was: 200 with every team's artifacts)",
    st == 401, f"got {st} {body[:120]}")

# The second defect: the grant subquery honoured REVOKED and EXPIRED grants, so revoking a
# share did not un-share it. Measured: 408 asset_grants, 26 revoked, 1 expired. This case
# builds the fixture directly — a published artifact owned by ANOTHER team, plus one grant to
# the member's team that starts REVOKED. Visible only if the filter ignores revoked_at.
try:
    import asyncio as _aio2
    from sqlalchemy import text as _t2
    from sqlalchemy.ext.asyncio import create_async_engine as _mk_engine

    # A DEDICATED engine, created and disposed inside this coroutine's own event loop.
    # NOT db.AsyncSessionLocal: T-S102-010 above already used it inside its own
    # asyncio.run(), which bound that engine's connection pool to THAT loop. A second
    # asyncio.run() gets a new loop and the pooled connections do not transfer —
    # "got Future attached to a different loop". Per-loop engine, no shared pool.

    OTHER_TEAM = "zzz-s102-otherteam"
    MEMBER_HDR = {"Authorization": "Bearer " + MEMBER_TOKEN}

    def catalog_names():
        """Full, UNTRUNCATED catalog list as the member. Deliberately not call(), which
        caps bodies at 300 chars so every other case can print one safely — a catalog
        listing is far longer than that and json.loads would raise on the slice."""
        req = urllib.request.Request(BASE + "/catalog", headers=MEMBER_HDR, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, [x.get("name") for x in json.loads(r.read())]
        except urllib.error.HTTPError as e:
            return e.code, []

    async def _s102_catalog():
        eng = _mk_engine(os.environ["DATABASE_URL"])
        async with eng.begin() as s:
            # Column list read off the live table, not assumed: published_artifacts is
            # (id, name, type, description, source_id, team, created_at, updated_at) — every
            # row here IS published by definition, so there is no publish_status column.
            aid = (await s.execute(_t2(
                "INSERT INTO published_artifacts (id, name, type, team, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 's102-other-artifact', 'agent', :t, now(), now()) "
                "RETURNING id"), {"t": OTHER_TEAM})).scalar()
            await s.execute(_t2(
                "INSERT INTO asset_grants (id, asset_id, asset_type, grantee_team, granted_by, "
                "  granted_at, revoked_at) "
                "VALUES (gen_random_uuid(), :a, 'agent', 'platform', 'suite-102', now(), now())"),
                {"a": aid})
        # revoked grant -> must NOT be visible
        st_r, names_r = catalog_names()
        hidden = st_r == 200 and "s102-other-artifact" not in names_r
        # un-revoke the SAME grant -> must become visible, so the case cannot pass by the
        # artifact being invisible for some unrelated reason
        async with eng.begin() as s:
            await s.execute(_t2("UPDATE asset_grants SET revoked_at = NULL WHERE asset_id = :a"),
                            {"a": aid})
        st_a, names_a = catalog_names()
        shown = st_a == 200 and "s102-other-artifact" in names_a
        async with eng.begin() as s:
            await s.execute(_t2("DELETE FROM asset_grants WHERE asset_id = :a"), {"a": aid})
            await s.execute(_t2("DELETE FROM published_artifacts WHERE id = :a"), {"a": aid})
        await eng.dispose()
        return hidden, shown, st_r, st_a

    h, sh, sr, sa = _aio2.run(_s102_catalog())
    rec("T-S102-016 a REVOKED asset grant does not confer catalog visibility",
        h, f"revoked: status={sr}, artifact hidden={h}")
    rec("T-S102-017 un-revoking the SAME grant makes it visible (filter really is the grant)",
        sh, f"active: status={sa}, artifact visible={sh}")
except Exception as exc:  # noqa: BLE001
    rec("T-S102-016 a REVOKED asset grant does not confer catalog visibility", False,
        f"{type(exc).__name__}: {exc}")
    rec("T-S102-017 un-revoking the SAME grant makes it visible", False,
        f"{type(exc).__name__}: {exc}")

# ── T-S102-009 — no false positive: a real user still works ──────────────────
# The whole suite would also pass if the fix were "refuse everything", so prove a VERIFIED
# caller still gets through. 404 (agent not found) is the success signal: auth passed and
# the handler ran.
st, body = call("POST", "/playground/runs", RUN_BODY, {"Authorization": "Bearer " + TOKEN})
rec("T-S102-009 a verified user token still reaches the handler (404, not 401)",
    st == 404, f"got {st} {body[:120]}")

failed = [n for n, ok, _ in results if not ok]
print(f"\n=== suite-102 summary: PASS={len(results) - len(failed)} FAIL={len(failed)} ===")
sys.exit(1 if failed else 0)
PY
)
DRIVER_RC=$?
set -e
echo "$RESULT"
if [ "$DRIVER_RC" -ne 0 ] || ! echo "$RESULT" | grep -qE "^(PASS|FAIL) "; then
  echo "❌ Suite 102 FAILED (rc=$DRIVER_RC)"
  exit 1
fi
echo "✅ Suite 102 PASSED"
