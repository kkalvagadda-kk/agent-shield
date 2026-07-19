#!/usr/bin/env bash
# scripts/e2e/suite-78-conversations.sh
#
# E2E Suite 78: Conversations (POC-5 list) — the REAL path, no fakes.
#
# Proves the two already-live (registry-api:0.2.195) list endpoints that back the
# Conversations UI surfaces:
#   GET /api/v1/agents/{name}/memory/conversations   (scoped: docked History + deployment tab)
#   GET /api/v1/me/conversations                      (cross-agent: standalone page)
# Both are `require_user` (JWT), so this suite fetches REAL Keycloak tokens for two
# distinct users the browser way (grant_type=password) — X-User-Sub header auth does
# NOT work on these endpoints (require_user ignores it). Runs in-pod (kubectl exec)
# so the assertions hit the real router + the real aggregate query + real Postgres.
#
# Seeding is done directly through the registry-api pod's own DB session
# (AsyncSessionLocal + the AgentMemory ORM model — the same in-pod mechanism
# suite-75 uses to insert rows), NOT via a cross-pod psql. A self-contained
# production_deployments chain (published_artifact -> published_version ->
# production_deployment) is created so the derived environment="production" case is
# provable regardless of ambient cluster state. Everything is torn down at the end.
#
#   T-S78-001 — GET /agents/{agent}/memory/conversations (USER_A): per-thread
#               summaries — title = first user message, correct message_count,
#               last_activity present, NEWEST-FIRST ordering.
#   T-S78-002 — ownership: USER_B's lists exclude USER_A's threads and vice-versa
#               (scoped endpoint + /me, both directions), identity from the JWT only.
#   T-S78-003 — ?deployment_id=<prod id> returns ONLY the production thread tagged
#               environment="production"; the no-deployment thread reports "sandbox".
#   T-S78-004 — GET /me/conversations (USER_A) returns BOTH threads (un-agent-scoped),
#               each carrying its derived environment.
#
# Boundary: if Keycloak tokens for the two users cannot be obtained, the cases SKIP
# (genuine env gap) rather than FAIL — the same discipline the other suites accept.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
SUFFIX="$(date +%s | tail -c 6)$(printf '%04x' $((RANDOM % 65536)))"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}')
[ -n "$API_POD" ] || { echo "FATAL: no running registry-api pod"; exit 1; }

echo "=== Suite 78: Conversations (POC-5 list) ==="
echo "  Pod:    $API_POD"
echo "  Suffix: $SUFFIX"

RESULT=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env SUFFIX="$SUFFIX" python3 - <<'PY'
import os, asyncio, json, base64, httpx
from datetime import datetime, timezone, timedelta
from sqlalchemy import delete
from db import AsyncSessionLocal
from models import (
    AgentMemory,
    AgentRun,
    CompositeWorkflow,
    PublishedArtifact,
    PublishedVersion,
    ProductionDeployment,
)

SUFFIX = os.environ["SUFFIX"]
ROOT = "http://localhost:8000"
BASE = ROOT + "/api/v1"
KC = "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token"

AGENT = f"s78-agent-{SUFFIX}"
T_P = f"s78-P-{SUFFIX}"          # USER_A production thread (newest)
T_S = f"s78-S-{SUFFIX}"          # USER_A sandbox thread (older)
T_B = f"s78-B-{SUFFIX}"          # USER_B sandbox thread (ownership fixture)
P_FIRST = f"Refund for order 4471 (prod {SUFFIX})"
S_FIRST = f"How do I reset my password? (sandbox {SUFFIX})"
B_FIRST = f"User B private thread ({SUFFIX})"

# Workflow-conversations fixture (T-S78-005): a workflow whose transcript is
# authored by a MEMBER (member agent_name, NULL user_id) — proving the workflow
# endpoint resolves ownership/identity through the PARENT run, not the member rows.
WF_NAME = f"s78-wf-{SUFFIX}"
WF_MEMBER = f"s78-member-{SUFFIX}"
T_WF = f"s78-WF-{SUFFIX}"        # the shared workflow session/thread
WF_FIRST = f"Summarize the Q3 report ({SUFFIX})"

