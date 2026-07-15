#!/usr/bin/env bash
# scripts/e2e/suite-72-eval-v2-durable.sh
#
# E2E Suite 72: Eval v2 E-1 — NO-FAKES durable trajectory + tool-call gate.
#
# This is E-1's acceptance gate (Phase 8 / Checkpoint 3). The plan calls it
# "suite-61", but suite-61 is TAKEN by E-0 (eval-mode-plumbing) and suites exist
# through 71 — so this is suite-72, test-case IDs T-S72-00X.
#
# THE NO-FAKES RULE IS THE ACCEPTANCE. This build shipped 11 live-only bugs green
# because earlier suites faked the dispatch→pod→callback→resume seam. This suite
# drives the WHOLE real seam, end to end, with the score read back from the DB:
#
#   create a REAL durable declarative agent with two REAL platform tools
#   (get_weather = http/LOW, refund_action = http/HIGH → in-cluster /echo) → DEPLOY
#   it to a REAL sandbox pod → author a REAL `durable` PlaygroundDataset with four
#   REAL items whose EXPECTED trajectories differ → POST /playground/eval-runs
#   (launches the REAL eval-runner K8s Job, MODE=durable) → the Job dispatches a
#   REAL durable playground run to the REAL pod → real step-update callbacks write
#   real `run_steps` (tool boundaries carry {tool,args}) → the high-risk tool PARKS
#   for real → the runner SELF-APPROVES the real Approval and resumes → the real
#   `judge.py` scorers (score_trajectory / score_tool_calls / weighted_mean) score
#   the REAL projected trajectory → dimension_scores/eval_detail/run_id persist →
#   we re-read them FROM THE DB (save→reload) and assert.
#
# NO faked _run_step, NO mocked judge, NO hand-built trajectory fixture, NO
# page.route stub. The trajectory/tool-call scores come from a real durable run's
# real run_steps — nowhere else.
#
#   T-S72-001 — the CORRECT item persists dimension_scores.{response,trajectory,
#               tool_call} + a composite, all non-null, read back from the DB.
#   T-S72-002 — the WRONG-TOOL item FAILS the composite (composite < pass
#               threshold) even though its response scores fine — the core Eval v2
#               win: a durable agent that answers well but calls the wrong tools
#               does NOT pass the gate.
#   T-S72-003 — the WRONG-ORDER item scores trajectory < 1.0 under match_mode
#               'ordered' (the expected tools were called, but out of order).
#   T-S72-004a— the GATED item fired a REAL OPA require_approval HITL park: the
#               gated run's run_steps carry a refund_action step with status
#               awaiting_approval + a non-null approval_id (the eval-runner then
#               self-approved and the run completed — the refund processed). This
#               is the real durable HITL park proven from the substrate.
#   T-S72-004b— the judge's eval_detail.approvals[] recorded the gated step with
#               parked:true AND args_matched:true. The earlier E-1 SCORING bug is
#               FIXED: the eval-runner projection now collapses a single logical
#               tool call's consecutive same-tool run_steps (running boundary +
#               separate awaiting_approval boundary) into ONE trajectory entry
#               carrying the parked disposition, so score_tool_calls matches the
#               expect_approval step to the parked entry. The REAL park (004a)
#               scored correctly, no fake.
#   T-S72-005 — FAIL-CLOSED invariant: NO persisted row is a pass on empty
#               scores. Every row is EITHER scored on a real trajectory
#               (dimension_scores non-null) OR recorded failed with null
#               dimension_scores — a poll-timeout/empty-trajectory item is never
#               scored as a pass. (Provoking an actual unreachable item against a
#               single shared agent isn't cleanly forcible — the invariant over
#               the real rows is the honest, no-fake assertion of the fail-closed
#               contract; boundary documented.)
#
# Real pod + real LLM + real tool calls + real park/approve/resume + real Job →
# SLOW (a full durable eval of four items). It creates ALL its own resources up
# front and tears them down. A detached in-pod driver (suite-70 pattern) runs the
# create→deploy→launch→poll→assert→write so a long wait can't kill the exec; the
# result file is written BEFORE cleanup.
#
# Fixture-unreachable is a FAIL, not a skip: if the durable agent does not deploy
# to a running pod, the gate cannot be proven → hard fail (never a fake pass). If
# the agent deploys but the eval Job cannot finish in the window on this cluster,
# the suite asserts the STRONGEST real persisted state on whatever rows landed and
# documents the boundary (the suite-58/70 bar) — but never fabricates a score.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 72: Eval v2 E-1 NO-FAKES durable trajectory + tool-call gate ==="
echo "  Pod: $API_POD"
echo ""

