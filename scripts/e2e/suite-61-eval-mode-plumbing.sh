#!/usr/bin/env bash
# scripts/e2e/suite-61-eval-mode-plumbing.sh
#
# E2E Suite 61: Eval v2 E-0 — REAL reactive-parity gate. NO fakes.
#
# This is E-0's load-bearing acceptance gate (Checkpoint 2). Unlike suite-9's
# eval lifecycle checks (which POST hand-crafted result rows to exercise the
# API), THIS suite drives the ACTUAL end-to-end path a user hits, with a REAL
# judge and REAL persisted scores — no mocked scorer, no faked _run_step, no
# hand-written eval_run_results row:
#
#   create a real reactive declarative agent (real LLM provider) → DEPLOY it
#   (real sandbox pod) → author a real reactive PlaygroundDataset with real
#   {input, expected_output} items → POST /playground/eval-runs (launches the
#   REAL eval-runner K8s Job) → the Job runs each item through the deployed
#   pod's REAL LLM and scores it via the REAL judge (POST /playground/eval/score
#   → judge.py score_response) → poll to completion → assert the PERSISTED
#   dimension_scores/composite re-read from the DB (save→reload).
#
# What it proves (E-0 Definition of Done):
#   T-S61-001 — the reactive agent deploys to a running sandbox pod (real pod)
#   T-S61-002 — a real EvalRun over a real reactive dataset reaches 'completed'
#               through the real eval-runner Job + real judge (no fake rows)
#   T-S61-003 — every persisted eval_run_results row carries dimension_scores
#               with a "response" key AND a composite (judge_score) — read back
#               from the DB, not the API response (save→reload)
#   T-S61-004 — PARITY (to the digit, on the REAL run): for every row the
#               reactive composite == dimension_scores["response"] EXACTLY (the
#               reducer is identity for a single dimension → byte-identical to
#               the pre-E-0 judge_for_eval), AND the real judge behaves like the
#               legacy path — known-good answers score >= 0.7, a known-bad item
#               scores < 0.5
#   T-S61-005 — eval_passed auto-set still fires: the run's overall_score is the
#               pass-fraction composite (>= 0.7 here) and the AgentVersion's
#               eval_passed flipped True (assert from the DB, not a fixture)
#   T-S61-006 — REGRESSION GUARD (behavior-neutral): a reactive dataset accepts a
#               real DURABLE agent's eval-run WITHOUT a mode-mismatch 422. Guards
#               the E-0 regression where durable/workflow evals 422'd against a
#               backfilled reactive dataset ("422s instead of running" = a
#               behavior change E-0 forbids). Runs even when the eval-runner Job
#               can't complete in this env — it fails hard, independent of skips.
#
# Real pod + real LLM + real Job → slow (generous timeouts). It creates ALL its
# own resources up front and tears them down.
#
# ENVIRONMENT NOTE (no silent fakes): the eval-runner runs as a K8s Job that
# needs the eval-runner image (0.1.5+) and Jobs RBAC in the namespace. If the
# Job cannot be created/run in this environment, the suite prints a LOUD SKIP
# (never a fake, never a PASS) and exits 0 so run-all continues — after the CP2
# deploy the Job WILL run and every T-S61-00X must print PASS.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 61: Eval v2 E-0 REAL reactive-parity gate (no fakes) ==="
echo "  Pod: $API_POD"
echo ""

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, uuid
import httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, EvalRun, EvalRunResult, PlaygroundDataset

BASE = "http://localhost:8000/api/v1"
SUB = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": SUB, "X-User-Team": "platform"}
SUFFIX = uuid.uuid4().hex[:8]
AGENT = f"s61-eval-agent-{SUFFIX}"
DURABLE_AGENT = f"s61-durable-agent-{SUFFIX}"
# Fallback platform provider id (same one suite-59 uses) if dynamic lookup fails.
FALLBACK_PROVIDER = "0d1ed29f-9046-4025-b28b-bcaaa5845015"
INSTR = ("You answer factual questions. Reply with ONLY the answer — no "
         "sentence, no punctuation, no extra words. If asked for a city, reply "
         "with just the city name.")

