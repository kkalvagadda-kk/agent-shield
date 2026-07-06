#!/usr/bin/env bash
# Suite 37: Organic workflow HITL via real OPA governance (gated — may skip)
# Tests T-S37-001 through T-S37-003
#
# PURPOSE
# -------
# Proves that OPA's require_approval decision causes a production agent run to
# pause organically: the agent pod's SDK consults real OPA (localhost:8181) →
# OPA returns require_approval for a high-risk tool → the SDK's hitl.require_approval
# POSTs a pending Approval row to the registry-api and interrupt()s the graph →
# the run halts at awaiting_approval. The suite then approves the pending request
# and verifies the status flips to 'approved'.
#
# NOTE: the approval origin is the AGENT POD's SDK, NOT the Safety Orchestrator
# (which is only a PII/content scanner). See docs/decisions.md Decision 26.
#
# GATING — WHY THIS SUITE SKIPS IN MOST ENVIRONMENTS
# ---------------------------------------------------
# Until the deploy-controller 0.1.8 fix, AGENTSHIELD_OPA_URL was not propagated to
# agent pods, so every SDK ran in DEV_MODE with a mock OPA (require_approval always
# false — see Decision 26 / gap Mi-06). Organic HITL only fires once:
#   (a) A production deployment exists for an agent that has at least one
#       high-risk (or critical) tool assigned — this is the gate below.
#   (b) The agent pod was deployed by controller >=0.1.8 so it has
#       AGENTSHIELD_OPA_URL set and actually consults OPA.
#   (c) The OPA bundle is loaded and the agent's policy contains
#       require_approval for that tool's risk level.
# The authoritative gate is (a): if no running high-risk agent exists, the suite
# prints "SKIP: <reason>" and exits 0. If one exists but (b)/(c) aren't green,
# T-S37-002 fails loudly — that's the intended organic-OPA canary signal.
#
# ASSERTION BOUNDARY
# ------------------
# T-S37-002 polls up to 30 s for a pending Approval row. T-S37-003 asserts the
# PATCH to /approvals/{id} returns 200 + status='approved'. Whether the agent
# pod fully completes after the resume is NOT asserted — consistent with the
# boundary accepted by the bash e2e suites (pods may be few; execution may
# not finish).
#
# Usage: bash scripts/e2e/suite-37-workflow-hitl-opa.sh
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
PASS=0; FAIL=0
TS=$(date +%s)

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
[ -z "${API_POD:-}" ] && { echo "FATAL: registry-api pod not found"; exit 1; }

# NOTE: AGENTSHIELD_OPA_URL lives on AGENT pods (read by the SDK), never on the
# registry-api pod — so we do NOT gate on the registry-api env. The authoritative
# gate is the Python find_target() below: skip only if no running high-risk agent
# exists. If one exists but its pod lacks AGENTSHIELD_OPA_URL (deployed by an old
# controller) or the bundle isn't loaded, T-S37-002 fails loudly (the canary).

echo "=== Suite 37: Organic Workflow HITL via OPA (gated) ==="

# NOTE: unquoted heredoc — bash expands ${TS}; Python body uses no bare '$'
# beyond that substitution.
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 - <<PY 2>&1 | grep -v "Defaulted container" | tee /tmp/s37_out.txt
import asyncio, sys, time as _time
from datetime import datetime, timezone
import httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, AgentTool, Tool, Deployment, Approval

TS   = "${TS}"
TEAM = "platform"
B    = "http://localhost:8000/api/v1"
H    = {"X-User-Sub": "system"}
P = 0; F = 0

def ok(n):        global P; P+=1; print("  PASS:", n)
def bad(n, d=""): global F; F+=1; print("  FAIL:", n, d)


# ── Precondition check: find any deployed agent that has a high-risk tool
# assigned and a running production deployment.
async def find_target():
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Agent.name, Tool.name.label("tool_name"), Tool.risk_level, Agent.team)
            .join(AgentTool, AgentTool.agent_id == Agent.id)
            .join(Tool, Tool.id == AgentTool.tool_id)
            .join(Deployment, Deployment.agent_id == Agent.id)
            .where(Tool.risk_level.in_(["high", "critical"]))
            .where(Deployment.status == "running")
            .limit(1)
        )
        return result.fetchone()

target = asyncio.run(find_target())
if target is None:
    print("__SKIP__ no deployed agent with a high-risk (or critical) tool found; organic HITL requires at least one")
    sys.exit(0)

agent_name = target[0]
tool_name  = target[1]
risk_level = target[2]
agent_team = target[3]
print(f"  precondition OK: agent='{agent_name}', tool='{tool_name}', risk_level='{risk_level}', team='{agent_team}'")


