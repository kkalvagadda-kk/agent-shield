#!/usr/bin/env bash
# scripts/e2e/suite-95-trigger-lifecycle-disarm.sh
#
# E2E Suite 95: an armed trigger on a DEAD artifact must be unrepresentable. NO fakes.
#
# THE BUG THIS REPRODUCES (docs/bugs/schedule-fires-on-deleted-artifact.md)
# ------------------------------------------------------------------------
# No lifecycle path disarmed a trigger. `delete_agent` soft-deletes (status ->
# 'deprecated'), `archive_workflow` sets 'archived', `quarantine_agent` sets
# 'quarantined' — none touched `agent_triggers`. The scheduler filtered on
# `t.enabled` ALONE. So artifact liveness had no bearing on whether its schedule
# fired, in either direction.
#
# Measured on the cluster before the fix: 37 triggers armed on dead artifacts —
# 8 archived workflows firing daily, a never-published DRAFT workflow firing every
# 15 minutes for days, 13 triggers on soft-deleted agents. The e2e suites were the
# main producer: their cleanup archives the workflow and soft-deletes the agent,
# which by design left the trigger armed.
#
# It was also demonstrated in one click by the Claude-in-Chrome journey's leg 8 —
# the CLEANUP step: UI delete produced `deprecated` agent + `enabled=True` hourly
# schedule. The step meant to tidy up was manufacturing the defect.
#
#   T-S95-001 — DELETE an agent -> its SCHEDULE trigger is REMOVED, and its WEBHOOK
#               trigger is disarmed-but-kept. Deletion ends the artifact's life and a
#               disarmed schedule on a deleted agent is inert, so it is only noise on
#               an operations page (63 of 100 live rows were exactly that). Webhooks
#               are exempt because webhook_clients.trigger_id is ON DELETE CASCADE —
#               removing one would destroy the applications registered against it.
#               Originally asserted disarm-and-keep; RED before the disarm fix
#               (enabled stayed true, no reason column) and again before the reap.
#   T-S95-002 — ARCHIVE a workflow -> same. RED before fix.
#   T-S95-003 — QUARANTINE an agent -> same. A quarantined agent must not be woken
#               by its own cron mid-incident; the pod is deliberately left running
#               for forensics, so disarming is the only thing stopping it.
#   T-S95-004 — READ-SIDE defence: a trigger force-re-armed by raw SQL on a dead
#               artifact is NOT returned by the scheduler's own query. Proves the
#               filter, not just the write gate — they are separate services and
#               fixing one alone was the bandaid. Uses the ARCHIVED WORKFLOW's
#               trigger: archive keeps the row, whereas agent delete now removes
#               schedule triggers outright, which would make this pass vacuously.
#   T-S95-005 — RE-ENABLE is explicit and CLEARS the reason: reactivating the agent
#               does NOT re-arm (fail closed), and a human PATCH enabled=true clears
#               `disabled_reason` so a stale explanation never sits on an armed row.
#   T-S95-006 — NEGATIVE CONTROL: a LIVE agent's trigger is untouched by all of the
#               above. The fix must not disarm things that should stay armed.
#
# Detached in-pod driver (PYTHONPATH=/app -> result file); polled with short execs.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: No registry-api pod in $NAMESPACE"; exit 1; fi
# Trigger CRUD needs a real JWT since 76b3570 — X-User-Sub is an audit stamp, not
# authentication. ONE definition of how a suite authenticates: scripts/e2e/lib/e2e-auth.sh.
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_require_token "$NAMESPACE" "$API_POD" >/dev/null   # fail fast + loud if Keycloak is unreachable
e2e_install_pyauth "$NAMESPACE" "$API_POD"

echo "=== Suite 95: trigger lifecycle disarm (no fakes) ==="
echo "  Pod: $API_POD"; echo ""

PASS=0; FAIL=0