DRIVER=/tmp/s72_driver.py
OUTFILE=/tmp/s72_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "rm -f $OUTFILE /tmp/s72_run.log; cat > $DRIVER" <<'PY'
import asyncio, json, uuid
import httpx
from sqlalchemy import select, desc, text
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, EvalRun, EvalRunResult

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:8]
AGENT = f"s72-durable-{SFX}"
OUT = "/tmp/s72_out.txt"

# The agent MUST call two real platform HTTP tools in a fixed order so the real
# run_steps carry a deterministic tool trajectory: get_weather (http/LOW) then
# refund_action (http/HIGH). Both hit the in-cluster /echo endpoint, so they
# execute for real. HTTP tools are used because python-type tools currently crash
# the agent pod at graph-build (langchain lc_tool over the bare-**kwargs governed
# wrapper → KeyError('kwargs'), a separate SDK issue — see the report).
#
# EXACTLY ONE high-risk tool (refund_action) so it fires a real OPA
# require_approval HITL PARK: with TWO high-risk tools the multi-tool
# post_model_hook suppresses the interrupt (verified — two high-risk tools never
# park), whereas a single high-risk tool parks reliably. refund_action is marked
# expect_approval on the gated item (real park → eval-runner self-approve → resume).
# The actual trajectory becomes [get_weather, refund_action(parked→approved)].
INSTR = (
    "You verify then refund fraudulent orders. For EVERY request you MUST follow "
    "this EXACT two-step procedure and MUST NOT deviate:\n"
    "STEP 1 — FIRST call the get_weather tool with location='HQ' (a required "
    "compliance location check).\n"
    "STEP 2 — ONLY AFTER get_weather returns, call the refund_action tool with "
    "the order_id and amount from the message.\n"
    "Always call BOTH tools. ALWAYS call get_weather BEFORE refund_action. NEVER "
    "call refund_action first. NEVER skip a tool. NEVER ask for more information — "
    "if a value is missing use location='HQ', order_id='12345', amount=10. After "
    "both tools return, reply with ONE short sentence confirming the refund."
)

# Four real durable items — SAME agent, DIFFERENT expected trajectories. The agent
# behaviour is constant ([get_weather, refund_action]); the four items differ only
# in what they EXPECT, which is exactly what the four scorers must discriminate.
ITEMS = [
    {   # (a) CORRECT — expected trajectory matches the real run (superset).
        "kind": "durable",
        "input_payload": {"message": "Refund order 12345 amount 25, account ACC-2."},
        "expected_output": "The refund for order 12345 was processed.",
        "expected_trajectory": {
            "match_mode": "superset",
            "steps": [{"tool": "get_weather"}, {"tool": "refund_action"}],
        },
    },
    {   # (b) WRONG-TOOL — expects a tool the agent NEVER calls (calculator).
        # trajectory + tool_call collapse to ~0 → composite fails even if the
        # response is fine. The core Eval v2 win.
        "kind": "durable",
        "input_payload": {"message": "Refund order 67890 amount 40, account ACC-6."},
        "expected_output": "The refund for order 67890 was processed.",
        "expected_trajectory": {
            "match_mode": "superset",
            "steps": [{"tool": "calculator"}],
        },
    },
    {   # (c) WRONG-ORDER — right tools, REVERSED, under match_mode 'ordered'.
        # actual [get_weather, refund_action] vs expected [refund_action,
        # get_weather] → in-order LCS = 1 of 2 → trajectory 0.5 < 1.0.
        "kind": "durable",
        "input_payload": {"message": "Refund order 11111 amount 15, account ACC-1."},
        "expected_output": "The refund for order 11111 was processed.",
        "expected_trajectory": {
            "match_mode": "ordered",
            "steps": [{"tool": "refund_action"}, {"tool": "get_weather"}],
        },
    },
    {   # (d) GATED — the high-risk refund_action step must PARK for HITL.
        # args_match is empty (the LLM's exact args aren't deterministic across
        # runs; strict arg-value dict-subset is proven deterministically in the
        # CP1 behaviour smoke test). parked:true is the real HITL signal here.
        "kind": "durable",
        "input_payload": {"message": "Refund order 12345 amount 30, account ACC-2."},
        "expected_output": "The refund for order 12345 was processed.",
        "expected_trajectory": {
            "match_mode": "superset",
            "steps": [
                {"tool": "get_weather"},
                {"tool": "refund_action", "expect_approval": True, "args_match": {}},
            ],
        },
    },
]
PASS_THRESHOLD = 0.7

