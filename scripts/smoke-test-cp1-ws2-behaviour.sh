#!/usr/bin/env bash
# scripts/smoke-test-cp1-ws2-behaviour.sh
#
# WS-2 Checkpoint 1 — BEHAVIOUR smoke (CP1c). Proves the WS-2 identity behaviour
# on REAL rows (no hand-crafted agent_runs / triggers for the assertions):
#
#   (a) OPA identity floor via `opa eval` (local opa image, agentshield.rego):
#     T-CP1C-001a daemon + user_id=""          -> user_identity_ok == true
#     T-CP1C-001b user_delegated + user_id=""  -> user_identity_ok == false
#     T-CP1C-001c user_delegated + user_id=""  -> deny_reason == "missing_user_identity"
#     T-CP1C-001d user_delegated + user_id=alice -> user_identity_ok == true
#
#   (b) armed_by persistence via the real API:
#     T-CP1C-002 arm a schedule trigger (X-User-Sub) -> agent_triggers.armed_by == arming user sub
#
#   (c) run_by identity split — the core CP1 proof, ONE shared resolve_principal:
#     T-CP1C-003 daemon agent /chat run (REAL JWT caller) -> AgentRun.run_by == caller sub
#     T-CP1C-004 daemon agent trigger run (/internal/runs/start, no caller) ->
#                AgentRun.run_by == agent's SERVICE identity subject (agent_identities),
#                and != caller sub, and != the body-supplied run_by (overridden).
#
# No fakes: the /chat caller uses a REAL Keycloak-minted JWT (password grant on the
# agentshield-studio public client, sub = platform-admin); the trigger run is the real
# internal dispatch endpoint; run_by is read from the real committed rows.
# Parts (b)+(c) run in a detached in-pod driver (deploy+wait can take ~2 min); the
# result file is written BEFORE cleanup (suite-69 lesson). Part (a) runs locally.
set -euo pipefail
NAMESPACE="${NAMESPACE:-agentshield-platform}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPA_IMAGE="openpolicyagent/opa:0.69.0-static"
ADMIN_SUB="75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"

PASS=0; FAIL=0
ok()  { echo "PASS  $1  |  $2"; PASS=$((PASS+1)); }
bad() { echo "FAIL  $1  |  $2"; FAIL=$((FAIL+1)); }

echo "=== WS-2 CP1c: behaviour smoke (identity floor + run_by split) ==="
echo "  namespace: $NAMESPACE"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# (a) OPA identity floor via opa eval (local docker image; test tooling)
# ─────────────────────────────────────────────────────────────────────────────
echo "--- (a) OPA identity floor (opa eval) ---"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT
# data bundle with one registered user_delegated agent (needed so the full
# allow-chain — identity_present/matches/tool_in_set/risk_allows — is satisfiable,
# which is what makes deny_reason == missing_user_identity the *live* reason).
cat > "$WORK/data.json" <<'JSON'
{"agents":{"system:serviceaccount:agents-platform:agent-refunds-sa":{"tools":[{"name":"lookup_order","risk":"low"}],"team":"platform","agent_class":"user_delegated","expected_sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","sa_namespace":"agents-platform"}},"grants":{"platform":[]}}
JSON
cat > "$WORK/in_daemon.json" <<'JSON'
{"sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","tool_name":"lookup_order","agent_class":"daemon","user_id":"","user_team":"","trigger_type":"schedule"}
JSON
cat > "$WORK/in_ud_empty.json" <<'JSON'
{"sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","tool_name":"lookup_order","agent_class":"user_delegated","user_id":"","user_team":"","trigger_type":"schedule"}
JSON
cat > "$WORK/in_ud_alice.json" <<'JSON'
{"sa_subject":"system:serviceaccount:agents-platform:agent-refunds-sa","tool_name":"lookup_order","agent_class":"user_delegated","user_id":"alice","user_team":"","trigger_type":"manual"}
JSON
opa_eval() {  # <input-file> <query>
  docker run --rm \
    -v "$REPO_ROOT/services/registry-api/opa_policy:/policy:ro" \
    -v "$WORK:/work:ro" "$OPA_IMAGE" \
    eval -d /policy/agentshield.rego -d /work/data.json -i "/work/$1" "$2" -f raw 2>/dev/null | tr -d '[:space:]'
}
A1=$(opa_eval in_daemon.json 'data.agentshield.user_identity_ok')
[ "$A1" = "true" ] && ok "T-CP1C-001a daemon+empty-user user_identity_ok" "=$A1" \
                    || bad "T-CP1C-001a daemon+empty-user user_identity_ok" "=$A1 expected true"
