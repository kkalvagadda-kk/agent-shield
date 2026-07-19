#!/usr/bin/env bash
# scripts/e2e/suite-75-eval-v2-scheduled.sh
#
# E2E Suite 75: Eval v2 E-3 — NO-FAKES scheduled eval (job_spec datasets +
# side-effect assertions). This is E-3's acceptance gate ([CP1c], the MVP gate).
#
# THE NO-FAKES RULE IS THE ACCEPTANCE. E-3's load-bearing claim is that a scheduled
# agent — whose whole point is the effect it fires unattended on a job spec — can be
# evaluated on THAT effect without ever delivering it. The only honest proof is a
# REAL scheduled dataset, a REAL armed schedule trigger, a REAL eval-runner Job, a
# REAL durable run of a REAL deployed daemon pod driven BY THE JOB SPEC, a REAL
# governed tool call that was recorded instead of sent, and REAL dimension_scores
# re-read FROM THE DB.
#
#   create a REAL side-effecting HTTP tool (POST /echo) → create + DEPLOY two REAL
#   declarative daemon agents to REAL sandbox pods (one durable-inner, one
#   reactive-inner) → publish + DEPLOY the durable one to a REAL production pod →
#   arm REAL schedule triggers → author REAL `scheduled` PlaygroundDatasets carrying
#   `job_spec` + `expected_side_effects` → POST /playground/eval-runs (launches the
#   REAL eval-runner K8s Job, MODE=scheduled) → the runner fires each job spec
#   through the SHARED durable dispatch under E-2's record seam → the REAL judge
#   scores the REAL recorded calls → dimension_scores/eval_detail/trigger_payload
#   persist → re-read FROM THE DB. Plus a REAL non-eval scheduled run through the
#   REAL /internal/runs/start door that still DELIVERS live.
#
# NO monkeypatch, NO mocked httpx, NO hand-built result rows, NO page.route stub.
# Every assertion reads REAL persisted rows produced by a REAL governed tool call.
#
# The delivered-vs-recorded marker (no stateful counter needed): the REAL /echo
# reflects the request — `{"ok": true, "method": "POST", "json": {…real args…}}`. The
# MOCK is a type-default sentinel — `{"status": "ok", "id": "mock-<uuid>"}`, no
# reflection. A tool step carrying the mock sentinel PROVES /echo was never hit; a
# step carrying the reflection PROVES it was.
#
#   T-S75-000 — PARITY grep (repo source): E-3 adds NO scheduled-only fork. No new
#               scorer (`def score_` exists only in judge.py's shipped set) and no
#               `trigger_type == 'schedule'` dispatch fork in internal.py /
#               durable_dispatch.py / workflow_orchestrator.py; `mode == "scheduled"`
#               appears in the eval surfaces only (score door + launch guard +
#               runner branch), never in a dispatch file.
#   T-S75-001 — a `scheduled` dataset with a real job_spec + expected_side_effects is
#               authored via the REAL API (201) and save→reload returns job_spec +
#               expected_side_effects intact; a malformed item (bad `occurs`, and a
#               trajectory step missing `tool`) → 422 at the door.
#   T-S75-002 — LAUNCH GUARD: the same dataset against the same agent BEFORE its
#               schedule trigger is armed → 422 naming the trigger; AFTER arming →
#               201 with EvalRun.mode == 'scheduled' and a real Job. (This 422'd
#               universally before E-3 — the execution_shape-only read could never
#               yield 'scheduled'.)
#   T-S75-003 — **THE MVP GATE: RECORD ⇒ NOT DELIVERED.** The REAL eval-runner Job
#               fires a REAL scheduled run of a REAL deployed daemon pod; the write
#               tool's REAL run_step carries the mock sentinel (no /echo reflection)
#               AND recorded_side_effects[] persisted. Re-read through the REAL API.
#   T-S75-004 — THE JOB SPEC IS THE INPUT: the eval's real PlaygroundRun has
#               trigger_type='schedule', trigger_payload == job_spec,
#               input_payload == job_spec, eval_mode='record' — re-read FROM THE DB.
#   T-S75-005 — scorer: a satisfied assertion ⇒ dimension_scores.side_effect == 1.0,
#               composite ≥ threshold + passed, and eval_run_results.trigger_payload
#               == job_spec — read back FROM THE DB (closes the E-0 column's writer).
#   T-S75-006 — scorer: a VIOLATED `occurs:'never'` ⇒ side_effect == 0.0 and the item
#               does NOT pass.
#   T-S75-007 — durable-inner trajectory + WEIGHT SKEW: an item carrying an
#               expected_trajectory also scores trajectory/tool_call (E-1 reused on a
#               scheduled item), and the composite matches the side-effect-skewed
#               weights {response .3, trajectory .2, tool_call .1, side_effect .4} —
#               not the durable set, not the equal-weight mean.
#   T-S75-008 — FAIL-CLOSED, BEFORE ANYTHING FIRES: a REAL deployed reactive-inner
#               agent with a REAL armed schedule trigger, whose item asserts side
#               effects, is recorded FAILED and **no run is ever created** (asserted:
#               zero playground_runs rows for that agent) — the record seam does not
#               ride /chat, so firing would DELIVER the real side effect.
#   T-S75-009 — **LIVE CONTROL — no fake-schedule gate.** A NON-eval scheduled run
#               fired through the REAL POST /internal/runs/start door (real armed
#               trigger, real PRODUCTION pod — the suite-71 fixture pattern) still
#               DELIVERS for real: the write step carries the REAL /echo reflection
#               and nothing is recorded. Proves the record seam is armed ONLY by the
#               eval and E-3 left the real scheduled door untouched. Also asserts the
#               eval's job_spec is the SAME shape as that trigger's real input_payload.
#
# Real pods + real LLM + real tool calls + real eval Jobs → SLOW. All resources are
# created up front and torn down; a detached in-pod driver (suite-70/72/74 pattern)
# runs the whole thing so a long wait cannot kill the exec, and the result file is
# written BEFORE cleanup.
#
# Fixture-unreachable is a FAIL, not a skip: if a tool cannot be created or an agent
# does not deploy to a running pod, the gate cannot be proven → hard fail (never a
# fake pass). If an eval Job cannot finish in the window, the suite asserts the
# STRONGEST real persisted state on the rows that landed and documents the boundary
# (the suite-58/70/72/74 bar) — but never fabricates a score.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "=== Suite 75: Eval v2 E-3 NO-FAKES scheduled eval (job_spec + side effects) ==="
echo ""