# ── Helper: poll the DB for a pending Approval created by this agent after
# since_ts (epoch float). Avoids HTTP authority-scoping on GET /approvals/.
async def poll_pending_approval(agent_name_param, since_ts):
    since_dt = datetime.fromtimestamp(since_ts, tz=timezone.utc)
    async with AsyncSessionLocal() as s:
        result = await s.execute(
            select(Approval)
            .where(
                Approval.agent_name == agent_name_param,
                Approval.status     == "pending",
                Approval.created_at >= since_dt,
            )
            .order_by(Approval.created_at.desc())
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            return dict(id=str(row.id), version=row.version, risk_level=row.risk_level,
                        thread_id=row.thread_id, tool_name=row.tool_name)
        return None


c = httpx.Client(base_url=B, timeout=30, headers=H)

# ── T-S37-001 — Trigger a production run for the agent.
# POST /internal/runs/start creates the run row and fires a background dispatch
# task to the agent pod. A message referencing the tool name improves the chance
# the agent calls it; exact triggering depends on agent policy/logic.
before_ts = _time.time()
run_resp = c.post("/internal/runs/start", json={
    "agent_name":   agent_name,
    "trigger_type": "manual",
    "run_by":       "e2e-suite37",
    "trigger_payload": {"message": f"please use {tool_name} with default parameters"},
})
if run_resp.status_code in (200, 201):
    run_data = run_resp.json()
    run_id   = run_data.get("id")
    ok(f"T-S37-001 production run created for agent '{agent_name}' (id={run_id}, status={run_data.get('status')})")
else:
    bad("T-S37-001", f"POST /internal/runs/start returned {run_resp.status_code}: {run_resp.text[:300]}")
    print("__RESULT__", P, F)
    sys.exit(1)


# ── T-S37-002 — Poll up to 30 s for a pending Approval row.
# The agent pod must consult OPA, receive require_approval, and POST the
# Approval row before this window expires. If OPA is live and the agent's
# policy maps this tool to require_approval, this will appear within a few
# seconds. A timeout here indicates the organic HITL path is not wired.
approval = None
deadline = _time.time() + 30
while _time.time() < deadline:
    approval = asyncio.run(poll_pending_approval(agent_name, before_ts))
    if approval:
        break
    _time.sleep(2)

if approval and approval["risk_level"] in ("high", "critical"):
    ok(f"T-S37-002 organic pending Approval found: tool='{approval['tool_name']}' "
       f"risk_level='{approval['risk_level']}' thread_id='{approval['thread_id']}'")
else:
    bad("T-S37-002",
        "no pending Approval appeared within 30 s — check: (a) agent pod has AGENTSHIELD_OPA_URL set, "
        "(b) OPA bundle loaded, (c) agent policy maps this tool to require_approval")


# ── T-S37-003 — PATCH approve → status flips to 'approved'.
# Uses reviewer_id='system' which bypasses the ApprovalAuthority check
# (same pattern as suite-35). The best-effort pod resume fires in the
# background; we only assert the approval record itself.
if approval:
    r3 = c.patch(f"/approvals/{approval['id']}", json={
        "decision":       "approved",
        "reviewer_id":    "system",
        "reviewer_notes": "E2E Suite 37 organic approve",
        "version":        approval["version"],
    })
    j3 = r3.json() if r3.status_code == 200 else {}
    if r3.status_code == 200 and j3.get("status") == "approved":
        ok(f"T-S37-003 PATCH approve → status=approved (decision_at set: {bool(j3.get('decision_at'))})")
    else:
        bad("T-S37-003", f"status_code={r3.status_code} body={r3.text[:200]}")
else:
    bad("T-S37-003", "skipped — no approval_id available (T-S37-002 failed)")


print("__RESULT__", P, F)
sys.exit(0)
PY

# Handle both skip and result paths
if grep -q "__SKIP__" /tmp/s37_out.txt 2>/dev/null; then
  SKIP_MSG=$(grep "__SKIP__" /tmp/s37_out.txt | head -1 | sed 's/__SKIP__ //')
  echo ""
  echo "==> Suite 37: SKIPPED — ${SKIP_MSG}"
  exit 0
fi

RES=$(grep -o '__RESULT__ [0-9]* [0-9]*' /tmp/s37_out.txt | tail -1 || true)
if [ -n "$RES" ]; then
  PASS=$(echo "$RES" | awk '{print $2}')
  FAIL=$(echo "$RES" | awk '{print $3}')
fi
echo ""
echo "==> Suite 37 Results: ${PASS} passed, ${FAIL} failed"
[ "${FAIL:-1}" -eq 0 ] || exit 1
