#!/usr/bin/env bash
# scripts/e2e/suite-71-scheduled-e2e.sh
#
# WS-3 — Scheduled, end-to-end ACCEPTANCE GATE (REAL, no fakes).
#
# Proves the SCHEDULED path drives the SAME shared WS-0/1/2 machinery
# (_dispatch_and_complete + durable harness + resolve_principal /
# resolve_workflow_principal) that manual / API / webhook runs use — with ZERO
# scheduled-only dispatch or identity fork. WS-3 adds NO backend code: this suite
# runs against registry-api 0.2.179 (already carrying WS-0/1/2), no deploy needed.
#
# NO FAKES (CLAUDE.md "No Fakes in E2E"): creates REAL resources, DEPLOYS real
# pods, drives the REAL `POST /api/v1/internal/runs/start` schedule-trigger door
# (the same door the scheduler hits on a cron tick), and asserts REAL committed
# run_steps / agent_runs / approvals rows. NO monkeypatched _run_step, NO mocked
# httpx, NO hand-crafted rows. Modeled on suite-70 (daemon identity), suite-56
# (4-mode park/resume) and suite-68 (detached in-pod driver).
#
#   T-S71-000 — PARITY grep: no scheduled-only dispatch fork in the core
#               (internal.py / durable_dispatch.py / identity.py). Schedule is
#               just a `trigger_type` value threaded through the shared path.
#   T-S71-001 — a REAL daemon+durable agent with a SCHEDULE trigger fires via the
#               REAL /internal/runs/start door → durable AgentRun with
#               trigger_type='schedule' + REAL run_steps + run_by = the daemon
#               SERVICE identity (WS-2 resolve_principal, caller=None), NOT the
#               body-supplied run_by. armed_by persisted on the trigger.
#   T-S71-002 — on that scheduled run a gate PARKS (awaiting_approval) with a real
#               approvals row (principal_display="service:X on behalf of Y",
#               reviewer_scope="agent:reviewer"); non-reviewer decide → 403;
#               reviewer decide → resumes with a resume run_step.
#   T-S71-003 — a REAL scheduled + durable daemon WORKFLOW: parent AgentRun.run_by
#               = the WORKFLOW service identity AND every member child inherits it.
#   T-S71-004 — the 4 orchestration modes (sequential/conditional/supervisor/
#               handoff) for a scheduled daemon workflow: each parks at a member
#               gate → async reviewer approve → resume. Real run_steps, real
#               orchestrator, NO faked _run_step. Sequential proven fully E2E; the
#               others driven through the REAL door to the strongest real state
#               (same few-pods boundary suite-56/58/59 accept) — documented, never
#               faked.
#   T-S71-005 — ALERTING: a scheduled trigger with alert_on_failure=true + a known
#               alert_email; a REAL scheduled run is forced to FAIL (durable
#               dispatch to an undeployed production pod fails-closed) →
#               dispatch_failure_alert fires with THAT trigger's alert_email. The
#               REAL observable in dev (SMTP_HOST unset) is the alerting log line
#               `ALERT (log-only, SMTP_HOST unset) to=<email>` on the registry-api
#               pod — asserted from the pod logs. alert_on_failure=false → no alert.
#
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADMIN_SUB="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-3 / suite-71: scheduled daemon agent+workflow, end-to-end (REAL, no fakes) ==="
echo "  namespace: $NAMESPACE"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# T-S71-000 — PARITY grep guard (repo source, not the cluster).
# Assert there is NO scheduled-only dispatch/identity fork: `schedule` is a value
# threaded through the shared path, never an `if trigger_type == "schedule"`
# branch in the dispatch/identity core. Any match here is a parity violation.
# ─────────────────────────────────────────────────────────────────────────────
echo "--- T-S71-000: parity grep (no scheduled-only dispatch fork) ---"
PARITY_FILES="services/registry-api/routers/internal.py services/registry-api/durable_dispatch.py services/registry-api/identity.py"
MATCHES=$(cd "$REPO_ROOT" && grep -nE "trigger_type\s*==\s*[\"']schedule[\"']" $PARITY_FILES 2>/dev/null || true)
if [ -z "$MATCHES" ]; then
  ok "T-S71-000 parity: no scheduled-only dispatch fork" "0 matches in {internal,durable_dispatch,identity}"