# ---------------------------------------------------------------------------
# T-S75-000 — PARITY grep over the REPO SOURCE (no cluster needed).
#
# E-3's alignment claim is that it adds NO scheduled-only code: the job spec rides
# the SAME dispatch as every other run, under E-2's record seam, scored by E-0/E-1/
# E-2's scorers. Three greps make that claim falsifiable rather than aspirational:
#   (a) no new scorer — `def score_` exists only in judge.py's shipped set;
#   (b) no scheduled-only DISPATCH fork — `trigger_type == 'schedule'` never appears
#       in the core dispatch files (suite-71 T-S71-000's bar, re-asserted here
#       because E-3 is the slice that would be tempted to add one);
#   (c) `mode == "scheduled"` lives ONLY in eval surfaces (the score-door
#       discriminator, the launch guard, the runner branch) — never in a dispatch file.
# ---------------------------------------------------------------------------
P_PASS=0; P_FAIL=0
pok()  { echo "PASS  $1  |  $2"; P_PASS=$((P_PASS+1)); }
pbad() { echo "FAIL  $1  |  $2"; P_FAIL=$((P_FAIL+1)); }

echo "--- T-S75-000: parity grep (no scheduled-only fork, no new scorer) ---"

# (a) no new scorer: every `def score_` must be judge.py's shipped set.
SCORER_FILES=$(cd "$REPO_ROOT" && grep -rl "def score_" services/ 2>/dev/null | grep -v "/tests/" | sort || true)
SCORERS=$(cd "$REPO_ROOT" && grep -rhoE "def score_[a-z_]+" services/registry-api/judge.py 2>/dev/null | sort | tr '\n' ' ')
# (b) no scheduled-only dispatch fork in the core dispatch path.
DISPATCH_FILES="services/registry-api/routers/internal.py services/registry-api/durable_dispatch.py services/registry-api/workflow_orchestrator.py"
FORK=$(cd "$REPO_ROOT" && grep -nE "trigger_type[[:space:]]*==[[:space:]]*[\"']schedule[\"']" $DISPATCH_FILES 2>/dev/null || true)
# (c) mode == "scheduled" only on eval surfaces.
MODE_HITS=$(cd "$REPO_ROOT" && grep -rlE "(mode|MODE)[[:space:]]*==[[:space:]]*\"scheduled\"" services/ 2>/dev/null | grep -v "/tests/" | sort | tr '\n' ' ')
MODE_DISPATCH=$(cd "$REPO_ROOT" && grep -nE "(mode|MODE)[[:space:]]*==[[:space:]]*\"scheduled\"" $DISPATCH_FILES 2>/dev/null || true)

if [ "$SCORER_FILES" = "services/registry-api/judge.py" ] && [ -z "$FORK" ] && [ -z "$MODE_DISPATCH" ]; then
  pok "T-S75-000 parity: no new scorer + no scheduled-only dispatch fork (E-3 reuses the shared path)" \
      "def score_ only in judge.py [$SCORERS]; 0 'trigger_type==schedule' and 0 'mode==scheduled' in {internal,durable_dispatch,workflow_orchestrator}; mode==scheduled files: $MODE_HITS"
else
  pbad "T-S75-000 parity: no new scorer + no scheduled-only dispatch fork" \
       "scorer_files=[$SCORER_FILES] (must be exactly services/registry-api/judge.py); dispatch trigger_type fork=[$FORK]; dispatch mode fork=[$MODE_DISPATCH]"
fi
echo ""

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "ERROR: No registry-api pod found in namespace $NAMESPACE"
  exit 1
fi
echo "  Pod: $API_POD"
echo ""

# Per-invocation paths + suffix (the suite-74 lesson): a fixed /tmp/s75_out.txt is a
# real hazard — two overlapping invocations (a retry, a second operator, a CI re-run
# against the same pod) would share the result file and one would read the OTHER's
# results. The run tag scopes the driver, its log, its result file AND the fixture
# suffix to this invocation, so concurrent runs stay independent.
RUN_TAG="$(date +%s)$$"
RUN_SFX="s$(printf '%s' "$RUN_TAG" | tail -c 8)"
DRIVER="/tmp/s75_driver_${RUN_TAG}.py"
OUTFILE="/tmp/s75_out_${RUN_TAG}.txt"
RUNLOG="/tmp/s75_run_${RUN_TAG}.log"

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  bash -c "cat > $DRIVER" <<'PY'
import asyncio, json, os, uuid
import httpx
from sqlalchemy import select, desc, func, text
from db import AsyncSessionLocal
from models import (Agent, AgentRun, AgentTrigger, AgentVersion, Deployment,
                    EvalRun, EvalRunResult, PlaygroundRun, RunStep)

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
H = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
# Both are injected by the bash layer so this invocation's fixtures and its result
# file share ONE identity and cannot collide with a concurrent run.
SFX = os.environ["S75_SFX"]
OUT = os.environ["S75_OUT"]

ECHO = "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/echo"

# --- the REAL side-effecting tool the whole gate turns on ---------------------------
# A POST — the nightly email / ticket / payment stand-in. The real call reflects its
# args back; the mock does not. HTTP type on purpose: python-type tools crash the
# agent pod (docs/bugs/python-tool-graph-build-kwargs.md).
WRITE = f"s75_write_{SFX}"

# agent_class=daemon on BOTH: a scheduled run carries no live user identity, so a
# `user_delegated` agent's tools are OPA-denied `missing_user_identity` (the WS-2
# identity floor) and the seam is never reached. A daemon agent runs as its own
# machine identity — which is exactly the shape a scheduled agent has in production.
AGENT_D = f"s75d{SFX}"   # durable-inner  → the eval + the live control
AGENT_R = f"s75r{SFX}"   # reactive-inner → the fail-closed refusal

# The body-supplied `run_by` the real scheduled door must OVERRIDE with the agent's
# own service identity (WS-3; suite-71 T-S71-001a is the dedicated gate for it).
LIVE_SENTINEL = f"s75-scheduler-body-sentinel-{SFX}"

# The job specs. `message` is the driving turn both doors derive identically
# (internal.py / declarative-runner: `input_payload.get("message") or json.dumps(...)`),
# and the extra keys make these the shape of a REAL AgentTrigger.input_payload rather
# than a bare chat string. JOB_SPEC_0 is ALSO armed on the real schedule trigger, so
# T-S75-009 can assert the eval fed the same shape the real door pulls.
JOB_SPEC_0 = {"message": "Refund order 12345 amount 25.",
              "report": "nightly-refunds", "run_window": "0 0 * * *"}
JOB_SPEC_1 = {"message": "Refund order 67890 amount 40.",
              "report": "nightly-refunds", "run_window": "0 0 * * *"}
JOB_SPEC_2 = {"message": "Refund order 24680 amount 15.",
              "report": "nightly-refunds", "run_window": "0 0 * * *"}
JOB_SPEC_R = {"message": "Refund order 13579 amount 5.",
              "report": "nightly-refunds", "run_window": "0 0 * * *"}

INSTR = (
    "You process scheduled refund jobs. For EVERY request you MUST call the "
    f"{WRITE} tool EXACTLY ONCE with the order_id and amount from the message. "
    "NEVER call it more than once. NEVER ask for more information — if a value is "
    "missing use order_id='12345' and amount='10'. After the tool returns, reply "
    "with ONE short sentence confirming the refund."
)

