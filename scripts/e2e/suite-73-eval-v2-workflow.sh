#!/usr/bin/env bash
# scripts/e2e/suite-73-eval-v2-workflow.sh
#
# E2E Suite 73: Eval v2 E-5 — NO-FAKES workflow run-tree (member-path) gate.
#
# This is E-5's acceptance gate (Checkpoint CP1b, the MVP gate). The whole reason
# E-5 exists is: score a workflow on its REAL run tree — the ordered MEMBER PATH
# (which members ran, in order) + an optional per-member rubric zoom into a child's
# run_steps + the final response — persist it, and gate on it. NO fakes: real
# member agents, a real CompositeWorkflow, real deployed pods, a real EvalRun → the
# real eval-runner Job → real durable workflow runs → the real parent/child run tree
# → the real judge (score_member_path + per-member score_response) → persisted
# dimension_scores.member_path + eval_detail + run_id, read back FROM THE DB.
#
# NO faked _run_step, NO mocked judge, NO hand-built tree/trajectory, NO page.route.
# The member path comes from the REAL run tree children (agent_name ordered by
# started_at) — nowhere else. Fixture-unreachable is a HARD FAIL, never a skip.
#
#   T-S73-001 — the CORRECT-route item persists dimension_scores.member_path (==1.0)
#               + composite + run_id (points at the parent workflow run tree), read
#               back from the DB; eval_detail.actual_member_path == the REAL ordered
#               child agent names from the tree.
#   T-S73-002 — the WRONG-route item (its expected_member_path names a member the
#               run does NOT take) scores member_path < 1.0 — the core E-5 win — so
#               its composite drops below the correct item's; member_diff.missing[]
#               names the un-taken member.
#   T-S73-003 — per-member evidence: eval_detail.per_member[] carries a score for the
#               real member child named in the item's `per_member` rubric (the child
#               whose run_steps the runner zoomed into).
#   T-S73-004 — the gate: overall_score is the item pass-rate; when it clears the
#               threshold, eval_passed auto-sets True on the WORKFLOW VERSION. (If
#               LLM response variance drops the pass-rate below threshold, the suite
#               asserts the EvalRun terminal status + overall_score instead — the
#               documented boundary — never fabricating the flip.)
#   T-S73-005 — FAIL-CLOSED invariant: no persisted row is a pass on an empty member
#               path. Every row is EITHER scored on a real member path
#               (dimension_scores non-null) OR recorded failed — never scored-as-pass
#               on an empty tree.
#
# Real pods + real LLM + real sequential workflow (3 members, one calling a real
# HTTP tool) + real Job → SLOW. It creates ALL its own resources up front and tears
# them down. A detached in-pod driver (suite-72 pattern) runs the
# create→deploy→version→launch→poll→assert→write so a long wait can't kill the exec;
# the result file is written BEFORE cleanup.
#
# Members are daemon-class (the eval-runner dispatches with no live user; a
# user_delegated member's tools get OPA-denied `missing_user_identity`). The tool is
# an HTTP tool (get_weather → in-cluster /echo); python-type tools crash the agent
# pod at graph-build (docs/bugs/python-tool-graph-build-kwargs.md).
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 73: Eval v2 E-5 NO-FAKES workflow run-tree (member-path) gate ==="
echo "  Pod: $API_POD"
echo ""

DRIVER=/tmp/s73_driver.py
OUTFILE=/tmp/s73_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "rm -f $OUTFILE /tmp/s73_run.log; cat > $DRIVER" <<'PY'
import asyncio, json, uuid
import httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment, EvalRun, EvalRunResult, WorkflowVersion

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:8]
OUT = "/tmp/s73_out.txt"

# Three real daemon durable members. The workflow is SEQUENTIAL so the member path
# is deterministic: [intake, triage, resolver] (children ordered by started_at).
INTAKE = f"s73-intake-{SFX}"
TRIAGE = f"s73-triage-{SFX}"   # calls get_weather → non-empty run_steps to zoom into
RESOLVER = f"s73-resolver-{SFX}"
GHOST = f"s73-ghost-{SFX}"      # a member the run NEVER takes (for the wrong-route item)
WF = f"s73-wf-{SFX}"
MEMBERS = [INTAKE, TRIAGE, RESOLVER]

