#!/usr/bin/env bash
# scripts/e2e/suite-101-run-context-anchor-p1.sh
#
# E2E Suite 101: identity P1 durable anchor + P1.5 resume re-hydration.
#
# WHAT THIS PROVES THAT SUITE-99 CANNOT
# -------------------------------------
# `suite-99` proves the RunContext PRIMITIVE: mint, verify, tamper, expiry, three copies
# agreeing. It deliberately asserts nothing about a live run. This suite is the other
# half — that a real run PERSISTS who it belongs to, and that a resume hours later gets
# that identity back.
#
# THE CASE THAT IS THE WHOLE POINT IS T-S101-004
# ----------------------------------------------
# The RCT has a 900-second TTL. A HITL approval routinely sits longer. Before P1.5 the
# resume carried no identity at all, so every post-approval OPA re-check saw `user_id=""`
# and the identity floor — live and denying since WS-2 (`agentshield.rego:22,101-108`,
# AND-ed into `allow` at `:116`) — denied it. The approval would succeed and the work it
# unblocked would then fail, which reads as a broken agent rather than a missing identity.
#
# 004 reproduces exactly that: mint with a 1-second TTL, wait past it, confirm the token
# is genuinely dead, then re-hydrate from the anchor and confirm the fresh token carries
# the SAME human. A suite that skipped the expiry step would pass against code that simply
# reused the original token, which is the design P1.5 rejects.
#
# CASES
#   T-S101-001 — migration 0082 applied: run_context exists on BOTH run tables
#   T-S101-001b — an absent anchor is SQL NULL, never JSON null (0083)
#   T-S101-002 — a REAL POST /playground/runs writes an anchor naming the caller
#   T-S101-003 — the anchor re-hydrates into a token that VERIFIES with the same identity
#   T-S101-004 — re-hydration works AFTER the original token has expired  ← the point
#   T-S101-005 — no anchor => no token. Never a fabricated user_sub=""
#   T-S101-006 — a CORRUPT anchor => no token, never a partial identity
#   T-S101-007 — inherit_anchor appends exactly one hop and is idempotent
#   T-S101-008 — a SERVICE/daemon anchor never gains a human user_sub on the round trip
#   T-S101-009 — every registry-side /resume POST sends the header (derived from the tree)
#
# 005/006/008 are the fail-closed trio. An anchor layer that invents `user_sub=""` on a
# miss is worse than none: "" is an ASSERTION that the run has no user, and for a daemon
# that assertion is exactly what walks it through OPA's identity floor.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
CONTAINER="registry-api"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

API_POD="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -n "$API_POD" ] || { echo "ERROR: no Running registry-api pod in $NAMESPACE"; exit 1; }

# Sourced AFTER API_POD is assigned, and e2e_set_token called BARE — a command
# substitution swallows its abort (lib/e2e-auth.sh; check-e2e-auth-hygiene.sh rule 9).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

echo "=== Suite 101: identity P1 anchor + P1.5 re-hydration ==="
echo "  Namespace: $NAMESPACE"
echo "  Pod:       $API_POD"
echo ""

PASS=0; FAIL=0
record() {
  if [ "$1" = "PASS" ]; then echo "PASS  $2"; PASS=$((PASS+1)); else echo "FAIL  $2"; FAIL=$((FAIL+1)); fi
}

# ── T-S101-001..008 — exercised IN THE POD against the real DB ────────────────
# In the pod, not on the host: the anchor is a Postgres column read through the same
# session factory the routers use, and a host-side reimplementation would prove nothing
# about what the running service does.
RESULT="$(kubectl exec -n "$NAMESPACE" "$API_POD" -c "$CONTAINER" -- env TOK="${E2E_TOKEN}" python3 -c '
import asyncio, json, os, sys, time, urllib.request, urllib.error, uuid
sys.path.insert(0, "/app")

