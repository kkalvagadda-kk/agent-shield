#!/usr/bin/env bash
# scripts/e2e/suite-94-trigger-dispatch-environment.sh
#
# E2E Suite 94: trigger dispatch resolves ONE environment — admission and address
# cannot disagree. NO fakes.
#
# THE BUG THIS REPRODUCES (docs/bugs/trigger-dispatch-environment-mismatch.md)
# ---------------------------------------------------------------------------
# `POST /internal/runs/start` admitted a run if ANY `deployments` row was
# `running` — no environment filter (routers/internal.py:394-404) — and then
# dispatched to a HARDCODED `{agent}-production` Service (:124 durable, :143
# reactive). A sandbox-only agent therefore passed the door and DNS-failed
# 300ms later with `dispatch failed: [Errno -2] Name or service not known`.
# 1,197 failed scheduled runs accumulated this way, none of them legible.
#
# This is the SAME class `agent_endpoints.py`'s module docstring was written to
# kill ("the pod URL was built in EIGHT places, some environment-aware and some
# hardcoding `-production`"). internal.py was a surviving instance: it imported
# `team_namespace` from that module but still hand-built the URL.
#
# The fix makes the illegal state unrepresentable rather than guarded:
# `agent_endpoints.resolve_dispatch_target(db, agent, environment=...)` owns BOTH
# "is this dispatchable" and "what is its address", and builds the host from the
# very row it validated — so there is no second place to disagree with.
#
# WHY THIS SUITE IS RED AGAINST THE OLD CODE (regression-test-first, CLAUDE.md #7)
# T-S94-001 asserts the failure reason names the ENVIRONMENT. Old code produces a
# DNS error instead, so it FAILS — which is the point. A suite that stayed green
# through 1,197 real failures is itself defective; this is the correction.
#
#   T-S94-001 — a sandbox-only agent's schedule fire is rejected at ADMISSION with
#               a reason naming the environment, NOT a DNS error. RED before fix.
#   T-S94-002 — the failed run is still RECORDED (a rejected fire is evidence, not
#               silence) and carries trigger_id, so it is reachable by schedule.
#   T-S94-003 — trigger-scoped run read: GET /agents/{name}/triggers/{tid}/runs
#               returns that run. This is what lets the UI show a schedule's
#               outcome without a deployment FK (the two deployment-FK columns
#               point at DIFFERENT tables, so stamping the validated row is not
#               possible — see the bug doc).
#   T-S94-004 — GET /agents/{name}/health exposes `last_error` for mode=scheduled,
#               so the "Failing" badge is explainable AT ITS SOURCE.
#   T-S94-005 — POSITIVE CONTROL: an agent deployed to PRODUCTION still dispatches
#               (the fix must reject the undeployable case WITHOUT breaking the
#               deployable one).
#   T-S94-006 — HEALTH IS CONFIG-FIRST: a schedule that can never dispatch reads
#               `failing` with a `dispatch_error` even with ZERO runs. Old code
#               said `healthy` here (nothing had failed yet), so the most broken
#               state the product can be in rendered GREEN. RED before fix.
#   T-S94-007 — ...and it CLEARS the moment production exists, with NO new run.
#               Old code stayed `failing` until the next successful fire — up to an
#               hour of "I fixed it and nothing changed", which is exactly how this
#               was reported from the UI. RED before fix.
#               006 and 007 pin the two directions health used to get wrong.
#   T-S94-008 — THE PUBLISH LEG: a PUBLISHED agent (production_deployments, its own
#               `production-{artifact}-{id8}` namespace) resolves. The first fix read
#               only `deployments` and always composed `agents-{team}`, so the only
#               production route Studio offers reported "no production deployment" and
#               told the operator to Publish — the action that lands in the unseen leg.
#               RED before fix. Read-only: no fire, no side effects.
#
# Detached in-pod driver (PYTHONPATH=/app -> result file); polled with short execs.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi

# R1/FR-11 widened what needs a JWT here: GET /llm-providers/, POST /agents/{name}/deploy
# and GET /agents/{name}/versions joined trigger CRUD on the protected list. The driver
# already minted ONE token at import; that is no longer enough, because the production
# deploy at the end of this suite happens minutes after two sandbox deploys and their
# wait_running polls — well past the 300s token lifespan. Install lib/e2e_auth.py and let
# BearerAuth re-mint per request (lib/e2e-auth.sh:88-96, lib/e2e_auth.py:1-21).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_require_token "$NAMESPACE" "$API_POD" >/dev/null
e2e_install_pyauth "$NAMESPACE" "$API_POD"
echo "=== Suite 94: trigger dispatch environment resolution (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