results = []          # (name, ok, detail)
observed = []
def rec(name, ok, detail=""):
    results.append((name, bool(ok), detail))
def obs(msg):
    observed.append(msg)


async def provider_id(c):
    try:
        r = await c.get("/llm-providers/", params={"team": "platform"})
        if r.status_code < 300:
            items = r.json()
            items = items if isinstance(items, list) else items.get("items", [])
            if items:
                return items[0]["id"]
    except Exception:
        pass
    return None


async def wait_deploy_running(name, timeout=240):
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(
                select(Deployment.status)
                .join(Agent, Deployment.agent_id == Agent.id)
                .where(Agent.name == name, Deployment.environment == "sandbox")
                .order_by(desc(Deployment.deployed_at)).limit(1)
            )).scalar()
        if st == "running":
            return True, st
        if st == "failed":
            return False, st
    return False, st


async def sandbox_deployment_id(name):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Deployment.id)
            .join(Agent, Deployment.agent_id == Agent.id)
            .where(Agent.name == name, Deployment.environment == "sandbox",
                   Deployment.status == "running")
            .order_by(desc(Deployment.deployed_at)).limit(1)
        )).scalar()


async def wait_eval_terminal(run_id, timeout=1500):
    """Poll the EvalRun row (DB, not API) until completed/failed. Four full durable
    runs (deploy → park → self-approve → resume) each item → generous window."""
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(
                select(EvalRun.status).where(EvalRun.id == uuid.UUID(run_id))
            )).scalar()
        if st in ("completed", "failed"):
            return st
    return st or "timeout"


async def read_rows(run_id):
    async with AsyncSessionLocal() as s:
        run = (await s.execute(
            select(EvalRun).where(EvalRun.id == uuid.UUID(run_id))
        )).scalar_one()
        rows = (await s.execute(
            select(EvalRunResult)
            .where(EvalRunResult.eval_run_id == uuid.UUID(run_id))
            .order_by(EvalRunResult.dataset_item_idx)
        )).scalars().all()
    return run, {r.dataset_item_idx: r for r in rows}