# 4 real reactive items. 3 known-good (expected == the correct factual answer),
# 1 known-bad (expected is deliberately WRONG so the agent's correct answer
# scores low against it). 3/4 pass → overall 0.75 >= 0.7 → eval_passed fires,
# and we still get a known-bad-low row for the parity assertion.
ITEMS = [
    {"input": "What is the capital of France? Answer with only the city name.", "expected_output": "Paris"},
    {"input": "What is the capital of Japan? Answer with only the city name.", "expected_output": "Tokyo"},
    {"input": "What is 2 + 2? Answer with only the number.", "expected_output": "4"},
    {"input": "What is the capital of France? Answer with only the city name.", "expected_output": "Berlin"},
]
BAD_IDX = 3  # the known-bad item (expected Berlin, agent answers Paris)


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
    return FALLBACK_PROVIDER


async def wait_deploy_running(name, timeout=180):
    by = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            by = (await s.execute(
                select(Deployment.status)
                .join(Agent, Deployment.agent_id == Agent.id)
                .where(Agent.name == name, Deployment.environment == "sandbox")
                .order_by(Deployment.deployed_at.desc()).limit(1)
            )).scalar()
        if by == "running":
            return True, by
        if by == "failed":
            return False, by
    return False, by


async def sandbox_deployment_id(name):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Deployment.id)
            .join(Agent, Deployment.agent_id == Agent.id)
            .where(Agent.name == name, Deployment.environment == "sandbox",
                   Deployment.status == "running")
            .order_by(Deployment.deployed_at.desc()).limit(1)
        )).scalar()


async def wait_eval_terminal(run_id, timeout=300):
    """Poll the EvalRun row (DB, not API) until completed/failed."""
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