A2=$(opa_eval in_ud_empty.json 'data.agentshield.user_identity_ok')
[ "$A2" = "false" ] && ok "T-CP1C-001b user_delegated+empty-user user_identity_ok" "=$A2" \
                    || bad "T-CP1C-001b user_delegated+empty-user user_identity_ok" "=$A2 expected false"
A3=$(opa_eval in_ud_empty.json 'data.agentshield.deny_reason')
[ "$A3" = "missing_user_identity" ] && ok "T-CP1C-001c user_delegated+empty-user deny_reason" "=$A3" \
                    || bad "T-CP1C-001c user_delegated+empty-user deny_reason" "=$A3 expected missing_user_identity"
A4=$(opa_eval in_ud_alice.json 'data.agentshield.user_identity_ok')
[ "$A4" = "true" ] && ok "T-CP1C-001d user_delegated+present-user user_identity_ok" "=$A4" \
                    || bad "T-CP1C-001d user_delegated+present-user user_identity_ok" "=$A4 expected true"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# (b)+(c) real rows via detached in-pod driver
# ─────────────────────────────────────────────────────────────────────────────
echo "--- (b) armed_by persistence + (c) run_by split (real API, real JWT, real rows) ---"
API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then echo "ERROR: no running registry-api pod"; exit 1; fi
echo "  pod: $API_POD"

DRIVER=/tmp/cp1c_driver.py; OUTFILE=/tmp/cp1c_out.txt
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c "rm -f $OUTFILE /tmp/cp1c_run.log; cat > $DRIVER" <<'PY'
import asyncio, os, uuid, httpx
from sqlalchemy import select, desc
from db import AsyncSessionLocal
from models import Agent, Deployment, AgentIdentity, AgentTrigger, AgentRun

BASE = "http://localhost:8000/api/v1"
ADMIN_SUB = "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6"
HDR = {"X-User-Sub": ADMIN_SUB, "X-User-Team": "platform"}
SFX = uuid.uuid4().hex[:6]
NAME = f"cp1c-daemon-{SFX}"
SENTINEL = f"scheduler-body-sentinel-{SFX}"  # body.run_by we expect to be OVERRIDDEN by the service identity
INSTR = "You are an autonomous check agent. Reply with the single word READY."

async def mint_token():
    kc = os.getenv("KEYCLOAK_URL", "http://agentshield-keycloak")
    realm = os.getenv("KEYCLOAK_REALM", "agentshield")
    url = f"{kc}/realms/{realm}/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.post(url, data={
            "grant_type": "password", "client_id": "agentshield-studio",
            "username": "platform-admin", "password": "PlatformAdmin2024"})
        r.raise_for_status()
        return r.json()["access_token"]

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

async def latest_run(name, trigger_type, context=None):
    async with AsyncSessionLocal() as s:
        q = select(AgentRun).where(AgentRun.agent_name == name, AgentRun.trigger_type == trigger_type)
        if context:
            q = q.where(AgentRun.context == context)
        return (await s.execute(q.order_by(desc(AgentRun.started_at)).limit(1))).scalars().first()