# --- the datasets --------------------------------------------------------------------
# Dataset D rides the SAME agent behaviour (one recorded write) with three DIFFERENT
# assertions, which is exactly what the scheduled score door must discriminate.
# args_match={} (matches any args) on purpose: the LLM's exact arg VALUES are not
# deterministic across runs (the suite-72 lesson). Arg-VALUE discrimination is
# suite-74's T-S74-009; this suite's job is the SCHEDULED plumbing.
ITEMS_D = [
    {   # (0) MATCH — the real recorded write satisfies `exactly 1` → side_effect 1.0.
        # Also the row T-S75-003/004 read: record⇒not-delivered + job-spec-is-input.
        "kind": "scheduled",
        "job_spec": JOB_SPEC_0,
        "expected_output": "The refund for order 12345 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "exactly", "count": 1},
        ],
    },
    {   # (1) VIOLATED — `never` on a tool the run DID record → 0.0, item fails.
        "kind": "scheduled",
        "job_spec": JOB_SPEC_1,
        "expected_output": "The refund for order 67890 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "never"},
        ],
    },
    {   # (2) TRAJECTORY — durable-inner: an expected_trajectory makes E-1's
        # trajectory/tool_call dimensions present on a SCHEDULED item, and gives
        # T-S75-007 a 4-dimension composite to check the skewed weights against.
        "kind": "scheduled",
        "job_spec": JOB_SPEC_2,
        "expected_output": "The refund for order 24680 was processed.",
        "expected_trajectory": {"match_mode": "superset",
                                "steps": [{"tool": WRITE}]},
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "exactly", "count": 1},
        ],
    },
]

# Dataset R: a REAL deployed reactive-inner agent with a REAL armed schedule trigger
# whose item asserts side effects. E-2's record seam is armed only on the durable
# /run dispatch — the reactive /chat path threads no eval_mode — so firing this would
# DELIVER the real write. The runner must refuse BEFORE creating the run.
ITEMS_R = [
    {
        "kind": "scheduled",
        "job_spec": JOB_SPEC_R,
        "expected_output": "The refund for order 13579 was processed.",
        "expected_side_effects": [
            {"tool": WRITE, "args_match": {}, "occurs": "exactly", "count": 1},
        ],
    },
]

# A malformed item, for the 422-at-the-door half of T-S75-001. Two independent
# violations, both structural: `occurs` is not a legal literal AND the trajectory
# step is missing its required `tool`. The discriminated union must reject it rather
# than let it be key-sniffed at score time.
BAD_ITEM = {
    "kind": "scheduled",
    "job_spec": {"message": "x"},
    "expected_trajectory": {"match_mode": "superset", "steps": [{"args_match": {}}]},
    "expected_side_effects": [{"tool": WRITE, "occurs": "sometimes"}],
}

# The side-effect-skewed default weights E-3 owns (e3/data-model.md §3, durable-inner:
# the trajectory FAMILY's .3 split .2/.1 across E-1's two dimensions), vs the two sets
# it must NOT be — the durable branch's own defaults, and the equal-weight fallback.
SKEW_W = {"response": 0.3, "trajectory": 0.2, "tool_call": 0.1, "side_effect": 0.4}
DUR_W = {"response": 0.4, "trajectory": 0.4, "tool_call": 0.2, "side_effect": 0.2}
THRESHOLD = 0.7

results = []
observed = []


def rec(name, ok, detail=""):
    results.append((name, bool(ok), detail))


def obs(msg):
    observed.append(msg)


def _sout(step):
    """A run_step's `output` as a DICT, always.

    `run_steps.output` is a JSONB column typed dict, but the agent's FINAL step is
    written from `output_text` — a plain string (the playground step-update writer).
    A string is truthy, so `step.get("output") or {}` yields the STRING and the next
    `.get` explodes ("'str' object has no attribute 'get'") — which crashed suite-74's
    driver mid-run. The shipped projection is already defensive the same way
    (eval-runner/main.py); the driver must be too."""
    o = step.get("output")
    return o if isinstance(o, dict) else {}


def _sout_row(row):
    """Same, for a RunStep ORM row (the /internal door's steps are read from the DB —
    there is no playground steps API for a production AgentRun)."""
    o = getattr(row, "output", None)
    return o if isinstance(o, dict) else {}


def is_mock(result_text):
    """The type-default sentinel the seam returns INSTEAD of invoking:
    {"status": "ok", "id": "mock-<uuid>"}. No reflection ⇒ /echo was never hit."""
    return '"mock-' in (result_text or "")


def is_real_echo(result_text):
    """The REAL /echo reflection: {"ok": true, "method": "...", "json": {...}}.
    Present ⇒ the downstream was actually invoked."""
    t = result_text or ""
    return '"method"' in t and '"ok"' in t


def wmean(dims, weights):
    """judge.score_composite's math, restated here so the suite can assert WHICH
    weight set the door actually used from the persisted composite alone."""
    tot = 0.0
    acc = 0.0
    for k, v in (dims or {}).items():
        w = weights.get(k)
        if w is None:
            continue
        acc += float(v) * float(w)
        tot += float(w)
    if tot > 0:
        return acc / tot
    return sum(float(v) for v in dims.values()) / len(dims) if dims else 0.0


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


async def wait_deploy_running(name, env="sandbox", timeout=300):
    st = None
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(
                select(Deployment.status)
                .join(Agent, Deployment.agent_id == Agent.id)
                .where(Agent.name == name, Deployment.environment == env)
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


async def latest_version_id(name):
    """The newest version's id AS A STRING.

    `str(...)` is load-bearing, not cosmetic: SQLAlchemy returns a `uuid.UUID`, and
    this value is sent as a JSON BODY field (`{"version_id": ...}`) to the deploy
    door. httpx serializes `json=` with the stdlib encoder, which raises
    `TypeError: Object of type UUID is not JSON serializable` — it crashed this
    driver's live-control block on the first run. (suite-71's `latest_version_id`
    returns `str(v.id)` for exactly this reason.) A UUID in an f-string URL is fine;
    a UUID in a JSON body is not."""
    async with AsyncSessionLocal() as s:
        vid = (await s.execute(
            select(AgentVersion.id).join(Agent, AgentVersion.agent_id == Agent.id)
            .where(Agent.name == name)
            .order_by(desc(AgentVersion.version_number)).limit(1)
        )).scalar()
    return str(vid) if vid else None


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


async def playground_run(run_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(PlaygroundRun).where(PlaygroundRun.id == uuid.UUID(run_id))
        )).scalars().first()


async def playground_run_count(agent_name):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(func.count(PlaygroundRun.id))
            .where(PlaygroundRun.agent_name == agent_name)
        )).scalar() or 0


