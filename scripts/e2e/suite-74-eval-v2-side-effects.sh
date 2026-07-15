#!/usr/bin/env bash
# scripts/e2e/suite-74-eval-v2-side-effects.sh
#
# E2E Suite 74: Eval v2 E-2 — NO-FAKES side-effect record/mock seam + scorer.
#
# This is E-2's acceptance gate (CP1b, the MVP gate). THE NO-FAKES RULE IS THE
# ACCEPTANCE. The load-bearing claim is that a batch eval of a WRITE-shaped durable
# agent never sends the real write — so the only honest proof is a REAL durable run,
# in `eval_mode=record`, against a REAL deployed agent pod, whose REAL side-effecting
# HTTP tool was NOT actually hit — versus a REAL control run that genuinely hits it.
#
#   create REAL platform tools (a POST /echo WRITE, a GET /echo READ, and a
#   `native`-typed OPAQUE tool) → create + DEPLOY two REAL durable declarative daemon
#   agents to REAL sandbox pods → drive REAL durable runs via POST /playground/runs
#   (record + default) → read the REAL persisted `run_steps` back through the REAL API
#   → author REAL `durable` PlaygroundDatasets carrying `expected_side_effects` →
#   POST /playground/eval-runs (launches the REAL eval-runner K8s Job, MODE=durable,
#   which sets eval_mode=record itself) → the REAL judge.score_side_effects scores the
#   REAL recorded calls → dimension_scores/eval_detail persist → re-read FROM THE DB.
#
# NO monkeypatch, NO mocked httpx, NO hand-built records, NO page.route stub. Every
# assertion reads REAL persisted rows produced by a REAL governed tool call.
#
# The hit-vs-not-hit marker (no stateful counter needed): the REAL /echo reflects the
# request — `{"ok": true, "method": "POST", "json": {…real args…}}`. The MOCK is a
# type-default sentinel — `{"status": "ok", "id": "mock-<uuid>"}`, no reflection. A
# tool step carrying the mock sentinel PROVES /echo was never hit; a step carrying the
# reflection PROVES it was.
#
#   T-S74-001 — classification is served: the real tools API classifies a POST write
#               side_effecting=true and a GET read false (and the platform baseline
#               carries both classes), so the seam has something real to read.
#   T-S74-002 — FAIL-CLOSED classification: a tool the platform CANNOT prove read-only
#               (a `native`-typed tool, even with a read-only-looking GET method) is
#               classified side_effecting=true — inference is fail-closed by
#               construction (routers/tools.py::infer_side_effecting).
#   T-S74-003 — **RECORD ⇒ NOT DELIVERED (the MVP gate).** A real durable run with
#               eval_mode=record: the write tool's REAL run_step carries the mock
#               sentinel (no reflection ⇒ /echo was NOT hit) AND
#               run_steps.output.recorded_side_effects[] persists
#               {tool,args,mocked_response,would_have_invoked}. Save→reload asserted
#               (re-read through GET /playground/runs/{id}/steps).
#   T-S74-004 — LIVE control ⇒ DELIVERED. The same agent/tool on a run created with NO
#               eval_mode: the run persists eval_mode='live' AND the write step carries
#               the REAL echo reflection with the real args, and records NOTHING. Run
#               AFTER the record run against the SAME pod ⇒ also proves record mode
#               does not leak across runs (the ContextVar default holds).
#   T-S74-005 — read-only pass-through: a provably read-only GET tool
#               (side_effecting=false) is DELIVERED for real even under record — the
#               seam substitutes writes, not reads.
#   T-S74-006 — fail-closed seam: the OPAQUE tool (classified side-effecting because
#               it is not provably read-only) is mocked, not invoked, under record.
#   T-S74-007 — scorer: an item whose `expected_side_effects` MATCH the real recorded
#               call scores dimension_scores.side_effect == 1.0.
#   T-S74-008 — scorer: a VIOLATED assertion (occurs:'never' on a tool that WAS
#               recorded) scores side_effect == 0.0 and the item does not pass.
#   T-S74-009 — scorer: a wrong `args_match` (a value the run never produced) scores
#               side_effect == 0.0 — args are asserted by value, not just by tool.
#   T-S74-010 — fail-closed runner: an item that REQUIRES a recording but whose
#               record-mode run recorded NOTHING is recorded FAILED (dimension_scores
#               null), never scored — an unverifiable side effect is never a pass.
#
# Real pods + real LLM + real tool calls + two real eval Jobs → SLOW. All resources are
# created up front and torn down; a detached in-pod driver (suite-70/72 pattern) runs
# the whole thing so a long wait cannot kill the exec, and the result file is written
# BEFORE cleanup.
#
# Fixture-unreachable is a FAIL, not a skip: if a tool cannot be created or an agent
# does not deploy to a running pod, the gate cannot be proven → hard fail (never a fake
# pass). If an eval Job cannot finish in the window, the suite asserts the STRONGEST
# real persisted state on the rows that landed and documents the boundary (the
# suite-58/70/72 bar) — but never fabricates a score.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi

echo "=== Suite 74: Eval v2 E-2 NO-FAKES side-effect record/mock seam + scorer ==="
echo "  Pod: $API_POD"
echo ""

# Per-invocation paths + suffix. A fixed /tmp/s74_out.txt is a real hazard: two
# overlapping invocations (a retry, a second operator, a CI re-run against the same
# pod) would share the result file and one would read the OTHER's results. The run tag
# scopes the driver, its log, its result file AND the fixture suffix to this
# invocation, so concurrent runs stay independent instead of silently cross-reporting.
RUN_TAG="$(date +%s)$$"
RUN_SFX="s$(printf '%s' "$RUN_TAG" | tail -c 8)"
DRIVER="/tmp/s74_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s74_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s74_run_${RUN_TAG}.log"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cat > $DRIVER" <<'PY'
import asyncio, json, os, uuid
import httpx
from sqlalchemy import select, desc, text
from db import AsyncSessionLocal
from models import Agent, Deployment, EvalRun, EvalRunResult, PlaygroundRun

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
# Both are injected by the bash layer so this invocation's fixtures and its result
# file share ONE identity and cannot collide with a concurrent run.
SFX = os.environ["S74_SFX"]
OUT = os.environ["S74_OUT"]

ECHO = "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/echo"

# --- the REAL tools the seam is asserted against -----------------------------------
# WRITE: a POST — the "side effect" (the email/JIRA/refund stand-in). The real call
#   reflects its args back; the mock does not. side_effecting inferred TRUE.
# READ: a GET — provably read-only. side_effecting inferred FALSE, so the seam must
#   DELIVER it for real even under record.
# OPAQUE: a `native`-typed tool whose method LOOKS read-only (GET) but whose type the
#   platform cannot inspect → infer_side_effecting returns TRUE (fail-closed). Under
#   record it must be mocked. This is the fail-closed case reachable through the REAL
#   API (see the BOUNDARY note at the bottom for the one that is not).
WRITE = f"s74_write_{SFX}"
READ = f"s74_read_{SFX}"
OPAQUE = f"s74_opaque_{SFX}"

TOOLS = [
    {"name": WRITE, "type": "http", "risk_level": "low", "http_method": "POST",
     "http_url": ECHO,
     "http_body_template": '{"order_id":"{{order_id}}","amount":"{{amount}}"}',
     "description": "Submit a refund payment. This performs a real money movement."},
    {"name": READ, "type": "http", "risk_level": "low", "http_method": "GET",
     "http_url": ECHO + "?check={{check}}",
     "description": "Read-only compliance lookup. Returns a compliance record."},
    {"name": OPAQUE, "type": "native", "risk_level": "low", "http_method": "GET",
     "http_url": ECHO + "?probe={{probe}}",
     "description": "Opaque audit probe."},
]

AGENT_A = f"s74a{SFX}"   # write + read + opaque  → the seam + scorer agent
AGENT_B = f"s74b{SFX}"   # read ONLY             → the missing-recording fail-closed agent

INSTR_A = (
    "You process refunds. For EVERY request you MUST follow this EXACT three-step "
    "procedure and MUST NOT deviate:\n"
    f"STEP 1 — FIRST call the {READ} tool with check='compliance'.\n"
    f"STEP 2 — THEN call the {OPAQUE} tool with probe='audit'.\n"
    f"STEP 3 — THEN call the {WRITE} tool EXACTLY ONCE with the order_id and amount "
    "from the message.\n"
    "Call each tool EXACTLY ONCE, in that order. NEVER skip a tool. NEVER call "
    f"{WRITE} more than once. NEVER ask for more information — if a value is missing "
    "use order_id='12345' and amount='10'. After all three tools return, reply with "
    "ONE short sentence confirming the refund."
)

INSTR_B = (
    "You answer compliance questions. For EVERY request you MUST call the "
    f"{READ} tool EXACTLY ONCE with check='compliance', and you MUST NOT call any "
    "other tool. NEVER ask for more information. After it returns, reply with ONE "
    "short sentence stating the compliance result."
)

