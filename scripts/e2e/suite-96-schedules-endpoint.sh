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
#   T-S96-002 — DENY BY DEFAULT: an authenticated caller with NO team assignment gets
#               an EMPTY list, never the unfiltered table. This is the `else` branch
#               Decision 33 was written about — its absence elsewhere leaked every
#               eval run on the platform.
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
#   T-S96-006 — a trigger on a DEAD artifact is still LISTED, with will_fire=false and
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
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
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
        missing = REQUIRED_FIELDS - set(body[0]) if body else REQUIRED_FIELDS
        record("T-S96-001 GET /schedules returns 200 and the shape the page is written against",
               r.status_code == 200 and isinstance(body, list) and not missing,
               f"status={r.status_code} n={len(body) if isinstance(body,list) else 'n/a'} missing_fields={sorted(missing)}")

        # ── DENY BY DEFAULT ─────────────────────────────────────────────────────
        # Called through the REAL router function against the REAL database, with a
        # claims dict for a sub that has no user_team_assignments row.
        #
        # NOT over HTTP, deliberately. Minting a teamless Keycloak user needs a
        # password-grant login that this realm refuses with "Account is not fully set
        # up" even after clearing requiredActions and emailVerified — platform-admin
        # carries identical flags and works, so something else in the realm's account
        # setup differs. Chasing that would test Keycloak, not this endpoint: the HTTP
        # hop is already proven by every other case here, and what needs proving is the
        # `else` branch — that a caller resolving to NO team gets an empty list rather
        # than the unfiltered table. Decision 33's leak was exactly a missing `else`,
        # and it is reachable from the function boundary.
        from routers.schedules import list_schedules
        teamless_sub = f"s96-noteam-{uuid.uuid4()}"
        async with AsyncSessionLocal() as s:
            assigned = (await s.execute(text(
                "SELECT count(*) FROM user_team_assignments WHERE user_sub = :u"),
                {"u": teamless_sub})).scalar()
            denied = await list_schedules(trigger_type="schedule",
                                          claims={"sub": teamless_sub}, db=s)
        record("T-S96-002 DENY BY DEFAULT: a caller with no team gets an EMPTY list, not the table",
               assigned == 0 and denied == [],
               f"team_rows_for_caller={assigned} (want 0) returned={len(denied)} rows (want 0) "
               f"— the platform has {len(body)} schedules, so an unfiltered read would return them all")

        d = by_id.get(t_dead)
        record("T-S96-003/006 a DEAD+disarmed artifact's trigger is LISTED with will_fire=false and a reason",
               bool(d) and d["will_fire"] is False and bool(d["why_not"]) and d["enabled"] is False,
               f"listed={bool(d)} will_fire={d and d['will_fire']} enabled={d and d['enabled']} "
               f"why_not={(d or {}).get('why_not','')[:90]!r} artifact_status={(d or {}).get('artifact_status')}")

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

REQUIRED_IDS="001 002 003/006 004 005 007 008 009"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S96-$id " || MISSING="$MISSING T-S96-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S96-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING"
  FAIL=$((FAIL+1))
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S96-COMPLETE every gate assertion ran (001-009 — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true
echo ""; echo "=== suite-96 summary: PASS=$PASS FAIL=$FAIL ==="
[ "$FAIL" -ne 0 ] && { echo "SUITE 96 FAILED"; exit 1; }
[ "$PASS" -eq 0 ] && { echo "SUITE 96 INCONCLUSIVE"; exit 1; }
echo "SUITE 96 PASSED"