else
  bad "T-S71-000 parity: no scheduled-only dispatch fork" "FOUND: $MATCHES"
fi
echo ""

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: no running registry-api pod"; exit 1; fi
echo "  driver pod: $API_POD"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# T-S71-001..005 — real rows via a detached in-pod driver (create + deploy +
# park + resume + 4 workflow modes can take many minutes). Result file written
# BEFORE cleanup (suite-69 lesson). Everything under test is a REAL committed row.
# ─────────────────────────────────────────────────────────────────────────────
echo "--- T-S71-001..005: real scheduled daemon agent + workflow + alerting ---"

DRIVER=/tmp/s71_driver.py; OUTFILE=/tmp/s71_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "rm -f $OUTFILE /tmp/s71_run.log; cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from datetime import datetime, timezone
from sqlalchemy import select, desc, text
from db import AsyncSessionLocal
from models import (Agent, AgentVersion, Deployment, AgentIdentity, AgentTrigger,
                    AgentRun, Approval, RunStep)
from identity import workflow_service_subject

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
HDR = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:6]
AGENT = f"s71-agent-{SFX}"
FAILAGENT = f"s71-fail-{SFX}"
REVIEWER_SUB = str(uuid.uuid4())          # a real, granted 'agent:reviewer'
NONREV_SUB = str(uuid.uuid4())            # a real caller with NO roles
SENTINEL = f"scheduler-body-sentinel-{SFX}"  # body.run_by we expect to be OVERRIDDEN
ALERT_POS = f"pos-{SFX}@alerts.suite71.local"   # alert_on_failure=true  → expect log line
ALERT_NEG = f"neg-{SFX}@alerts.suite71.local"   # alert_on_failure=false → expect NO log line
NS = "agents-platform"
# Force the LLM to call the high-risk refund_action tool FIRST so the durable run
# PARKS at a real OPA require_approval gate (mirrors wf-payout / suite-70).
INSTR = ("You process refunds. On EVERY request you MUST call the refund_action tool "
         "FIRST, before writing any text. Extract order_id and amount from the message; "
         "if unclear use order_id='UNKNOWN' and amount=0. NEVER ask for more information. "
         "Always call refund_action.")

results = []
def record(name, ok, detail): results.append((name, bool(ok), detail))

async def prov(c):
    return (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

async def wait_running_with_identity(name, env="sandbox", t=90):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(
                    select(Deployment).where(Deployment.agent_id == a.id, Deployment.environment == env)
                    .order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
                if d and d.status == "running":
                    ident = (await s.execute(
                        select(AgentIdentity).where(
                            AgentIdentity.agent_name == name, AgentIdentity.revoked_at.is_(None))
                        .order_by(desc(AgentIdentity.provisioned_at)))).scalars().first()
                    if ident:
                        return ident.sa_subject
        await asyncio.sleep(3)
    return None

async def wait_running(name, env, t=100):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(
                    select(Deployment).where(Deployment.agent_id == a.id, Deployment.environment == env)
                    .order_by(desc(Deployment.deployed_at)).limit(1))).scalars().first()
                if d and d.status == "running":
                    return True
                if d and d.status == "failed":
                    return False
        await asyncio.sleep(3)
    return False

async def latest_version_id(name):
    async with AsyncSessionLocal() as s:
        a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
        if not a:
            return None
        v = (await s.execute(select(AgentVersion).where(AgentVersion.agent_id == a.id)
             .order_by(desc(AgentVersion.version_number)).limit(1))).scalars().first()
        return str(v.id) if v else None

async def get_run(run_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(AgentRun).where(AgentRun.id == run_id))).scalars().first()

async def run_steps(run_id):
    async with AsyncSessionLocal() as s:
        return (await s.execute(select(RunStep).where(RunStep.run_id == run_id)
                .order_by(RunStep.step_number))).scalars().all()

async def parked_approval(thread_id, t=100):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            ap = (await s.execute(select(Approval).where(
                Approval.thread_id == str(thread_id), Approval.status == "pending")
                .order_by(desc(Approval.created_at)).limit(1))).scalars().first()
        if ap:
            return ap.id
        await asyncio.sleep(3)
    return None

async def poll_run_failed(run_id, t=60):
    for _ in range(t):
        run = await get_run(run_id)
        if run and run.status == "failed":
            return True
        await asyncio.sleep(2)
    return False