# ─────────────────────────────────────────────────────────────────────────────
# T-S95-000 — PARITY grep: ONE liveness definition, and both consumers read IT.
#
# WHY THIS EXISTS: T-S95-004 below asserts the filter using a COPY of the
# scheduler's query embedded in the driver. A copy passes whether or not the real
# scheduler has the predicate — it would be testing the test. This grep pins the
# actual source, so 004 cannot go green against a scheduler that lost the filter.
#
# WHAT IT PINS, AND WHY IT CHANGED SHAPE: it used to grep each service for the
# literal predicates (`a.status = 'active'`, `w.status <> 'archived'`). That check
# started FAILING the moment the predicate was correctly centralised into the
# `trigger_liveness` view (migration 0077) and both services switched to reading it
# — the very improvement it should have been protecting. A check that hardcodes
# WHERE a definition lives breaks exactly when the definition stops being duplicated.
#
# So it now asserts the property that actually matters, across three images that
# share no Python:
#   (a) the view carries the liveness predicates — one definition, in SQL;
#   (b) each consumer READS it (`trigger_liveness` + `artifact_is_live`);
#   (c) neither consumer RESTATES it. A restated predicate is the drift the view
#       exists to delete, and it was wrong twice in one day when stated
#       independently: `w.status='published'` matched 0 of 140 rows, and
#       `w.publish_status='published'` was too strict.
# ─────────────────────────────────────────────────────────────────────────────
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SCHED_SRC="$REPO_ROOT/services/scheduler/main.py"
GW_SRC="$REPO_ROOT/services/event-gateway/webhook_auth.py"
VIEW_SRC="$REPO_ROOT/services/registry-api/alembic/versions/0077_trigger_liveness_view.py"
_missing=""
# Strip comment lines before grepping. The explanation of WHY the old predicates were
# wrong necessarily quotes them, and a naive grep then flags the very file that fixed
# it — a check that cannot tell code from prose about the code is worse than none.
_code() { grep -vE "^[[:space:]]*(--|#)" "$1"; }
# The migration's MODULE DOCSTRING quotes both wrong predicates verbatim to explain
# why they were wrong — that is the doc doing its job, and grepping it as if it were
# SQL flags the very file that fixed the bug. Drop the docstring, then read the code.
_view_code() {
  python3 - "$1" <<'PYEOF' | grep -vE "^[[:space:]]*(--|#)"
import ast, sys
src = open(sys.argv[1]).read()
tree = ast.parse(src)
doc = ast.get_docstring(tree)
lines = src.splitlines()
if doc is not None and tree.body and isinstance(tree.body[0], ast.Expr):
    node = tree.body[0]
    del lines[node.lineno - 1 : node.end_lineno]
print("\n".join(lines))
PYEOF
}

# (a) the single definition
_view_code "$VIEW_SRC" | grep -q "a.status = 'active'"     || _missing="$_missing view:agent-status"
_view_code "$VIEW_SRC" | grep -q "w.status <> 'archived'"  || _missing="$_missing view:workflow-not-archived"
_view_code "$VIEW_SRC" | grep -q "w.status = 'published'"         && _missing="$_missing view:DEAD-status-eq-published"
_view_code "$VIEW_SRC" | grep -q "w.publish_status = 'published'" && _missing="$_missing view:TOO-STRICT-publish_status"

for _src in "$SCHED_SRC" "$GW_SRC"; do
  _n=$(basename "$_src")
  # (b) reads the shared definition
  _code "$_src" | grep -q "trigger_liveness"  || _missing="$_missing $_n:does-not-read-the-view"
  _code "$_src" | grep -q "artifact_is_live"  || _missing="$_missing $_n:does-not-gate-on-liveness"
  # (c) and does not restate it
  _code "$_src" | grep -qE "(a|w)\.(publish_)?status[[:space:]]*(=|<>)" \
    && _missing="$_missing $_n:RESTATES-liveness-instead-of-reading-the-view"
done
if [ -z "$_missing" ]; then
  echo "PASS  T-S95-000 ONE liveness definition (the view); both consumers read it, neither restates it"
  PASS=$((PASS+1))
else
  echo "FAIL  T-S95-000 read-side liveness parity  |  PROBLEMS:$_missing"
  FAIL=$((FAIL+1))
fi

# R2/R3 gated agents/tools/skills mutations. This suite authenticated with X-User-Sub
# alone and has been 401ing on setup; the relative-path form `c.post('/agents/', ...)`
# hid it from every earlier grep. Call e2e_set_token BARE (lib/e2e-auth.sh).
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib/e2e-auth.sh"
e2e_set_token "$NAMESPACE" "$API_POD"
echo ""

RUN_TAG="$(date +%s)$$"
DRIVER="/tmp/s95_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s95_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s95_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "cat > $DRIVER" <<'PY'
import asyncio, os, uuid, httpx
import sys as _sys; _sys.path.insert(0, "/tmp")
# Per-REQUEST auth: Keycloak tokens live 300s. See
# docs/bugs/trigger-e2e-suites-dead-since-require-user.md.
from e2e_auth import BearerAuth
from sqlalchemy import text
from db import AsyncSessionLocal

