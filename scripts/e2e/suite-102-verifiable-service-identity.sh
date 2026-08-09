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
#
# T-S102-008 is the one that proves the mechanism rather than the refusal: it mints a REAL
# token with the scheduler's client secret and checks `azp` survives verification. Without
# it, every other case here would still pass if the fix were "deny everything".
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "$API_POD" ] && { echo "ERROR: no registry-api pod in $NAMESPACE"; exit 1; }

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

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
  python3 - <<'PY' 2>&1
import json, os, sys, urllib.error, urllib.parse, urllib.request
import uuid

BASE = "http://localhost:8000/api/v1"
# From the ENVIRONMENT. This heredoc is quoted, so "${E2E_SUB}" would arrive as literal
# text — hygiene rule 8d exists because that shipped in eleven suites.
TOKEN = os.environ["S102_TOKEN"]
SUB = os.environ["S102_SUB"]
SCHED_SECRET = os.environ["S102_SCHED_SECRET"]

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