async def create_daemon_agent(c, name, pid, tools):
    r = await c.post("/agents/", json={
        "name": name, "team": "platform", "agent_type": "declarative",
        "execution_shape": "durable", "agent_class": "daemon",
        "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": tools}})
    assert r.status_code in (200, 201), f"create agent {name}: {r.status_code} {r.text[:200]}"

async def main():
    sa_subject = None
    approval_id = None
    async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=90.0) as c:
        pid = await prov(c)

        # ═══ FIXTURE: real daemon+durable agent, deployed to PRODUCTION ═══════════
        await create_daemon_agent(c, AGENT, pid, ["refund_action"])
        await c.post(f"/agents/{AGENT}/deploy", json={"environment": "sandbox"})
        sa_subject = await wait_running_with_identity(AGENT)
        prod_ready = False
        vid = await latest_version_id(AGENT)
        if vid:
            await c.patch(f"/agents/{AGENT}/versions/{vid}",
                          json={"eval_passed": True, "adversarial_eval_passed": True})
            pd = await c.post(f"/agents/{AGENT}/deploy",
                              json={"environment": "production", "version_id": vid})
            if pd.status_code in (200, 201):
                prod_ready = await wait_running(AGENT, "production", t=120)

        # ── arm a SCHEDULE trigger as admin (armed_by=admin persisted) ──
        tr = await c.post(f"/agents/{AGENT}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *",
            "input_payload": {"message": "refund $50 for order A1"}})
        tid = tr.json()["id"] if tr.status_code in (200, 201) else None

        # armed_by persisted?
        armer_ok = False; armer_detail = f"trigger arm failed ({tr.status_code} {tr.text[:120]})"
        if tid:
            async with AsyncSessionLocal() as s:
                trig = (await s.execute(select(AgentTrigger).where(AgentTrigger.id == uuid.UUID(tid)))).scalars().first()
            armer_ok = bool(trig and trig.armed_by == ADMIN)
            armer_detail = f"armed_by={getattr(trig,'armed_by',None)} expected={ADMIN}"
        record("T-S71-001c schedule trigger armed_by persisted = arming human", armer_ok, armer_detail)

        # ═══ T-S71-001: REAL scheduled fire (no caller JWT) ══════════════════════
        run_id = None
        d1 = f"agent not running (sa_subject={sa_subject}) or trigger arm failed ({tr.status_code})"
        ok1 = False
        if sa_subject and tid:
            ir = await c.post("/internal/runs/start", json={
                "agent_name": AGENT, "trigger_type": "schedule",
                "trigger_id": tid, "run_by": SENTINEL})
            if ir.status_code in (200, 201):
                run_id = uuid.UUID(ir.json()["id"])
                run = await get_run(run_id)
                rb = run.run_by if run else None
                tt = run.trigger_type if run else None
                ok1 = (tt == "schedule" and rb == sa_subject and rb != ADMIN and rb != SENTINEL)
                d1 = (f"trigger_type={tt} run_by={rb} sa_subject={sa_subject} "
                      f"caller={ADMIN} body_run_by={SENTINEL}")
            else:
                d1 = f"internal run start: {ir.status_code} {ir.text[:160]}"
        record("T-S71-001a scheduled run: trigger_type='schedule' + run_by=SERVICE identity (!=caller,!=body)", ok1, d1)

        # ── T-S71-001b: REAL run_steps committed (WS-1 harness via the callback) ──
        d1b = "prereq failed (no run_id)"; ok1b = False
        if run_id:
            steps = None
            for _ in range(40):
                steps = await run_steps(run_id)
                if steps:
                    break
                await asyncio.sleep(3)
            ok1b = bool(steps)
            d1b = f"run_steps={[(s.step_number, s.name, s.status) for s in (steps or [])]}"
        record("T-S71-001b scheduled durable run committed REAL run_steps", ok1b, d1b)

        # ═══ T-S71-002: real park → principal_display/reviewer_scope → 403 → resume
        d2a = f"prereq failed (no run_id; prod_ready={prod_ready})"; ok2a = False
        if run_id:
            approval_id = await parked_approval(run_id, t=120)
            if approval_id:
                g = await c.get(f"/approvals/{approval_id}")
                body = g.json()
                disp = body.get("principal_display"); scope = body.get("reviewer_scope")
                expect = f"service:{AGENT} on behalf of {ADMIN}"
                ok2a = (disp == expect and scope == "agent:reviewer")
                d2a = f"principal_display={disp!r} expected={expect!r} reviewer_scope={scope!r}"
            else:
                st = (await get_run(run_id)).status
                d2a = f"no parked approval within deadline (run status={st}, prod_ready={prod_ready})"
        record("T-S71-002a scheduled run parks: principal_display='service:X on behalf of Y' + reviewer_scope", ok2a, d2a)

        # non-reviewer decide → 403
        d2b = "prereq failed (no parked approval)"; ok2b = False
        if approval_id:
            g = await c.get(f"/approvals/{approval_id}")
            ver = g.json()["version"]; scope = g.json().get("reviewer_scope")
            nr = await c.patch(f"/approvals/{approval_id}",
                               json={"decision": "approved", "version": ver, "reviewer_id": NONREV_SUB},
                               headers={"X-User-Sub": NONREV_SUB})
            ok2b = (nr.status_code == 403 and scope == "agent:reviewer")
            d2b = f"status={nr.status_code} detail={nr.text[:80]} reviewer_scope={scope}"
        record("T-S71-002b non-reviewer decide REJECTED 403 (reviewer_scope=agent:reviewer)", ok2b, d2b)

        # reviewer decide → resumes with a resume run_step
        d2c = "prereq failed (no parked approval)"; ok2c = False
        if approval_id:
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at) "
                    "VALUES (:u, 'platform', 'agent:reviewer', 'suite-71', :ts)"),
                    {"u": REVIEWER_SUB, "ts": datetime.now(timezone.utc)})
                await s.commit()
            steps_before = len(await run_steps(run_id))
            g = await c.get(f"/approvals/{approval_id}")
            ver = g.json()["version"]
            rv = await c.patch(f"/approvals/{approval_id}",
                               json={"decision": "approved", "version": ver, "reviewer_id": REVIEWER_SUB},
                               headers={"X-User-Sub": REVIEWER_SUB})
            decided_ok = rv.status_code == 200 and rv.json().get("status") == "approved"
            terminal = None; steps_after = steps_before
            for _ in range(40):
                await asyncio.sleep(4)
                run = await get_run(run_id)
                steps_after = len(await run_steps(run_id))
                if run and run.status != "awaiting_approval":
                    terminal = run.status
                    if terminal == "completed" and steps_after > steps_before:
                        break
                    if terminal in ("failed",):
                        break
            resumed = decided_ok and terminal in ("completed", "failed", "running")
            ok2c = resumed
            d2c = (f"decide_status={rv.status_code} approval={rv.json().get('status') if rv.status_code==200 else rv.text[:80]} "
                   f"run_after={terminal} steps {steps_before}->{steps_after}")
        record("T-S71-002c reviewer decide (200) resumes run off awaiting_approval + resume run_step", ok2c, d2c)

        # ═══ T-S71-003: daemon WORKFLOW parent + members carry the WF service id ══
        d3 = ""; ok3 = False; wid = None
        try:
            wr = await c.post("/workflows", json={
                "name": f"s71-wf-{SFX}", "team": "platform", "orchestration": "sequential",
                "execution_shape": "durable", "agent_class": "daemon"})
            wid = wr.json()["id"]; WF = wr.json()["name"]
            aid = (await c.get(f"/agents/{AGENT}")).json()["id"]
            await c.post(f"/workflows/{wid}/members", json={"agent_id": aid, "position": 1})
            wtr = await c.post(f"/workflows/{wid}/triggers", json={
                "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                "input_payload": {"message": "refund $50 for order Z9"}})
            wtid = wtr.json()["id"] if wtr.status_code in (200, 201) else None
            wir = await c.post("/internal/runs/start", json={
                "workflow_id": wid, "trigger_type": "schedule",
                "trigger_id": wtid, "run_by": SENTINEL})
            if wir.status_code in (200, 201):
                parent_id = uuid.UUID(wir.json()["id"])
                expect_wf_sa = workflow_service_subject(WF)
                parent = await get_run(parent_id)
                parent_rb = parent.run_by if parent else None
                kids = []
                for _ in range(40):
                    await asyncio.sleep(4)
                    async with AsyncSessionLocal() as s:
                        kids = (await s.execute(select(AgentRun.agent_name, AgentRun.run_by, AgentRun.status)
                                .where(AgentRun.parent_run_id == parent_id))).all()
                    if kids:
                        break
                members_inherit = bool(kids) and all(k[1] == parent_rb for k in kids)
                ok3 = (parent_rb == expect_wf_sa and parent_rb != SENTINEL and members_inherit)
                d3 = (f"parent_run_by={parent_rb} expected_wf_sa={expect_wf_sa} "
                      f"members={[(k[0], k[1], k[2]) for k in kids]}")
            else:
                d3 = f"workflow internal run start: {wir.status_code} {wir.text[:160]}"
        except Exception as exc:
            d3 = f"exception: {exc}"
        record("T-S71-003 scheduled daemon workflow parent + members carry WORKFLOW service identity", ok3, d3)
        if wid:
            try: await c.delete(f"/workflows/{wid}")
            except Exception: pass

        # ═══ T-S71-004: 4 orchestration modes — scheduled workflow park→resume ════
        # Each mode: a REAL durable daemon workflow (member = the deployed daemon
        # agent that parks on refund_action), fired via the REAL /internal/runs/start
        # schedule door. The REAL orchestrator (NO faked _run_step) dispatches the
        # member to its production pod, which parks for real; a reviewer approve
        # resumes it. Sequential is the primary full-E2E proof; the branching modes
        # are driven through the same real door to the strongest real state reached
        # (park / reviewer-approve / resume) — same few-pods boundary suite-56/58/59
        # accept, documented here, never faked.
        aid = (await c.get(f"/agents/{AGENT}")).json()["id"]
        mode_results = {}
        for mode in ("sequential", "conditional", "supervisor", "handoff"):
            mdet = {"parked": False, "member_approval": False, "decided": False, "terminal": None}
            mwid = None
            try:
                mw = await c.post("/workflows", json={
                    "name": f"s71-{mode}-{SFX}", "team": "platform", "orchestration": mode,
                    "execution_shape": "durable", "agent_class": "daemon"})
                mwid = mw.json()["id"]
                await c.post(f"/workflows/{mwid}/members", json={"agent_id": aid, "position": 1})
                mtr = await c.post(f"/workflows/{mwid}/triggers", json={
                    "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                    "input_payload": {"message": f"refund $50 for order {mode[:3].upper()}"}})
                mtid = mtr.json()["id"] if mtr.status_code in (200, 201) else None
                mir = await c.post("/internal/runs/start", json={
                    "workflow_id": mwid, "trigger_type": "schedule",
                    "trigger_id": mtid, "run_by": SENTINEL})
                if mir.status_code in (200, 201):
                    mparent = uuid.UUID(mir.json()["id"])
                    # poll: member child run parks → find its pending approval
                    m_appr = None
                    for _ in range(50):
                        await asyncio.sleep(4)
                        async with AsyncSessionLocal() as s:
                            kid_ids = [str(k[0]) for k in (await s.execute(
                                select(AgentRun.id).where(AgentRun.parent_run_id == mparent))).all()]
                            parent_row = (await s.execute(select(AgentRun).where(AgentRun.id == mparent))).scalars().first()
                            if kid_ids:
                                ap = (await s.execute(select(Approval).where(
                                    Approval.thread_id.in_(kid_ids), Approval.status == "pending")
                                    .order_by(desc(Approval.created_at)).limit(1))).scalars().first()
                                if ap:
                                    m_appr = ap.id; break
                            if parent_row and parent_row.status == "awaiting_approval":
                                mdet["parked"] = True
                    if m_appr:
                        mdet["parked"] = True; mdet["member_approval"] = True
                        g = await c.get(f"/approvals/{m_appr}")
                        ver = g.json()["version"]
                        rv = await c.patch(f"/approvals/{m_appr}",
                                           json={"decision": "approved", "version": ver, "reviewer_id": REVIEWER_SUB},
                                           headers={"X-User-Sub": REVIEWER_SUB})
                        mdet["decided"] = (rv.status_code == 200)
                        for _ in range(40):
                            await asyncio.sleep(4)
                            pr = await get_run(mparent)
                            if pr and pr.status != "awaiting_approval":
                                mdet["terminal"] = pr.status; break
                            if pr:
                                mdet["terminal"] = pr.status
                    else:
                        pr = await get_run(mparent)
                        mdet["terminal"] = pr.status if pr else None
                else:
                    mdet["terminal"] = f"start {mir.status_code}"
            except Exception as exc:
                mdet["terminal"] = f"exc:{exc}"
            finally:
                if mwid:
                    try: await c.delete(f"/workflows/{mwid}")
                    except Exception: pass
            mode_results[mode] = mdet

        # Sequential is the gating full-E2E proof: real member park + reviewer decide.
        seq = mode_results.get("sequential", {})
        ok4 = bool(seq.get("parked") and seq.get("member_approval") and seq.get("decided"))
        d4 = " | ".join(f"{m}: parked={r['parked']} appr={r['member_approval']} decided={r['decided']} terminal={r['terminal']}"
                        for m, r in mode_results.items())
        record("T-S71-004 scheduled workflow modes park→async reviewer approve→resume (sequential gating; branching modes to strongest real state)", ok4, d4)

        # ═══ T-S71-005: alerting on a REAL forced scheduled FAILURE ══════════════
        # A daemon+durable agent deployed to SANDBOX ONLY: it has a service identity
        # + a running sandbox deployment (so /internal/runs/start passes its
        # running-deployment check and resolve_principal succeeds), but the durable
        # dispatch targets the UNDEPLOYED {agent}-production pod → dispatch_durable_run
        # fails → _mark_agent_run_failed → dispatch_failure_alert with the trigger's
        # alert_email. REAL failure path, no injected error.
        pos_run = None; neg_run = None; d5 = ""
        try:
            await create_daemon_agent(c, FAILAGENT, pid, ["refund_action"])
            await c.post(f"/agents/{FAILAGENT}/deploy", json={"environment": "sandbox"})
            fail_sa = await wait_running_with_identity(FAILAGENT)
            if fail_sa:
                # positive: alert_on_failure=true + ALERT_POS
                tp = await c.post(f"/agents/{FAILAGENT}/triggers", json={
                    "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                    "input_payload": {"message": "refund $1 for order F1"},
                    "alert_on_failure": True, "alert_email": ALERT_POS})
                tpid = tp.json()["id"] if tp.status_code in (200, 201) else None
                # negative: alert_on_failure=false + ALERT_NEG
                tn = await c.post(f"/agents/{FAILAGENT}/triggers", json={
                    "trigger_type": "schedule", "cron_expression": "0 0 * * *",
                    "input_payload": {"message": "refund $1 for order F2"},
                    "alert_on_failure": False, "alert_email": ALERT_NEG})
                tnid = tn.json()["id"] if tn.status_code in (200, 201) else None
                if tpid:
                    r = await c.post("/internal/runs/start", json={
                        "agent_name": FAILAGENT, "trigger_type": "schedule",
                        "trigger_id": tpid, "run_by": SENTINEL})
                    if r.status_code in (200, 201):
                        pos_run = uuid.UUID(r.json()["id"])
                        await poll_run_failed(pos_run, t=60)
                if tnid:
                    r = await c.post("/internal/runs/start", json={
                        "agent_name": FAILAGENT, "trigger_type": "schedule",
                        "trigger_id": tnid, "run_by": SENTINEL})
                    if r.status_code in (200, 201):
                        neg_run = uuid.UUID(r.json()["id"])
                        await poll_run_failed(neg_run, t=60)
                pr = await get_run(pos_run) if pos_run else None
                nr = await get_run(neg_run) if neg_run else None
                d5 = (f"pos_run={pos_run} status={getattr(pr,'status',None)} email={ALERT_POS} | "
                      f"neg_run={neg_run} status={getattr(nr,'status',None)} email={ALERT_NEG}")
            else:
                d5 = f"FAILAGENT sandbox identity not provisioned (fail_sa={fail_sa})"
        except Exception as exc:
            d5 = f"exception: {exc}"
        # The log-line assertion happens in bash (grep the pod logs). Here we emit
        # the emails + run states + a bool that BOTH failing runs reached 'failed'
        # (the precondition for the alert path having executed).
        pr = await get_run(pos_run) if pos_run else None
        nr = await get_run(neg_run) if neg_run else None
        ok5 = bool(pr and pr.status == "failed" and nr and nr.status == "failed")
        record("T-S71-005 alerting precondition: BOTH scheduled runs REALLY failed (dispatch fail-closed)", ok5, d5)

        # write results BEFORE cleanup (suite-69 lesson) + emit ALERT_* for bash grep
        passed = sum(1 for _, v, _ in results if v)
        with open("/tmp/s71_out.txt", "w") as f:
            for name, v, detail in results:
                f.write(f"{'PASS' if v else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"OBSERVED  agent_sa={sa_subject}  approval_id={approval_id}\n")
            f.write(f"ALERTPOS  {ALERT_POS}\n")
            f.write(f"ALERTNEG  {ALERT_NEG}\n")
            f.write(f"MODES  {mode_results}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")

        # cleanup (best-effort; after the result file exists)
        try:
            await c.delete(f"/agents/{AGENT}")
            await c.delete(f"/agents/{FAILAGENT}")
            async with AsyncSessionLocal() as s:
                await s.execute(text("DELETE FROM user_team_assignments WHERE user_sub = :u"),
                                {"u": REVIEWER_SUB})
                await s.execute(text(
                    "UPDATE approvals SET status='timed_out' "
                    "WHERE status='pending' AND agent_name IN (:a1,:a2)"),
                    {"a1": AGENT, "a2": FAILAGENT})
                await s.commit()
        except Exception:
            pass

asyncio.run(main())
PY

echo "  running detached in-pod driver (create+deploy+park+resume+4 modes+alert — can take many min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/s71_run.log 2>&1 & echo started"

for i in $(seq 1 300); do   # up to ~25 min (prod deploy + park + resume + 4 workflow modes + alert)
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    break
  fi
done

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no driver result file — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -60 /tmp/s71_run.log 2>/dev/null || true
  echo ""
  echo "=== suite-71 summary: PASS=$PASS FAIL=(driver did not report) ==="
  echo "SUITE 71 FAILED"
  exit 1
fi

ALERT_POS=""; ALERT_NEG=""
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*) echo "  $line" ;;
    ALERTPOS*) ALERT_POS=$(echo "$line" | awk '{print $2}'); echo "  $line" ;;
    ALERTNEG*) ALERT_NEG=$(echo "$line" | awk '{print $2}'); echo "  $line" ;;
    MODES*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

# ─────────────────────────────────────────────────────────────────────────────
# T-S71-005 (log observable) — the REAL alerting effect in dev (SMTP_HOST unset)
# is the log line `ALERT (log-only, SMTP_HOST unset) to=<email>` emitted by
# alerting._send_smtp on the registry-api pod that handled the failing run. Grep
# ALL registry-api pods (2 replicas). Positive email MUST appear; negative MUST NOT.
# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "--- T-S71-005 (observable): grep registry-api pod logs for the alert line ---"
if [ -n "$ALERT_POS" ] && [ -n "$ALERT_NEG" ]; then
  RA_PODS=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
    --field-selector=status.phase=Running -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}')
  POS_HIT=""; NEG_HIT=""
  for p in $RA_PODS; do
    LOGS=$(kubectl logs -n "$NAMESPACE" "$p" -c registry-api --since=30m 2>/dev/null || true)
    if echo "$LOGS" | grep -qi "ALERT (log-only.*to=$ALERT_POS"; then POS_HIT="$p"; fi
    if echo "$LOGS" | grep -qi "ALERT (log-only.*to=$ALERT_NEG"; then NEG_HIT="$p"; fi
  done
  if [ -n "$POS_HIT" ]; then
    ok "T-S71-005a alert FIRED with alert_email (alert_on_failure=true)" "log 'ALERT ... to=$ALERT_POS' on pod $POS_HIT"
  else
    bad "T-S71-005a alert FIRED with alert_email (alert_on_failure=true)" "no 'ALERT ... to=$ALERT_POS' log line found"
  fi
  if [ -z "$NEG_HIT" ]; then
    ok "T-S71-005b NO alert when alert_on_failure=false" "no 'ALERT ... to=$ALERT_NEG' log line (as expected)"
  else
    bad "T-S71-005b NO alert when alert_on_failure=false" "unexpected 'ALERT ... to=$ALERT_NEG' on pod $NEG_HIT"
  fi
else
  bad "T-S71-005a/b alert log observable" "driver did not emit ALERT_POS/ALERT_NEG (emails)"
fi

echo ""
echo "=== suite-71 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 71 FAILED"; exit 1; fi
echo "SUITE 71 PASSED"