async def main():
    ds_id = None
    run_id = None
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=90)
    try:
        pid = await provider_id(c)
        if not pid:
            rec("T-S72-000 llm provider resolvable", False, "no platform LLM provider")
            return

        # 1. create + deploy a REAL durable agent that calls two real tools.
        # agent_class=daemon: the eval-runner dispatches durable runs as a SERVICE
        # identity with NO user_id. For a `user_delegated` agent the WS-2 OPA
        # identity floor fail-closes (missing_user_identity) and DENIES every tool
        # — so a high-risk tool is denied, never parked, and expect_approval can't
        # be observed. A daemon agent runs as its own machine identity (no live
        # user required), so its high-risk tools are ALLOWED and fire a real OPA
        # require_approval HITL park (verified: OPA returns allow=true,
        # require_approval=true, resolved_risk=high for daemon+empty-user). This is
        # the autonomous/batch shape suite-70 uses for real park→approve→resume.
        r = await c.post("/agents/", json={
            "name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "daemon",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid,
                         "tools": ["get_weather", "refund_action"]},
        })
        assert r.status_code in (200, 201), f"create agent {r.status_code}: {r.text[:200]}"
        await c.post(f"/agents/{AGENT}/deploy", json={"environment": "sandbox"})
        deployed, dep_status = await wait_deploy_running(AGENT)
        # Fixture unreachable is a HARD FAIL (never a fake pass) — the gate can't
        # be proven without a real pod to dispatch to.
        rec("T-S72-000 durable agent fixture deploys to a running sandbox pod (real pod)",
            deployed, f"deploy status={dep_status}")
        if not deployed:
            return

        # 2. author a REAL durable dataset via the real API (mode=durable persists;
        #    a malformed expected_trajectory would 422 at the door).
        r = await c.post("/playground/datasets", json={
            "name": f"s72-ds-{SFX}", "mode": "durable", "items": ITEMS,
        })
        assert r.status_code in (200, 201), f"dataset create {r.status_code}: {r.text[:300]}"
        ds_id = r.json()["id"]
        # Save→reload: the durable items + steps survive the round-trip.
        rg = await c.get(f"/playground/datasets/{ds_id}")
        ritems = rg.json().get("items", [])
        rt = (ritems[0].get("expected_trajectory") if ritems else None) or {}
        ds_ok = (rg.json().get("mode") == "durable" and len(ritems) == len(ITEMS)
                 and [s.get("tool") for s in (rt.get("steps") or [])] == ["get_weather", "refund_action"])
        obs(f"OBSERVED dataset reload: mode={rg.json().get('mode')} items={len(ritems)} "
            f"item0_steps={[s.get('tool') for s in (rt.get('steps') or [])]}")

        # 3. launch a REAL EvalRun (launches the real eval-runner K8s Job, MODE=durable).
        dep_id = await sandbox_deployment_id(AGENT)
        er = await c.post("/playground/eval-runs", json={
            "dataset_id": ds_id,
            "sandbox_deployment_id": str(dep_id) if dep_id else None,
            "agent_name": AGENT,
        })
        if er.status_code != 201:
            rec("T-S72-run eval-runner Job launched (real durable EvalRun)", False,
                f"POST /eval-runs {er.status_code}: {er.text[:200]}")
            return
        run_id = er.json()["id"]
        obs(f"OBSERVED eval_run_id={run_id}")

        # 4. poll the real Job to terminal (four full durable runs → long).
        status = await wait_eval_terminal(run_id)
        run, by_idx = await read_rows(run_id)
        obs(f"OBSERVED eval_run status={status} rows={len(by_idx)}/{len(ITEMS)} "
            f"overall_score={run.overall_score} pass_threshold={run.pass_threshold}")
        for i in sorted(by_idx):
            r0 = by_idx[i]
            appr = (r0.eval_detail or {}).get("approvals") if r0.eval_detail else None
            obs(f"OBSERVED item{i}: composite={r0.judge_score} dims={r0.dimension_scores} "
                f"passed={r0.passed} approvals={appr}")

        thr = float(run.pass_threshold) if run.pass_threshold is not None else PASS_THRESHOLD

        # ---- T-S72-001: CORRECT item persists all three dims + composite ----
        r0 = by_idx.get(0)
        if r0 is None:
            rec("T-S72-001 correct item persists response+trajectory+tool_call dims + composite",
                False, "no row for item 0 (eval Job did not score it in the window)")
        else:
            ds = r0.dimension_scores or {}
            # Require the trajectory/tool_call dims to be scored on REAL projected
            # tools (> 0), not just present-but-zero — an all-zero correct item
            # means run_steps carried no {tool,args} (producer not emitting), which
            # must surface here, not silently "pass" on non-null.
            ok = (r0.judge_score is not None
                  and ds.get("response") is not None
                  and ds.get("trajectory") is not None and float(ds["trajectory"]) > 0
                  and ds.get("tool_call") is not None and float(ds["tool_call"]) > 0)
            rec("T-S72-001 correct item persists response+trajectory+tool_call dims + composite (real tools projected)",
                ok, f"composite={r0.judge_score} dims={ds}")

        # ---- T-S72-002: WRONG-TOOL item FAILS the composite (core Eval v2 win) ----
        r1 = by_idx.get(1)
        if r1 is None:
            rec("T-S72-002 wrong-tool item FAILS composite (< pass threshold)",
                False, "no row for item 1 (eval Job did not score it in the window)")
        elif r1.dimension_scores is None:
            rec("T-S72-002 wrong-tool item FAILS composite (< pass threshold)",
                False, f"item 1 not scored on a real trajectory (fail-closed): "
                       f"passed={r1.passed} reason={(r1.eval_detail or {}).get('reason')}")
        else:
            ds = r1.dimension_scores
            # The win is strongest when the RESPONSE scored fine yet the composite
            # still failed because the wrong tools were called.
            comp_fails = r1.judge_score is not None and float(r1.judge_score) < thr
            traj_low = ds.get("trajectory") is not None and float(ds["trajectory"]) < 0.5
            rec("T-S72-002 wrong-tool item FAILS composite (< pass threshold)",
                comp_fails and traj_low and r1.passed is False,
                f"composite={r1.judge_score} (< {thr}?) trajectory={ds.get('trajectory')} "
                f"response={ds.get('response')} tool_call={ds.get('tool_call')} passed={r1.passed}")

        # ---- T-S72-003: WRONG-ORDER item scores trajectory < 1.0 under 'ordered' ----
        r2 = by_idx.get(2)
        if r2 is None:
            rec("T-S72-003 wrong-order item scores trajectory < 1.0 under match_mode=ordered",
                False, "no row for item 2 (eval Job did not score it in the window)")
        elif r2.dimension_scores is None:
            rec("T-S72-003 wrong-order item scores trajectory < 1.0 under match_mode=ordered",
                False, f"item 2 not scored on a real trajectory (fail-closed): passed={r2.passed}")
        else:
            tr = r2.dimension_scores.get("trajectory")
            # 0 < trajectory < 1.0 — the expected tools DID run (so the score is
            # non-zero) but out of the expected order under `ordered` (so it's
            # below 1.0). A flat 0 would mean no tools projected (producer gap),
            # not an order penalty — so require > 0 too.
            rec("T-S72-003 wrong-order item scores 0 < trajectory < 1.0 under match_mode=ordered",
                tr is not None and 0.0 < float(tr) < 1.0,
                f"trajectory={tr} (ordered; expected tools ran but out of expected order)")

        # ---- T-S72-004: GATED item — a REAL OPA require_approval HITL PARK ----
        r3 = by_idx.get(3)
        if r3 is None:
            rec("T-S72-004a gated item fired a REAL HITL park (run_steps awaiting_approval + approval_id)",
                False, "no row for item 3 (eval Job did not score it in the window)")
        else:
            # 004a (HARD): prove the REAL park from run_steps — the strongest,
            # unambiguous evidence the high-risk tool parked for real. The 0.1.45
            # producer emits the awaiting_approval boundary with {tool,args}+
            # approval_id; the eval-runner self-approved and the run completed
            # (the refund was actually processed). This is the real durable HITL
            # park, dispatch→pod→interrupt→self-approve→resume, end to end.
            parked_step = None
            if r3.run_id is not None:
                async with AsyncSessionLocal() as s:
                    parked_step = (await s.execute(text(
                        "SELECT step_number, approval_id FROM run_steps "
                        "WHERE run_id = :r AND name LIKE 'tool:refund_action%' "
                        "AND status = 'awaiting_approval' AND approval_id IS NOT NULL "
                        "ORDER BY step_number LIMIT 1"), {"r": str(r3.run_id)})).first()
            rec("T-S72-004a gated item fired a REAL HITL park (refund_action run_step awaiting_approval + approval_id)",
                parked_step is not None,
                f"parked_step={tuple(parked_step) if parked_step else None} run_id={r3.run_id}")

            # 004b (HARD): the judge's tool-arg review recorded the gated step with
            # BOTH parked:true AND args_matched:true. The former E-1 SCORING bug is
            # FIXED: the eval-runner projection (_project_trajectory /
            # _collapse_tool_calls, eval-runner/main.py) now COLLAPSES a single
            # logical tool call's consecutive same-tool run_steps — the gated call's
            # running(no appr) boundary and its separate awaiting_approval(appr_id)
            # boundary — into ONE trajectory entry carrying the parked disposition.
            # judge.score_tool_calls then matches the expect_approval step to that
            # collapsed entry, so _step_parked sees awaiting_approval + approval_id →
            # parked:true. This is the REAL park (004a) scored correctly, no fake.
            apprs = (r3.eval_detail or {}).get("approvals") or []
            gated = next((a for a in apprs if a.get("step") == "refund_action"), None)
            parked_ok = bool(gated and gated.get("parked") is True and gated.get("args_matched") is True)
            rec("T-S72-004b gated approvals[] recorded parked:true + args_matched:true (E-1 scoring bug fixed)",
                parked_ok, f"approvals={apprs}")

        # ---- T-S72-005: FAIL-CLOSED invariant over the real rows ----
        # No row is ever a PASS on empty scores. Every row is EITHER scored on a
        # real trajectory (dimension_scores non-null) OR recorded failed with null
        # dimension_scores (fail-closed) — never scored-as-pass on an empty
        # trajectory. Boundary: provoking a real unreachable item against a single
        # shared agent isn't cleanly forcible; this invariant is the honest
        # assertion of the fail-closed contract over the real persisted rows.
        rows = list(by_idx.values())
        if not rows:
            rec("T-S72-005 fail-closed invariant: no row is a pass on empty scores",
                False, "no rows persisted")
        else:
            violations = [r.dataset_item_idx for r in rows
                          if r.dimension_scores is None and r.passed is True]
            rec("T-S72-005 fail-closed invariant: no row is a pass on empty scores",
                len(violations) == 0,
                f"rows={len(rows)} pass-on-empty-score violations={violations}")

        # Boundary note when the Job didn't fully complete on this cluster.
        if status != "completed" or len(by_idx) < len(ITEMS):
            obs(f"BOUNDARY: eval_run status={status}, {len(by_idx)}/{len(ITEMS)} items scored — "
                f"asserted the strongest REAL persisted state on the rows that landed (suite-58/70 bar); "
                f"no score fabricated.")
        # Dataset round-trip (secondary evidence — the durable authoring survives).
        rec("T-S72-006 durable dataset survives save→reload with its steps", ds_ok,
            f"reload mode/items/item0-steps ok={ds_ok}")

    finally:
        # write results BEFORE cleanup (suite-69 lesson), then tear down.
        lines = []
        for name, ok, detail in results:
            lines.append(f"{'PASS' if ok else 'FAIL'}  {name}  |  {detail}")
        for o in observed:
            lines.append(o)
        lines.append("SUMMARY done")
        with open(OUT, "w") as f:
            f.write("\n".join(lines) + "\n")
        try:
            if ds_id:
                await c.delete(f"/playground/datasets/{ds_id}")
        except Exception:
            pass
        try:
            await c.delete(f"/agents/{AGENT}")
        except Exception:
            pass
        await c.aclose()


asyncio.run(main())
PY

echo "  running detached in-pod driver (create+deploy+4 durable runs+park/approve can take ~10-20 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/s72_run.log 2>&1 & echo started"

FOUND=""
for i in $(seq 1 360); do   # up to ~30 min
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    FOUND=1
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "ERROR: no driver result file after ~30 min — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -50 /tmp/s72_run.log 2>/dev/null || true
  echo "❌ Suite 72 FAILED (driver did not report)"
  exit 1
fi

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
echo ""
PASS=0; FAIL=0
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*|BOUNDARY*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

echo ""
echo "=== suite-72 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ Suite 72 FAILED"
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ Suite 72 INCONCLUSIVE (no assertions ran)"
  exit 1
fi
echo "✅ Suite 72 PASSED"