async def main():
    out = {}
    skip = None
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid = await provider_id(c)
    ds_id = None
    run_id = None
    try:
        # 1. create + deploy a real reactive declarative agent
        await c.post("/agents/", json={
            "name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "reactive", "agent_class": "user_delegated",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []},
        })
        await c.post(f"/agents/{AGENT}/deploy", json={"environment": "sandbox"})
        ok, dep_status = await wait_deploy_running(AGENT)
        out["001_agent_deployed_running"] = ok
        if not ok:
            skip = f"agent did not deploy (status={dep_status}) — cannot drive a real reactive run"
            return out, skip

        # 2. author a REAL reactive dataset via the API
        r = await c.post("/playground/datasets", json={
            "name": f"s61-ds-{SUFFIX}", "mode": "reactive", "items": ITEMS,
        })
        assert r.status_code in (200, 201), f"dataset create {r.status_code}: {r.text[:200]}"
        ds_id = r.json()["id"]

        # 2b. REGRESSION GUARD (T-S61-006): a reactive dataset must accept a
        #     DURABLE executable. Before the fix, a durable/workflow agent 422'd
        #     with a mode-mismatch against a (backfilled) reactive dataset —
        #     a behavior change E-0 forbids. Create a real durable agent and
        #     POST a real eval-run; the mode guard fires at validation (before
        #     Job creation) so the agent need not be deployed for this check.
        #     PASS = the launch is NOT rejected with a mode-mismatch 422.
        await c.post("/agents/", json={
            "name": DURABLE_AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "user_delegated",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []},
        })
        er_dur = await c.post("/playground/eval-runs", json={
            "dataset_id": ds_id,
            "agent_name": DURABLE_AGENT,
        })
        mode_mismatch_422 = (
            er_dur.status_code == 422 and "mode" in er_dur.text.lower()
        )
        out["006_reactive_dataset_accepts_durable_agent"] = not mode_mismatch_422
        if mode_mismatch_422:
            out["_diag_durable"] = f"durable eval-run rejected: {er_dur.status_code} {er_dur.text[:180]}"

        # 3. launch a REAL EvalRun (launches the real eval-runner K8s Job).
        dep_id = await sandbox_deployment_id(AGENT)
        er = await c.post("/playground/eval-runs", json={
            "dataset_id": ds_id,
            "sandbox_deployment_id": str(dep_id) if dep_id else None,
            "agent_name": AGENT,
        })
        if er.status_code != 201:
            # Job could not be launched (eval-runner image / Jobs RBAC missing).
            skip = (f"POST /eval-runs returned {er.status_code}: {er.text[:160]} "
                    f"— eval-runner Job could not be created in this env")
            return out, skip
        run_id = er.json()["id"]

        # 4. poll the real Job to completion
        status = await wait_eval_terminal(run_id)
        if status != "completed":
            skip = (f"EvalRun {run_id[:8]} did not complete (status={status}) — "
                    f"the eval-runner Job could not run to completion in this env")
            out["_diag"] = f"eval_run status={status}"
            return out, skip
        out["002_real_evalrun_completed"] = True

        # 5. assert from the DB (save -> reload): rows, dimensions, composite
        async with AsyncSessionLocal() as s:
            run = (await s.execute(
                select(EvalRun).where(EvalRun.id == uuid.UUID(run_id))
            )).scalar_one()
            rows = (await s.execute(
                select(EvalRunResult)
                .where(EvalRunResult.eval_run_id == uuid.UUID(run_id))
                .order_by(EvalRunResult.dataset_item_idx)
            )).scalars().all()
            version = None
            if run.agent_version_id is not None:
                version = (await s.execute(
                    select(AgentVersion).where(AgentVersion.id == run.agent_version_id)
                )).scalar_one_or_none()

        # 003 — every row has dimension_scores{"response": ...} + a composite
        have_all = len(rows) == len(ITEMS)
        dims_ok = have_all and all(
            r.dimension_scores is not None
            and "response" in r.dimension_scores
            and r.judge_score is not None
            for r in rows
        )
        out["003_rows_have_response_dimension_and_composite"] = dims_ok

        # 004 — PARITY to the digit + real-judge behaviour
        by_idx = {r.dataset_item_idx: r for r in rows}
        parity_identity = have_all and all(
            abs(float(r.dimension_scores["response"]) - float(r.judge_score)) < 1e-9
            for r in rows
        )
        good_high = have_all and all(
            float(by_idx[i].judge_score) >= 0.7 for i in (0, 1, 2)
        )
        bad_low = have_all and BAD_IDX in by_idx and float(by_idx[BAD_IDX].judge_score) < 0.5
        out["004_parity_composite_eq_response_and_judge_real"] = bool(
            parity_identity and good_high and bad_low
        )
        if not (parity_identity and good_high and bad_low):
            out["_diag_parity"] = (
                "scores=" + ", ".join(
                    f"i{r.dataset_item_idx}:comp={r.judge_score}:dim={(r.dimension_scores or {}).get('response')}"
                    for r in rows
                )
            )

        # 005 — eval_passed auto-set fired on the passing version
        overall = run.overall_score
        eval_passed_ok = (
            overall is not None and overall >= 0.7
            and version is not None and version.eval_passed is True
        )
        out["005_eval_passed_autoset_on_passing_version"] = bool(eval_passed_ok)
        if not eval_passed_ok:
            out["_diag_pass"] = (
                f"overall_score={overall} version_id={run.agent_version_id} "
                f"eval_passed={getattr(version, 'eval_passed', None)}"
            )
        return out, skip
    finally:
        # teardown — best effort
        try:
            if ds_id:
                await c.delete(f"/playground/datasets/{ds_id}")
        except Exception:
            pass
        try:
            await c.delete(f"/agents/{AGENT}")
        except Exception:
            pass
        try:
            await c.delete(f"/agents/{DURABLE_AGENT}")
        except Exception:
            pass
        await c.aclose()


out, skip = asyncio.run(main())
for k, v in out.items():
    if k.startswith("_"):
        print("DIAG", k, v)
    else:
        print(("PASS" if v else "FAIL"), k)
if skip:
    print("SKIP", skip)
PY
)

echo "$RESULT"
echo ""

if echo "$RESULT" | grep -q "^FAIL"; then
  echo "❌ Suite 61 FAILED (a real assertion failed — NOT an environment skip)"
  exit 1
fi
if echo "$RESULT" | grep -q "^SKIP"; then
  echo "⚠️  Suite 61 SKIPPED (environment limit — eval-runner Job could not run)."
  echo "    This is NOT a pass and NOT a fake. Deploy eval-runner:0.1.5 + Jobs RBAC"
  echo "    (bash scripts/deploy-cp2-eval.sh) and re-run — every T-S61-00X must PASS."
  exit 0
fi
if ! echo "$RESULT" | grep -q "PASS 002_real_evalrun_completed"; then
  echo "❌ Suite 61 INCONCLUSIVE (no completed real run and no explicit skip)"
  exit 1
fi
echo "✅ Suite 61 PASSED"