BASE = "http://localhost:8000/api/v1"
ADMIN = "${E2E_SUB}"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
OUT = os.environ["S95_OUT"]
SFX = uuid.uuid4().hex[:6]
DEL_AGENT   = f"s95-del-{SFX}"      # deleted    -> triggers must disarm
QUAR_AGENT  = f"s95-quar-{SFX}"     # quarantined-> triggers must disarm
LIVE_AGENT  = f"s95-live-{SFX}"     # untouched  -> triggers must STAY armed
WF_NAME     = f"s95-wf-{SFX}"       # archived   -> triggers must disarm
INSTR = "Autonomous check agent. Reply READY."


_HAS_REASON_COL = None


async def _has_reason_col():
    """Is migration 0076 applied? Probed ONCE.

    Deliberately schema-tolerant: run against a database WITHOUT 0076 and the cases
    below must fail on the REAL defect ("enabled stayed true after delete") rather
    than crash on a missing column. A crash tells you the column is absent; an
    assertion tells you the trigger is still armed, which is the thing under test.
    """
    global _HAS_REASON_COL
    if _HAS_REASON_COL is None:
        async with AsyncSessionLocal() as s:
            _HAS_REASON_COL = bool((await s.execute(text(
                "SELECT 1 FROM information_schema.columns "
                " WHERE table_name='agent_triggers' AND column_name='disabled_reason'"
            ))).first())
    return _HAS_REASON_COL


async def trig_state(trigger_id):
    """(enabled, disabled_reason) straight from the row — no API interpretation."""
    cols = "enabled, disabled_reason" if await _has_reason_col() else "enabled, NULL"
    async with AsyncSessionLocal() as s:
        r = (await s.execute(text(
            f"SELECT {cols} FROM agent_triggers WHERE id = :i"),
            {"i": str(trigger_id)})).first()
    return (r[0], r[1]) if r else (None, None)


async def scheduler_sees(trigger_id):
    """Exactly the scheduler's own query (services/scheduler/main.py), so this asserts
    the READ-side filter rather than a paraphrase of it."""
    async with AsyncSessionLocal() as s:
        r = (await s.execute(text("""
            SELECT t.id::text FROM agent_triggers t JOIN agents a ON t.agent_id = a.id
             WHERE t.trigger_type='schedule' AND t.enabled = true
               AND t.cron_expression IS NOT NULL AND a.status = 'active'
            UNION ALL
            SELECT t.id::text FROM agent_triggers t JOIN workflows w ON t.workflow_id = w.id
             WHERE t.trigger_type='schedule' AND t.enabled = true
               AND t.cron_expression IS NOT NULL
               AND w.status <> 'archived'
        """))).all()
    return str(trigger_id) in {row[0] for row in r}


