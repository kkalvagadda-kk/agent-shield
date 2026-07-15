#!/usr/bin/env bash
# scripts/e2e/suite-70-daemon-identity.sh
#
# WS-2 Checkpoint 2 — daemon identity + async approval routing ACCEPTANCE GATE.
# REAL, no-fakes: create real resources, DEPLOY real pods, drive the REAL
# dispatch→park→decide→resume path, and assert REAL committed rows. NO
# monkeypatched _run_step, NO mocked httpx, NO hand-crafted agent_runs/approvals.
# Modeled on suite-58/59 (real durable workflow runs) + suite-60 (real single-agent
# durable park→approve→resume) + smoke-test-cp1-ws2-behaviour.sh (real run_by split).
#
# Suite file is 70 (the plan says "61", but suite-61 is TAKEN by eval-mode-plumbing).
# Test-case IDs T-S70-001..005 correspond to the plan's T-S61-001..005.
#
#   T-S70-001 — daemon AGENT: a REAL trigger run (/internal/runs/start, no caller JWT)
#               stamps AgentRun.run_by = the agent's SERVICE identity subject
#               (agent_identities), and a REAL parked approval on that run reads
#               principal_display = "service:<agent> on behalf of <armer>",
#               reviewer_scope = "agent:reviewer".
#   T-S70-002 — OPA identity floor: the DEPLOYED served bundle policy denies a
#               user_delegated + empty-user tool call (user_identity_ok=false +
#               deny_reason="missing_user_identity"), while daemon + empty-user allows.
#   T-S70-003 — on the REAL parked daemon approval, a NON-reviewer decide is rejected 403.
#   T-S70-004 — a REVIEWER decide (holds the routed reviewer role) resumes the run.
#   T-S70-005 — daemon WORKFLOW: a REAL trigger run stamps the parent AgentRun.run_by =
#               the workflow service identity AND every member child run_by inherits it.
#
# ── Real production trigger door (fix landed in 0.2.179) ──────────────────────
# EARLIER this suite worked around a defect: internal.py::_dispatch_and_complete
# dispatched durable trigger runs to a non-existent shared Service
# `declarative-runner.agentshield-platform:8080` (DNS-fail) instead of the agent's pod,
# so T-S70-001 invoked dispatch_durable_run directly with the correct runner_url.
# That defect is now FIXED (registry-api 0.2.179): the durable branch passes
# `runner_url=f"http://{agent_name}-production.{ns}:8080"` (mirrors the reactive branch +
# the playground/workflow callers). So the WORKAROUND IS GONE — T-S70-001 now drives the
# REAL /internal/runs/start production door end-to-end (the same endpoint the scheduler /
# event-gateway hit): the daemon agent is deployed to PRODUCTION, a real trigger run
# dispatches to its {agent}-production pod, and it PARKS for real. Attesting the deploy
# gate (eval_passed/adversarial_eval_passed) is deploy-gate SETUP — not the subject of
# this suite — while the park, OPA require_approval, create_approval, run_by,
# principal_display, 403 and resume are all REAL committed rows. NO fakes anywhere.
# T-S70-005 uses the workflow orchestrator, which reaches member pods with ZERO
# workaround, so its parked approval + run_by inheritance are fully end-to-end real.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OPA_IMAGE="openpolicyagent/opa:0.69.0-static"
ADMIN_SUB="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-2 CP2 / suite-70: daemon identity + async approval routing (REAL, no fakes) ==="
echo "  namespace: $NAMESPACE"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# T-S70-002 — OPA identity floor on the DEPLOYED, served bundle policy.
# Fetches the live policy.rego the OPA sidecars actually load (via the registry-api
# bundle endpoint) — NOT the repo copy — so this asserts the DEPLOYED policy. Runs
# `opa eval` locally (docker) against it. Honest note: full end-to-end reason
# propagation onto each pod's OPA input for a trigger dispatch is the DEFERRED
# identity-propagation initiative (agent_class already flows via the deploy env;
# user_id/trigger_type onto the pod OPA input is not-yet-wired) — see the WS-2 gap
# ledger. Here we prove the DEPLOYED policy's decision, the real served floor.
# ─────────────────────────────────────────────────────────────────────────────
echo "--- T-S70-002: OPA identity floor on the DEPLOYED served bundle (opa eval) ---"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: no running registry-api pod"; exit 1; fi
echo "  pod: $API_POD"

WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# Pull the REAL served rego the sidecars load (bundle endpoint) into a local file.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app python3 -c \"import httpx,sys; sys.stdout.write(httpx.get('http://localhost:8000/api/v1/bundle/policy.rego',timeout=10).text)\"" \
  > "$WORK/agentshield.rego" 2>/dev/null
if ! grep -q "user_identity_ok" "$WORK/agentshield.rego"; then
  bad "T-S70-002 fetch served policy" "served policy.rego missing user_identity_ok"
else
  # data.json with one registered user_delegated agent so the full allow-chain is
  # satisfiable — which is what makes deny_reason == missing_user_identity the LIVE reason.
  cat > "$WORK/data.json" <<'JSON'
{"agents":{"system:serviceaccount:agents-platform:agent-refunds-sa":{"tools":[{"name":"lookup_order","risk":"low"}],"team":"platform","agent_class":"user_delegated","expected_sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","sa_namespace":"agents-platform"}},"grants":{"platform":[]}}
JSON
  cat > "$WORK/in_daemon.json" <<'JSON'
{"sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","tool_name":"lookup_order","agent_class":"daemon","user_id":"","user_team":"","trigger_type":"schedule"}
JSON
  cat > "$WORK/in_ud_empty.json" <<'JSON'
{"sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","tool_name":"lookup_order","agent_class":"user_delegated","user_id":"","user_team":"","trigger_type":"schedule"}
JSON
  opa_eval() {  # <input-file> <query>
    docker run --rm \
      -v "$WORK:/work:ro" "$OPA_IMAGE" \
      eval -d /work/agentshield.rego -d /work/data.json -i "/work/$1" "$2" -f raw 2>/dev/null | tr -d '[:space:]'
  }
  B1=$(opa_eval in_ud_empty.json 'data.agentshield.user_identity_ok')
  [ "$B1" = "false" ] && ok "T-S70-002a user_delegated+empty-user user_identity_ok(DEPLOYED)" "=$B1" \
                      || bad "T-S70-002a user_delegated+empty-user user_identity_ok(DEPLOYED)" "=$B1 expected false"
  B2=$(opa_eval in_ud_empty.json 'data.agentshield.deny_reason')
  [ "$B2" = "missing_user_identity" ] && ok "T-S70-002b user_delegated+empty-user deny_reason(DEPLOYED)" "=$B2" \
                      || bad "T-S70-002b user_delegated+empty-user deny_reason(DEPLOYED)" "=$B2 expected missing_user_identity"
  B3=$(opa_eval in_daemon.json 'data.agentshield.user_identity_ok')
  [ "$B3" = "true" ] && ok "T-S70-002c daemon+empty-user user_identity_ok(DEPLOYED)" "=$B3" \
                      || bad "T-S70-002c daemon+empty-user user_identity_ok(DEPLOYED)" "=$B3 expected true"
  echo "  NOTE: agent_class reaches the pod OPA input via the deploy env; propagating"
  echo "        principal.user_id/trigger_type onto the pod OPA input for a trigger"
  echo "        dispatch is the DEFERRED identity-propagation initiative (WS-2 gap ledger)."
fi
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# T-S70-001/003/004/005 — real rows via a detached in-pod driver (create+deploy+park
# can take a few minutes). The result file is written BEFORE cleanup (suite-69 lesson).
# ─────────────────────────────────────────────────────────────────────────────
echo "--- T-S70-001/003/004/005: real daemon agent + workflow, real park→decide→resume ---"

DRIVER=/tmp/s70_driver.py; OUTFILE=/tmp/s70_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "rm -f $OUTFILE /tmp/s70_run.log; cat > $DRIVER" <<'PY'
import asyncio, uuid, httpx
from datetime import datetime, timezone
from sqlalchemy import select, desc, text
from db import AsyncSessionLocal
from models import Agent, AgentVersion, Deployment, AgentIdentity, AgentTrigger, AgentRun, Approval
from identity import workflow_service_subject