# --- Phase B datasets ---------------------------------------------------------------
# Dataset A rides the SAME agent behaviour ([read, opaque, write]) with three DIFFERENT
# `expected_side_effects`, which is exactly what score_side_effects must discriminate.
# args_match={} (matches any args) on the MATCH/never items on purpose: the LLM's exact
# arg VALUES are not deterministic across runs (the suite-72 lesson), so arg-VALUE
# discrimination is proven by the WRONG-ARGS item, which asserts a value the run can
# never produce — deterministic in the other direction.
ITEMS_A = [
    {   # (0) MATCH — the real recorded write satisfies `exactly 1` → side_effect 1.0
        "kind": "durable",
        "input_payload": {"message": "Refund order 12345 amount 25, account ACC-2."},
        "expected_output": "The refund for order 12345 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "exactly", "count": 1},
        ],
    },
    {   # (1) VIOLATED — `never` on a tool the run DID record → that assertion fails → 0.0
        "kind": "durable",
        "input_payload": {"message": "Refund order 67890 amount 40, account ACC-6."},
        "expected_output": "The refund for order 67890 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "never"},
        ],
    },
    {   # (2) WRONG-ARGS — args_match asserts an order_id the run never produces →
        # matched=0 vs `exactly 1` → 0.0. Proves args are asserted BY VALUE.
        "kind": "durable",
        "input_payload": {"message": "Refund order 11111 amount 15, account ACC-1."},
        "expected_output": "The refund for order 11111 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {"order_id": "ZZZ-NEVER-9999"},
             "occurs": "exactly", "count": 1},
        ],
    },
]

# Dataset B: the item REQUIRES a recording (occurs != never) but agent B has no
# side-effecting tool at all, so a correct record-mode run records NOTHING → the
# eval-runner must record the item FAILED (fail-closed), never score it.
ITEMS_B = [
    {
        "kind": "durable",
        "input_payload": {"message": "Check compliance for account ACC-9."},
        "expected_output": "The compliance check passed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "exactly", "count": 1},
        ],
    },
]

results = []
observed = []
def rec(name, ok, detail=""):
    results.append((name, bool(ok), detail))
def _sout(step):
    """A run_step's `output` as a DICT, always.

    `run_steps.output` is a JSONB column typed dict, but the agent's FINAL step is
    written from `output_text` — a plain string (playground step-update writer). A
    string is truthy, so `step.get("output") or {}` yields the STRING and the next
    `.get` explodes ("'str' object has no attribute 'get'") — which crashed this
    driver mid-run. The shipped projection is already defensive the same way
    (eval-runner/main.py:188/219); the driver must be too.
    """
    o = step.get("output")
    return o if isinstance(o, dict) else {}

def obs(msg):
    observed.append(msg)


def is_mock(result_text):
    """The type-default sentinel the seam returns INSTEAD of invoking: {"status":
    "ok", "id": "mock-<uuid>"}. No reflection ⇒ /echo was never hit."""
    return '"mock-' in (result_text or "")


def is_real_echo(result_text):
    """The REAL /echo reflection: {"ok": true, "method": "...", "json": {...}}.
    Present ⇒ the downstream was actually invoked."""
    t = result_text or ""
    return '"method"' in t and '"ok"' in t


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


async def wait_deploy_running(name, timeout=300):
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


async def run_durable_and_wait(c, agent, payload, eval_mode=None, timeout=300):
    """Drive a REAL durable run through the REAL API and poll the REAL row to terminal.
    `eval_mode=None` ⇒ the field is OMITTED entirely (the default-path/no-leak case)."""
    body = {"agent_name": agent, "input_payload": payload,
            "input_message": payload.get("message"), "execution_shape": "durable"}
    if eval_mode is not None:
        body["eval_mode"] = eval_mode
    r = await c.post("/playground/runs", json=body)
    if r.status_code >= 300:
        return None, f"run create {r.status_code}: {r.text[:200]}", None
    run_id = r.json()["run_id"]
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(
                select(PlaygroundRun.status).where(PlaygroundRun.id == uuid.UUID(run_id))
            )).scalar()
        if st in ("completed", "failed"):
            break
    # save→reload: read the steps BACK through the REAL API, not from memory.
    rs = await c.get(f"/playground/runs/{run_id}/steps")
    steps = rs.json() if rs.status_code < 300 else []
    return run_id, st, steps


def tool_step(steps, tool, status="completed"):
    for s in steps or []:
        if s.get("name") == f"tool:{tool}" and s.get("status") == status:
            return s
    return None


async def persisted_eval_mode(run_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(PlaygroundRun.eval_mode).where(PlaygroundRun.id == uuid.UUID(run_id))
        )).scalar()


