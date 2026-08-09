#!/usr/bin/env bash
# scripts/e2e/suite-96-schedules-endpoint.sh
#
# E2E Suite 96: GET /api/v1/schedules — the cross-artifact operations read (R5). NO fakes.
#
# WHY THIS ENDPOINT EXISTS
# ------------------------
# Triggers were reachable only per-artifact, so "what is scheduled on this platform,
# and is it actually going to run?" had no home. 37 triggers sat armed on deleted and
# archived artifacts — including a never-published workflow firing every 15 minutes for
# days — because there was nowhere to notice them.
#
# WHAT THIS SUITE IS REALLY GUARDING
# ----------------------------------
# `will_fire`. The page's contract says it is "computed server-side from the SAME
# predicate the scheduler reads". That sentence is the whole point: a page that says a
# schedule will run, while a real fire would refuse it, is worse than no page. The
# predicate has been wrong TWICE in one day when stated independently
# (`w.status='published'` matched nothing; `w.publish_status='published'` was too
# strict), so the cases below check `will_fire` against the real causes of not-firing
# rather than against a restatement of the rule.
#
#   T-S96-001 — the endpoint returns 200 and the shape the page is written against.
#               RED before routers/schedules.py exists (404).
#   T-S96-002 — CONTRACT CHANGE (R0 / FR-5): DENY BY DEFAULT now means REFUSED, not
#               "given an empty list". A caller with NO user_team_assignments row is
#               refused with rbac.NoPlatformRole (403 over HTTP) *before* the team
#               filter ever runs, because team_name is NOT NULL — so "no team" and "no
#               row" are the same condition, and schedules.py's `else` is unreachable.
#               The security property under test is UNCHANGED and still non-vacuous:
#               an unfiltered read must never happen, and the evidence line still names
#               how many schedules the platform holds so "returned nothing" cannot pass
#               trivially. The old assertion (`denied == []`) would now RAISE, and
#               leaving it — or softening it to swallow the raise — would be a test that
#               stayed green through a real contract change. Decision 33 is still what
#               this guards; R0 made the refusal louder.
#   T-S96-003 — a DISARMED trigger is LISTED (the page's job is showing it) with
#               will_fire=false and why_not carrying the disarm reason.
#   T-S96-004 — a sandbox-only agent's schedule: will_fire=false and why_not names the
#               ENVIRONMENT, because it comes from resolve_dispatch_target — the same
#               call the run door makes — not from a second rule.
#   T-S96-005 — POSITIVE CONTROL: a production-deployed agent's armed schedule reports
#               will_fire=true. Without it, 003/004 are equally satisfied by
#               "everything reports false", which is how the liveness predicate stayed
#               green while it was matching nothing.
#   T-S96-007 — an undeclared PATCH field ({"armed": false}) cannot masquerade as a
#               write: the row is re-read and must be unchanged. This is the defect the
#               page shipped with — 200 OK, "Disarmed" toast, nothing written.
#   T-S96-008 — disarming via `enabled` PERSISTS and flips will_fire on re-read.
#   T-S96-009 — re-enabling clears disarm_reason/disarmed_at, so no stale explanation
#               survives beside a live schedule.
#   T-S96-006 — CONTRACT CHANGE (2026-08-02): a deleted agent's SCHEDULE is no longer
#               listed, because it is no longer stored — agent delete now REMOVES
#               schedule triggers (trigger_lifecycle.delete_schedule_triggers). What
#               is still guaranteed, and what this now asserts, is that a DISARMED
#               trigger on a dead artifact that IS kept (a deleted agent's WEBHOOK,
#               an archived workflow's schedule) remains LISTED with a reason — that
#               is the zombie-visibility guarantee, and it is unchanged.
#   (was)      — a trigger on a DEAD artifact is still LISTED, with will_fire=false and
#               why_not naming the artifact state. Listing it is the feature; the 37
#               zombies were invisible precisely because nothing listed them.
#
# Detached in-pod driver (PYTHONPATH=/app -> result file); polled with short execs.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_require_token "$NAMESPACE" "$API_POD" >/dev/null
e2e_install_pyauth "$NAMESPACE" "$API_POD"

echo "=== Suite 96: GET /api/v1/schedules (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s96_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s96_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s96_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, uuid, httpx
import sys as _sys; _sys.path.insert(0, "/tmp")
from e2e_auth import BearerAuth, mint
from sqlalchemy import select, text
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment

BASE = "http://localhost:8000/api/v1"
# AUTH IS ON THE CLIENT (auth=BearerAuth()), NOT IN THIS FILE.
# The R3/E2E_SUB scripted passes spliced BASH lines into this Python heredoc
# (`source .../lib/e2e-auth.sh`, `e2e_set_token ...`). Python received shell text
# and died with SyntaxError, so the driver produced no result and the suite
# reported a driver error instead of a test failure. Removed 2026-08-09; the
# suite already sourced the lib, called e2e_require_token and e2e_install_pyauth
# ABOVE the heredoc, which is where they belong.