async def create_daemon_agent(c, name, shape, pid):
    r = await c.post("/agents/", json={
        "name": name, "team": "platform", "agent_type": "declarative",
        "execution_shape": shape, "agent_class": "daemon",
        "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": [WRITE]},
    })
    return r


async def arm_schedule_trigger(c, agent, job_spec):
    r = await c.post(f"/agents/{agent}/triggers", json={
        "trigger_type": "schedule", "cron_expression": "0 0 * * *",
        "input_payload": job_spec, "enabled": True})
    return (r.json()["id"], None) if r.status_code in (200, 201) \
        else (None, f"{r.status_code}: {r.text[:200]}")


async def launch_eval(c, agent, ds_id):
    dep_id = await sandbox_deployment_id(agent)
    er = await c.post("/playground/eval-runs", json={
        "dataset_id": ds_id,
        "sandbox_deployment_id": str(dep_id) if dep_id else None,
        "agent_name": agent,
    })
    return er


async def run_live_control(c, tid_d):
    """T-S75-009 — the LIVE CONTROL, through the REAL scheduled door.

    `/internal/runs/start` is the door the scheduler hits on a real cron tick. It
    dispatches ONLY to a `{agent}-production` pod, so the agent must be published +
    deployed to production first (the suite-71 fixture pattern: PATCH the version's
    eval gates, then deploy). E-3 deliberately does NOT fire evals through this door
    (e3/tasks.md §D1) — so this control is what keeps the claim honest: the real
    scheduled door must STILL deliver for real, proving the record seam is armed only
    by the eval and E-3 left this path untouched.

    Returns (ok, detail). Never raises for an expected fixture failure — an
    unreachable production pod is reported as a FAILED control with the reason."""
    vid = await latest_version_id(AGENT_D)
    if not vid:
        return False, "production fixture unreachable: no version id for the agent"

    await c.patch(f"/agents/{AGENT_D}/versions/{vid}",
                  json={"eval_passed": True, "adversarial_eval_passed": True})
    pd = await c.post(f"/agents/{AGENT_D}/deploy",
                      json={"environment": "production", "version_id": vid})
    if pd.status_code not in (200, 201):
        return False, (f"production fixture unreachable: deploy {pd.status_code}: "
                       f"{pd.text[:200]}")
    prod_ready, prod_st = await wait_deploy_running(AGENT_D, env="production", timeout=300)
    if not prod_ready:
        return False, f"production fixture unreachable: deployment status={prod_st}"

    # `run_by` is REQUIRED by the door's schema (422 without it — the scheduler always
    # sends one). It is a sentinel on purpose: WS-3 OVERRIDES the body-supplied value
    # with the agent's own service identity (suite-71 T-S71-001a asserts exactly that),
    # so seeing it survive here would be a real identity regression.
    ir = await c.post("/internal/runs/start", json={
        "agent_name": AGENT_D, "trigger_type": "schedule", "trigger_id": tid_d,
        "run_by": LIVE_SENTINEL})
    if ir.status_code not in (200, 201):
        return False, f"/internal/runs/start -> {ir.status_code}: {ir.text[:200]}"

    lrid = uuid.UUID(ir.json()["id"])
    lsteps = []
    lrun = None
    for _ in range(60):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            lsteps = (await s.execute(
                select(RunStep).where(RunStep.run_id == lrid)
                .order_by(RunStep.step_number))).scalars().all()
            lrun = (await s.execute(
                select(AgentRun).where(AgentRun.id == lrid))).scalars().first()
        if lrun and lrun.status in ("completed", "failed"):
            break

    # The trigger's REAL input_payload — what the real door pulls and feeds as the
    # run's input. The eval fed the SAME shape (JOB_SPEC_0), which is the parity half
    # of this control.
    async with AsyncSessionLocal() as s:
        trig_payload = (await s.execute(
            select(AgentTrigger.input_payload)
            .where(AgentTrigger.id == uuid.UUID(tid_d)))).scalar()

    obs(f"OBSERVED live control: run={lrid} status={getattr(lrun,'status',None)} "
        f"trigger_type={getattr(lrun,'trigger_type',None)} "
        f"run_by={getattr(lrun,'run_by',None)} (body sentinel was {LIVE_SENTINEL!r} — "
        f"WS-3 overrides it with the agent's service identity) "
        f"trigger.input_payload={json.dumps(trig_payload)} "
        f"steps={[(s.step_number, s.name, s.status) for s in lsteps]}")

    ws = next((s for s in lsteps
               if s.name == f"tool:{WRITE}" and s.status == "completed"), None)
    if ws is None:
        return False, (f"no completed tool:{WRITE} run_step on the live scheduled run "
                       f"(status={getattr(lrun,'status',None)}, "
                       f"steps={[(s.name, s.status) for s in lsteps]})")

    o = _sout_row(ws)
    res = str(o.get("result") or "")
    recorded = o.get("recorded_side_effects") or []
    run_by = getattr(lrun, "run_by", None)
    ok = (is_real_echo(res) and not is_mock(res) and not recorded
          and getattr(lrun, "trigger_type", None) == "schedule"
          and trig_payload == JOB_SPEC_0
          and run_by != LIVE_SENTINEL)
    return ok, (f"trigger_type={getattr(lrun,'trigger_type',None)} "
                f"result={res[:200]!r} real_reflection={is_real_echo(res)} "
                f"mock_sentinel={is_mock(res)} recorded={recorded} "
                f"run_by={run_by!r} (body sentinel overridden: {run_by != LIVE_SENTINEL}) "
                f"trigger.input_payload==eval job_spec: {trig_payload == JOB_SPEC_0}")


async def tool_step_via_api(c, run_id, tool, status="completed"):
    """save→reload: read the run's steps BACK through the REAL API, not from memory."""
    rs = await c.get(f"/playground/runs/{run_id}/steps")
    steps = rs.json() if rs.status_code < 300 else []
    for s in steps or []:
        if s.get("name") == f"tool:{tool}" and s.get("status") == status:
            return s, steps
    return None, steps