# Per-invocation paths (the suite-74 lesson): a fixed /tmp path lets two overlapping
# invocations share a result file and silently read each OTHER's results.
RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s94_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s94_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s94_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, json, os, uuid, httpx
import sys as _sys; _sys.path.insert(0, "/tmp")
from e2e_auth import BearerAuth, mint
from sqlalchemy import select, desc, text
from db import AsyncSessionLocal
from models import Agent, Deployment, AgentRun

BASE = "http://localhost:8000/api/v1"
# R2/R3 gated agents/tools/skills mutations. This suite authenticated with X-User-Sub
# alone and has been 401ing on setup; the relative-path form `c.post('/agents/', ...)`
# hid it from every earlier grep. Call e2e_set_token BARE (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"

# Trigger CRUD is gated by `require_user` (routers/triggers.py), and since R1/FR-11 so
# are /llm-providers/, /agents/{name}/deploy and /agents/{name}/versions — `X-User-Sub`
# alone returns 401. X-User-Sub STAYS: it is the audit stamp `armed_by` reads.
#
# Authentication is a per-REQUEST concern here, not a per-run one: this driver is
# detached and its last deploy lands long after a single token would have expired. The
# grant itself lives in lib/e2e_auth.py — one definition, every suite — rather than a
# private token_for() copy.
H = {"X-User-Sub": "${E2E_SUB}", "X-User-Team": "platform"}
try:
    mint()  # prove a token is obtainable NOW so a bad fixture fails loud, not at case 6
except Exception as _exc:  # surfaced as a case failure below, never a silent skip
    H["_token_error"] = str(_exc)
OUT = os.environ["S94_OUT"]
SFX = uuid.uuid4().hex[:6]
SBX_ONLY = f"s94-sbxonly-{SFX}"     # sandbox only -> must be REJECTED at admission
PROD_OK  = f"s94-prodok-{SFX}"      # deployed to production -> must still dispatch
NORUN    = f"s94-norun-{SFX}"       # NEVER fired -> health must judge CONFIG, not history
INSTR = ("You are an autonomous check agent. When you run, reply with exactly the "
         "word READY and nothing else. There is no user to talk to.")

# The reason must name the ENVIRONMENT problem. A DNS error ("Name or service not
# known", "Errno -2") is the OLD behaviour and must not appear.
DNS_MARKERS = ("name or service not known", "errno -2", "nodename nor servname")
ENV_MARKERS = ("production", "deploy")


async def prov(c):
    return (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]


async def create_daemon_agent(c, name, pid):
    r = await c.post("/agents/", json={
        "name": name, "team": "platform", "agent_type": "declarative",
        "execution_shape": "durable", "agent_class": "daemon",
        "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []},
    })
    assert r.status_code in (200, 201), f"create {name}: {r.status_code} {r.text[:200]}"


async def wait_running(name, environment, t=80):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(
                    select(Deployment).where(Deployment.agent_id == a.id,
                                             Deployment.environment == environment)
                    .order_by(desc(Deployment.deployed_at)).limit(1)
                )).scalars().first()
                if d and d.status == "running":
                    return True
        await asyncio.sleep(3)
    return False


async def get_run(run_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(AgentRun).where(AgentRun.id == run_id))).scalars().first()


async def poll_terminal(run_id, t=60):
    """Wait for the run to leave 'running'. A rejected fire is terminal immediately."""
    for _ in range(t):
        r = await get_run(run_id)
        if r and r.status in ("failed", "completed", "awaiting_approval"):
            return r
        await asyncio.sleep(2)
    return await get_run(run_id)