async def main():
    results = []

    def record(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=60.0, auth=BearerAuth())
    wid = None
    wid_pub = None
    try:
        pid = (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

        async def mk_agent(name):
            r = await c.post("/agents/", json={
                "name": name, "team": "platform", "agent_type": "declarative",
                "execution_shape": "durable", "agent_class": "daemon",
                "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []}})
            assert r.status_code in (200, 201), f"create {name}: {r.status_code} {r.text[:160]}"

        async def arm(name):
            r = await c.post(f"/agents/{name}/triggers", json={
                "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                "alert_on_failure": False})
            assert r.status_code in (200, 201), f"arm {name}: {r.status_code} {r.text[:160]}"
            return r.json()["id"]

        # ── T-S95-001: DELETE an agent ───────────────────────────────────────────
        # Both trigger kinds on ONE agent, so the asymmetry is proven rather than
        # assumed: the schedule goes, the webhook stays (disarmed).
        await mk_agent(DEL_AGENT)
        t_del = await arm(DEL_AGENT)
        rh = await c.post(f"/agents/{DEL_AGENT}/triggers",
                          json={"trigger_type": "webhook", "alert_on_failure": False})
        t_hook = rh.json()["id"] if rh.status_code in (200, 201) else None
        before = await trig_state(t_del)
        dr = await c.delete(f"/agents/{DEL_AGENT}")
        sched_en, _sched_reason = await trig_state(t_del)
        hook_en, hook_reason = await trig_state(t_hook) if t_hook else (None, None)

        record("T-S95-001 DELETE agent REMOVES its schedule trigger, KEEPS the webhook disarmed",
               dr.status_code == 204 and before[0] is True
               and sched_en is None                      # row gone entirely
               and (t_hook is None or (hook_en is False and bool(hook_reason))),
               f"delete={dr.status_code} before_enabled={before[0]} "
               f"schedule_row_after={sched_en!r} (want None — deleted) "
               f"webhook_enabled={hook_en!r} webhook_reason={hook_reason!r} "
               f"(want False + a reason — deleting it would cascade away its webhook_clients)")

        # ── T-S95-003: QUARANTINE an agent ───────────────────────────────────────
        await mk_agent(QUAR_AGENT)
        t_quar = await arm(QUAR_AGENT)
        qr = await c.post(f"/agents/{QUAR_AGENT}/quarantine")
        q_en, q_reason = await trig_state(t_quar)
        record("T-S95-003 QUARANTINE agent disarms its triggers (no cron mid-incident)",
               qr.status_code in (200, 201) and q_en is False and bool(q_reason),
               f"quarantine={qr.status_code} enabled={q_en} reason={q_reason!r}")

        # ── T-S95-002: ARCHIVE a workflow ────────────────────────────────────────
        # Two members: a workflow needs agents to be runnable, and the trigger is
        # armed on the WORKFLOW, exercising the workflow_id branch of disarm_triggers.
        await mk_agent(LIVE_AGENT)
        wr = await c.post("/workflows", json={
            "name": WF_NAME, "team": "platform", "orchestration": "sequential",
            "execution_shape": "durable", "agent_class": "daemon"})
        assert wr.status_code in (200, 201), f"create wf: {wr.status_code} {wr.text[:160]}"
        wid = wr.json()["id"]
        wt = await c.post(f"/workflows/{wid}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *", "alert_on_failure": False})
        assert wt.status_code in (200, 201), f"arm wf: {wt.status_code} {wt.text[:160]}"
        t_wf = wt.json()["id"]
        # Archive IS `DELETE /api/v1/workflows/{id}` (composite_workflows.py:9) — the
        # workflow "delete" verb sets status='archived'. There is no /archive path.
        ar = await c.delete(f"/workflows/{wid}")
        w_en, w_reason = await trig_state(t_wf)
        record("T-S95-002 ARCHIVE workflow disarms its triggers and records a reason",
               ar.status_code in (200, 204) and w_en is False and bool(w_reason),
               f"archive={ar.status_code} enabled={w_en} reason={w_reason!r}")

        # ── T-S95-004: READ-SIDE defence ─────────────────────────────────────────
        # Force an ARCHIVED WORKFLOW's schedule back on behind the API's back. The
        # write gate cannot help here — this is exactly the "next lifecycle path that
        # forgets" scenario, and the scheduler must still refuse to see it.
        #
        # FIXTURE NOTE: this used to force-re-arm the DELETED AGENT's schedule. That
        # row no longer exists — agent delete now REMOVES schedule triggers — so the
        # UPDATE hit zero rows and the scheduler "did not see it" for the trivial
        # reason that there was nothing to see. A vacuous pass. The archived workflow
        # is the right fixture now: archive DISARMS and KEEPS, so there is a real row
        # on a real dead artifact to force back on.
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "UPDATE agent_triggers SET enabled = true WHERE id = :i"), {"i": t_wf})
            await s.commit()
        forced_en, _ = await trig_state(t_wf)
        seen = await scheduler_sees(t_wf)
        record("T-S95-004 READ-SIDE: a force-re-armed trigger on a dead artifact is invisible to the scheduler",
               forced_en is True and seen is False,
               f"row_enabled={forced_en} (forced on — must be True or this passes vacuously) "
               f"scheduler_sees={seen} (want False)")

        # ── T-S95-005: re-enable is explicit and clears the reason ───────────────
        # Uses the QUARANTINED agent: quarantine disarms and KEEPS the row, so there
        # is still a trigger to re-enable. (The deleted agent's schedule is gone, and
        # a 404 would prove nothing about re-enable semantics.)
        async with AsyncSessionLocal() as s:
            await s.execute(text(
                "UPDATE agents SET status = 'active' WHERE name = :n"), {"n": QUAR_AGENT})
            await s.commit()
        reactivated_en, reactivated_reason = await trig_state(t_quar)
        pr = await c.patch(f"/agents/{QUAR_AGENT}/triggers/{t_quar}", json={"enabled": True})
        final_en, final_reason = await trig_state(t_quar)
        record("T-S95-005 reactivating does NOT re-arm; an explicit PATCH does and clears the reason",
               reactivated_en is False and bool(reactivated_reason)
               and pr.status_code == 200 and final_en is True and final_reason is None,
               f"after_reactivate enabled={reactivated_en} reason={reactivated_reason!r} | "
               f"patch={pr.status_code} enabled={final_en} reason={final_reason!r} (want None)")

        # ── T-S95-007: POSITIVE CONTROL for the WORKFLOW leg ────────────────────
        # The missing half. Every other case asserts a DEAD artifact does not fire —
        # which is equally satisfied by NOTHING firing, and that is exactly what
        # happened: gating on `w.status='published'` (a value nothing writes) killed
        # every workflow schedule while suite-95 stayed green. A liveness filter needs
        # a positive control or it cannot tell success from total failure.
        wr2 = await c.post("/workflows", json={
            "name": f"{WF_NAME}-pub", "team": "platform", "orchestration": "sequential",
            "execution_shape": "durable", "agent_class": "daemon"})
        assert wr2.status_code in (200, 201), f"create pub wf: {wr2.status_code} {wr2.text[:160]}"
        wid_pub = wr2.json()["id"]
        # Deliberately left DRAFT/private — that is the shape a real production
        # workflow has (suite-66 puts one in production by deploying its MEMBER AGENTS;
        # the workflow row never changes). Requiring publication here would have hidden
        # exactly the over-strict predicate this case now guards against.
        wt2 = await c.post(f"/workflows/{wid_pub}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *", "alert_on_failure": False})
        assert wt2.status_code in (200, 201), f"arm pub wf: {wt2.status_code} {wt2.text[:160]}"
        t_pub = wt2.json()["id"]
        pub_en, _ = await trig_state(t_pub)
        pub_seen = await scheduler_sees(t_pub)
        record("T-S95-007 POSITIVE CONTROL: a LIVE (non-archived) workflow's schedule IS visible to the scheduler",
               pub_en is True and pub_seen is True,
               f"enabled={pub_en} scheduler_sees={pub_seen} (want True) "
               f"— gating on workflows.status (nothing writes it) or on publish_status (too strict) both broke this")

        # ── T-S95-006: NEGATIVE CONTROL ──────────────────────────────────────────
        t_live = await arm(LIVE_AGENT)
        l_en, l_reason = await trig_state(t_live)
        l_seen = await scheduler_sees(t_live)
        record("T-S95-006 NEGATIVE CONTROL: a LIVE agent's trigger stays armed and visible",
               l_en is True and l_reason is None and l_seen is True,
               f"enabled={l_en} reason={l_reason!r} scheduler_sees={l_seen} (want True)")

    except Exception as exc:
        import traceback
        record("T-S95-999 driver ran every case without crashing", False,
               f"driver CRASHED mid-run — cases after this point never ran: "
               f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        passed = sum(1 for _, ok, _ in results if ok)
        with open(OUT, "w") as f:
            for name, ok, detail in results:
                f.write(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")
        for n in (DEL_AGENT, QUAR_AGENT, LIVE_AGENT):
            try:
                await c.delete(f"/agents/{n}")
            except Exception:
                pass
        # T-S95-002's DELETE already archived the first workflow. The POSITIVE-CONTROL
        # workflow is PUBLISHED with an ARMED daily schedule — leave it and the suite
        # becomes the very thing it tests, a fixture firing forever. Archiving it also
        # re-exercises the write gate, so the teardown is itself an assertion.
        if wid_pub:
            try:
                await c.delete(f"/workflows/{wid_pub}")
            except Exception:
                pass
        await c.aclose()

asyncio.run(main())
PY

echo "Running driver detached in-pod…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S95_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

for i in $(seq 1 60); do
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

while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# Completeness gate: FAIL=0 is only a pass if every gate assertion actually RAN.
REQUIRED_IDS="000 001 002 003 004 005 006 007"
MISSING=""
for id in $REQUIRED_IDS; do
  [ "$id" = "000" ] && continue   # 000 runs in bash above, not in the driver result file
  echo "$RES" | grep -q "T-S95-$id " || MISSING="$MISSING T-S95-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S95-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S95-COMPLETE every gate assertion ran (000-007 — none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-95 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 95 FAILED"; exit 1; fi
if [ "$PASS" -eq 0 ]; then echo "SUITE 95 INCONCLUSIVE (no assertions ran)"; exit 1; fi
echo "SUITE 95 PASSED"