BASE = "http://localhost:8000/api/v1"
TOK = os.environ["TOK"]
TS = str(int(time.time()))
out = []

def check(tid, ok, detail):
    verdict = "PASS" if ok else "FAIL"
    out.append(tid + "|" + verdict + "|" + str(detail)[:340])

def call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Authorization": "Bearer " + TOK}
    if data: h["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        raw = e.read()
        try: return e.code, (json.loads(raw) if raw else {})
        except Exception: return e.code, {"raw": raw[:200].decode("utf-8", "replace")}

import run_context as rc
import run_context_anchor as anchor
from db import AsyncSessionLocal
from models import PlaygroundRun
from sqlalchemy import select, text


async def main():
    # ── 001 — the migration actually ran ──────────────────────────────────────
    async with AsyncSessionLocal() as s:
        cols = (await s.execute(text(
            "SELECT table_name FROM information_schema.columns "
            "WHERE column_name = :c AND table_name IN (:a, :b)"
        ), {"c": "run_context", "a": "playground_runs", "b": "agent_runs"})).scalars().all()
    check("T-S101-001", set(cols) == {"playground_runs", "agent_runs"},
          "tables carrying run_context = %s (both required: anchoring one and not the "
          "other makes resume identity work in sandbox and fail in production)" % sorted(cols))

    # ── 002 — a REAL run writes the anchor ────────────────────────────────────
    # Through the endpoint, not by inserting a row: the question is whether the SHIPPED
    # create path anchors, and a hand-built row would answer a different question.
    agent = "s101-anchor-" + TS
    sc, _ = call("POST", "/agents/", {"name": agent, "team": "platform",
                                      "description": "identity P1 anchor probe"})
    if sc != 201:
        for tid in ("T-S101-002", "T-S101-003", "T-S101-004"):
            check(tid, False, "fixture agent create -> %s; cases proved nothing" % sc)
        run_id = None
    else:
        rc_code, run_body = call("POST", "/playground/runs",
                                 {"agent_name": agent, "message": "identity anchor probe"})
        run_id = run_body.get("run_id")
        anchored = None
        if run_id:
            async with AsyncSessionLocal() as s:
                anchored = (await s.execute(
                    select(PlaygroundRun.run_context).where(PlaygroundRun.id == uuid.UUID(run_id))
                )).scalar_one_or_none()
        me_sc, me = call("GET", "/me")
        caller_sub = (me or {}).get("sub") or (me or {}).get("user_sub") or ""
        # NO conditional on caller_sub. The first cut wrote `if ok and caller_sub:` around
        # the equality, so a /me response without a sub silently dropped the STRONGEST
        # assertion and the case still went green — proving only "an anchor exists", not
        # "the anchor names the right person". An anchor holding the WRONG sub is worse
        # than none: the run resumes as somebody else and OPA correctly authorizes it.
        # If the caller cannot be identified the case must FAIL and say so.
        ok = (
            bool(caller_sub)
            and bool(anchored)
            and anchored.get("origin") == "playground"
            and anchored.get("user_sub") == caller_sub
        )
        check("T-S101-002", ok,
              "run=%s anchor=%s caller=%s%s" % (
                  rc_code, json.dumps(anchored)[:140], caller_sub[:12],
                  "" if caller_sub else "  <-- GET /me returned no sub; the case cannot "
                                        "compare and fails rather than proving less"))

    # ── 003 — the anchor re-hydrates into a VERIFIABLE token ──────────────────
    if run_id:
        async with AsyncSessionLocal() as s:
            tok = await anchor.rehydrate(s, run_id)
        ident = None
        try:
            ctx = rc.verify(tok) if tok else None
            ident = (ctx.user_sub, ctx.origin) if ctx else None
        except Exception as exc:
            ident = ("VERIFY_FAILED", str(exc))
        check("T-S101-003", bool(tok) and ident and ident[1] == "playground" and ident[0],
              "token=%s identity=%s" % (bool(tok), ident))
    else:
        check("T-S101-003", False, "no run from 002 — case proved nothing")

    # ── 004 — THE POINT: re-hydration outlives the original token ─────────────
    if run_id:
        async with AsyncSessionLocal() as s:
            claims = (await s.execute(
                select(PlaygroundRun.run_context).where(PlaygroundRun.id == uuid.UUID(run_id))
            )).scalar_one_or_none()
        # Mint the SHORT-lived transport token the way a run start does, then let it die.
        original = rc.mint(rc.RunContext.from_claims(claims), ttl_seconds=1)
        time.sleep(2)
        expired = False
        try:
            rc.verify(original)
        except rc.RunContextError:
            expired = True
        # Now the resume path, hours later in real life.
        async with AsyncSessionLocal() as s:
            fresh = await anchor.rehydrate(s, run_id)
        same_user = False
        try:
            same_user = bool(fresh) and rc.verify(fresh).user_sub == claims.get("user_sub")
        except Exception:
            same_user = False
        check("T-S101-004", expired and same_user,
              "original_token_expired=%s rehydrated_same_user=%s "
              "(if expired=False the case proved nothing — the token never died)"
              % (expired, same_user))
    else:
        check("T-S101-004", False, "no run from 002 — case proved nothing")

    # ── 001b — an absent anchor is SQL NULL, never JSON null ─────────────────
    # Folded into 001 because it is the same question: does the column tell the truth
    # about its own presence. SQLAlchemy JSONB defaults to none_as_null=False, so
    # assigning Python None wrote JSON `null` — a value for which `run_context IS NOT
    # NULL` is TRUE while there is no identity. 52 such rows existed in agent_runs before
    # the fix (workflow member children inheriting from an unanchored parent). Behaviour
    # was fine; the DATA lied, and every future audit query would inherit the lie.
    async with AsyncSessionLocal() as s:
        jn = {}
        for tbl in ("playground_runs", "agent_runs"):
            jn[tbl] = (await s.execute(text(
                "SELECT count(*) FROM " + tbl + " WHERE jsonb_typeof(run_context) = :t"
            ), {"t": "null"})).scalar()
    check("T-S101-001b", all(v == 0 for v in jn.values()),
          "rows storing JSON null instead of SQL NULL: %s (model declares "
          "JSONB(none_as_null=True); migration 0083 cleaned the pre-existing ones)" % jn)

    # ── 005 — no anchor => NO token. Never a fabricated empty user ────────────
    async with AsyncSessionLocal() as s:
        missing = await anchor.rehydrate(s, str(uuid.uuid4()))
    check("T-S101-005", missing is None,
          # NO APOSTROPHES anywhere in this driver: the whole program is a single-quoted
          # bash string, so one closes it early and Python then sees a truncated file.
          # It failed here as "unterminated string literal (line 134)" and took all eight
          # in-pod cases down at once. Same trap suite-42 and suite-99 have paid for.
          "rehydrate(unknown thread) -> %r. None means we do not know; a token with "
          "user_sub=EMPTY would ASSERT the run has no user, which for a daemon is exactly "
          "what walks it through the OPA identity floor" % (missing,))

    # ── 006 — a corrupt anchor is corruption, and re-hydration is TOTAL ───────
    # Two properties in one case, both about the same contract. A corrupt anchor must
    # yield NO token (never a partial identity), and NOTHING here may raise: four of the
    # five resume call sites sit inside a broad `except Exception: return`, so an escaping
    # exception would not degrade identity — it would silently cancel the RESUME and hang
    # the run. Losing identity is a denial someone can see; losing the resume is a run
    # that never finishes and names nothing.
    # The {"user_sub": <non-str>} shapes are the ones that found a REAL hole:
    # RunContext.from_claims does str(claims.get("user_sub") or ""), which stringifies
    # anything, so an anchor holding {"user_sub": {"a": 1}} minted a perfectly valid token
    # whose user is the literal text "{\x27a\x27: 1}" — corruption laundered into an
    # identity OPA would then authorize. Type validation now happens at the DB boundary
    # (run_context_anchor._anchor_is_well_formed), which is where the data stops being
    # signed. Keep these shapes: they are the regression.
    shapes = [{"actor_chain": "not-a-list"}, {"actor_chain": 7}, {"actor_chain": ["ok", 5]},
              {"user_sub": {"a": 1}}, {"user_sub": 123}, {"is_service_call": "yes"},
              "not-a-dict", 42, []]
    results, raised = [], None
    for i, bad in enumerate(shapes):
        try:
            results.append(anchor.remint(bad, label="corrupt-probe-%d" % i))
        except Exception as exc:  # the contract says this cannot happen
            raised = "%s: %r" % (type(exc).__name__, bad)
            break
    check("T-S101-006", raised is None and all(r is None for r in results),
          "remint over %d corrupt shapes -> %r raised=%s"
          % (len(shapes), results, raised or "none (TOTAL, as contracted)"))

    # ── 007 — inherit_anchor appends ONE hop and is idempotent ────────────────
    base = anchor.anchor_value(anchor.build_context(user_sub="u1", user_team="platform",
                                                    origin="production"))
    once = anchor.inherit_anchor(base, "member-a")
    twice = anchor.inherit_anchor(once, "member-a")
    thrice = anchor.inherit_anchor(twice, "member-b")
    check("T-S101-007",
          once.get("actor_chain") == ["member-a"]
          and twice.get("actor_chain") == ["member-a"]
          and thrice.get("actor_chain") == ["member-a", "member-b"]
          and anchor.inherit_anchor(None, "x") is None,
          "chains: once=%s twice=%s thrice=%s none=%r"
          % (once.get("actor_chain"), twice.get("actor_chain"), thrice.get("actor_chain"),
             anchor.inherit_anchor(None, "x")))

    # ── 008 — a service/daemon anchor never gains a human on the round trip ───
    # The escalation this guards: agent_runs.run_by holds a SERVICE subject for a daemon
    # run. Re-deriving user_sub from it would hand the daemon a fabricated human identity
    # and walk it straight through OPA user_delegated arm.
    svc = anchor.anchor_value(anchor.build_context(
        user_sub="", user_team="platform", origin="production",
        is_service_call=True, service_name="serviceaccount:scheduler"))
    svc_tok = anchor.remint(svc, label="service-probe")
    svc_ctx = rc.verify(svc_tok) if svc_tok else None
    check("T-S101-008",
          svc_ctx is not None and svc_ctx.user_sub == ""
          and svc_ctx.is_service_call is True
          and svc_ctx.service_name == "serviceaccount:scheduler",
          "user_sub=%r is_service_call=%s service_name=%r (an empty user_sub here is "
          "CORRECT — a daemon has no human, and OPA daemon arm is what admits it)"
          % (getattr(svc_ctx, "user_sub", None), getattr(svc_ctx, "is_service_call", None),
             getattr(svc_ctx, "service_name", None)))

    # cleanup
    call("DELETE", "/agents/" + agent)
    print("\n".join(out))

asyncio.run(main())
' 2>&1 || true)"

for tid in T-S101-001 T-S101-001b T-S101-002 T-S101-003 T-S101-004 T-S101-005 T-S101-006 T-S101-007 T-S101-008; do
  line="$(echo "$RESULT" | grep "^${tid}|" || true)"
  if [ -z "$line" ]; then
    record FAIL "${tid} produced no result  |  driver tail: $(echo "$RESULT" | tail -3 | tr '\n' ' ')"
  elif [ "$(echo "$line" | cut -d'|' -f2)" = "PASS" ]; then
    record PASS "${tid}  |  $(echo "$line" | cut -d'|' -f3)"
  else
    record FAIL "${tid}  |  $(echo "$line" | cut -d'|' -f3)"
  fi
done

# ── T-S101-009 — every resume POST carries the header ─────────────────────────
# DERIVED FROM THE TREE, never a hand-written list — and that is not a stylistic
# preference. Writing this phase, the hand list of resume doors had FOUR entries; the
# derived sweep found FIVE. The fifth (`approval_timeout_worker.py`) resumes a pod after
# an approval times out, re-enters the graph, and can make further governed tool calls.
# It would have shipped unidentified. R2 shipped a hand-written sweep that missed 28
# suites; this is the same lesson at a smaller scale.
#
# WHAT THIS CASE CATCHES, AND WHAT IT DOES NOT — stated so nobody over-reads a green:
#   CATCHES  a NEW resume door added with no identity wiring near it (verified red-first
#            by appending exactly such a function and watching this flag it).
#   MISSES   a door whose wiring is present but WRONG — an empty dict, a stale variable,
#            the wrong run's anchor. It is a structural check, not a semantic one.
#            T-S101-002/003/004 are what prove the identity is actually correct.
MISSING="$(cd "$REPO_ROOT" && python3 - <<'PY'
import pathlib, re
root = pathlib.Path("services/registry-api")
bad = []
for f in sorted(root.rglob("*.py")):
    lines = f.read_text().splitlines()
    for i, ln in enumerate(lines):
        if "/resume/" not in ln:
            continue
        # Only POSTs to a pod. Comments and docstring mentions are not call sites.
        if ln.lstrip().startswith("#"):
            continue
        if not re.search(r'f"\{[^}]+\}/resume/', ln):
            continue
        # TWO conditions, because the first one ALONE IS VACUOUS — and this check shipped
        # that way for about ten minutes. `from run_context import RCT_HEADER` at the top
        # of the file sits inside any reasonable proximity window, so deleting the header
        # from the actual call left the check green. Only red-first surfaced it: removing
        # a header and seeing NOTHING flagged. The import lines are now excluded from the
        # window and the call site itself must pass a `headers=` kwarg.
        #
        # The window is two-sided on purpose: some paths set the header ABOVE the call,
        # the streaming resumes build the dict inline BELOW it. A one-sided window was the
        # first bug here — it wrongly flagged playground.py.
        lo, hi = max(0, i - 35), min(len(lines), i + 35)
        window = [l for l in lines[lo:hi] if not re.match(r"\s*(from|import)\s", l)]
        named = any("RCT_HEADER" in l for l in window)
        # `client.post(url, json=..., headers=...)` on one line, or an httpx
        # `stream("POST", url, ..., headers={...})` spread over several.
        # Forward window matched to `window` above: the URL is built first and the POST
        # can be 20+ lines later once the re-hydration block sits between them. An i+20
        # window was too tight and flagged approval_timeout_worker.py while it was
        # correctly wired — a FALSE positive is as much a broken check as a false negative.
        call_area = "\n".join(lines[max(0, i - 4): min(len(lines), i + 40)])
        passes_headers = bool(re.search(r"\.(post|stream)\((?:.|\n)*?headers=", call_area))
        if not (named and passes_headers):
            bad.append(f"{f}:{i+1}(named={named},headers={passes_headers})")
print(",".join(bad))
PY
)"
if [ -z "$MISSING" ]; then
  record PASS "T-S101-009 every registry-side /resume POST sends RCT_HEADER  |  derived from the tree, not a list"
else
  record FAIL "T-S101-009 resume POST(s) with NO run-context header: ${MISSING}  |  a resume without identity reaches OPA as user_id='' and the identity floor denies it"
fi

echo ""
echo "======================================================="
echo "  Suite 101 Results: PASS=${PASS}  FAIL=${FAIL}"
echo "======================================================="
[ "$FAIL" -gt 0 ] && exit 1 || exit 0