async def main():
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    tok_err = H.pop("_token_error", None)
    c = httpx.AsyncClient(base_url=BASE, headers=H, auth=BearerAuth(), timeout=40.0)
    try:
        # Fail LOUD on a missing token rather than letting every later case 401 and
        # read as "the feature is broken" — a wrong diagnosis is worse than a red X.
        assert not tok_err, f"could not obtain a Keycloak token: {tok_err}"
        pid = await prov(c)

        # ── Sandbox-only agent: the case that used to DNS-fail hourly ────────────
        await create_daemon_agent(c, SBX_ONLY, pid)
        await c.post(f"/agents/{SBX_ONLY}/deploy", json={"environment": "sandbox"})
        sbx_up = await wait_running(SBX_ONLY, "sandbox")

        tr = await c.post(f"/agents/{SBX_ONLY}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *",
            "input_payload": {"message": "scheduled check"},
            "alert_on_failure": False,
        })
        tid = tr.json()["id"] if tr.status_code in (200, 201) else None
        assert tid, f"create trigger: {tr.status_code} {tr.text[:200]}"

        start = await c.post("/internal/runs/start", json={
            "agent_name": SBX_ONLY, "trigger_type": "schedule",
            "trigger_id": tid, "run_by": "serviceaccount:scheduler",
        })
        # The door may either 4xx OR record a failed run. Both are acceptable
        # shapes; what is NOT acceptable is dispatching to a Service that does not
        # exist. Resolve whichever happened and judge the REASON.
        run_id, reason, status_val = None, "", ""
        if start.status_code in (200, 201):
            run_id = uuid.UUID(start.json()["id"])
            row = await poll_terminal(run_id)
            reason = (getattr(row, "error_message", None) or "")
            status_val = getattr(row, "status", "")
        else:
            reason = start.text or ""
            status_val = f"HTTP {start.status_code}"

        low = reason.lower()
        is_dns = any(m in low for m in DNS_MARKERS)
        names_env = any(m in low for m in ENV_MARKERS)
        record("T-S94-001 sandbox-only schedule fire rejected with an ENVIRONMENT reason, not a DNS error",
               (not is_dns) and names_env and bool(reason),
               f"status={status_val} reason={reason[:220]!r} dns_marker={is_dns} env_marker={names_env}")

        # The rejection must still be EVIDENCE — a recorded run, linked to its
        # trigger. Silence would be the same operator experience as the bug.
        row = await get_run(run_id) if run_id else None
        record("T-S94-002 the rejected fire is recorded as a failed run carrying trigger_id",
               bool(row) and row.status == "failed" and str(row.trigger_id) == str(tid),
               f"run={run_id} status={getattr(row,'status',None)} trigger_id={getattr(row,'trigger_id',None)}")

        # Trigger-scoped read — how the UI reaches a schedule's outcome without a
        # deployment FK (the two FK columns target different tables).
        rr = await c.get(f"/agents/{SBX_ONLY}/triggers/{tid}/runs")
        body = rr.json() if rr.status_code == 200 else []
        found = any(str(x.get("id")) == str(run_id) for x in body) if isinstance(body, list) else False
        record("T-S94-003 GET /agents/{name}/triggers/{id}/runs returns the schedule's run",
               rr.status_code == 200 and found,
               f"status={rr.status_code} n={len(body) if isinstance(body,list) else 'n/a'} found={found}")

        # The badge must be explainable at its own source.
        hh = await c.get(f"/agents/{SBX_ONLY}/health")
        hj = hh.json() if hh.status_code == 200 else {}
        record("T-S94-004 GET /agents/{name}/health exposes last_error for mode=scheduled",
               hh.status_code == 200 and hj.get("mode") == "scheduled"
               and hj.get("health") == "failing" and bool(hj.get("last_error")),
               f"status={hh.status_code} mode={hj.get('mode')} health={hj.get('health')} "
               f"last_error={str(hj.get('last_error'))[:160]!r}")

        # ── HEALTH IS CONFIG-FIRST, NOT LAST-RUN-DERIVED ─────────────────────────
        # Reported from the UI: "I deployed the agent and it still says Failing."
        # health used to be `"failing" if last_run == "failed"`, which comes apart
        # from reality in BOTH directions — pinned here.
        #
        # (a) A schedule that can NEVER dispatch, with ZERO runs, must read failing.
        #     Old behaviour: healthy, because nothing had failed yet — the most
        #     broken state the product can be in rendered green.
        await create_daemon_agent(c, NORUN, pid)
        await c.post(f"/agents/{NORUN}/deploy", json={"environment": "sandbox"})
        await wait_running(NORUN, "sandbox")
        tnr = await c.post(f"/agents/{NORUN}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *", "alert_on_failure": False})
        h6 = await c.get(f"/agents/{NORUN}/health")
        j6 = h6.json() if h6.status_code == 200 else {}
        nruns = 0
        async with AsyncSessionLocal() as s:
            nruns = len((await s.execute(select(AgentRun).where(AgentRun.agent_name == NORUN))).scalars().all())
        record("T-S94-006 a never-run schedule that cannot dispatch reads failing on CONFIG alone",
               tnr.status_code in (200, 201) and nruns == 0
               and j6.get("health") == "failing" and bool(j6.get("dispatch_error")),
               f"trigger={tnr.status_code} runs={nruns} (want 0) health={j6.get('health')} "
               f"dispatch_error={str(j6.get('dispatch_error'))[:110]!r} last_error={j6.get('last_error')!r}")

        # (b) After the cause is fixed, health must clear WITHOUT waiting for a fire.
        #     Old behaviour: stayed failing until the next successful run — up to an
        #     hour of "I fixed it and nothing changed". We deploy production and
        #     re-read health WITHOUT firing anything.
        vid_nr = None
        av_nr = await c.get(f"/agents/{NORUN}/versions")
        if av_nr.status_code == 200 and av_nr.json():
            vid_nr = av_nr.json()[0].get("id")
        if vid_nr:
            from models import AgentVersion
            async with AsyncSessionLocal() as s:
                v = (await s.execute(select(AgentVersion).where(AgentVersion.id == uuid.UUID(vid_nr)))).scalars().first()
                if v:
                    v.eval_passed = True
                    await s.commit()
        pdn = await c.post(f"/agents/{NORUN}/deploy",
                           json={"environment": "production", **({"version_id": vid_nr} if vid_nr else {})})
        prod_nr = pdn.status_code in (200, 201) and await wait_running(NORUN, "production")
        h7 = await c.get(f"/agents/{NORUN}/health")
        j7 = h7.json() if h7.status_code == 200 else {}
        async with AsyncSessionLocal() as s:
            nruns2 = len((await s.execute(select(AgentRun).where(AgentRun.agent_name == NORUN))).scalars().all())
        record("T-S94-007 health clears the moment production exists, with NO new run",
               prod_nr and nruns2 == 0 and j7.get("health") == "healthy" and not j7.get("dispatch_error"),
               f"prod_running={prod_nr} runs={nruns2} (want 0 — nothing fired) "
               f"health={j7.get('health')} dispatch_error={j7.get('dispatch_error')!r}")

        # ── THE PUBLISH LEG: production_deployments, not just deployments ─────────
        # An agent reaches production by TWO routes, in DIFFERENT namespaces:
        #   deployments(environment='production')  -> agents-{team}
        #   production_deployments (Publish)       -> production-{artifact}-{id8}
        # The first version of resolve_dispatch_target read only the former and
        # always composed `agents-{team}`, so a PUBLISHED agent — the only
        # production route Studio offers — was reported as having no production
        # deployment, and the refusal told the operator to Publish: the very action
        # that lands in the unseen leg. See sandbox-production-parity-architecture
        # §41 ("any query that assumes a deployment id is a `deployments` id breaks
        # for production") and bundle_generator's UNION.
        #
        # Read-only assertion: arm a schedule on a REALLY published agent and read
        # health. No fire — proving the resolver SEES the leg needs no side effects.
        pub_name, pub_ns = None, None
        async with AsyncSessionLocal() as s:
            row = (await s.execute(text("""
                SELECT pa.name, pd.namespace
                FROM production_deployments pd
                JOIN published_artifacts pa ON pd.artifact_id = pa.id
                JOIN agents a ON a.id = pa.source_id
                WHERE pd.status = 'running' AND pa.type = 'agent' AND a.status = 'active'
                LIMIT 1"""))).first()
            if row:
                pub_name, pub_ns = row[0], row[1]

        if not pub_name:
            # Honest skip, never a silent pass: without a published agent on the
            # cluster this case cannot be evaluated at all.
            record("T-S94-008 a PUBLISHED agent's schedule resolves the production_deployments leg",
                   False, "SKIP-AS-FAIL: no running production_deployments for an active agent — "
                          "publish an agent so this leg can be exercised")
        else:
            tp = await c.post(f"/agents/{pub_name}/triggers", json={
                "trigger_type": "schedule", "cron_expression": "0 0 * * *", "alert_on_failure": False})
            tpid = tp.json().get("id") if tp.status_code in (200, 201) else None
            h8 = await c.get(f"/agents/{pub_name}/health")
            j8 = h8.json() if h8.status_code == 200 else {}
            derr = j8.get("dispatch_error")
            record("T-S94-008 a PUBLISHED agent's schedule resolves the production_deployments leg",
                   tpid is not None and not derr and j8.get("health") != "failing",
                   f"agent={pub_name} ns={pub_ns} trigger={tp.status_code} "
                   f"health={j8.get('health')} dispatch_error={str(derr)[:130]!r} (want None)")
            # leave no armed schedule behind on somebody else's published agent
            if tpid:
                try:
                    await c.delete(f"/agents/{pub_name}/triggers/{tpid}")
                except Exception:
                    pass

        # ── POSITIVE CONTROL: production-deployed agent must STILL dispatch ──────
        # Without this the fix could "pass" by rejecting everything.
        await create_daemon_agent(c, PROD_OK, pid)
        await c.post(f"/agents/{PROD_OK}/deploy", json={"environment": "sandbox"})
        await wait_running(PROD_OK, "sandbox")
        vid = None
        av = await c.get(f"/agents/{PROD_OK}/versions")
        if av.status_code == 200 and av.json():
            vid = av.json()[0].get("id")

        # Satisfy the production eval gate as an explicit FIXTURE step, not by
        # running a real eval. `deployments.py:623` rejects production deploys with
        # `eval_passed=False` (Decision 20) — that gate is owned and proven by
        # suite-14/15/17. THIS suite is about dispatch, and spending an eval-runner
        # Job here would make it slow and couple it to a gate it does not test.
        # Setting the flag directly is honest: it states the precondition instead of
        # simulating one.
        if vid:
            from models import AgentVersion
            async with AsyncSessionLocal() as s:
                v = (await s.execute(
                    select(AgentVersion).where(AgentVersion.id == uuid.UUID(vid))
                )).scalars().first()
                if v:
                    v.eval_passed = True
                    await s.commit()

        pd = await c.post(f"/agents/{PROD_OK}/deploy",
                          json={"environment": "production", **({"version_id": vid} if vid else {})})
        prod_up = pd.status_code in (200, 201) and await wait_running(PROD_OK, "production")

        if prod_up:
            tr2 = await c.post(f"/agents/{PROD_OK}/triggers", json={
                "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                "input_payload": {"message": "scheduled check"}, "alert_on_failure": False,
            })
            tid2 = tr2.json()["id"] if tr2.status_code in (200, 201) else None
            s2 = await c.post("/internal/runs/start", json={
                "agent_name": PROD_OK, "trigger_type": "schedule",
                "trigger_id": tid2, "run_by": "serviceaccount:scheduler",
            })
            ok2 = s2.status_code in (200, 201)
            reason2 = ""
            if ok2:
                row2 = await poll_terminal(uuid.UUID(s2.json()["id"]), t=30)
                reason2 = (getattr(row2, "error_message", None) or "")
                # It must NOT be rejected for the environment reason. Whether the
                # agent pod then completes the work is the few-pods boundary every
                # other suite accepts — we assert it was ADMITTED and addressed.
                ok2 = not any(m in reason2.lower() for m in DNS_MARKERS + ("not deployed",))
            record("T-S94-005 POSITIVE CONTROL: production-deployed agent is still admitted + addressed",
                   ok2, f"start={s2.status_code} reason={reason2[:200]!r}")
        else:
            record("T-S94-005 POSITIVE CONTROL: production-deployed agent is still admitted + addressed",
                   False, f"production deploy never reached running (deploy={pd.status_code}) — "
                          f"cannot prove the fix leaves the deployable case working")

    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson): a partial run must never look like a pass.
        import traceback
        record("T-S94-999 driver ran every case without crashing", False,
               f"driver CRASHED mid-run — cases after this point never ran: "
               f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # write results BEFORE cleanup (the suite-69 lesson)
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")
        for n in (SBX_ONLY, PROD_OK, NORUN):
            try:
                await c.delete(f"/agents/{n}")
            except Exception:
                pass
        await c.aclose()

asyncio.run(main())
PY

echo "Running driver detached in-pod (two deploys + fires can take ~4 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S94_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

for i in $(seq 1 90); do
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    break
  fi
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

# Completeness gate (the suite-74 lesson): FAIL=0 is only a pass if every gate
# assertion actually RAN. REQUIRED_IDS is the ONE source of truth — add a case here
# and nowhere else. Trailing space in the grep guards against prefix collisions.
REQUIRED_IDS="001 002 003 004 005 006 007 008"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S94-$id " || MISSING="$MISSING T-S94-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S94-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S94-COMPLETE every gate assertion ran (001-008 — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-94 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 94 FAILED"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "SUITE 94 INCONCLUSIVE (no assertions ran)"; exit 1; fi
echo "SUITE 94 PASSED"