async def main():
    results = []
    sa_subject = None
    chat_run_by = trig_run_by = None
    async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=90.0) as c:
        pid = await prov(c)
        r = await c.post("/agents/", json={
            "name": NAME, "team": "platform", "agent_type": "declarative",
            "execution_shape": "durable", "agent_class": "daemon",
            "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []}})
        assert r.status_code in (200, 201), f"create agent: {r.status_code} {r.text[:200]}"
        await c.post(f"/agents/{NAME}/deploy", json={"environment": "sandbox"})
        sa_subject = await wait_running_with_identity(NAME)

        # ── (b) arm a schedule trigger; assert armed_by = arming user sub ──
        tr = await c.post(f"/agents/{NAME}/triggers", json={
            "trigger_type": "schedule", "cron_expression": "0 0 * * *",
            "input_payload": {"message": "daemon tick"}})
        armed_ok, armed_detail = False, f"trigger create status={tr.status_code} {tr.text[:140]}"
        if tr.status_code in (200, 201):
            tid = tr.json()["id"]
            async with AsyncSessionLocal() as s:
                row = (await s.execute(
                    select(AgentTrigger).where(AgentTrigger.id == uuid.UUID(tid)))).scalars().first()
            armed_val = row.armed_by if row else None
            armed_ok = armed_val == ADMIN_SUB
            armed_detail = f"armed_by={armed_val} expected={ADMIN_SUB}"
        results.append(("T-CP1C-002 trigger armed_by = arming user sub", armed_ok, armed_detail))

        # ── (c1) /chat interactive run with a REAL JWT → run_by = caller ──
        try:
            token = await mint_token()
            chat = await c.post(
                f"/agents/{NAME}/chat",
                json={"message": "status check", "context": "playground"},
                headers={"Authorization": f"Bearer {token}"})
            chat_detail = f"chat status={chat.status_code} {chat.text[:140]}"
            if chat.status_code == 200:
                run = await latest_run(NAME, "api")
                chat_run_by = run.run_by if run else None
                chat_detail = f"run_by={chat_run_by} caller={ADMIN_SUB}"
        except Exception as exc:
            chat_detail = f"exception: {exc}"
        chat_ok = chat_run_by == ADMIN_SUB
        results.append(("T-CP1C-003 /chat run_by = caller (daemon under caller, R3 floor)", chat_ok, chat_detail))

        # ── (c2) trigger run via internal path (no caller) → run_by = service identity ──
        internal = await c.post("/internal/runs/start", json={
            "agent_name": NAME, "trigger_type": "schedule", "run_by": SENTINEL})
        trig_detail = f"internal status={internal.status_code} {internal.text[:140]}"
        if internal.status_code in (200, 201):
            run = await latest_run(NAME, "schedule", context="production")
            trig_run_by = run.run_by if run else None
            trig_detail = (f"run_by={trig_run_by} sa_subject={sa_subject} "
                           f"caller={ADMIN_SUB} body_run_by={SENTINEL}")
        trig_ok = (trig_run_by is not None and sa_subject is not None
                   and trig_run_by == sa_subject
                   and trig_run_by != ADMIN_SUB and trig_run_by != SENTINEL)
        results.append(("T-CP1C-004 trigger run_by = service identity (!= caller, != body run_by)", trig_ok, trig_detail))

    # write results BEFORE cleanup (suite-69 lesson)
    passed = sum(1 for _, okv, _ in results if okv)
    with open("/tmp/cp1c_out.txt", "w") as f:
        for name, okv, detail in results:
            f.write(f"{'PASS' if okv else 'FAIL'}  {name}  |  {detail}\n")
        f.write(f"OBSERVED-SPLIT  chat_run_by={chat_run_by}  trigger_run_by={trig_run_by}  sa_subject={sa_subject}\n")
        f.write(f"SUMMARY {passed}/{len(results)}\n")

    # cleanup (best-effort; runs after the result file exists)
    try:
        async with httpx.AsyncClient(base_url=BASE, headers=HDR, timeout=30.0) as c:
            await c.delete(f"/agents/{NAME}")
    except Exception:
        pass

asyncio.run(main())
PY

echo "  running detached in-pod driver (create+deploy+wait ~2 min)…"
kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- bash -c \
  "cd /app && PYTHONPATH=/app nohup python3 $DRIVER > /tmp/cp1c_run.log 2>&1 & echo started"

for i in $(seq 1 72); do
  sleep 5
  if kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- test -f "$OUTFILE" 2>/dev/null; then
    break
  fi
done

RES=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- cat "$OUTFILE" 2>/dev/null || true)
if [ -z "$RES" ]; then
  echo "ERROR: no driver result file — last log lines:"
  kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- tail -30 /tmp/cp1c_run.log 2>/dev/null || true
  echo ""
  echo "=== CP1c summary: PASS=$PASS FAIL=(driver did not report) ==="
  echo "CP1c BEHAVIOUR SMOKE FAILED"
  exit 1
fi

# Merge driver lines into the outer tally.
while IFS= read -r line; do
  case "$line" in
    PASS*) echo "$line"; PASS=$((PASS+1)) ;;
    FAIL*) echo "$line"; FAIL=$((FAIL+1)) ;;
    OBSERVED-SPLIT*) echo "  $line" ;;
    SUMMARY*) : ;;
    *) [ -n "$line" ] && echo "  $line" ;;
  esac
done <<< "$RES"

echo ""
echo "=== CP1c summary: PASS=$PASS FAIL=$FAIL ==="
if [ "$FAIL" -ne 0 ]; then echo "CP1c BEHAVIOUR SMOKE FAILED"; exit 1; fi
echo "CP1c BEHAVIOUR SMOKE PASSED"