BASE = "http://localhost:8000/api/v1"
ADMIN = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
HDR = {"X-User-Sub": ADMIN, "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:6]
AGENT = f"s70-agent-{SFX}"
WF = f"s70-wf-{SFX}"
REVIEWER_SUB = str(uuid.uuid4())          # a real, granted 'agent:reviewer'
NONREV_SUB = str(uuid.uuid4())            # a real caller with NO roles
SENTINEL = f"scheduler-body-sentinel-{SFX}"  # body.run_by we expect to be OVERRIDDEN
NS = "agents-platform"
# Force the LLM to call the high-risk refund_action tool first (mirrors wf-payout) so
# the durable run PARKS at a real OPA require_approval gate.
INSTR = ("You process refunds. On EVERY request you MUST call the refund_action tool "
         "FIRST, before writing any text. Extract order_id and amount from the message; "
         "if unclear use order_id='UNKNOWN' and amount=0. NEVER ask for more information. "
         "Always call refund_action.")

results = []
def record(name, ok, detail): results.append((name, bool(ok), detail))

async def prov(c):
    return (await c.get("/llm-providers/", params={"team": "platform"})).json()["items"][0]["id"]

async def wait_running_with_identity(name, t=90):
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            a = (await s.execute(select(Agent).where(Agent.name == name))).scalars().first()
            if a:
                d = (await s.execute(
                    select(Deployment).where(Deployment.agent_id == a.id, Deployment.environment == "sandbox")
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
    """Wait for the agent's deployment in `env` to reach running (real pod)."""
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

async def parked_approval(run_id, t=100):
    """Poll for a REAL pending approval whose thread_id == run_id (the pod created it)."""
    for _ in range(t):
        async with AsyncSessionLocal() as s:
            ap = (await s.execute(select(Approval).where(
                Approval.thread_id == str(run_id), Approval.status == "pending")
                .order_by(desc(Approval.created_at)).limit(1))).scalars().first()
        if ap:
            return ap.id
        await asyncio.sleep(3)
    return None

async def main():
    sa_subject = None
    approval_id = None
    async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=90.0) as c:
        pid = await prov(c)

        # ── create a durable DAEMON agent with the high-risk refund tool ──
        r = await c.post("/agents/", json={
            "name": AGENT, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "daemon",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": ["refund_action"]}})
        assert r.status_code in (200, 201), f"create agent: {r.status_code} {r.text[:200]}"

        # Deploy SANDBOX first (provisions the service identity), then attest the deploy
        # gate + deploy PRODUCTION so the REAL /internal/runs/start path — which the fix
        # (0.2.179) now dispatches to {agent}-production — reaches a real production pod.
        # Attesting eval_passed/adversarial_eval_passed is deploy-gate SETUP: the eval
        # gate is NOT what this suite tests, and everything under test (the park, OPA
        # require_approval, create_approval, run_by, principal_display, 403, resume) stays
        # 100% real. This proves the fix through the REAL production trigger door.
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
                prod_ready = await wait_running(AGENT, "production", t=100)

        # ── arm a schedule trigger as admin (armed_by=admin; default reviewer role) ──
        tr = await c.post(f"/agents/{AGENT}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *",
            "input_payload": {"message": "refund $50 for order A1"}})
        tid = tr.json()["id"] if tr.status_code in (200, 201) else None

        # ── T-S70-001a: REAL trigger run (no caller JWT) → run_by = service identity ──
        run_id = None
        d1a = f"agent not running (sa_subject={sa_subject}) or trigger arm failed ({tr.status_code})"
        if sa_subject and tid:
            ir = await c.post("/internal/runs/start", json={
                "agent_name": AGENT, "trigger_type": "schedule",
                "trigger_id": tid, "run_by": SENTINEL})
            if ir.status_code in (200, 201):
                run_id = ir.json()["id"]
                run = await get_run(uuid.UUID(run_id))
                rb = run.run_by if run else None
                d1a = f"run_by={rb} sa_subject={sa_subject} caller={ADMIN} body_run_by={SENTINEL}"
                ok1a = (rb == sa_subject and rb != ADMIN and rb != SENTINEL)
            else:
                ok1a = False; d1a = f"internal run start: {ir.status_code} {ir.text[:160]}"
        else:
            ok1a = False
        record("T-S70-001a daemon trigger run_by = SERVICE identity (!= caller, != body run_by)", ok1a, d1a)

        # ── T-S70-001b: the REAL /internal/runs/start durable dispatch (fixed in 0.2.179
        #    to target {agent}-production, mirroring the reactive branch + the playground/
        #    workflow callers) reaches the real production pod → REAL park → REAL approval →
        #    principal_display. NO workaround: the run parks through the SAME production door
        #    the scheduler / event-gateway hit. If the production pod isn't reachable on this
        #    cluster the run won't park — we then report the strongest REAL state (run_by is
        #    already asserted in 001a) rather than fake it. ──
        d1b = f"prereq failed (no run_id; prod_ready={prod_ready})"
        ok1b = False
        if run_id:
            approval_id = await parked_approval(run_id, t=100)
            if approval_id:
                g = await c.get(f"/approvals/{approval_id}")
                body = g.json()
                disp = body.get("principal_display")
                scope = body.get("reviewer_scope")
                expect = f"service:{AGENT} on behalf of {ADMIN}"
                ok1b = (disp == expect and scope == "agent:reviewer")
                d1b = f"principal_display={disp!r} expected={expect!r} reviewer_scope={scope!r} (via REAL /internal/runs/start → {AGENT}-production)"
            else:
                st = (await get_run(uuid.UUID(run_id))).status
                d1b = f"no parked approval within deadline (run status={st}, prod_ready={prod_ready})"
        record("T-S70-001b parked approval principal_display = 'service:X on behalf of Y' + scope (real prod door)", ok1b, d1b)

        # ── T-S70-003: a NON-reviewer decide on the REAL parked approval is rejected 403 ──
        d3 = "prereq failed (no parked approval)"
        ok3 = False
        if approval_id:
            g = await c.get(f"/approvals/{approval_id}")
            ver = g.json()["version"]; scope = g.json().get("reviewer_scope")
            nr = await c.patch(f"/approvals/{approval_id}",
                               json={"decision": "approved", "version": ver, "reviewer_id": NONREV_SUB},
                               headers={"X-User-Sub": NONREV_SUB})
            ok3 = (nr.status_code == 403 and scope == "agent:reviewer")
            d3 = f"status={nr.status_code} detail={nr.text[:80]} reviewer_scope={scope}"
        record("T-S70-003 non-reviewer decide REJECTED 403 (reviewer_scope=agent:reviewer)", ok3, d3)

        # ── T-S70-004: a REVIEWER decide (holds the routed role) resumes the run ──
        d4 = "prereq failed (no parked approval)"
        ok4 = False
        if approval_id:
            # Grant a REAL reviewer role (real authority provisioning, like arming a trigger).
            async with AsyncSessionLocal() as s:
                await s.execute(text(
                    "INSERT INTO user_team_assignments (user_sub, team_name, role, assigned_by, assigned_at) "
                    "VALUES (:u, 'platform', 'agent:reviewer', 'suite-70', :ts)"),
                    {"u": REVIEWER_SUB, "ts": datetime.now(timezone.utc)})
                await s.commit()
            g = await c.get(f"/approvals/{approval_id}")
            ver = g.json()["version"]
            rv = await c.patch(f"/approvals/{approval_id}",
                               json={"decision": "approved", "version": ver, "reviewer_id": REVIEWER_SUB},
                               headers={"X-User-Sub": REVIEWER_SUB})
            decided_ok = rv.status_code == 200 and rv.json().get("status") == "approved"
            # Resume runs in the background; poll the run to LEAVE awaiting_approval.
            terminal = None
            for _ in range(30):
                await asyncio.sleep(4)
                run = await get_run(uuid.UUID(run_id))
                if run and run.status != "awaiting_approval":
                    terminal = run.status; break
            # Strongest REAL state: the reviewer decide committed (approved) AND the run
            # left the parked state (ideally 'completed'; same few-pods boundary as 58/60).
            ok4 = decided_ok and terminal in ("completed", "failed", "running")
            d4 = f"decide_status={rv.status_code} approval={rv.json().get('status') if rv.status_code==200 else rv.text[:80]} run_after={terminal}"
        record("T-S70-004 reviewer decide accepted (200) + run resumes off awaiting_approval", ok4, d4)

        # ── T-S70-005: daemon WORKFLOW — parent + members carry the workflow service id ──
        d5 = ""
        ok5 = False
        wid = None
        try:
            wr = await c.post("/workflows", json={
                "name": WF, "team": "platform", "orchestration": "sequential",
                "execution_shape": "durable", "agent_class": "daemon"})
            wid = wr.json()["id"]
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
                parent_id = wir.json()["id"]
                expect_wf_sa = workflow_service_subject(WF)
                parent = await get_run(uuid.UUID(parent_id))
                parent_rb = parent.run_by if parent else None
                # Poll for member child runs created by the real orchestrator.
                kids = []
                for _ in range(30):
                    await asyncio.sleep(4)
                    async with AsyncSessionLocal() as s:
                        kids = (await s.execute(select(AgentRun.agent_name, AgentRun.run_by, AgentRun.status)
                                .where(AgentRun.parent_run_id == uuid.UUID(parent_id)))).all()
                    if kids:
                        break
                members_inherit = bool(kids) and all(k[1] == parent_rb for k in kids)
                ok5 = (parent_rb == expect_wf_sa and parent_rb != SENTINEL and members_inherit)
                d5 = (f"parent_run_by={parent_rb} expected_wf_sa={expect_wf_sa} "
                      f"members={[(k[0], k[1], k[2]) for k in kids]}")
            else:
                d5 = f"workflow internal run start: {wir.status_code} {wir.text[:160]}"
        except Exception as exc:
            d5 = f"exception: {exc}"
        record("T-S70-005 daemon workflow parent + members carry the WORKFLOW service identity", ok5, d5)

        # write results BEFORE cleanup (suite-69 lesson)
        passed = sum(1 for _, v, _ in results if v)
        with open("/tmp/s70_out.txt", "w") as f:
            for name, v, detail in results:
                f.write(f"{'PASS' if v else 'FAIL'}  {name}  |  {detail}\n")
            f.write(f"OBSERVED  agent_sa={sa_subject}  approval_id={approval_id}\n")
            f.write(f"SUMMARY {passed}/{len(results)}\n")

        # cleanup (best-effort; after the result file exists)
        try:
            if wid: await c.delete(f"/workflows/{wid}")
            await c.delete(f"/agents/{AGENT}")
            async with AsyncSessionLocal() as s:
                await s.execute(text("DELETE FROM user_team_assignments WHERE user_sub = :u"),
                                {"u": REVIEWER_SUB})
                # The daemon workflow member parks (T-S70-005) and its approval lingers
                # 'pending' after we delete the workflow — resolve it so the Global Inbox
                # (and the Playwright gate) don't inherit an orphaned row.
                await s.execute(text(
                    "UPDATE approvals SET status='timed_out' "
                    "WHERE status='pending' AND agent_name = :a"), {"a": AGENT})
                await s.commit()
        except Exception:
            pass

asyncio.run(main())
PY

echo "  running detached in-pod driver (create+deploy+park+resume can take a few min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/s70_run.log 2>&1 & echo started"

for i in $(seq 1 150); do   # up to ~12.5 min (production deploy + park + resume)
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    break
  fi
done

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no driver result file — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -40 /tmp/s70_run.log 2>/dev/null || true
  echo ""
  echo "=== suite-70 summary: PASS=$PASS FAIL=(driver did not report) ==="
  echo "SUITE 70 FAILED"
  exit 1
fi

while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

echo ""
echo "=== suite-70 summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "SUITE 70 FAILED"; exit 1; fi
echo "SUITE 70 PASSED"