INSTR_INTAKE = (
    "You are the intake step. Acknowledge the request in ONE short sentence, then "
    "pass it along. Never ask questions."
)
# triage MUST call the get_weather tool so its child run_steps are non-empty (the
# per-member rubric zooms into these real steps).
INSTR_TRIAGE = (
    "You are the triage step. You MUST FIRST call the get_weather tool with "
    "location='HQ' (a required compliance check). ONLY AFTER get_weather returns, "
    "reply with ONE short sentence and pass the case along. Always call get_weather. "
    "Never ask questions."
)
# resolver emits a FIXED sentence so the parent workflow output (== last member's
# output) matches expected_output deterministically → a high response dimension,
# independent of the input, for BOTH items.
INSTR_RESOLVER = (
    "You are the resolver step. Regardless of the input, reply with EXACTLY this "
    "sentence and nothing else: Case resolved successfully."
)
EXPECTED_OUTPUT = "Case resolved successfully."

# Correct-route item: expected member path == the real sequential path.
# Wrong-route item: expected path names GHOST (never runs) → member_path<1.0,
# member_diff.missing=[GHOST]. Both answer correctly (same workflow output), so the
# ONLY thing that separates them is the member-path dimension — exactly the E-5 win.
CORRECT_ITEM = {
    "kind": "workflow",
    "input_message": "Please handle support case 12345.",
    "expected_output": EXPECTED_OUTPUT,
    "expected_member_path": [INTAKE, TRIAGE, RESOLVER],
    "per_member": {TRIAGE: {"rubric": "The member performed a compliance/weather check step (called a tool)."}},
}
WRONG_ITEM = {
    "kind": "workflow",
    "input_message": "Please handle support case 67890.",
    "expected_output": EXPECTED_OUTPUT,
    # names GHOST after resolver — the run never routes through it → ordered LCS 3/4.
    "expected_member_path": [INTAKE, TRIAGE, RESOLVER, GHOST],
}
ITEMS = [CORRECT_ITEM, WRONG_ITEM]
PASS_THRESHOLD = 0.7

results = []
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


async def wait_deploy_running(names, timeout=300):
    by = {}
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(
                select(Agent.name, Deployment.status)
                .join(Deployment, Deployment.agent_id == Agent.id)
                .where(Agent.name.in_(names), Deployment.environment == "sandbox")
            )).all()
        by = {n: st for (n, st) in rows}
        if all(by.get(n) == "running" for n in names):
            return True, by
        if any(by.get(n) == "failed" for n in names):
            return False, by
    return False, by


async def wait_eval_terminal(run_id, timeout=1500):
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


async def wf_version_eval_passed(version_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(WorkflowVersion.eval_passed).where(WorkflowVersion.id == uuid.UUID(version_id))
        )).scalar()