IDS = ["T-S78-001", "T-S78-002", "T-S78-003", "T-S78-004", "T-S78-005", "T-S78-006"]
fails = []


def result(tid, verdict, msg=""):
    print(f"RESULT {tid} {verdict} {msg}")
    if verdict == "FAIL":
        fails.append(tid)


def skip_all(reason):
    for t in IDS:
        result(t, "SKIP", reason)


async def get_token(user, pw):
    # The conversations endpoints are require_user (JWT); the X-User-Sub header only
    # works on playground routes. Fetch a real token the browser way.
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post(KC, data={
                "grant_type": "password", "client_id": "agentshield-studio",
                "username": user, "password": pw})
        if r.status_code != 200:
            return None, None
        tok = r.json()["access_token"]
        p = tok.split(".")[1]; p += "=" * (4 - len(p) % 4)
        sub = json.loads(base64.urlsafe_b64decode(p)).get("sub")
        return tok, sub
    except Exception:
        return None, None


async def seed(sub_a, sub_b):
    """Seed a self-contained production_deployments chain + three threads.

    Returns (prod_dep_id, art_id, ver_id, dep_id). Uses the registry-api pod's own
    DB session — direct ORM inserts, fully in one transaction."""
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as s:
        art = PublishedArtifact(name=f"s78-art-{SUFFIX}", type="agent", team="platform")
        s.add(art); await s.flush()
        ver = PublishedVersion(artifact_id=art.id, version_label="v1", config_snapshot={})
        s.add(ver); await s.flush()
        dep = ProductionDeployment(artifact_id=art.id, version_id=ver.id, status="running")
        s.add(dep); await s.flush()
        prod_dep_id = dep.id

        def add_thread(thread_id, user_id, deployment_id, base, turns):
            for i, (role, content) in enumerate(turns):
                s.add(AgentMemory(
                    agent_name=AGENT, team="platform", thread_id=thread_id,
                    user_id=user_id, role=role, content=content, message_index=i,
                    session_id=thread_id, deployment_id=deployment_id, scope="agent",
                    message_kind=("user" if role == "user" else "agent_output"),
                    created_at=base + timedelta(seconds=i)))

        # USER_A production thread — deployment_id IS a production_deployments.id.
        add_thread(T_P, sub_a, prod_dep_id, now, [
            ("user", P_FIRST),
            ("assistant", "Sure, processing your refund now."),
            ("user", "Thanks — also cancel the subscription.")])
        # USER_A sandbox thread — no deployment_id (older, so P sorts first).
        add_thread(T_S, sub_a, None, now - timedelta(minutes=5), [
            ("user", S_FIRST),
            ("assistant", "Click 'Forgot password' on the login page.")])
        # USER_B thread — ownership fixture, same agent, different owner.
        add_thread(T_B, sub_b, None, now - timedelta(minutes=3), [
            ("user", B_FIRST),
            ("assistant", "Acknowledged.")])

        # --- Workflow fixture (T-S78-005) --------------------------------------
        # A workflow + a parent run owned by USER_A + a workflow_run transcript
        # authored by a MEMBER with a NULL user_id (exactly how the orchestrator
        # writes it). The workflow endpoint must still surface it for USER_A via
        # the parent run, while the per-agent path (member name, NULL user) can't.
        wf = CompositeWorkflow(name=WF_NAME, team="platform")
        s.add(wf); await s.flush()
        s.add(AgentRun(
            agent_name=WF_NAME, team="platform", workflow_id=wf.id,
            session_id=T_WF, user_id=sub_a, parent_run_id=None,
            status="completed", started_at=now - timedelta(minutes=2)))
        for i, (role, content) in enumerate([
                ("user", WF_FIRST),
                ("assistant", "Here is the Q3 summary."),
        ]):
            s.add(AgentMemory(
                agent_name=WF_MEMBER, team="platform", thread_id=T_WF,
                user_id=None, role=role, content=content, message_index=i,
                session_id=T_WF, deployment_id=None, scope="workflow_run",
                message_kind=("user" if role == "user" else "agent_output"),
                created_at=(now - timedelta(minutes=2)) + timedelta(seconds=i)))

        await s.commit()
        return prod_dep_id, art.id, ver.id, dep.id, str(wf.id)