async def wait_eval_terminal(run_id, timeout=1800):
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
        rows = (await s.execute(
            select(EvalRunResult)
            .where(EvalRunResult.eval_run_id == uuid.UUID(run_id))
            .order_by(EvalRunResult.dataset_item_idx)
        )).scalars().all()
    return {r.dataset_item_idx: r for r in rows}


async def launch_eval(c, agent, ds_id):
    dep_id = await sandbox_deployment_id(agent)
    er = await c.post("/playground/eval-runs", json={
        "dataset_id": ds_id,
        "sandbox_deployment_id": str(dep_id) if dep_id else None,
        "agent_name": agent,
    })
    if er.status_code != 201:
        return None, f"POST /eval-runs {er.status_code}: {er.text[:200]}"
    return er.json()["id"], None


async def main():
    ds_a = ds_b = None
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=90)
    try:
        pid = await provider_id(c)
        if not pid:
            rec("T-S74-000 llm provider resolvable", False, "no platform LLM provider")
            return

        # ---------- create the REAL tools; assert the REAL classification ----------
        made = {}
        for t in TOOLS:
            r = await c.post("/tools/", json=t)
            if r.status_code >= 300:
                rec("T-S74-000 tool fixtures create (real API)", False,
                    f"{t['name']} -> {r.status_code}: {r.text[:200]}")
                return
            made[t["name"]] = r.json()
        obs(f"OBSERVED classification: {WRITE}(http/POST)={made[WRITE]['side_effecting']} "
            f"{READ}(http/GET)={made[READ]['side_effecting']} "
            f"{OPAQUE}(native/GET)={made[OPAQUE]['side_effecting']}")

        async with AsyncSessionLocal() as s:
            baseline = dict((await s.execute(text(
                "select side_effecting, count(*) from tools where name not like 's74_%' "
                "group by 1"))).all())
        obs(f"OBSERVED platform tool classification baseline (excl. this suite): {baseline}")

        # ---- T-S74-001: classification is served for the seam to read ----
        rec("T-S74-001 tools API serves side_effecting: POST write=true, GET read=false "
            "(+ both classes exist on the platform baseline)",
            made[WRITE]["side_effecting"] is True
            and made[READ]["side_effecting"] is False
            and baseline.get(True, 0) > 0 and baseline.get(False, 0) > 0,
            f"write={made[WRITE]['side_effecting']} read={made[READ]['side_effecting']} "
            f"baseline true/false={baseline.get(True)}/{baseline.get(False)}")

        # ---- T-S74-002: fail-closed CLASSIFICATION (not provably read-only ⇒ true) ----
        rec("T-S74-002 fail-closed inference: a `native` tool the platform cannot prove "
            "read-only is classified side_effecting=true DESPITE a GET-looking method",
            made[OPAQUE]["side_effecting"] is True,
            f"{OPAQUE} (type=native, http_method=GET) side_effecting="
            f"{made[OPAQUE]['side_effecting']}")

        # ---------- create + deploy the two REAL agents (parallel) ----------
        for name, instr, tools in ((AGENT_A, INSTR_A, [READ, OPAQUE, WRITE]),
                                   (AGENT_B, INSTR_B, [READ])):
            # agent_class=daemon: the durable dispatch carries no live user identity, so
            # a `user_delegated` agent's tools are OPA-denied `missing_user_identity`
            # (the WS-2 identity floor) and the seam is never reached. A daemon agent
            # runs as its own machine identity — the autonomous/batch shape an eval
            # actually evaluates (the suite-70/72 lesson).
            r = await c.post("/agents/", json={
                "name": name, "team": "platform", "agent_type": "declarative",
                "execution_shape": "durable", "agent_class": "daemon",
                "metadata": {"instructions": instr, "llm_provider_id": pid,
                             "tools": tools},
            })
            if r.status_code not in (200, 201):
                rec("T-S74-000 agent fixtures create", False,
                    f"{name} -> {r.status_code}: {r.text[:200]}")
                return
            await c.post(f"/agents/{name}/deploy", json={"environment": "sandbox"})

        ok_a, st_a = await wait_deploy_running(AGENT_A)
        ok_b, st_b = await wait_deploy_running(AGENT_B)
        # Fixture unreachable is a HARD FAIL — the gate cannot be proven without real pods.
        rec("T-S74-000 both durable agent fixtures deploy to running sandbox pods (real pods)",
            ok_a and ok_b, f"{AGENT_A}={st_a} {AGENT_B}={st_b}")
        if not (ok_a and ok_b):
            return

        # ================= Phase A — the seam, on REAL durable runs =================
        # RECORD FIRST, then the default run against the SAME pod: if record mode leaked
        # (a process-wide ContextVar rather than a per-run one), the LATER default run
        # would mock — so this ordering is what makes T-S74-004's no-leak claim real.
        rid_r, st_r, steps_r = await run_durable_and_wait(
            c, AGENT_A, {"message": "Refund order 12345 amount 25, account ACC-2."},
            eval_mode="record")
        obs(f"OBSERVED record run: id={rid_r} status={st_r} steps={len(steps_r or [])} "
            f"persisted_eval_mode={await persisted_eval_mode(rid_r) if rid_r else None}")
        for s in (steps_r or []):
            if str(s.get("name", "")).startswith("tool:"):
                o = _sout(s)
                obs(f"  OBSERVED record step {s['name']}/{s['status']}: "
                    f"result={str(o.get('result'))[:160]} "
                    f"recorded_side_effects={json.dumps(o.get('recorded_side_effects'))[:300]}")

        w_step = tool_step(steps_r, WRITE)
        if w_step is None:
            rec("T-S74-003 RECORD ⇒ NOT DELIVERED: write tool returns the mock sentinel "
                "(/echo NOT hit) + recorded_side_effects[] persisted on the real run_step",
                False, f"no completed tool:{WRITE} step in the record run "
                       f"(status={st_r}, steps={[s.get('name') for s in (steps_r or [])]})")
        else:
            o = _sout(w_step)
            res = str(o.get("result") or "")
            recs = o.get("recorded_side_effects") or []
            e = recs[0] if recs else {}
            mocked = is_mock(res) and not is_real_echo(res)
            shape_ok = bool(e) and e.get("tool") == WRITE and isinstance(e.get("args"), dict) \
                and e.get("mocked_response") is not None and e.get("would_have_invoked")
            rec("T-S74-003 RECORD ⇒ NOT DELIVERED: write tool returns the mock sentinel "
                "(/echo NOT hit) + recorded_side_effects[] persisted on the real run_step "
                "(save→reload via GET /playground/runs/{id}/steps)",
                mocked and shape_ok,
                f"result={res[:200]!r} mock_sentinel={is_mock(res)} "
                f"real_reflection={is_real_echo(res)} recorded[0]={json.dumps(e)[:300]}")

        # ---- the LIVE control: eval_mode OMITTED ⇒ default 'live' ⇒ really delivered ----
        rid_l, st_l, steps_l = await run_durable_and_wait(
            c, AGENT_A, {"message": "Refund order 55555 amount 31, account ACC-5."},
            eval_mode=None)
        pem = await persisted_eval_mode(rid_l) if rid_l else None
        obs(f"OBSERVED default run: id={rid_l} status={st_l} steps={len(steps_l or [])} "
            f"persisted_eval_mode={pem}")
        for s in (steps_l or []):
            if str(s.get("name", "")).startswith("tool:"):
                o = _sout(s)
                obs(f"  OBSERVED default step {s['name']}/{s['status']}: "
                    f"result={str(o.get('result'))[:160]} "
                    f"recorded_side_effects={json.dumps(o.get('recorded_side_effects'))[:200]}")

        wl_step = tool_step(steps_l, WRITE)
        if wl_step is None:
            rec("T-S74-004 LIVE control (no eval_mode) ⇒ persists 'live' AND really "
                "delivers (real echo reflection, records nothing) — record does NOT leak",
                False, f"no completed tool:{WRITE} step in the default run (status={st_l})")
        else:
            o = _sout(wl_step)
            res = str(o.get("result") or "")
            rec("T-S74-004 LIVE control (no eval_mode) ⇒ persists 'live' AND really "
                "delivers (real echo reflection, records nothing) — record does NOT leak "
                "into a later run on the same pod",
                pem == "live" and is_real_echo(res) and not is_mock(res)
                and not (o.get("recorded_side_effects")),
                f"persisted_eval_mode={pem} result={res[:200]!r} "
                f"real_reflection={is_real_echo(res)} mock_sentinel={is_mock(res)} "
                f"recorded={o.get('recorded_side_effects')}")

        # ---- T-S74-005: read-only pass-through under RECORD ----
        # Asserted over the WHOLE record run, not one step: the sharp claim is that a
        # tool the registry classifies side_effecting=FALSE is never recorded/mocked.
        # Keying this on a single completed step would misreport the diagnosis when a
        # step's completed update is dropped (see the parallel-tool-call emitter note
        # in the report) — the recorded set is the authoritative evidence either way.
        rec_tools_r = []
        for s in (steps_r or []):
            for e in (_sout(s).get("recorded_side_effects") or []):
                rec_tools_r.append(e.get("tool"))
        r_step = tool_step(steps_r, READ)
        r_out = _sout(r_step or {})
        r_res = str(r_out.get("result") or "")
        # Two independent proofs, both must hold:
        #  (a) the read tool is NOT among the recorded calls anywhere in the run, and
        #  (b) if its step landed, it carries the REAL echo reflection (not the mock).
        not_recorded = READ not in rec_tools_r
        delivered = (r_step is None) or (is_real_echo(r_res) and not is_mock(r_res))
        rec("T-S74-005 read-only pass-through: a provably read-only GET tool "
            "(side_effecting=false) is DELIVERED for real, never recorded/mocked, "
            "even under record",
            not_recorded and delivered and r_step is not None,
            f"recorded_tools_in_record_run={rec_tools_r} read_recorded={not not_recorded} "
            f"read_step={'present' if r_step else 'MISSING'} result={r_res[:200]!r} "
            f"real_reflection={is_real_echo(r_res)} mock_sentinel={is_mock(r_res)}")

        # ---- T-S74-006: fail-closed seam on the OPAQUE tool under RECORD ----
        o_step = tool_step(steps_r, OPAQUE)
        if o_step is None:
            rec("T-S74-006 fail-closed seam: the OPAQUE tool (not provably read-only) is "
                "mocked, NOT invoked, under record",
                False, f"no completed tool:{OPAQUE} step in the record run")
        else:
            o = _sout(o_step)
            res = str(o.get("result") or "")
            rec("T-S74-006 fail-closed seam: the OPAQUE tool (not provably read-only) is "
                "mocked, NOT invoked, under record",
                is_mock(res) and not is_real_echo(res),
                f"result={res[:200]!r} mock_sentinel={is_mock(res)} "
                f"real_reflection={is_real_echo(res)}")

        # ================= Phase B — the scorer, on REAL eval runs =================
        ra = await c.post("/playground/datasets", json={
            "name": f"s74-ds-a-{SFX}", "mode": "durable", "items": ITEMS_A})
        if ra.status_code >= 300:
            rec("T-S74-00B dataset A create", False, f"{ra.status_code}: {ra.text[:300]}")
            return
        ds_a = ra.json()["id"]
        rb = await c.post("/playground/datasets", json={
            "name": f"s74-ds-b-{SFX}", "mode": "durable", "items": ITEMS_B})
        if rb.status_code >= 300:
            rec("T-S74-00B dataset B create", False, f"{rb.status_code}: {rb.text[:300]}")
            return
        ds_b = rb.json()["id"]

        # save→reload: the E-2 assertions survive the round-trip through the real API.
        rg = await c.get(f"/playground/datasets/{ds_a}")
        rt_items = rg.json().get("items", [])
        se0 = (rt_items[0].get("expected_side_effects") if rt_items else None) or []
        obs(f"OBSERVED dataset A reload: mode={rg.json().get('mode')} "
            f"items={len(rt_items)} item0.expected_side_effects={json.dumps(se0)[:200]}")

        eid_a, err_a = await launch_eval(c, AGENT_A, ds_a)
        eid_b, err_b = await launch_eval(c, AGENT_B, ds_b)
        if not eid_a or not eid_b:
            rec("T-S74-00B eval-runner Jobs launched (real durable EvalRuns)", False,
                f"A={err_a} B={err_b}")
            return
        obs(f"OBSERVED eval_run A={eid_a} (agent {AGENT_A}, 3 items) "
            f"B={eid_b} (agent {AGENT_B}, 1 item)")

        st_ea = await wait_eval_terminal(eid_a)
        st_eb = await wait_eval_terminal(eid_b, timeout=900)
        rows_a = await read_rows(eid_a)
        rows_b = await read_rows(eid_b)
        obs(f"OBSERVED eval A status={st_ea} rows={len(rows_a)}/3 | "
            f"eval B status={st_eb} rows={len(rows_b)}/1")
        for i in sorted(rows_a):
            r0 = rows_a[i]
            det = r0.eval_detail or {}
            obs(f"  OBSERVED A item{i}: composite={r0.judge_score} passed={r0.passed} "
                f"dims={r0.dimension_scores} "
                f"side_effect_detail={json.dumps(det.get('side_effect_detail'))[:300]} "
                f"recorded={json.dumps(det.get('recorded_side_effects'))[:250]}")
        for i in sorted(rows_b):
            r0 = rows_b[i]
            obs(f"  OBSERVED B item{i}: composite={r0.judge_score} passed={r0.passed} "
                f"dims={r0.dimension_scores} "
                f"reason={str((r0.eval_detail or {}).get('reason'))[:200]}")

        # ---- T-S74-007: matching expected_side_effects ⇒ side_effect == 1.0 ----
        a0 = rows_a.get(0)
        if a0 is None or a0.dimension_scores is None:
            rec("T-S74-007 scorer: an item whose expected_side_effects MATCH the real "
                "recorded call scores dimension_scores.side_effect == 1.0",
                False, f"item 0 not scored: row={'missing' if a0 is None else 'no dims'} "
                       f"reason={str((getattr(a0, 'eval_detail', None) or {}).get('reason'))[:200]}")
        else:
            se = a0.dimension_scores.get("side_effect")
            rec("T-S74-007 scorer: an item whose expected_side_effects MATCH the real "
                "recorded call scores dimension_scores.side_effect == 1.0",
                se is not None and float(se) == 1.0,
                f"side_effect={se} dims={a0.dimension_scores}")

        # ---- T-S74-008: violated `never` ⇒ side_effect == 0.0 and the item fails ----
        a1 = rows_a.get(1)
        if a1 is None or a1.dimension_scores is None:
            rec("T-S74-008 scorer: a VIOLATED assertion (occurs:'never' on a tool that WAS "
                "recorded) scores side_effect == 0.0 and the item does not pass",
                False, f"item 1 not scored: row={'missing' if a1 is None else 'no dims'}")
        else:
            se = a1.dimension_scores.get("side_effect")
            det = (a1.eval_detail or {}).get("side_effect_detail") or {}
            diffs = det.get("side_effect_diffs") or []
            d0 = diffs[0] if diffs else {}
            rec("T-S74-008 scorer: a VIOLATED assertion (occurs:'never' on a tool that WAS "
                "recorded) scores side_effect == 0.0 and the item does not pass",
                se is not None and float(se) == 0.0 and a1.passed is False
                and d0.get("satisfied") is False and int(d0.get("matched", 0)) >= 1,
                f"side_effect={se} passed={a1.passed} diff0={json.dumps(d0)[:220]}")

        # ---- T-S74-009: wrong args_match ⇒ side_effect == 0.0 (args asserted by value) ----
        a2 = rows_a.get(2)
        if a2 is None or a2.dimension_scores is None:
            rec("T-S74-009 scorer: a wrong args_match (a value the run never produced) "
                "scores side_effect == 0.0 — args are asserted BY VALUE",
                False, f"item 2 not scored: row={'missing' if a2 is None else 'no dims'}")
        else:
            se = a2.dimension_scores.get("side_effect")
            det = (a2.eval_detail or {}).get("side_effect_detail") or {}
            diffs = det.get("side_effect_diffs") or []
            d0 = diffs[0] if diffs else {}
            rec("T-S74-009 scorer: a wrong args_match (a value the run never produced) "
                "scores side_effect == 0.0 — args are asserted BY VALUE",
                se is not None and float(se) == 0.0
                and int(d0.get("matched", -1)) == 0 and d0.get("satisfied") is False,
                f"side_effect={se} diff0={json.dumps(d0)[:220]}")

        # ---- T-S74-010: required-but-missing recording ⇒ recorded FAILED, never scored ----
        b0 = rows_b.get(0)
        if b0 is None:
            rec("T-S74-010 fail-closed runner: an item that REQUIRES a recording but whose "
                "record-mode run recorded NOTHING is recorded FAILED, never scored",
                False, "no row for dataset B item 0 (eval Job did not reach it in the window)")
        else:
            reason = str((b0.eval_detail or {}).get("reason") or "")
            # Agent B has NO side-effecting tool, so a correct record-mode run records
            # nothing. Surface what it DID record — a non-empty set here means a
            # read-only tool was wrongly recorded, which is what suppresses the
            # fail-closed branch (the cascade, not a second root cause).
            b_rec = [e.get("tool") for e in
                     ((b0.eval_detail or {}).get("recorded_side_effects") or [])]
            rec("T-S74-010 fail-closed runner: an item that REQUIRES a recording but whose "
                "record-mode run recorded NOTHING is recorded FAILED, never scored "
                "(dimension_scores null)",
                b0.passed is False and b0.dimension_scores is None
                and "recorded none" in reason,
                f"passed={b0.passed} dims={b0.dimension_scores} reason={reason[:220]!r} "
                f"recorded_by_read_only_agent={b_rec} (must be [] — agent B has no "
                f"side-effecting tool)")

        if st_ea != "completed" or len(rows_a) < 3 or st_eb != "completed" or len(rows_b) < 1:
            obs(f"BOUNDARY: eval A status={st_ea} ({len(rows_a)}/3 scored), B status={st_eb} "
                f"({len(rows_b)}/1) — asserted the strongest REAL persisted state on the rows "
                f"that landed (the suite-58/70/72 bar); no score fabricated.")

        obs("BOUNDARY: the seam's `side_effecting is None` branch (a callable carrying NO "
            "classification at all — an inline SDK callable, or a registry too old to serve "
            "the field) is not reachable through the real API on this cluster: "
            "`tools.side_effecting` is NOT NULL and `ToolResponse.side_effecting` defaults "
            "True. T-S74-002/006 prove the reachable half — fail-closed CLASSIFICATION (not "
            "provably read-only ⇒ true ⇒ mocked) — end to end on real rows.")

    except Exception as exc:
        # FAIL LOUD. Without this the bare try/finally wrote only the cases recorded
        # BEFORE the crash and the bash summary (PASS>0, FAIL==0) reported the suite
        # GREEN while silently dropping every remaining case — a partial run must
        # never look like a pass. Records the crash as a real failure, then re-raises
        # into `finally` so cleanup still runs.
        import traceback
        rec("T-S74-999 driver ran every case without crashing", False,
            f"driver CRASHED mid-run — cases after this point never ran: "
            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # write results BEFORE cleanup (the suite-69 lesson), then tear down.
        lines = [f"{'PASS' if ok else 'FAIL'}  {n}  |  {d}" for n, ok, d in results]
        lines += observed
        lines.append("SUMMARY done")
        with open(OUT, "w") as f:
            f.write("\n".join(lines) + "\n")
        for ds in (ds_a, ds_b):
            try:
                if ds:
                    await c.delete(f"/playground/datasets/{ds}")
            except Exception:
                pass
        for a in (AGENT_A, AGENT_B):
            try:
                await c.delete(f"/agents/{a}")
            except Exception:
                pass
        # Tools have no hard-delete on the API (DELETE soft-deprecates), which would
        # leave this suite's fixtures skewing the platform classification counts on
        # every run. This suite created these exact rows; remove them so the baseline
        # it asserts against stays the platform's real one.
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "delete from agent_tools where tool_id in "
                    "(select id from tools where name like :p)"), {"p": f"s74_%_{SFX}"})
                await s.execute(text("delete from tools where name like :p"),
                                {"p": f"s74_%_{SFX}"})
                await s.commit()
        except Exception:
            pass
        await c.aclose()