async def main():
    ds_id = None
    wf_id = None
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=90)
    try:
        pid = await provider_id(c)
        if not pid:
            rec("T-S73-000 llm provider resolvable", False, "no platform LLM provider")
            return

        # 1. create + deploy three real daemon durable members.
        specs = [(INTAKE, INSTR_INTAKE, []),
                 (TRIAGE, INSTR_TRIAGE, ["get_weather"]),
                 (RESOLVER, INSTR_RESOLVER, [])]
        for name, instr, tools in specs:
            r = await c.post("/agents/", json={
                "name": name, "team": "platform", "agent_type": "declarative",
                "execution_shape": "durable", "agent_class": "daemon",
                "metadata": {"instructions": instr, "llm_provider_id": pid, "tools": tools},
            })
            assert r.status_code in (200, 201), f"create {name} {r.status_code}: {r.text[:200]}"
            await c.post(f"/agents/{name}/deploy", json={"environment": "sandbox"})
        deployed, statuses = await wait_deploy_running(MEMBERS)
        # Fixture unreachable is a HARD FAIL — the gate can't be proven without real pods.
        rec("T-S73-000 three daemon durable members deploy to running sandbox pods (real pods)",
            deployed, f"deploy statuses={statuses}")
        if not deployed:
            return

        # 2. create a real SEQUENTIAL daemon durable workflow over the three members.
        r = await c.post("/workflows", json={
            "name": WF, "team": "platform", "orchestration": "sequential",
            "execution_shape": "durable", "agent_class": "daemon",
        })
        assert r.status_code in (200, 201), f"create workflow {r.status_code}: {r.text[:200]}"
        wf_id = r.json()["id"]
        for i, name in enumerate(MEMBERS):
            g = await c.get(f"/agents/{name}")
            aid = g.json()["id"]
            mr = await c.post(f"/workflows/{wf_id}/members", json={"agent_id": aid, "position": i + 1})
            assert mr.status_code in (200, 201), f"add member {name} {mr.status_code}: {mr.text[:200]}"
        # snapshot a version so eval_passed can auto-set on it from the composite.
        vr = await c.post(f"/workflows/{wf_id}/versions", json={})
        assert vr.status_code in (200, 201), f"create version {vr.status_code}: {vr.text[:200]}"
        wf_version_id = vr.json()["id"]
        obs(f"OBSERVED workflow={wf_id} version={wf_version_id} members={MEMBERS}")

        # 3. author a real mode=workflow dataset (a malformed item would 422 at the door).
        r = await c.post("/playground/datasets", json={
            "name": f"s73-ds-{SFX}", "mode": "workflow", "items": ITEMS,
        })
        assert r.status_code in (200, 201), f"dataset create {r.status_code}: {r.text[:300]}"
        ds_id = r.json()["id"]
        # save→reload: the workflow items survive the round-trip with their member path.
        rg = await c.get(f"/playground/datasets/{ds_id}")
        ritems = rg.json().get("items", [])
        r0mp = (ritems[0].get("expected_member_path") if ritems else None) or []
        ds_ok = (rg.json().get("mode") == "workflow" and len(ritems) == len(ITEMS)
                 and r0mp == [INTAKE, TRIAGE, RESOLVER])
        obs(f"OBSERVED dataset reload: mode={rg.json().get('mode')} items={len(ritems)} "
            f"item0_member_path={r0mp}")

        # 4. launch a REAL EvalRun against the workflow VERSION → real eval-runner Job
        #    (WORKFLOW_ID set → the workflow run-tree branch).
        er = await c.post("/playground/eval-runs", json={
            "dataset_id": ds_id,
            "workflow_id": wf_id,
            "workflow_version_id": wf_version_id,
            "agent_name": WF,
        })
        if er.status_code != 201:
            rec("T-S73-run eval-runner Job launched (real workflow EvalRun)", False,
                f"POST /eval-runs {er.status_code}: {er.text[:300]}")
            return
        run_id = er.json()["id"]
        obs(f"OBSERVED eval_run_id={run_id}")

        # 5. poll the real Job to terminal (two real workflow runs, 3 members each).
        status = await wait_eval_terminal(run_id)
        run, by_idx = await read_rows(run_id)
        obs(f"OBSERVED eval_run status={status} rows={len(by_idx)}/{len(ITEMS)} "
            f"overall_score={run.overall_score} pass_threshold={run.pass_threshold}")
        for i in sorted(by_idx):
            r0 = by_idx[i]
            det = r0.eval_detail or {}
            obs(f"OBSERVED item{i}: composite={r0.judge_score} dims={r0.dimension_scores} "
                f"passed={r0.passed} run_id={r0.run_id} "
                f"actual_member_path={det.get('actual_member_path')} "
                f"member_diff={det.get('member_diff')} "
                f"per_member={det.get('per_member')}")

        thr = float(run.pass_threshold) if run.pass_threshold is not None else PASS_THRESHOLD

        # ---- T-S73-001: CORRECT item persists member_path==1.0 + composite + run_id ----
        r0 = by_idx.get(0)
        if r0 is None:
            rec("T-S73-001 correct item persists member_path+composite+run_id; actual_member_path from real tree",
                False, "no row for item 0 (eval Job did not score it in the window)")
        else:
            ds = r0.dimension_scores or {}
            det = r0.eval_detail or {}
            actual_mp = det.get("actual_member_path") or []
            ok = (r0.judge_score is not None
                  and ds.get("member_path") is not None and float(ds["member_path"]) == 1.0
                  and r0.run_id is not None
                  and actual_mp == [INTAKE, TRIAGE, RESOLVER])
            rec("T-S73-001 correct item persists member_path==1.0 + composite + run_id; actual_member_path == real tree order",
                ok, f"member_path={ds.get('member_path')} composite={r0.judge_score} "
                    f"run_id={r0.run_id} actual_member_path={actual_mp}")

        # ---- T-S73-002: WRONG-route item scores member_path < 1.0 (the core E-5 win) ----
        r1 = by_idx.get(1)
        if r1 is None:
            rec("T-S73-002 wrong-route item scores member_path < 1.0 (composite drops; member_diff.missing)",
                False, "no row for item 1 (eval Job did not score it in the window)")
        elif r1.dimension_scores is None:
            rec("T-S73-002 wrong-route item scores member_path < 1.0",
                False, f"item 1 not scored on a real member path (fail-closed): passed={r1.passed}")
        else:
            ds1 = r1.dimension_scores
            det1 = r1.eval_detail or {}
            mp1 = ds1.get("member_path")
            diff = det1.get("member_diff") or {}
            missing = diff.get("missing") or []
            # member_path strictly < 1.0 AND > 0 (the expected members DID run, just
            # not the extra GHOST) AND the diff names the un-taken member.
            comp0 = float(by_idx[0].judge_score) if by_idx.get(0) and by_idx[0].judge_score is not None else None
            comp1 = float(r1.judge_score) if r1.judge_score is not None else None
            comp_drops = comp0 is not None and comp1 is not None and comp1 < comp0
            ok = (mp1 is not None and 0.0 < float(mp1) < 1.0
                  and GHOST in missing and comp_drops)
            rec("T-S73-002 wrong-route item scores 0 < member_path < 1.0; member_diff.missing names the un-taken member; composite drops",
                ok, f"member_path={mp1} (<1.0?) member_diff.missing={missing} "
                    f"composite_wrong={comp1} composite_correct={comp0}")

        # ---- T-S73-003: per-member evidence for the real member child ----
        if r0 is None:
            rec("T-S73-003 per-member evidence: eval_detail.per_member[] carries a score for the rubric member",
                False, "no row for item 0")
        else:
            det = r0.eval_detail or {}
            pm = det.get("per_member") or []
            triage_pm = next((p for p in pm if p.get("member") == TRIAGE), None)
            # backend emits {member, score, reason, rubric, had_steps} — score present.
            ok = triage_pm is not None and triage_pm.get("score") is not None
            rec("T-S73-003 per-member evidence: per_member[] carries a score for the real member child (triage)",
                ok, f"per_member={pm}")

        # ---- T-S73-004: the gate — overall pass-rate → eval_passed on the wf version ----
        wf_ep = await wf_version_eval_passed(wf_version_id)
        overall = float(run.overall_score) if run.overall_score is not None else None
        if overall is not None and overall >= thr:
            rec("T-S73-004 gate: overall pass-rate >= threshold → eval_passed auto-set True on the WORKFLOW VERSION",
                wf_ep is True, f"overall_score={overall} (>= {thr}) wf_version.eval_passed={wf_ep}")
        else:
            # BOUNDARY: LLM response variance dropped the pass-rate below threshold —
            # assert the EvalRun terminal state + overall_score instead of the flip
            # (never fabricate eval_passed). eval_passed must then be NOT set.
            rec("T-S73-004 gate (boundary): EvalRun terminal + overall_score present; eval_passed NOT falsely set when pass-rate < threshold",
                status == "completed" and overall is not None and wf_ep is not True,
                f"status={status} overall_score={overall} (< {thr}) wf_version.eval_passed={wf_ep} — "
                f"documented boundary: response-dim variance kept an item below composite pass")

        # ---- T-S73-005: FAIL-CLOSED invariant over the real rows ----
        rows = list(by_idx.values())
        if not rows:
            rec("T-S73-005 fail-closed invariant: no row is a pass on an empty member path",
                False, "no rows persisted")
        else:
            # no row is passed with a null/empty member-path dimension.
            violations = []
            for r in rows:
                dsx = r.dimension_scores
                if r.passed is True and (dsx is None or dsx.get("member_path") is None):
                    violations.append(r.dataset_item_idx)
            rec("T-S73-005 fail-closed invariant: no row is a pass on an empty member path",
                len(violations) == 0,
                f"rows={len(rows)} pass-on-empty-member-path violations={violations}")

        # Boundary note when the Job didn't fully complete on this cluster.
        if status != "completed" or len(by_idx) < len(ITEMS):
            obs(f"BOUNDARY: eval_run status={status}, {len(by_idx)}/{len(ITEMS)} items scored — "
                f"asserted the strongest REAL persisted state on the rows that landed "
                f"(suite-58/72 bar); no score fabricated.")
        rec("T-S73-006 workflow dataset survives save→reload with its member path", ds_ok,
            f"reload mode/items/item0-member-path ok={ds_ok}")

    finally:
        # write results BEFORE cleanup, then tear down.
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
            if wf_id:
                await c.delete(f"/workflows/{wf_id}")
        except Exception:
            pass
        for name in MEMBERS:
            try:
                await c.delete(f"/agents/{name}")
            except Exception:
                pass
        await c.aclose()


asyncio.run(main())
PY

echo "  running detached in-pod driver (create+deploy 3 pods + 2 workflow runs can take ~10-20 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/s73_run.log 2>&1 & echo started"

FOUND=""
for i in $(seq 1 420); do   # up to ~35 min
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    FOUND=1
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "ERROR: no driver result file after ~35 min — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -50 /tmp/s73_run.log 2>/dev/null || true
  echo "❌ Suite 73 FAILED (driver did not report)"
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
echo "=== suite-73 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ Suite 73 FAILED"
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ Suite 73 INCONCLUSIVE (no assertions ran)"
  exit 1
fi
echo "✅ Suite 73 PASSED"