async def main():
    ds_d = ds_r = None
    c = httpx.AsyncClient(base_url=BASE, headers=H, timeout=90)
    try:
        pid = await provider_id(c)
        if not pid:
            rec("T-S75-00F llm provider resolvable", False, "no platform LLM provider")
            return

        # ---------- the REAL side-effecting tool ----------
        tr = await c.post("/tools/", json={
            "name": WRITE, "type": "http", "risk_level": "low", "http_method": "POST",
            "http_url": ECHO,
            "http_body_template": '{"order_id":"{{order_id}}","amount":"{{amount}}"}',
            "description": "Submit a refund payment. This performs a real money movement.",
        })
        if tr.status_code >= 300:
            rec("T-S75-00F write tool fixture create (real API)", False,
                f"{tr.status_code}: {tr.text[:200]}")
            return
        obs(f"OBSERVED write tool {WRITE} (http/POST) side_effecting="
            f"{tr.json().get('side_effecting')}")

        # ---------- the two REAL daemon agents, deployed to REAL sandbox pods ----------
        for name, shape in ((AGENT_D, "durable"), (AGENT_R, "reactive")):
            r = await create_daemon_agent(c, name, shape, pid)
            if r.status_code not in (200, 201):
                rec("T-S75-00F agent fixtures create", False,
                    f"{name} -> {r.status_code}: {r.text[:200]}")
                return
            await c.post(f"/agents/{name}/deploy", json={"environment": "sandbox"})

        ok_d, st_d = await wait_deploy_running(AGENT_D)
        ok_r, st_r = await wait_deploy_running(AGENT_R)
        # Fixture unreachable is a HARD FAIL — the gate cannot be proven without real pods.
        rec("T-S75-00F both daemon agent fixtures deploy to running sandbox pods (real pods)",
            ok_d and ok_r, f"{AGENT_D}(durable)={st_d} {AGENT_R}(reactive)={st_r}")
        if not (ok_d and ok_r):
            return

        # ================= T-S75-001 — the scheduled dataset contract =================
        rd = await c.post("/playground/datasets", json={
            "name": f"s75-ds-d-{SFX}", "mode": "scheduled", "items": ITEMS_D})
        if rd.status_code >= 300:
            rec("T-S75-001 a scheduled dataset (job_spec + expected_side_effects) is "
                "authored via the REAL API (201) and survives save→reload; a malformed "
                "item is rejected 422 at the door",
                False, f"dataset D create {rd.status_code}: {rd.text[:300]}")
            return
        ds_d = rd.json()["id"]

        # save→reload through the REAL API — the round-trip is the claim, not the POST.
        rg = await c.get(f"/playground/datasets/{ds_d}")
        gj = rg.json() if rg.status_code < 300 else {}
        rt_items = gj.get("items", [])
        rt0 = rt_items[0] if rt_items else {}
        rt2 = rt_items[2] if len(rt_items) > 2 else {}
        se0 = rt0.get("expected_side_effects") or []

        rbad = await c.post("/playground/datasets", json={
            "name": f"s75-ds-bad-{SFX}", "mode": "scheduled", "items": [BAD_ITEM]})
        obs(f"OBSERVED dataset reload: mode={gj.get('mode')} items={len(rt_items)} "
            f"item0.kind={rt0.get('kind')} item0.job_spec={json.dumps(rt0.get('job_spec'))} "
            f"item0.expected_side_effects={json.dumps(se0)}")
        obs(f"OBSERVED malformed scheduled item POST -> {rbad.status_code} "
            f"(bad `occurs` literal + trajectory step missing `tool`): "
            f"{rbad.text[:220]}")

        rec("T-S75-001 a scheduled dataset (job_spec + expected_side_effects) is authored "
            "via the REAL API (201) and survives save→reload intact; a malformed item "
            "(bad `occurs`, trajectory step missing `tool`) is rejected 422 at the door",
            gj.get("mode") == "scheduled" and len(rt_items) == 3
            and rt0.get("kind") == "scheduled"
            and rt0.get("job_spec") == JOB_SPEC_0
            and len(se0) == 1 and se0[0].get("tool") == WRITE
            and se0[0].get("occurs") == "exactly" and se0[0].get("count") == 1
            and (rt2.get("expected_trajectory") or {}).get("steps")
            and rbad.status_code == 422,
            f"mode={gj.get('mode')} items={len(rt_items)} "
            f"job_spec_roundtrip={rt0.get('job_spec') == JOB_SPEC_0} "
            f"expected_side_effects={json.dumps(se0)} "
            f"item2.expected_trajectory={json.dumps(rt2.get('expected_trajectory'))} "
            f"malformed_status={rbad.status_code} (want 422)")
        if rbad.status_code < 300:
            # A malformed dataset that WAS created is a real fixture leak — clean it.
            try:
                await c.delete(f"/playground/datasets/{rbad.json()['id']}")
            except Exception:
                pass

        # ================= T-S75-002 — the launch guard ==================================
        # BEFORE arming: the agent is durable + deployed, and the ONLY thing it lacks is a
        # schedule trigger. So a 422 here isolates the trigger as the discriminator — and
        # a 201 AFTER arming, on the SAME agent + SAME dataset, isolates it in the other
        # direction. This pair is E-3's load-bearing launch change (before E-3 the
        # execution_shape-only read could never yield 'scheduled' ⇒ this 422'd forever).
        pre = await launch_eval(c, AGENT_D, ds_d)
        pre_detail = ""
        try:
            pre_detail = str(pre.json().get("detail"))
        except Exception:
            pre_detail = pre.text[:200]
        obs(f"OBSERVED launch BEFORE arming a schedule trigger -> {pre.status_code}: "
            f"{pre_detail[:260]}")

        tid_d, terr_d = await arm_schedule_trigger(c, AGENT_D, JOB_SPEC_0)
        tid_r, terr_r = await arm_schedule_trigger(c, AGENT_R, JOB_SPEC_R)
        if not tid_d or not tid_r:
            rec("T-S75-00F schedule triggers armed (real API)", False,
                f"{AGENT_D}={terr_d} {AGENT_R}={terr_r}")
            return

        post = await launch_eval(c, AGENT_D, ds_d)
        eid_d = post.json()["id"] if post.status_code == 201 else None
        eval_mode_db = None
        if eid_d:
            async with AsyncSessionLocal() as s:
                eval_mode_db = (await s.execute(
                    select(EvalRun.mode).where(EvalRun.id == uuid.UUID(eid_d))
                )).scalar()
        obs(f"OBSERVED launch AFTER arming -> {post.status_code} eval_run={eid_d} "
            f"EvalRun.mode(DB)={eval_mode_db}")

        rec("T-S75-002 launch guard: the SAME scheduled dataset + SAME agent 422s with no "
            "schedule trigger (naming the trigger) and 201s once one is armed, with "
            "EvalRun.mode == 'scheduled' persisted",
            pre.status_code == 422 and "schedule trigger" in pre_detail
            and post.status_code == 201 and eval_mode_db == "scheduled",
            f"before_arm={pre.status_code} detail={pre_detail[:200]!r} "
            f"after_arm={post.status_code} EvalRun.mode={eval_mode_db!r}")
        if not eid_d:
            rec("T-S75-00F scheduled eval Job launched", False,
                f"POST /eval-runs {post.status_code}: {post.text[:200]}")
            return

        # The reactive fail-closed eval (T-S75-008). Launched now so both Jobs run
        # concurrently — the refusal happens before any run is created, so it is fast.
        rr = await c.post("/playground/datasets", json={
            "name": f"s75-ds-r-{SFX}", "mode": "scheduled", "items": ITEMS_R})
        if rr.status_code >= 300:
            rec("T-S75-00F reactive dataset create", False,
                f"{rr.status_code}: {rr.text[:300]}")
            return
        ds_r = rr.json()["id"]
        rpost = await launch_eval(c, AGENT_R, ds_r)
        eid_r = rpost.json()["id"] if rpost.status_code == 201 else None
        obs(f"OBSERVED reactive-inner scheduled eval launch -> {rpost.status_code} "
            f"eval_run={eid_r} (a reactive agent with an armed schedule trigger is a "
            f"LEGAL scheduled eval at the door — the refusal is the runner's, per-item)")
        if not eid_r:
            rec("T-S75-00F reactive scheduled eval Job launched", False,
                f"{rpost.status_code}: {rpost.text[:200]}")
            return

        # ============ T-S75-009 — the LIVE CONTROL, through the REAL door ================
        # Runs WHILE the eval Jobs run. `/internal/runs/start` dispatches only to a
        # `{agent}-production` pod, so the agent must be published + deployed to
        # production first (the suite-71 fixture pattern: PATCH the version's eval gates,
        # then deploy). This is the door the scheduler hits on a real cron tick.
        try:
            live_ok, live_detail = await run_live_control(c, tid_d)
        except Exception as exc:
            # Bounded blast radius: the live control needs a PRODUCTION pod, which is
            # the most failure-prone fixture in this suite. An unexpected error here
            # must fail T-S75-009 by name and let the eval cases (003-008) still run —
            # the outer handler would otherwise drop seven assertions to diagnose one.
            # It is still a hard FAIL: the control is the no-fake-schedule gate.
            import traceback
            live_ok, live_detail = False, (
                f"live control raised {type(exc).__name__}: {exc} :: "
                f"{traceback.format_exc()[-260:]}")

        rec("T-S75-009 LIVE CONTROL (no fake-schedule gate): a NON-eval scheduled run "
            "through the REAL /internal/runs/start door (real armed trigger, real "
            "production pod) still DELIVERS for real — real /echo reflection, nothing "
            "recorded — and the eval's job_spec is the SAME shape as that trigger's real "
            "input_payload. The record seam is armed ONLY by the eval.",
            live_ok, live_detail)

        # ================= the REAL eval Jobs land =======================================
        st_ed = await wait_eval_terminal(eid_d)
        st_er = await wait_eval_terminal(eid_r, timeout=600)
        rows_d = await read_rows(eid_d)
        rows_r = await read_rows(eid_r)
        obs(f"OBSERVED eval D status={st_ed} rows={len(rows_d)}/3 | "
            f"eval R status={st_er} rows={len(rows_r)}/1")
        for i in sorted(rows_d):
            r0 = rows_d[i]
            det = r0.eval_detail or {}
            obs(f"  OBSERVED D item{i}: composite={r0.judge_score} passed={r0.passed} "
                f"dims={r0.dimension_scores} run_id={r0.run_id} "
                f"trigger_payload={json.dumps(r0.trigger_payload)} "
                f"detail.job_spec={json.dumps(det.get('job_spec'))} "
                f"recorded={json.dumps(det.get('recorded_side_effects'))[:250]} "
                f"side_effect_detail={json.dumps(det.get('side_effect_detail'))[:260]}")
        for i in sorted(rows_r):
            r0 = rows_r[i]
            obs(f"  OBSERVED R item{i}: passed={r0.passed} dims={r0.dimension_scores} "
                f"trigger_payload={json.dumps(r0.trigger_payload)} "
                f"reason={str((r0.eval_detail or {}).get('reason'))[:240]}")

        d0 = rows_d.get(0)

        # ---- T-S75-003: THE MVP GATE — record ⇒ NOT delivered ----
        if d0 is None or not d0.run_id:
            rec("T-S75-003 MVP GATE — RECORD ⇒ NOT DELIVERED: the REAL eval Job's REAL "
                "scheduled run of a REAL deployed daemon pod returns the mock sentinel "
                "for the write tool (/echo NOT hit) + persists recorded_side_effects[]",
                False, f"item 0 has no run_id (row={'missing' if d0 is None else 'no run'}; "
                       f"eval D status={st_ed})")
        else:
            w_step, all_steps = await tool_step_via_api(c, str(d0.run_id), WRITE)
            if w_step is None:
                rec("T-S75-003 MVP GATE — RECORD ⇒ NOT DELIVERED: the REAL eval Job's REAL "
                    "scheduled run of a REAL deployed daemon pod returns the mock sentinel "
                    "for the write tool (/echo NOT hit) + persists recorded_side_effects[]",
                    False, f"no completed tool:{WRITE} step on the eval's scheduled run "
                           f"{d0.run_id} (steps="
                           f"{[(s.get('name'), s.get('status')) for s in (all_steps or [])]})")
            else:
                o = _sout(w_step)
                res = str(o.get("result") or "")
                recs = o.get("recorded_side_effects") or []
                e = recs[0] if recs else {}
                shape_ok = (bool(e) and e.get("tool") == WRITE
                            and isinstance(e.get("args"), dict)
                            and e.get("mocked_response") is not None
                            and e.get("would_have_invoked"))
                rec("T-S75-003 MVP GATE — RECORD ⇒ NOT DELIVERED: the REAL eval Job's REAL "
                    "scheduled run of a REAL deployed daemon pod returns the mock sentinel "
                    "for the write tool (/echo NOT hit) + persists recorded_side_effects[] "
                    "(save→reload via GET /playground/runs/{id}/steps)",
                    is_mock(res) and not is_real_echo(res) and shape_ok,
                    f"result={res[:200]!r} mock_sentinel={is_mock(res)} "
                    f"real_reflection={is_real_echo(res)} recorded[0]={json.dumps(e)[:300]}")

        # ---- T-S75-004: the job spec IS the input (re-read from the DB) ----
        if d0 is None or not d0.run_id:
            rec("T-S75-004 THE JOB SPEC IS THE INPUT: the eval's real PlaygroundRun carries "
                "trigger_type='schedule', trigger_payload == job_spec, input_payload == "
                "job_spec, eval_mode='record' — re-read FROM THE DB",
                False, f"item 0 has no run_id (eval D status={st_ed})")
        else:
            pr = await playground_run(str(d0.run_id))
            obs(f"OBSERVED eval PlaygroundRun {d0.run_id}: shape={getattr(pr,'execution_shape',None)} "
                f"trigger_type={getattr(pr,'trigger_type',None)} "
                f"eval_mode={getattr(pr,'eval_mode',None)} status={getattr(pr,'status',None)} "
                f"input_payload={json.dumps(getattr(pr,'input_payload',None))} "
                f"trigger_payload={json.dumps(getattr(pr,'trigger_payload',None))}")
            rec("T-S75-004 THE JOB SPEC IS THE INPUT: the eval's real PlaygroundRun carries "
                "trigger_type='schedule', trigger_payload == job_spec, input_payload == "
                "job_spec, eval_mode='record' — re-read FROM THE DB",
                pr is not None and pr.trigger_type == "schedule"
                and pr.trigger_payload == JOB_SPEC_0
                and pr.input_payload == JOB_SPEC_0
                and pr.eval_mode == "record"
                and pr.execution_shape == "durable",
                f"trigger_type={getattr(pr,'trigger_type',None)!r} "
                f"trigger_payload==job_spec={getattr(pr,'trigger_payload',None) == JOB_SPEC_0} "
                f"input_payload==job_spec={getattr(pr,'input_payload',None) == JOB_SPEC_0} "
                f"eval_mode={getattr(pr,'eval_mode',None)!r} "
                f"execution_shape={getattr(pr,'execution_shape',None)!r}")

        # ---- T-S75-005: satisfied assertion ⇒ side_effect 1.0 + trigger_payload persisted ----
        if d0 is None or d0.dimension_scores is None:
            rec("T-S75-005 scorer: a satisfied expected_side_effects ⇒ dimension_scores."
                "side_effect == 1.0, composite ≥ threshold + passed, and "
                "eval_run_results.trigger_payload == job_spec — read back FROM THE DB",
                False, f"item 0 not scored: row={'missing' if d0 is None else 'no dims'} "
                       f"reason={str((getattr(d0,'eval_detail',None) or {}).get('reason'))[:220]}")
        else:
            se = d0.dimension_scores.get("side_effect")
            det = d0.eval_detail or {}
            rec("T-S75-005 scorer: a satisfied expected_side_effects ⇒ dimension_scores."
                "side_effect == 1.0, composite ≥ threshold + passed, and "
                "eval_run_results.trigger_payload == job_spec + eval_detail.job_spec — "
                "read back FROM THE DB",
                se is not None and float(se) == 1.0
                and d0.judge_score is not None and float(d0.judge_score) >= THRESHOLD
                and d0.passed is True
                and d0.trigger_payload == JOB_SPEC_0
                and det.get("job_spec") == JOB_SPEC_0,
                f"side_effect={se} composite={d0.judge_score} passed={d0.passed} "
                f"dims={d0.dimension_scores} "
                f"trigger_payload==job_spec={d0.trigger_payload == JOB_SPEC_0} "
                f"eval_detail.job_spec==job_spec={det.get('job_spec') == JOB_SPEC_0}")

        # ---- T-S75-006: violated `never` ⇒ side_effect 0.0 + item fails ----
        d1 = rows_d.get(1)
        if d1 is None or d1.dimension_scores is None:
            rec("T-S75-006 scorer: a VIOLATED occurs:'never' (on a tool the scheduled run "
                "DID record) ⇒ side_effect == 0.0 and the item does NOT pass",
                False, f"item 1 not scored: row={'missing' if d1 is None else 'no dims'} "
                       f"reason={str((getattr(d1,'eval_detail',None) or {}).get('reason'))[:220]}")
        else:
            se = d1.dimension_scores.get("side_effect")
            det = (d1.eval_detail or {}).get("side_effect_detail") or {}
            diffs = det.get("side_effect_diffs") or []
            df0 = diffs[0] if diffs else {}
            rec("T-S75-006 scorer: a VIOLATED occurs:'never' (on a tool the scheduled run "
                "DID record) ⇒ side_effect == 0.0 and the item does NOT pass",
                se is not None and float(se) == 0.0 and d1.passed is False
                and df0.get("satisfied") is False and int(df0.get("matched", 0)) >= 1,
                f"side_effect={se} passed={d1.passed} composite={d1.judge_score} "
                f"dims={d1.dimension_scores} diff0={json.dumps(df0)[:240]}")

        # ---- T-S75-007: durable-inner trajectory dims + the side-effect-skewed weights ----
        # Two claims on one real row: (a) E-1's trajectory family is REUSED on a
        # scheduled item (no scheduled-only scorer), and (b) the composite the door
        # actually produced matches the SKEWED weight set — and is distinguishable from
        # the durable set / the equal-weight mean, so this is a real discrimination and
        # not an arithmetic coincidence.
        d2 = rows_d.get(2)
        if d2 is None or d2.dimension_scores is None:
            rec("T-S75-007 durable-inner: a scheduled item with an expected_trajectory ALSO "
                "scores trajectory/tool_call (E-1 reused, no new scorer) and the composite "
                "matches the side-effect-skewed weights {response .3, trajectory .2, "
                "tool_call .1, side_effect .4}",
                False, f"item 2 not scored: row={'missing' if d2 is None else 'no dims'} "
                       f"reason={str((getattr(d2,'eval_detail',None) or {}).get('reason'))[:220]}")
        else:
            dims = d2.dimension_scores
            comp = float(d2.judge_score) if d2.judge_score is not None else -1.0
            c_skew = wmean(dims, SKEW_W)
            c_dur = wmean(dims, DUR_W)
            c_eq = wmean(dims, {})
            has_all = all(k in dims for k in
                          ("response", "trajectory", "tool_call", "side_effect"))
            matches_skew = abs(comp - c_skew) < 0.01
            # Discriminating only if the alternatives actually differ; when every
            # dimension scored identically all three sets collapse to the same number
            # and the composite cannot, by arithmetic, distinguish them.
            discriminating = abs(c_skew - c_dur) > 0.01 or abs(c_skew - c_eq) > 0.01
            disc_note = (
                "alternatives DIFFER — the composite really discriminates the weight set"
                if discriminating else
                "alternatives COINCIDE on these scores — the composite cannot discriminate "
                "the sets here; the 4 present dimensions are still the reuse claim"
            )
            obs(f"OBSERVED weight check on item2 dims={dims}: composite={comp} "
                f"skewed={c_skew:.4f} durable_set={c_dur:.4f} equal_weight={c_eq:.4f} "
                f"discriminating={discriminating}")
            rec("T-S75-007 durable-inner: a scheduled item with an expected_trajectory ALSO "
                "scores trajectory/tool_call (E-1 reused, no new scorer) and the composite "
                "matches the side-effect-skewed weights {response .3, trajectory .2, "
                "tool_call .1, side_effect .4}",
                has_all and matches_skew,
                f"dims={dims} composite={comp} skewed={c_skew:.4f} "
                f"durable_set={c_dur:.4f} equal_weight={c_eq:.4f} "
                f"matches_skew={matches_skew} ({disc_note})")

        # ---- T-S75-008: fail-closed BEFORE anything fires (reactive-inner + side effects) ----
        r0 = rows_r.get(0)
        r_runs = await playground_run_count(AGENT_R)
        obs(f"OBSERVED reactive-inner agent {AGENT_R}: playground_runs rows={r_runs} "
            f"(must be 0 — the refusal happens BEFORE the run is created)")
        if r0 is None:
            rec("T-S75-008 FAIL-CLOSED before anything fires: a REAL deployed reactive-inner "
                "agent with a REAL armed schedule trigger, whose item asserts side effects, "
                "is recorded FAILED and NO run is ever created (the record seam does not "
                "ride /chat — firing would DELIVER the real side effect)",
                False, f"no row for reactive dataset item 0 (eval R status={st_er})")
        else:
            reason = str((r0.eval_detail or {}).get("reason") or "")
            rec("T-S75-008 FAIL-CLOSED before anything fires: a REAL deployed reactive-inner "
                "agent with a REAL armed schedule trigger, whose item asserts side effects, "
                "is recorded FAILED and NO run is ever created (the record seam does not "
                "ride /chat — firing would DELIVER the real side effect)",
                r0.passed is False and r0.dimension_scores is None
                and "reactive-inner" in reason and r_runs == 0
                and r0.trigger_payload == JOB_SPEC_R,
                f"passed={r0.passed} dims={r0.dimension_scores} "
                f"playground_runs_for_agent={r_runs} (want 0) "
                f"trigger_payload==job_spec={r0.trigger_payload == JOB_SPEC_R} "
                f"reason={reason[:260]!r}")

        if st_ed != "completed" or len(rows_d) < 3 or st_er != "completed" or len(rows_r) < 1:
            obs(f"BOUNDARY: eval D status={st_ed} ({len(rows_d)}/3 scored), R status={st_er} "
                f"({len(rows_r)}/1) — asserted the strongest REAL persisted state on the rows "
                f"that landed (the suite-58/70/72/74 bar); no score fabricated.")

        obs("BOUNDARY: E-3 fires the eval through the SANDBOX playground door, not "
            "/internal/runs/start (e3/tasks.md §D1: the real door is production-only, "
            "threads no eval_mode, and is CIRCULAR with the eval_passed publish gate). "
            "Both doors converge on the same dispatch_durable_run → declarative-runner "
            "/run, and T-S75-009 keeps the real door honest with a live-delivery control. "
            "Recorded in the Gap Ledger as deferred (intentional).")
        obs("BOUNDARY: cron TIMING is not evaluated — E-3 fires the job spec once "
            "(e3/plan.md §8 'fire once, don't wait for cron'). Next-fire timing is WS-3's "
            "operate surface, gated by suite-71/suite-26, not an eval dimension.")

    except Exception as exc:
        # FAIL LOUD (the suite-74 lesson). Without this, a bare try/finally writes only
        # the cases recorded BEFORE the crash and the bash summary (PASS>0, FAIL==0)
        # reports the suite GREEN while silently dropping every remaining case — a
        # partial run must never look like a pass. Records the crash as a real failure,
        # then re-raises into `finally` so cleanup still runs.
        import traceback
        rec("T-S75-999 driver ran every case without crashing", False,
            f"driver CRASHED mid-run — cases after this point never ran: "
            f"{type(exc).__name__}: {exc} :: {traceback.format_exc()[-400:]}")
    finally:
        # write results BEFORE cleanup (the suite-69 lesson), then tear down.
        lines = [f"{'PASS' if ok else 'FAIL'}  {n}  |  {d}" for n, ok, d in results]
        lines += observed
        lines.append("SUMMARY done")
        with open(OUT, "w") as f:
            f.write("\n".join(lines) + "\n")
        for ds in (ds_d, ds_r):
            try:
                if ds:
                    await c.delete(f"/playground/datasets/{ds}")
            except Exception:
                pass
        for a in (AGENT_D, AGENT_R):
            try:
                await c.delete(f"/agents/{a}")
            except Exception:
                pass
        # Tools have no hard-delete on the API (DELETE soft-deprecates), which would
        # leave this suite's fixture skewing the platform tool list on every run. This
        # suite created this exact row; remove it.
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "delete from agent_tools where tool_id in "
                    "(select id from tools where name like :p)"), {"p": f"s75_%_{SFX}"})
                await s.execute(text("delete from tools where name like :p"),
                                {"p": f"s75_%_{SFX}"})
                await s.commit()
        except Exception:
            pass
        await c.aclose()