# X-User-Sub removed 2026-08-09: this heredoc is QUOTED, so "${E2E_SUB}" was never
# interpolated and the header carried that literal 12-character string. It is a
# fallback the handlers only consult when there is no token (armed_by =
# (user or {}).get("sub") or x_user_sub), and BearerAuth() below always supplies
# one — so the value was both wrong and unused. Sending a real sub would need it
# threaded via env, which nothing here asserts on.
# Bearer: POST /agents/ is gated (R2). This suite passed only because its agent create
# tolerates a non-201, so the 401 was absorbed and the later cases ran on rows left
# behind by earlier runs — green while asserting against stale fixtures.
H = {"X-User-Team": "platform"}
OUT = os.environ["S96_OUT"]
SFX = uuid.uuid4().hex[:6]
SBX  = f"s96-sbx-{SFX}"      # sandbox only  -> will_fire False, env reason
PROD = f"s96-prod-{SFX}"     # production    -> will_fire True (positive control)
DEAD = f"s96-dead-{SFX}"     # deleted       -> listed, will_fire False
INSTR = "Autonomous check agent. Reply READY."

REQUIRED_FIELDS = {
    "trigger_id","trigger_type","artifact_kind","artifact_id","artifact_name",
    "artifact_status","cron_expression","enabled","will_fire","why_not",
    "last_run_status","alert_on_failure",
}


async def wait_running(name, environment, t=80):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(select(Deployment).where(
                    Deployment.agent_id == a.id, Deployment.environment == environment)
                    .order_by(Deployment.deployed_at.desc()).limit(1))).scalars().first()
                if d and d.status == "running":
                    return True
        await asyncio.sleep(3)
    return False


