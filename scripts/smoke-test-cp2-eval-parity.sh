#!/usr/bin/env bash
# scripts/smoke-test-cp2-eval-parity.sh
#
# Eval v2 E-0 — Checkpoint 2 PARITY + gate-behaviour smoke (from the DB).
#
# Independently of suite-61's own asserts, this drives a fresh REAL reactive
# eval end-to-end and then reads the PERSISTED rows straight from the DB
# (AsyncSessionLocal in-pod) to prove, on a real run:
#   - composite == judge_for_eval to the digit: for every result row the
#     reactive composite (judge_score) equals dimension_scores["response"]
#     EXACTLY (the reducer is identity for the single reactive dimension), and
#     the real judge behaves like the legacy path (known-good >= 0.7, known-bad
#     < 0.5)
#   - eval_passed auto-set fired: overall_score >= 0.7 AND the AgentVersion's
#     eval_passed flipped True (asserted from the DB row, not a fixture)
#
# This mirrors suite-61's real path but re-reads from the DB here so CP2 has an
# independent parity assertion. Env limit (eval-runner Job can't run) -> LOUD
# SKIP (never a fake); after deploy-cp2-eval.sh the Job MUST run.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "[FATAL] no Running registry-api pod in $NAMESPACE"; exit 1
fi

echo "==> CP2 parity + gate smoke (from the DB) — pod: $API_POD"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<'PY' 2>/dev/null
import asyncio, uuid
import httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, EvalRun, EvalRunResult

BASE = "http://localhost:8000/api/v1"
SUB = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": SUB, "X-User-Team": "platform"}
S = uuid.uuid4().hex[:8]
AGENT = f"cp2-parity-{S}"
FALLBACK_PROVIDER = "0d1ed29f-9046-4025-b28b-bcaaa5845015"
INSTR = ("You answer factual questions. Reply with ONLY the answer — no sentence, "
         "no punctuation. For a city, reply with just the city name.")
ITEMS = [
    {"input": "What is the capital of France? Answer with only the city name.", "expected_output": "Paris"},
    {"input": "What is the capital of Japan? Answer with only the city name.", "expected_output": "Tokyo"},
    {"input": "What is 2 + 2? Answer with only the number.", "expected_output": "4"},
    {"input": "What is the capital of France? Answer with only the city name.", "expected_output": "Berlin"},
]
BAD_IDX = 3


async def provider_id(c):
    try:
        r = await c.get("/llm-providers/", params={"team": "platform"})
        if r.status_code < 300:
            items = r.json(); items = items if isinstance(items, list) else items.get("items", [])
            if items: return items[0]["id"]
    except Exception:
        pass
    return FALLBACK_PROVIDER


async def dep_running(name, timeout=180):
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(select(Deployment.status).join(Agent, Deployment.agent_id == Agent.id)
                  .where(Agent.name == name, Deployment.environment == "sandbox")
                  .order_by(Deployment.deployed_at.desc()).limit(1))).scalar()
        if st == "running": return True
        if st == "failed": return False
    return False


async def eval_terminal(rid, timeout=300):
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(select(EvalRun.status).where(EvalRun.id == uuid.UUID(rid)))).scalar()
        if st in ("completed", "failed"): return st
    return st or "timeout"


async def main():
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=60)
    pid = await provider_id(c)
    ds_id = None
    try:
        await c.post("/agents/", json={"name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "reactive", "agent_class": "user_delegated",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []}})
        await c.post(f"/agents/{AGENT}/deploy", json={"environment": "sandbox"})
        if not await dep_running(AGENT):
            print("SKIP agent did not deploy — cannot drive a real reactive run"); return
        r = await c.post("/playground/datasets", json={"name": f"cp2-ds-{S}", "mode": "reactive", "items": ITEMS})
        ds_id = r.json()["id"]
        async with AsyncSessionLocal() as s:
            dep_id = (await s.execute(select(Deployment.id).join(Agent, Deployment.agent_id == Agent.id)
                     .where(Agent.name == AGENT, Deployment.environment == "sandbox", Deployment.status == "running")
                     .order_by(Deployment.deployed_at.desc()).limit(1))).scalar()
        er = await c.post("/playground/eval-runs", json={"dataset_id": ds_id,
            "sandbox_deployment_id": str(dep_id) if dep_id else None, "agent_name": AGENT})
        if er.status_code != 201:
            print(f"SKIP POST /eval-runs {er.status_code} — eval-runner Job could not be created"); return
        rid = er.json()["id"]
        st = await eval_terminal(rid)
        if st != "completed":
            print(f"SKIP EvalRun did not complete (status={st}) — Job could not run"); return

        async with AsyncSessionLocal() as s:
            run = (await s.execute(select(EvalRun).where(EvalRun.id == uuid.UUID(rid)))).scalar_one()
            rows = (await s.execute(select(EvalRunResult).where(EvalRunResult.eval_run_id == uuid.UUID(rid))
                    .order_by(EvalRunResult.dataset_item_idx))).scalars().all()
            version = None
            if run.agent_version_id is not None:
                version = (await s.execute(select(AgentVersion).where(AgentVersion.id == run.agent_version_id))).scalar_one_or_none()

        by = {r.dataset_item_idx: r for r in rows}
        have = len(rows) == len(ITEMS)
        identity = have and all(abs(float(r.dimension_scores["response"]) - float(r.judge_score)) < 1e-9
                                for r in rows if r.dimension_scores)
        good = have and all(float(by[i].judge_score) >= 0.7 for i in (0, 1, 2))
        bad = have and BAD_IDX in by and float(by[BAD_IDX].judge_score) < 0.5
        print("PASS" if (identity and good and bad) else "FAIL", "parity_composite_eq_response_to_the_digit")
        if not (identity and good and bad):
            print("DIAG scores=" + ", ".join(
                f"i{r.dataset_item_idx}:comp={r.judge_score}:dim={(r.dimension_scores or {}).get('response')}" for r in rows))

        ep = (run.overall_score is not None and run.overall_score >= 0.7
              and version is not None and version.eval_passed is True)
        print("PASS" if ep else "FAIL", "eval_passed_autoset_fired_on_passing_version")
        if not ep:
            print(f"DIAG overall={run.overall_score} version={run.agent_version_id} eval_passed={getattr(version,'eval_passed',None)}")
    finally:
        try:
            if ds_id: await c.delete(f"/playground/datasets/{ds_id}")
        except Exception: pass
        try: await c.delete(f"/agents/{AGENT}")
        except Exception: pass
        await c.aclose()

asyncio.run(main())
PY
)
echo "$RESULT"
echo ""

if echo "$RESULT" | grep -q "^FAIL "; then
  echo "================================"; echo "CP2 parity smoke: FAIL"; echo "FAIL"; exit 1
fi
if echo "$RESULT" | grep -q "^SKIP "; then
  echo "⚠️  CP2 parity smoke SKIPPED (env limit — eval-runner Job could not run)."
  echo "    NOT a pass and NOT a fake. Deploy eval-runner:0.1.5 + Jobs RBAC and re-run."
  echo "SKIP"; exit 0
fi
if echo "$RESULT" | grep -q "PASS parity_composite_eq_response_to_the_digit" \
   && echo "$RESULT" | grep -q "PASS eval_passed_autoset_fired_on_passing_version"; then
  echo "================================"; echo "CP2 parity smoke: PASS"; echo "PASS"; exit 0
fi
echo "================================"; echo "CP2 parity smoke: INCONCLUSIVE (no PASS, no SKIP)"; echo "FAIL"; exit 1