asyncio.run(main())
PY

echo "--- T-S75-001..009: real scheduled datasets + real eval Jobs + live control ---"
echo "  running detached in-pod driver (2 real agent deploys + 1 real production deploy +"
echo "  2 real eval Jobs + a real live scheduled run — can take ~25-45 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app S75_SFX=$RUN_SFX S75_OUT=$OUTFILE nohup python3 $DRIVER > $RUNLOG 2>&1 & echo started"

FOUND=""
for i in $(seq 1 720); do   # up to ~60 min
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    FOUND=1
    break
  fi
done

if [ -z "$FOUND" ]; then
  echo "ERROR: no driver result file after ~60 min — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -50 "$RUNLOG" 2>/dev/null || true
  echo "❌ Suite 75 FAILED (driver did not report)"
  exit 1
fi

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
echo ""
PASS=$P_PASS; FAIL=$P_FAIL
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*|BOUNDARY*|"  OBSERVED"*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# Completeness gate (the suite-74 lesson): a suite that silently stops early must NEVER
# read as green. FAIL=0 is only a pass if every gate assertion actually RAN — an
# exception, an early return, or a truncated result file otherwise produces "0 failures"
# on a half-run gate. REQUIRED_IDS is the ONE source of truth for "did the gate run in
# full"; a separate hardcoded case COUNT was tried alongside this in suite-74 and
# immediately drifted. Add a case here and nowhere else.
REQUIRED_IDS="000 001 002 003 004 005 006 007 008 009"
MISSING=""
for id in $REQUIRED_IDS; do
  echo "$RES" | grep -q "T-S75-$id" || {
    # T-S75-000 is asserted by the bash parity layer above, not the in-pod driver.
    if [ "$id" = "000" ]; then continue; fi
    MISSING="$MISSING T-S75-$id"
  }
done
if [ -n "$MISSING" ]; then
  echo "FAIL  T-S75-COMPLETE every gate assertion ran  |  NEVER RAN:$MISSING — a gate that stops early is not a pass"
  FAIL=$((FAIL+1))
  echo "  --- driver log tail (why it stopped) ---"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 "$RUNLOG" 2>/dev/null | sed 's/^/    /' || true
else
  echo "PASS  T-S75-COMPLETE every gate assertion ran (000-009, none skipped)"
  PASS=$((PASS+1))
fi

kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  rm -f "$DRIVER" "$OUTFILE" "$RUNLOG" 2>/dev/null || true

echo ""
echo "=== suite-75 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then
  echo "❌ Suite 75 FAILED"
  exit 1
fi
if [ "$PASS" -eq 0 ]; then
  echo "❌ Suite 75 INCONCLUSIVE (no assertions ran)"
  exit 1
fi
echo "✅ Suite 75 PASSED ($PASS assertions, all $(echo $REQUIRED_IDS | wc -w | tr -d ' ') required cases reported)"