async def main():
    tok_a, sub_a = await get_token("platform-admin", "PlatformAdmin2024")
    tok_b, sub_b = await get_token("agent-reviewer", "Reviewer2024")
    if not (tok_a and sub_a and tok_b and sub_b):
        skip_all("no keycloak token(s) — /conversations endpoints are require_user (JWT)")
        return
    if sub_a == sub_b:
        skip_all("USER_A and USER_B resolved to the same sub — cannot prove ownership")
        return

    hdr_a = {"X-User-Sub": sub_a, "X-User-Team": "platform"}
    # The scoped endpoint 404s for an unknown agent, so the agent must exist.
    try:
        async with httpx.AsyncClient(timeout=30) as c:
            cr = await c.post(f"{BASE}/agents/", headers=hdr_a, json={
                "name": AGENT, "team": "platform", "agent_type": "declarative",
                "memory_enabled": True})
        cr_status, cr_text = cr.status_code, cr.text
    except Exception as e:
        skip_all(f"agent create errored: {e!r}")
        return
    if cr_status not in (200, 201, 409):
        skip_all(f"agent create http={cr_status}: {cr_text[:120]}")
        return

    prod_dep_id = art_id = ver_id = dep_id = wf_id = None
    try:
        prod_dep_id, art_id, ver_id, dep_id, wf_id = await seed(sub_a, sub_b)
        ba = {"Authorization": f"Bearer {tok_a}"}
        bb = {"Authorization": f"Bearer {tok_b}"}
        async with httpx.AsyncClient(timeout=30) as c:
            # ---- T-S78-001 — scoped list for USER_A (title/count/last_activity/order) ----
            r1 = await c.get(f"{BASE}/agents/{AGENT}/memory/conversations", headers=ba)
            convs = r1.json() if r1.status_code == 200 else []
            tids = [x["thread_id"] for x in convs]
            by = {x["thread_id"]: x for x in convs}
            p, sc = by.get(T_P), by.get(T_S)
            ok1 = (r1.status_code == 200 and set(tids) == {T_P, T_S} and p and sc
                   and p["title"] == P_FIRST and sc["title"] == S_FIRST
                   and p["message_count"] == 3 and sc["message_count"] == 2
                   and p.get("last_activity") and sc.get("last_activity")
                   and tids.index(T_P) < tids.index(T_S))
            result("T-S78-001", "PASS" if ok1 else "FAIL",
                   f"order={tids} title_ok={bool(p) and p['title'] == P_FIRST} "
                   f"counts=({p['message_count'] if p else '-'},{sc['message_count'] if sc else '-'}) "
                   f"last_activity={bool(p) and bool(p.get('last_activity'))} http={r1.status_code}")

            # ---- T-S78-002 — ownership disjointness (both directions) ----
            rb = await c.get(f"{BASE}/agents/{AGENT}/memory/conversations", headers=bb)
            btids = {x["thread_id"] for x in (rb.json() if rb.status_code == 200 else [])}
            rmA = await c.get(f"{BASE}/me/conversations", headers=ba, params={"limit": 200})
            meA = {x["thread_id"] for x in (rmA.json() if rmA.status_code == 200 else [])}
            rmB = await c.get(f"{BASE}/me/conversations", headers=bb, params={"limit": 200})
            meB = {x["thread_id"] for x in (rmB.json() if rmB.status_code == 200 else [])}
            ok2 = (rb.status_code == 200
                   and T_B in btids and T_P not in btids and T_S not in btids
                   and T_P in meA and T_S in meA and T_B not in meA
                   and T_B in meB and T_P not in meB and T_S not in meB)
            result("T-S78-002", "PASS" if ok2 else "FAIL",
                   f"B_scoped={sorted(btids)} A_owns_only_A={T_P in meA and T_S in meA and T_B not in meA} "
                   f"B_owns_only_B={T_B in meB and T_P not in meB and T_S not in meB}")

            # ---- T-S78-003 — deployment filter + environment derivation ----
            rf = await c.get(f"{BASE}/agents/{AGENT}/memory/conversations", headers=ba,
                             params={"deployment_id": str(prod_dep_id)})
            fconvs = rf.json() if rf.status_code == 200 else []
            ftids = {x["thread_id"] for x in fconvs}
            pf = next((x for x in fconvs if x["thread_id"] == T_P), None)
            p_env = (by.get(T_P) or {}).get("environment")
            s_env = (by.get(T_S) or {}).get("environment")
            ok3 = (rf.status_code == 200 and ftids == {T_P} and pf
                   and pf["environment"] == "production"
                   and str(pf.get("deployment_id")) == str(prod_dep_id)
                   and p_env == "production" and s_env == "sandbox")
            result("T-S78-003", "PASS" if ok3 else "FAIL",
                   f"filtered={sorted(ftids)} prod_env={pf['environment'] if pf else None} "
                   f"sandbox_env={s_env} http={rf.status_code}")

            # ---- T-S78-004 — /me returns both USER_A threads, each with environment ----
            meA_list = rmA.json() if rmA.status_code == 200 else []
            mp = next((x for x in meA_list if x["thread_id"] == T_P), None)
            ms = next((x for x in meA_list if x["thread_id"] == T_S), None)
            ok4 = (rmA.status_code == 200 and mp and ms
                   and mp["environment"] == "production" and ms["environment"] == "sandbox"
                   and mp["agent_name"] == AGENT and ms["agent_name"] == AGENT)
            result("T-S78-004", "PASS" if ok4 else "FAIL",
                   f"me_has_both={bool(mp and ms)} "
                   f"envs=({mp['environment'] if mp else None},{ms['environment'] if ms else None}) "
                   f"http={rmA.status_code}")

            # ---- T-S78-005 — WORKFLOW conversations resolve via the parent run ----
            # The transcript row's agent_name is the MEMBER and its user_id is NULL,
            # so the workflow endpoint must surface the thread for USER_A (owner of
            # the parent run), title = first user message, agent_name = workflow name;
            # USER_B (not the owner) must NOT see it.
            rw = await c.get(f"{BASE}/workflows/{wf_id}/conversations", headers=ba)
            wconvs = rw.json() if rw.status_code == 200 else []
            wf_row = next((x for x in wconvs if x["thread_id"] == T_WF), None)
            rwB = await c.get(f"{BASE}/workflows/{wf_id}/conversations", headers=bb)
            wB_tids = {x["thread_id"] for x in (rwB.json() if rwB.status_code == 200 else [])}
            ok5 = (rw.status_code == 200 and wf_row is not None
                   and wf_row["title"] == WF_FIRST
                   and wf_row["agent_name"] == WF_NAME
                   and wf_row["message_count"] == 2
                   and wf_row.get("last_activity")
                   and T_WF not in wB_tids)          # ownership via the parent run
            result("T-S78-005", "PASS" if ok5 else "FAIL",
                   f"found={wf_row is not None} title_ok={bool(wf_row) and wf_row['title'] == WF_FIRST} "
                   f"name={wf_row['agent_name'] if wf_row else None} "
                   f"count={wf_row['message_count'] if wf_row else None} "
                   f"userB_excluded={T_WF not in wB_tids} http={rw.status_code}")

            # ---- T-S78-006 — WORKFLOW MEMORY entries resolve via the parent run ----
            # The new GET /workflows/{id}/memory returns the member transcript ENTRIES
            # (agent_name=MEMBER, scope=workflow_run) for the owner of the parent run;
            # ?thread_id returns that one thread oldest-first; USER_B (not owner) sees [].
            rm6 = await c.get(f"{BASE}/workflows/{wf_id}/memory", headers=ba)
            m6 = rm6.json() if rm6.status_code == 200 else []
            m6_all_wf = bool(m6) and all(
                x["thread_id"] == T_WF and x["agent_name"] == WF_MEMBER
                and x["scope"] == "workflow_run" for x in m6)
            first_user = next((x for x in m6 if x["role"] == "user"), None)
            rm6t = await c.get(f"{BASE}/workflows/{wf_id}/memory", headers=ba,
                               params={"thread_id": T_WF})
            m6t = rm6t.json() if rm6t.status_code == 200 else []
            idx_ordered = [x["message_index"] for x in m6t]
            rm6B = await c.get(f"{BASE}/workflows/{wf_id}/memory", headers=bb)
            m6B = rm6B.json() if rm6B.status_code == 200 else []
            ok6 = (rm6.status_code == 200 and len(m6) >= 2 and m6_all_wf
                   and first_user is not None and first_user["content"] == WF_FIRST
                   and rm6t.status_code == 200 and len(m6t) == 2
                   and idx_ordered == sorted(idx_ordered)
                   and rm6B.status_code == 200 and len(m6B) == 0)
            result("T-S78-006", "PASS" if ok6 else "FAIL",
                   f"count={len(m6)} all_wf={m6_all_wf} "
                   f"first_ok={bool(first_user) and first_user['content'] == WF_FIRST} "
                   f"thread_order={idx_ordered} userB_empty={len(m6B) == 0} http={rm6.status_code}")
    except Exception as e:
        result("T-S78-SEED", "FAIL", f"unexpected error during seed/asserts: {e!r}")
    finally:
        # Tear down: memory rows (safety-net over ALL s78-% threads), prod chain, agent.
        try:
            async with AsyncSessionLocal() as s:
                await s.execute(delete(AgentMemory).where(AgentMemory.thread_id.like("s78-%")))
                # Workflow fixture: the parent run then the workflow row.
                await s.execute(delete(AgentRun).where(AgentRun.session_id.like("s78-%")))
                if wf_id:
                    await s.execute(delete(CompositeWorkflow).where(CompositeWorkflow.id == wf_id))
                if dep_id:
                    await s.execute(delete(ProductionDeployment).where(ProductionDeployment.id == dep_id))
                if ver_id:
                    await s.execute(delete(PublishedVersion).where(PublishedVersion.id == ver_id))
                if art_id:
                    await s.execute(delete(PublishedArtifact).where(PublishedArtifact.id == art_id))
                await s.commit()
        except Exception:
            pass
        try:
            async with httpx.AsyncClient(timeout=30) as c:
                await c.delete(f"{BASE}/agents/{AGENT}", headers=hdr_a)
        except Exception:
            pass


asyncio.run(main())
print("FAILS", ",".join(fails) if fails else "NONE")
PY
) || { echo "$RESULT"; echo "FATAL: in-pod block errored"; exit 1; }

echo "$RESULT"
PASSED=$(echo "$RESULT" | grep -c "PASS" || true)
FAILED=$(echo "$RESULT" | grep -c " FAIL " || true)
SKIPPED=$(echo "$RESULT" | grep -c " SKIP " || true)
echo "==> Suite 78 Results: ${PASSED} passed, ${FAILED} failed, ${SKIPPED} skipped"
echo "$RESULT" | grep -q "^FAILS NONE" || { echo "SUITE 78 FAILED"; exit 1; }
echo "OK: suite-78 all green"