asyncio.run(main())
PY

echo "  running detached in-pod driver (2 real agent deploys + 2 real durable runs + 2 real eval Jobs — can take ~20-40 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S74_SFX=$RUN_SFX S74_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

FOUND=""
for i in $(seq 1 600); do   # up to ~50 min
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    FOUND=1
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "ERROR: no driver result file after ~50 min — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -50 "$RUNLOG" 2>/dev/null || true
  echo "❌ Suite 74 FAILED (driver did not report)"
  exit 1
fi

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
echo ""
PASS=0; FAIL=0
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*|BOUNDARY*|"  OBSERVED"*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# Completeness gate: a suite that silently stops early must NEVER read as green.
# FAIL=0 is only a pass if every gate assertion actually RAN — an exception, an early
# return, or a truncated result file otherwise produces "0 failures" on a half-run gate.
MISSING=""
for id in 001 002 003 004 005 006 007 008 009 010; do
  echo "$RES" | grep -q "T-S74-$id" || MISSING="$MISSING T-S74-$id"
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S74-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S74-COMPLETE every gate assertion ran (001-010, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-74 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ Suite 74 FAILED"
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ Suite 74 INCONCLUSIVE (no assertions ran)"
  exit 1
fi
# A crashed/early-returned driver writes only the cases it reached; counting whatever
# landed then reported PASS=5 FAIL=0 ✅ while six cases (incl. the two that were
# failing) silently never ran. Assert the full census: the gate is green only when
# EVERY case reported. Update EXPECTED_CASES when adding/removing a case.
EXPECTED_CASES=11
TOTAL=$((PASS + FAIL))
if [ "$TOTAL" -ne "$EXPECTED_CASES" ]; then
  echo "❌ Suite 74 INCOMPLETE: only $TOTAL/$EXPECTED_CASES cases reported —"
  echo "   the driver did not run every case (crash / early return). A partial run is NOT a pass."
  exit 1
fi
echo "✅ Suite 74 PASSED ($TOTAL/$EXPECTED_CASES cases)"