async def main():
    results = []
    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=60.0, auth=BearerAuth())
    try:
        pid = (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

        async def mk(name):
            r = await c.post("/agents/", json={
                "name": name, "team": "platform", "agent_type": "declarative",
                "execution_shape": "durable", "agent_class": "daemon",
                "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []}})
            assert r.status_code in (200, 201), f"create {name}: {r.status_code} {r.text[:150]}"

        async def arm(name):
            r = await c.post(f"/agents/{name}/triggers", json={
                "trigger_type": "schedule", "cron_expression": "0 0 * * *", "alert_on_failure": False})
            assert r.status_code in (200, 201), f"arm {name}: {r.status_code} {r.text[:150]}"
            return r.json()["id"]

        # fixtures
        await mk(SBX);  await c.post(f"/agents/{SBX}/deploy",  json={"environment": "sandbox"})
        await mk(PROD); await c.post(f"/agents/{PROD}/deploy", json={"environment": "sandbox"})
        await mk(DEAD)
        t_sbx, t_prod, t_dead = await arm(SBX), await arm(PROD), await arm(DEAD)
        # A webhook on the SAME agent, so the asymmetry is proven on one artifact.
        rh = await c.post(f"/agents/{DEAD}/triggers",
                          json={"trigger_type": "webhook", "alert_on_failure": False})
        t_dead_hook = rh.json()["id"] if rh.status_code in (200, 201) else None
        await wait_running(SBX, "sandbox"); await wait_running(PROD, "sandbox")

        # PROD -> production (eval gate satisfied as an explicit fixture step; the gate
        # itself is owned by suite-14/15/17 and running a real eval here would couple
        # this suite to something it does not test)
        av = await c.get(f"/agents/{PROD}/versions")
        vid = av.json()[0]["id"] if av.status_code == 200 and av.json() else None
        if vid:
            async with AsyncSessionLocal() as s:
                v = (await s.execute(select(AgentVersion).where(AgentVersion.id == uuid.UUID(vid)))).scalars().first()
                if v: v.eval_passed = True; await s.commit()
        await c.post(f"/agents/{PROD}/deploy", json={"environment": "production", "version_id": vid})
        prod_up = await wait_running(PROD, "production")
        # DEAD -> deleted (write-side disarm fires, so it is both dead AND disarmed)
        await c.delete(f"/agents/{DEAD}")

        r = await c.get("/schedules", params={"trigger_type": "schedule"})
        body = r.json() if r.status_code == 200 else []
        by_id = {x["trigger_id"]: x for x in body} if isinstance(body, list) else {}
        rw = await c.get("/schedules", params={"trigger_type": "webhook"})
        by_hook = {x["trigger_id"]: x for x in rw.json()} if rw.status_code == 200 else {}
        missing = REQUIRED_FIELDS - set(body[0]) if body else REQUIRED_FIELDS
        record("T-S96-001 GET /schedules returns 200 and the shape the page is written against",
               r.status_code == 200 and isinstance(body, list) and not missing,
               f"status={r.status_code} n={len(body) if isinstance(body,list) else 'n/a'} missing_fields={sorted(missing)}")

        # ── DENY BY DEFAULT ─────────────────────────────────────────────────────
        # Called through the REAL router function against the REAL database, with a
        # claims dict for a sub that has no user_team_assignments row.
        #
        # CONTRACT CHANGE (R0 / FR-5). This case used to assert `denied == []`. It now
        # asserts REFUSAL: rbac.get_user_global_role raises NoPlatformRole for a sub
        # with no row, so the call never reaches the team filter — and because
        # team_name is NOT NULL, "no team" and "no row" are the same condition. Keeping
        # the old assertion would have errored; softening it to tolerate either outcome
        # would have proven nothing. Rewritten, not deleted (Decision 39's lesson).
        #
        # NOT over HTTP, deliberately. Minting a teamless Keycloak user needs a
        # password-grant login that this realm refuses with "Account is not fully set
        # up" even after clearing requiredActions and emailVerified — platform-admin
        # carries identical flags and works, so something else in the realm's account
        # setup differs. Chasing that would test Keycloak, not this endpoint: the HTTP
        # hop is already proven by every other case here, and what needs proving is
        # that a caller resolving to NO row is refused rather than handed the
        # unfiltered table. Decision 33's leak was exactly a missing refusal, and it is
        # reachable from the function boundary. (T-S97-010b covers the 403 over HTTP.)
        from routers.schedules import list_schedules
        from rbac import NoPlatformRole
        teamless_sub = f"s96-noteam-{uuid.uuid4()}"
        async with AsyncSessionLocal() as s:
            assigned = (await s.execute(text(
                "SELECT count(*) FROM user_team_assignments WHERE user_sub = :u"),
                {"u": teamless_sub})).scalar()
            refused = False; returned = None
            try:
                returned = await list_schedules(trigger_type="schedule",
                                                claims={"sub": teamless_sub}, db=s)
            except NoPlatformRole:
                refused = True
        record("T-S96-002 DENY BY DEFAULT: a caller with NO role row is REFUSED, never given the table",
               assigned == 0 and refused and returned is None,
               f"team_rows_for_caller={assigned} (want 0) refused={refused} (want True) "
               f"returned={returned!r} (want None) — the platform has {len(body)} schedules, "
               f"so an unfiltered read would return them all")

        # The deleted agent's SCHEDULE must be GONE — not merely disarmed. Delete now
        # removes schedule triggers outright; a disarmed schedule on a deleted agent
        # is inert (T-S95-004) and was two-thirds of this page's rows.
        d = by_id.get(t_dead)
        # ...and its WEBHOOK must SURVIVE, disarmed and listed. Deleting a webhook
        # trigger cascades away `webhook_clients` and the credentials registered
        # against it, so webhooks keep the disarm treatment — and a kept-but-disarmed
        # trigger on a dead artifact is exactly the row this page exists to show.
        h = by_hook.get(t_dead_hook) if t_dead_hook else None
        record("T-S96-003/006 a deleted agent's SCHEDULE is removed; its WEBHOOK stays LISTED and disarmed",
               d is None
               and (t_dead_hook is None or (h is not None and h["enabled"] is False and bool(h["why_not"]))),
               f"schedule_listed={d is not None} (want False — the row is deleted) | "
               f"webhook_listed={h is not None} enabled={h and h['enabled']} "
               f"why_not={(h or {}).get('why_not','')[:70]!r} (want listed + disarmed + a reason)")

        s_ = by_id.get(t_sbx)
        why = ((s_ or {}).get("why_not") or "").lower()
        record("T-S96-004 sandbox-only agent: will_fire=false and why_not names the ENVIRONMENT",
               bool(s_) and s_["will_fire"] is False and ("production" in why or "deploy" in why),
               f"will_fire={s_ and s_['will_fire']} why_not={(s_ or {}).get('why_not','')[:110]!r}")

        p_ = by_id.get(t_prod)
        record("T-S96-005 POSITIVE CONTROL: a production-deployed agent's schedule reports will_fire=true",
               prod_up and bool(p_) and p_["will_fire"] is True and p_["why_not"] is None,
               f"prod_running={prod_up} will_fire={p_ and p_['will_fire']} why_not={(p_ or {}).get('why_not')!r} "
               f"— without this, 003/004 pass equally well when EVERYTHING reports false")

        # ── The write the page performs, read back ──────────────────────────────
        # The Schedules page's Disarm button PATCHed `{"armed": false}`. There is no
        # `armed` field on AgentTriggerUpdate, so FastAPI dropped it, the handler's
        # `exclude_none` loop saw an EMPTY body, and the request answered 200 having
        # written nothing — while the UI toasted "Disarmed". A status-code assertion
        # cannot catch that; only re-reading the row can.
        r_ghost = await c.patch(f"/agents/{PROD}/triggers/{t_prod}", json={"armed": False})
        after_ghost = await c.get("/schedules", params={"trigger_type": "schedule"})
        g = {x["trigger_id"]: x for x in after_ghost.json()}.get(t_prod) or {}
        record("T-S96-007 an undeclared field cannot masquerade as a write (armed= is not a column)",
               g.get("enabled") is True,
               f"PATCH armed=false -> {r_ghost.status_code}; enabled is still {g.get('enabled')} "
               f"(want True — the field does not exist, so nothing may change). Arm state is `enabled`.")

        r_off = await c.patch(f"/agents/{PROD}/triggers/{t_prod}", json={"enabled": False})
        after_off = await c.get("/schedules", params={"trigger_type": "schedule"})
        o = {x["trigger_id"]: x for x in after_off.json()}.get(t_prod) or {}
        record("T-S96-008 disarming via `enabled` PERSISTS and flips will_fire on re-read",
               r_off.status_code == 200 and o.get("enabled") is False and o.get("will_fire") is False,
               f"status={r_off.status_code} enabled={o.get('enabled')} will_fire={o.get('will_fire')} "
               f"why_not={(o.get('why_not') or '')[:70]!r}")

        # Re-enabling must clear the disarm record. It is one column shared by an
        # author's pause and a lifecycle disarm, so a reason that outlives the disarm
        # renders as "disabled because the agent was deleted" beside a LIVE schedule.
        r_on = await c.patch(f"/agents/{PROD}/triggers/{t_prod}", json={"enabled": True})
        after_on = await c.get("/schedules", params={"trigger_type": "schedule"})
        n_ = {x["trigger_id"]: x for x in after_on.json()}.get(t_prod) or {}
        record("T-S96-009 re-enabling clears the disarm record — no stale reason on a live row",
               r_on.status_code == 200 and n_.get("enabled") is True
               and n_.get("disarm_reason") is None and n_.get("disarmed_at") is None,
               f"status={r_on.status_code} enabled={n_.get('enabled')} "
               f"disarm_reason={n_.get('disarm_reason')!r} disarmed_at={n_.get('disarmed_at')!r}")

        # ── The refusal must name a remedy that WORKS, not just one that exists ──
        # 0.2.244 fixed "deploy to production" (unreachable from the UI) -> "Publish".
        # Publish is reachable but INSUFFICIENT: it writes a published_artifacts row
        # (catalog listing) and NOT a production_deployments row, so an operator does
        # the named thing, watches it succeed, and gets this identical message back.
        # Reaching production takes three steps; the message must say so.
        # docs/bugs/publish-does-not-create-a-production-deployment.md
        msg = (s_ or {}).get("why_not") or ""
        low = msg.lower()
        names_all_three = ("publish" in low
                           and ("queue" in low or "approve" in low)
                           and ("marketplace" in low or "deploy latest" in low))
        record("T-S96-010 the production remedy names ALL THREE steps, not just publish",
               names_all_three,
               f"why_not={msg[:170]!r} — publish={'publish' in low} "
               f"approve={'queue' in low or 'approve' in low} "
               f"catalog_deploy={'marketplace' in low or 'deploy latest' in low}. "
               f"Publishing alone only creates the catalog listing; naming it as THE "
               f"remedy sends the operator round a loop.")

    except Exception as exc:
        import traceback
        record("T-S96-999 driver ran every case without crashing", False,
               f"driver CRASHED: {type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")
        for n in (SBX, PROD, DEAD):
            try: await c.delete(f"/agents/{n}")
            except Exception: pass
        await c.aclose()

asyncio.run(main())
PY

echo "Running driver detached in-pod (two deploys can take ~3 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S96_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

for i in $(seq 1 72); do
  sleep 5
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null && break
done

echo ""; echo "=== Results ==="
RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no result file — driver log:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -30 "$RUNLOG" 2>/dev/null || true
  exit 1
fi



PASS=0; FAIL=0
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

REQUIRED_IDS="001 002 003/006 004 005 007 008 009 010"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S96-$id " || MISSING="$MISSING T-S96-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S96-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING"
  FAIL=$((FAIL+1))
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S96-COMPLETE every gate assertion ran (001-010 — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true
echo ""; echo "=== suite-96 summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "SUITE 96 FAILED"; exit 1; }
[ "$PASS" -eq 0 ] && { echo "SUITE 96 INCONCLUSIVE"; exit 1; }
echo "SUITE 96 PASSED"
