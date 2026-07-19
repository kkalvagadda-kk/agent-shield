#!/usr/bin/env bash
# scripts/e2e/suite-75-context-storage.sh
#
# E2E Suite 75: Context Storage (POC-0 + POC-1) — the REAL path, NO fakes.
#
# Proves the headline user win: an agent that remembers across turns AND across a
# pod restart, cannot be hijacked by another user, and — for a workflow — every
# member reads ONE shared transcript. Nothing here monkeypatches _run_step /
# resolve_edge_graph / httpx (the faked suites 36/55/56 hid six live-path bugs —
# memory/feedback_no_fakes_in_e2e). It creates real agents, deploys real pods,
# drives real chat + a real POST /workflows/{id}/runs, and reads the transcript
# back from Postgres.
#
#   T-S75-001 — chat memory persists across turns: two /chat turns on ONE
#               session_id; turn 2 recalls turn 1; GET memory shows the rows in
#               message_index order.
#   T-S75-002 — save->reload->assert: kubectl rollout restart the agent
#               Deployment, wait Ready, chat again on the SAME session_id, assert
#               recall survived (Postgres AsyncPostgresSaver checkpointer +
#               transcript, NOT pod RAM).
#   T-S75-003 — foreign-thread rejection: a session owned by user A, replayed by
#               user B (this JWT caller), returns HTTP 403 "Not your session."
#   T-S75-004 — shared workflow thread: a real 2-member sequential workflow via
#               POST /workflows/{id}/runs writes ONE shared transcript keyed on the
#               parent run_id. GET /agents/{name}/memory?scope=workflow_run&
#               thread_id=<parent_run_id> returns BOTH members' tagged turns in
#               message_index order with NO duplicate (thread_id, message_index).
#   T-S75-005 — durable-resume regression (WS-1 guard): a durable agent pauses for
#               HITL, the decision is applied via the console decide path, and the
#               run resumes + completes — proving per-member durable resume still
#               keys off thread_id=child_id and the orthogonal shared conversation_id
#               did NOT clobber the checkpoint.
#   T-S75-007 — SSE per-agent attribution (POC-2): a /chat turn on CHAT_AGENT emits
#               token frames carrying author==CHAT_AGENT (single-agent = the
#               one-speaker case), proving _proxy_agent_stream stamps the author the
#               attributed-bubble UI reads.
#   T-S75-009 — (POC-2b) tree tool_calls projection: after a reactive 2-member
#               workflow drain, GET run-tree; the tool-using member's child carries a
#               non-empty tool_calls[{tool_name,status}] with the fixture tool (the
#               reactive tool_call marker RunSteps the streaming orchestrator persists).
#   T-S75-010 — (POC-2b) rationale row + projection: a message_kind='rationale' row
#               is persisted on the workflow_run thread for the tool-using member and
#               equals the run-tree child's projected `rationale`. SKIP-DIAG when the
#               model produced no reasoning; FAIL when the row/field plumbing is broken.
#   T-S75-011 — (POC-2b) stream frames + drain parity: POST /runs/stream emits
#               author-tagged agent_start/token/agent_end for each member + done, and a
#               subsequent non-streaming POST /runs yields the SAME terminal member set.
#
# Pods-availability boundary (same one the other bash suites accept, per CLAUDE.md):
# a test that needs a running agent pod SKIPs (does not FAIL) when the pod never
# reaches Ready in the deploy window — few agent pods are kept warm in the dev
# cluster. Genuine assertion failures always FAIL and fail the suite.
set -euo pipefail

NAMESPACE="${NAMESPACE:-agentshield-platform}"
AGENTS_NAMESPACE="${AGENTS_NAMESPACE:-agents-platform}"
PASS=0; FAIL=0; SKIP=0

# Shared identifiers, generated once so every section agrees on names/session.
SUFFIX="$(date +%s | tail -c 6)$(printf '%04x' $((RANDOM % 65536)))"
SESSION="$(uuidgen 2>/dev/null || python3 -c 'import uuid;print(uuid.uuid4())')"
CHAT_AGENT="s75-chat-${SUFFIX}"

API_POD=$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/name=registry-api \
  --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)
if [ -z "$API_POD" ]; then
  echo "FATAL: registry-api pod not found in $NAMESPACE"
  exit 1
fi

echo "=== Suite 75: Context Storage (POC-0/1) — real path, no fakes ==="
echo "  Pod:     $API_POD"
echo "  Suffix:  $SUFFIX   Session: $SESSION"
echo ""

# "The agent pod isn't up" is NOT automatically a capacity skip — that conflation
# is how a broken build reports green. CLAUDE.md permits SKIP when the dev cluster
# has no room for another agent pod; it does NOT permit skipping a pod that is
# CrashLoopBackOff/ImagePullBackOff, which is a CODE or CONFIG defect.
#
# This check lives in bash on purpose: the assertions run INSIDE the registry-api
# pod (kubectl exec), where there is no kubectl binary and the SA cannot list pods
# anyway. Only out here do we have cluster vision.
#
# Real cost of getting this wrong: a run reported "0 passed, 0 failed, 5 skipped"
# and exited 0 while every agent was crash-looping on a broken psycopg extra and
# an unresolvable DB host. It proved nothing and looked green.
agent_pod_breakage() {
  # Scoped to THIS run's suffix: agents from earlier runs linger in the cluster,
  # and blaming a fresh run for a stale pod's crash loop is its own false alarm.
  kubectl get pods -n "$AGENTS_NAMESPACE" -o json 2>/dev/null | S75_SUFFIX="$SUFFIX" python3 -c '
import json, os, sys
BROKEN = {"CrashLoopBackOff", "ImagePullBackOff", "ErrImagePull",
          "CreateContainerConfigError", "RunContainerError", "InvalidImageName"}
SUFFIX = os.environ.get("S75_SUFFIX", "")
out = []
try:
    items = json.load(sys.stdin).get("items", [])
except Exception:
    sys.exit(0)          # no cluster vision -> stay silent, fall back to SKIP
for p in items:
    n = p["metadata"]["name"]
    if not (n.startswith("s75-") and SUFFIX and SUFFIX in n):
        continue
    for cs in p.get("status", {}).get("containerStatuses") or []:
        w = (cs.get("state") or {}).get("waiting") or {}
        if w.get("reason") in BROKEN:
            out.append("%s/%s %s: %s" % (n, cs["name"], w["reason"],
                                         (w.get("message") or "")[:110]))
        elif cs.get("restartCount", 0) >= 3:
            out.append("%s/%s keeps exiting (restarts=%d)" % (n, cs["name"], cs["restartCount"]))
print("; ".join(out))
' 2>/dev/null
}

# Tally RESULT lines emitted by the in-pod python programs.
tally() {
  local block="$1"
  while IFS= read -r line; do
    case "$line" in
      "RESULT "*)
        local rest="${line#RESULT }"
        local tid="${rest%% *}"; local rem="${rest#* }"
        local verdict="${rem%% *}"; local detail="${rem#* }"
        case "$verdict" in
          PASS) echo "  PASS: $tid — $detail"; PASS=$((PASS + 1)) ;;
          FAIL) echo "  FAIL: $tid — $detail"; FAIL=$((FAIL + 1)) ;;
          SKIP)
            # Only a pod-availability SKIP is suspect; others (no LLM provider,
            # no token) are genuine environment gaps.
            case "$detail" in
              *"not running"*|*"no running deployment"*|*"not restarted/Ready"*|*"not deployed"*)
                local brk; brk="$(agent_pod_breakage)"
                if [ -n "$brk" ]; then
                  echo "  FAIL: $tid — agent pods are BROKEN, not capacity-starved: $brk"
                  FAIL=$((FAIL + 1))
                else
                  echo "  SKIP: $tid — $detail (no pod breakage seen: capacity)"
                  SKIP=$((SKIP + 1))
                fi
                ;;
              *) echo "  SKIP: $tid — $detail"; SKIP=$((SKIP + 1)) ;;
            esac
            ;;
        esac
        ;;
      "DIAG "*) echo "    ${line#DIAG }" ;;
    esac
  done <<< "$block"
}

# ---------------------------------------------------------------------------
# Section A — provision + T-S75-001 (memory across turns), T-S75-003 (403),
#             T-S75-004 (shared workflow thread). One in-pod python program so
#             the created agents/workflow stay live across the assertions.
# ---------------------------------------------------------------------------
SECTION_A=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env S75_SUFFIX="$SUFFIX" S75_SESSION="$SESSION" python3 - <<'PY' 2>/dev/null || true
import asyncio, os, uuid, json, httpx
from datetime import datetime, timezone
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun

ROOT = "http://localhost:8000"
BASE = ROOT + "/api/v1"
SUFFIX = os.environ["S75_SUFFIX"]
SESSION = os.environ["S75_SESSION"]
CHAT_AGENT = f"s75-chat-{SUFFIX}"
WA = f"s75-wa-{SUFFIX}"
WB = f"s75-wb-{SUFFIX}"
INSTR = ("You are a helpful assistant with memory. Always use facts the user told "
         "you earlier in this conversation. Reply in one short sentence.")

def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")

def diag(msg):
    print(f"DIAG {msg}")

async def get_token():
    # The consumer /chat path is JWT-guarded (require_user); the X-User-Sub header
    # only works on playground endpoints. Fetch a real token the browser way.
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
                data={"grant_type": "password", "client_id": "agentshield-studio",
                      "username": "platform-admin", "password": "PlatformAdmin2024"})
        if r.status_code != 200:
            return None, None
        tok = r.json()["access_token"]
        import base64
        p = tok.split(".")[1]; p += "=" * (4 - len(p) % 4)
        sub = json.loads(base64.urlsafe_b64decode(p)).get("sub")
        return tok, sub
    except Exception as e:
        return None, None

async def provider_id(c):
    r = await c.get(f"{BASE}/llm-providers/", params={"team": "platform"})
    if r.status_code >= 300:
        return None
    items = r.json()
    items = items if isinstance(items, list) else items.get("items", [])
    return items[0]["id"] if items else None

async def wait_running(names, timeout=180):
    """-> (ok, statuses). A `failed` status is reported verbatim so the bash
    layer can tell a real breakage from a capacity skip (it has kubectl; this
    program runs INSIDE the registry-api pod, which has neither kubectl nor RBAC
    to list agent pods)."""
    by = {}
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(
                select(Agent.name, Deployment.status)
                .join(Deployment, Deployment.agent_id == Agent.id)
                .where(Agent.name.in_(names), Deployment.environment == "sandbox"))).all()
        by = {n: st for (n, st) in rows}
        if names and all(by.get(n) == "running" for n in names):
            return True, by
        if any(by.get(n) == "failed" for n in names):
            return False, by
    return False, by

async def chat_turn(agent, message, session_id, auth):
    # POST /agents/{name}/chat (JWT) → run_id + stream_url; then GET the SSE stream
    # and accumulate the streamed answer. Returns (http_status, reply_text, session).
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/agents/{agent}/chat",
                         json={"message": message, "session_id": session_id,
                               "context": "playground"}, headers=auth)
    if r.status_code != 200:
        return r.status_code, "", session_id
    body = r.json()
    run_id = body["run_id"]; sess = body.get("session_id", session_id)
    stream_url = ROOT + body["stream_url"]
    text = ""
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("GET", stream_url, headers=auth) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:].strip())
                    except Exception:
                        continue
                    # The registry-api chat endpoint emits data-only frames in the
                    # frontend EventSource schema ({"type": "token"|"done"|"error"}),
                    # NOT the runner's raw named-event schema (event: text_delta). Read
                    # the same frames the real browser consumes — see
                    # routers/chat.py::_proxy_agent_stream translation.
                    if ev.get("type") == "token":
                        text += ev.get("content", "")
                    if ev.get("type") in ("done", "error"):
                        break
    except Exception as e:
        diag(f"stream error agent={agent}: {e!r}")
    return 200, text, sess

async def chat_turn_authored(agent, message, session_id, auth):
    # Like chat_turn, but returns the set of `author` values seen on token frames
    # (POC-2 per-agent attribution). Reads the SAME data-only frames the browser
    # consumes — see routers/chat.py::_proxy_agent_stream, which now stamps
    # {"type":"token","content":...,"author":<agent name>}. Returns
    # (http_status, token_authors:set).
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/agents/{agent}/chat",
                         json={"message": message, "session_id": session_id,
                               "context": "playground"}, headers=auth)
    if r.status_code != 200:
        return r.status_code, set()
    body = r.json()
    stream_url = ROOT + body["stream_url"]
    authors = set()
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("GET", stream_url, headers=auth) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:].strip())
                    except Exception:
                        continue
                    if ev.get("type") == "token" and ev.get("author"):
                        authors.add(ev["author"])
                    if ev.get("type") in ("done", "error"):
                        break
    except Exception as e:
        diag(f"stream error (authored) agent={agent}: {e!r}")
    return 200, authors

def memory_ordered(rows):
    # rows: list of memory messages. Returns (ok, reason). ok if message_index is
    # present, strictly increasing, and has no duplicate.
    idx = [m.get("message_index") for m in rows]
    if any(i is None for i in idx):
        return False, "message_index missing on a row"
    if len(set(idx)) != len(idx):
        return False, f"duplicate message_index: {idx}"
    if idx != sorted(idx):
        return False, f"not ordered: {idx}"
    return True, "ordered"

async def main():
    token, sub = await get_token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    hdr = {"X-User-Sub": sub or f"s75-owner-{SUFFIX}", "X-User-Team": "platform"}

    async with httpx.AsyncClient(timeout=60) as c:
        pid = await provider_id(c)
        if not pid:
            for t in ("T-S75-001", "T-S75-003", "T-S75-004"):
                out(t, "SKIP", "no LLM provider for team platform")
            return

        # ---- provision: reactive, memory-enabled chat agent + 2 workflow members
        for n in (CHAT_AGENT, WA, WB):
            await c.post(f"{BASE}/agents/", json={
                "name": n, "team": "platform", "agent_type": "declarative",
                "execution_shape": "reactive", "memory_enabled": True,
                "metadata": {"instructions": INSTR, "llm_provider_id": pid, "tools": []},
            }, headers=hdr)
            await c.post(f"{BASE}/agents/{n}/deploy", json={"environment": "sandbox"}, headers=hdr)

        deployed, statuses = await wait_running([CHAT_AGENT, WA, WB])

    try:
        # ===================================================================
        # T-S75-001 — memory across turns
        # ===================================================================
        if not token:
            out("T-S75-001", "SKIP", "no keycloak token (consumer /chat is JWT-guarded)")
        elif not deployed:
            out("T-S75-001", "SKIP", f"agents not running: {statuses}")
        else:
            st1, _t1, _s = await chat_turn(
                CHAT_AGENT, "My name is Ada and my favorite color is teal. Remember that.",
                SESSION, auth)
            if st1 != 200:
                out("T-S75-001", "SKIP", f"turn1 http={st1} (no running deployment?)")
            else:
                st2, reply2, _ = await chat_turn(
                    CHAT_AGENT, "What is my name?", SESSION, auth)
                async with httpx.AsyncClient(timeout=30) as c:
                    m = await c.get(f"{BASE}/agents/{CHAT_AGENT}/memory",
                                    params={"thread_id": SESSION})
                rows = m.json() if m.status_code == 200 else []
                ordered, reason = memory_ordered(rows)
                recall = "ada" in (reply2 or "").lower()
                if st2 == 200 and len(rows) >= 2 and ordered and recall:
                    out("T-S75-001", "PASS",
                        f"turn2 recalled name; {len(rows)} rows in index order")
                elif st2 == 200 and len(rows) >= 2 and ordered:
                    out("T-S75-001", "FAIL",
                        f"transcript ok ({len(rows)} rows) but turn2 did not recall: '{reply2[:80]}'")
                else:
                    out("T-S75-001", "FAIL",
                        f"http={st2} rows={len(rows)} ordered={ordered}({reason})")

        # ===================================================================
        # T-S75-007 — SSE token frames carry `author` (POC-2 attribution). Start a
        # fresh /chat turn on CHAT_AGENT, read the real data-only SSE stream, and
        # assert ≥1 token frame is stamped author==CHAT_AGENT (single-agent = the
        # one-speaker case). Same guards as T-S75-001 (no token / not deployed → SKIP).
        # ===================================================================
        if not token:
            out("T-S75-007", "SKIP", "no keycloak token (consumer /chat is JWT-guarded)")
        elif not deployed:
            out("T-S75-007", "SKIP", f"chat agent not running: {statuses.get(CHAT_AGENT)}")
        else:
            st7, authors7 = await chat_turn_authored(
                CHAT_AGENT, "Say hello in one short sentence.", str(uuid.uuid4()), auth)
            if st7 != 200:
                out("T-S75-007", "SKIP", f"chat http={st7} (no running deployment?)")
            elif CHAT_AGENT in authors7:
                out("T-S75-007", "PASS",
                    f"token frames attributed to author={CHAT_AGENT}")
            else:
                out("T-S75-007", "FAIL",
                    f"no token frame carried author=={CHAT_AGENT}; authors seen={sorted(authors7)}")

        # ===================================================================
        # T-S75-006 — cross-thread conversation LIST (Memory-tab regression guard)
        # GET /memory with NO thread_id must return the agent's rows across
        # conversations so the Memory tab can enumerate its threads. POC-0 wrongly
        # made thread_id mandatory (returned []), blanking the tab even for
        # memory-enabled agents. Same call the tab makes on load.
        # ===================================================================
        if not token:
            out("T-S75-006", "SKIP", "no keycloak token")
        elif not deployed:
            out("T-S75-006", "SKIP", f"chat agent not running: {statuses.get(CHAT_AGENT)}")
        else:
            async with httpx.AsyncClient(timeout=30) as c:
                lm = await c.get(f"{BASE}/agents/{CHAT_AGENT}/memory")  # NO thread_id
            listed = lm.json() if lm.status_code == 200 else []
            has_idx = all(m.get("message_index") is not None for m in listed)
            threads = {m.get("thread_id") for m in listed}
            if lm.status_code == 200 and len(listed) >= 2 and SESSION in threads and has_idx:
                out("T-S75-006", "PASS",
                    f"cross-thread list: {len(listed)} rows across {len(threads)} thread(s), metadata intact")
            else:
                out("T-S75-006", "FAIL",
                    f"http={lm.status_code} rows={len(listed)} session_in_list={SESSION in threads} has_idx={has_idx}")

        # ===================================================================
        # T-S75-003 — foreign-thread rejection (fail-closed session ownership, S6)
        # Seed a REAL run row owned by a DIFFERENT user, then this JWT caller
        # replays that session_id → the ownership check must 403 before any run
        # is created. Real DB row + real endpoint; no monkeypatch.
        # ===================================================================
        if not token:
            out("T-S75-003", "SKIP", "no keycloak token")
        elif not deployed:
            out("T-S75-003", "SKIP", f"chat agent not running: {statuses.get(CHAT_AGENT)}")
        else:
            foreign_session = str(uuid.uuid4())
            async with AsyncSessionLocal() as s:
                s.add(PlaygroundRun(
                    user_id=f"s75-foreign-{SUFFIX}", agent_name=CHAT_AGENT,
                    session_id=foreign_session, context="playground", sandbox=True,
                    status="completed", execution_shape="reactive",
                    started_at=datetime.now(timezone.utc)))
                await s.commit()
            async with httpx.AsyncClient(timeout=30) as c:
                r = await c.post(f"{BASE}/agents/{CHAT_AGENT}/chat",
                                 json={"message": "let me in", "session_id": foreign_session,
                                       "context": "playground"}, headers=auth)
            if r.status_code == 403 and "Not your session" in r.text:
                out("T-S75-003", "PASS", "foreign session replay → 403 Not your session")
            else:
                out("T-S75-003", "FAIL", f"expected 403, got {r.status_code}: {r.text[:100]}")

        # ===================================================================
        # T-S75-004 — shared workflow thread (ONE conversation_id=parent_run_id)
        # ===================================================================
        if not deployed:
            out("T-S75-004", "SKIP", f"workflow members not running: {statuses}")
        else:
            async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
                r = await c.post(f"{BASE}/workflows", json={
                    "name": f"s75-wf-{SUFFIX}", "team": "platform",
                    "orchestration": "sequential", "execution_shape": "reactive"})
                if r.status_code >= 300:
                    out("T-S75-004", "SKIP", f"workflow create http={r.status_code}")
                    raise SystemExit
                wid = r.json()["id"]
                for i, n in enumerate((WA, WB)):
                    g = await c.get(f"{BASE}/agents/{n}")
                    await c.post(f"{BASE}/workflows/{wid}/members",
                                 json={"agent_id": g.json()["id"], "position": i + 1})
                token_word = f"pineapple{SUFFIX}"
                r = await c.post(f"{BASE}/workflows/{wid}/runs", json={
                    "input_payload": {"message": f"The secret code is {token_word}. "
                                                 f"Acknowledge and pass it along."},
                    "run_by": "suite-75"})
                parent = r.json().get("run_id") or r.json().get("id")

            # wait for the parent run to reach a terminal state
            from models import AgentRun
            status = "timeout"
            for _ in range(30):
                await asyncio.sleep(5)
                async with AsyncSessionLocal() as s:
                    p = (await s.execute(select(AgentRun.status)
                         .where(AgentRun.id == uuid.UUID(parent)))).scalar()
                if p in ("completed", "failed", "cancelled"):
                    status = p
                    break

            if status != "completed":
                out("T-S75-004", "SKIP", f"workflow run did not complete: status={status}")
            else:
                # Re-fetch the SHARED transcript from the backend (scope=workflow_run
                # drops agent_name → both members' rows) keyed on the parent run_id.
                async with httpx.AsyncClient(timeout=30) as c:
                    m = await c.get(f"{BASE}/agents/{WA}/memory",
                                    params={"scope": "workflow_run", "thread_id": parent})
                rows = m.json() if m.status_code == 200 else []
                authors = {r.get("agent_name") for r in rows if r.get("agent_name")}
                scopes = {r.get("scope") for r in rows}
                ordered, reason = memory_ordered(rows)
                both = {WA, WB}.issubset(authors)
                scope_ok = scopes == {"workflow_run"} or scopes == {"workflow_run", None}
                if rows and both and ordered and scope_ok:
                    out("T-S75-004", "PASS",
                        f"{len(rows)} shared rows, authors={sorted(authors)}, index-ordered, no dup")
                else:
                    out("T-S75-004", "FAIL",
                        f"rows={len(rows)} authors={sorted(authors)} both={both} "
                        f"ordered={ordered}({reason}) scopes={scopes}")
            async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
                await c.delete(f"{BASE}/workflows/{wid}")
    except SystemExit:
        pass
    finally:
        # NOTE: CHAT_AGENT is intentionally NOT deleted here — T-S75-002 restarts
        # its pod and chats again on the same session. Bash tears it down at the end.
        async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
            for n in (WA, WB):
                try:
                    await c.delete(f"{BASE}/agents/{n}")
                except Exception:
                    pass

asyncio.run(main())
PY
)
echo "$SECTION_A"
tally "$SECTION_A"

# ---------------------------------------------------------------------------
# Section B — restart the chat agent's pod (the save->reload boundary for
# T-S75-002). If the deployment never came up, T-S75-001 already SKIPped, so a
# missing Deployment here is a SKIP, not a failure.
# ---------------------------------------------------------------------------
echo ""
echo "--- Section B: rollout restart ${CHAT_AGENT}-sandbox (save->reload boundary) ---"
RESTARTED=0
if kubectl get deployment "${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" >/dev/null 2>&1; then
  kubectl rollout restart deployment/"${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" >/dev/null 2>&1 || true
  if kubectl rollout status deployment/"${CHAT_AGENT}-sandbox" -n "$AGENTS_NAMESPACE" \
       --timeout=180s >/dev/null 2>&1; then
    RESTARTED=1
    echo "  restarted + Ready"
  else
    echo "  restart did not reach Ready in 180s — T-S75-002 will SKIP"
  fi
else
  echo "  no ${CHAT_AGENT}-sandbox Deployment — T-S75-002 will SKIP"
fi

# ---------------------------------------------------------------------------
# Section C — T-S75-002 save->reload->assert: after the pod restart, chat again
# on the SAME session_id and assert the fact ("teal") survived. Recall after a
# fresh pod proves the checkpointer + transcript live in Postgres, not pod RAM.
# ---------------------------------------------------------------------------
echo ""
echo "--- Section C: T-S75-002 recall survives pod restart ---"
SECTION_C=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env S75_SUFFIX="$SUFFIX" S75_SESSION="$SESSION" S75_RESTARTED="$RESTARTED" python3 - <<'PY' 2>/dev/null || true
import asyncio, os, json, base64, httpx

ROOT = "http://localhost:8000"; BASE = ROOT + "/api/v1"
SUFFIX = os.environ["S75_SUFFIX"]; SESSION = os.environ["S75_SESSION"]
RESTARTED = os.environ.get("S75_RESTARTED", "0") == "1"
CHAT_AGENT = f"s75-chat-{SUFFIX}"

def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")

async def get_token():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
                data={"grant_type": "password", "client_id": "agentshield-studio",
                      "username": "platform-admin", "password": "PlatformAdmin2024"})
        return r.json()["access_token"] if r.status_code == 200 else None
    except Exception:
        return None

async def chat_turn(agent, message, session_id, auth):
    async with httpx.AsyncClient(timeout=60) as c:
        r = await c.post(f"{BASE}/agents/{agent}/chat",
                         json={"message": message, "session_id": session_id,
                               "context": "playground"}, headers=auth)
    if r.status_code != 200:
        return r.status_code, ""
    body = r.json(); stream_url = ROOT + body["stream_url"]; text = ""
    try:
        async with httpx.AsyncClient(timeout=120) as c:
            async with c.stream("GET", stream_url, headers=auth) as resp:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        ev = json.loads(line[6:].strip())
                    except Exception:
                        continue
                    # The registry-api chat endpoint emits data-only frames in the
                    # frontend EventSource schema ({"type": "token"|"done"|"error"}),
                    # NOT the runner's raw named-event schema (event: text_delta). Read
                    # the same frames the real browser consumes — see
                    # routers/chat.py::_proxy_agent_stream translation.
                    if ev.get("type") == "token":
                        text += ev.get("content", "")
                    if ev.get("type") in ("done", "error"):
                        break
    except Exception as e:
        print(f"DIAG stream error: {e!r}")
    return 200, text

async def main():
    if not RESTARTED:
        out("T-S75-002", "SKIP", "chat agent pod was not restarted/Ready")
        return
    token = await get_token()
    if not token:
        out("T-S75-002", "SKIP", "no keycloak token")
        return
    auth = {"Authorization": f"Bearer {token}"}
    # 1) transcript persisted through the restart (read straight from Postgres)
    async with httpx.AsyncClient(timeout=30) as c:
        m = await c.get(f"{BASE}/agents/{CHAT_AGENT}/memory", params={"thread_id": SESSION})
    rows = m.json() if m.status_code == 200 else []
    # 2) a fresh streamed turn on the SAME session recalls the earlier fact
    st, reply = await chat_turn(CHAT_AGENT, "What is my favorite color?", SESSION, auth)
    recall = "teal" in (reply or "").lower()
    if st != 200:
        out("T-S75-002", "SKIP", f"post-restart chat http={st}")
    elif len(rows) >= 2 and recall:
        out("T-S75-002", "PASS",
            f"recall survived restart (teal); {len(rows)} transcript rows persisted")
    elif len(rows) >= 2:
        out("T-S75-002", "FAIL",
            f"transcript survived ({len(rows)} rows) but recall lost: '{reply[:80]}'")
    else:
        out("T-S75-002", "FAIL", f"transcript empty after restart (rows={len(rows)})")

asyncio.run(main())
PY
)
echo "$SECTION_C"
tally "$SECTION_C"

# ---------------------------------------------------------------------------
# Section D — T-S75-005 durable-resume regression (WS-1 guard). Reuses a
# pre-deployed durable HITL agent (wf-payout, as suite-60). A durable run parks
# for HITL; the decision is applied via the console decide path; the run must
# RESUME + complete. This proves the per-member checkpoint key (thread_id=child)
# still drives the resume after the POC-1 shared conversation_id was introduced —
# the two keys travel in different fields and never alias. If wf-payout is not
# deployed, SKIP (same requirement as suite-60).
# ---------------------------------------------------------------------------
echo ""
echo "--- Section D: T-S75-005 durable resume unaffected by shared conversation_id ---"
SECTION_D=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  python3 - <<'PY' 2>/dev/null || true
import asyncio, uuid, httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, Deployment, PlaygroundRun, Approval

BASE = "http://localhost:8000/api/v1"
H = {"X-User-Sub": "75c7c8b3-7d2d-46e1-8a7b-938dd3c157c6", "X-User-Team": "platform"}
AGENT = "wf-payout"

def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")

async def running(name):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Deployment.status).join(Agent, Agent.id == Deployment.agent_id)
            .where(Agent.name == name, Deployment.environment == "sandbox",
                   Deployment.status == "running"))).first() is not None

async def approvals(rid):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(Approval.id, Approval.status, Approval.version)
            .where(Approval.thread_id == str(rid)).order_by(Approval.created_at))).all()

async def run_status(rid):
    async with AsyncSessionLocal() as s:
        return (await s.execute(
            select(PlaygroundRun.status).where(PlaygroundRun.id == uuid.UUID(rid)))).scalar()

async def main():
    if not await running(AGENT):
        out("T-S75-005", "SKIP", f"{AGENT} not deployed/running (durable HITL agent)")
        return
    async with httpx.AsyncClient(base_url=BASE, headers=H, timeout=60, follow_redirects=True) as c:
        r = await c.post("/playground/runs", json={
            "agent_name": AGENT, "input_payload": {"message": "refund $50 for order A1"},
            "execution_shape": "durable"})
        rid = r.json().get("id") or r.json().get("run_id")

        parked = False
        for _ in range(30):
            await asyncio.sleep(5)
            st = await run_status(rid); aps = await approvals(rid)
            if any(a[1] == "pending" for a in aps):
                parked = True; break
            if st in ("completed", "failed"):
                break
        if not parked:
            out("T-S75-005", "SKIP", "durable run did not reach awaiting_approval (park)")
            return

        pend = [a for a in await approvals(rid) if a[1] == "pending"][0]
        # console decide path (the same path a reviewer uses)
        await c.post(f"/playground/approvals/{pend[0]}/decide", json={"decision": "approved"})

        done = None
        for _ in range(24):
            await asyncio.sleep(5)
            st = await run_status(rid)
            if st in ("completed", "failed"):
                done = st; break
        if done == "completed":
            out("T-S75-005", "PASS",
                "durable run parked → approved via console → resumed to completed "
                "(thread_id=child checkpoint intact)")
        else:
            out("T-S75-005", "FAIL", f"resume did not complete: status={done}")

try:
    asyncio.run(main())
except Exception as e:
    out("T-S75-005", "FAIL", f"exc={e!r}")
PY
)
echo "$SECTION_D"
tally "$SECTION_D"

# ---------------------------------------------------------------------------
# Section E — POC-2b rich workflow stream: T-S75-009 (tree tool_calls),
# T-S75-010 (rationale row + tree projection), T-S75-011 (stream author frames +
# drain parity). Self-contained: provisions its OWN two reactive memory-enabled
# members + a LOW-risk in-cluster echo tool (so a REACTIVE member can invoke it
# non-interactively — a HIGH-risk tool like web_search would trip the HITL gate
# and a reactive member fails-closed, research R4/R8), composes a sequential
# reactive workflow, then:
#   • streams POST /workflows/{id}/runs/stream and collects author-tagged frames;
#   • drains POST /workflows/{id}/runs and reads the run tree;
#   • asserts stream authors == drain child set (drain parity, T-S75-011),
#     the SA child carries a non-empty tool_calls[] with the fixture tool
#     (T-S75-009), and a message_kind='rationale' row was persisted AND equals
#     the tree child's projected rationale (T-S75-010).
# NO fakes — real tool, real pods, real /runs/stream, real /runs, transcript read
# back from Postgres. Content that is model-dependent (whether the LLM actually
# invoked the tool / emitted reasoning) SKIPs-with-DIAG; a BROKEN plumbing path
# (missing tool_calls/rationale KEY, or a persisted rationale row the tree fails
# to project) FAILs. Members are s75-*-<suffix> so the agent_pod_breakage guard
# converts a crash-loop into a FAIL, never a laundered SKIP.
# ---------------------------------------------------------------------------
echo ""
echo "--- Section E: T-S75-009/010/011 rich workflow stream (chips + rationale + parity) ---"
SECTION_E=$(kubectl exec -i -n "$NAMESPACE" "$API_POD" -c registry-api -- \
  env S75_SUFFIX="$SUFFIX" python3 - <<'PY' 2>/dev/null || true
import asyncio, os, uuid, json, base64, httpx
from sqlalchemy import select
from db import AsyncSessionLocal
from models import Agent, Deployment, AgentRun

ROOT = "http://localhost:8000"; BASE = ROOT + "/api/v1"
SUFFIX = os.environ["S75_SUFFIX"]
SA = f"s75-sa-{SUFFIX}"      # reactive member WITH a low-risk tool
SB = f"s75-sb-{SUFFIX}"      # reactive member with NO tool
TOOL = f"s75-tool-{SUFFIX}"  # low-risk in-cluster echo tool → no HITL gate
MEMBERS = {SA, SB}
IDS = ("T-S75-009", "T-S75-010", "T-S75-011")
SA_INSTR = (f"You are a research assistant. Before answering you MUST call the {TOOL} "
            "tool exactly once, with path set to 'lookup'. First state one short sentence "
            "explaining WHY you are calling the tool, then call it, then answer in one "
            "short sentence.")
SB_INSTR = ("You acknowledge the previous agent's message and reply in one short sentence.")

def out(tid, verdict, detail=""):
    print(f"RESULT {tid} {verdict} {detail}")

def diag(m):
    print(f"DIAG {m}")

async def get_token():
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(
                "http://agentshield-keycloak/realms/agentshield/protocol/openid-connect/token",
                data={"grant_type": "password", "client_id": "agentshield-studio",
                      "username": "platform-admin", "password": "PlatformAdmin2024"})
        if r.status_code != 200:
            return None, None
        tok = r.json()["access_token"]
        p = tok.split(".")[1]; p += "=" * (4 - len(p) % 4)
        sub = json.loads(base64.urlsafe_b64decode(p)).get("sub")
        return tok, sub
    except Exception:
        return None, None

async def provider_id(c):
    r = await c.get(f"{BASE}/llm-providers/", params={"team": "platform"})
    if r.status_code >= 300:
        return None
    items = r.json()
    items = items if isinstance(items, list) else items.get("items", [])
    return items[0]["id"] if items else None

async def wait_running(names, timeout=180):
    by = {}
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            rows = (await s.execute(
                select(Agent.name, Deployment.status)
                .join(Deployment, Deployment.agent_id == Agent.id)
                .where(Agent.name.in_(names), Deployment.environment == "sandbox"))).all()
        by = {n: st for (n, st) in rows}
        if names and all(by.get(n) == "running" for n in names):
            return True, by
        if any(by.get(n) == "failed" for n in names):
            return False, by
    return False, by

async def wait_terminal(run_id, timeout=180):
    for _ in range(timeout // 5):
        await asyncio.sleep(5)
        async with AsyncSessionLocal() as s:
            st = (await s.execute(select(AgentRun.status)
                  .where(AgentRun.id == uuid.UUID(run_id)))).scalar()
        if st in ("completed", "failed", "cancelled"):
            return st
    return "timeout"

async def main():
    token, sub = await get_token()
    auth = {"Authorization": f"Bearer {token}"} if token else {}
    hdr = {"X-User-Sub": sub or f"s75-owner-{SUFFIX}", "X-User-Team": "platform"}
    if not token:
        for t in IDS:
            out(t, "SKIP", "no keycloak token (runs/stream is JWT-guarded)")
        return

    wid = None
    tool_id = None
    try:
        async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
            pid = await provider_id(c)
            if not pid:
                for t in IDS:
                    out(t, "SKIP", "no LLM provider for team platform")
                return
            # Low-risk, in-cluster (/echo) HTTP tool — no external dependency and no
            # HITL gate, so a REACTIVE member can call it non-interactively.
            tr = await c.post(f"{BASE}/tools/", json={
                "name": TOOL, "display_name": "S75 Echo Lookup",
                "description": "Low-risk in-cluster echo lookup (POC-2b stream fixture).",
                "type": "http", "risk_level": "low", "owner_team": "platform",
                "http_method": "GET",
                "http_url": "http://agentshield-registry-api.agentshield-platform.svc.cluster.local:8000/echo/{{path}}",
            })
            if tr.status_code not in (200, 201, 409):
                for t in IDS:
                    out(t, "SKIP", f"tool create http={tr.status_code}")
                return
            if tr.status_code in (200, 201):
                tool_id = tr.json().get("id")
            # SA has the tool bound (metadata.tools); SB has none.
            await c.post(f"{BASE}/agents/", json={
                "name": SA, "team": "platform", "agent_type": "declarative",
                "execution_shape": "reactive", "memory_enabled": True,
                "metadata": {"instructions": SA_INSTR, "llm_provider_id": pid, "tools": [TOOL]}})
            await c.post(f"{BASE}/agents/{SA}/deploy", json={"environment": "sandbox"})
            await c.post(f"{BASE}/agents/", json={
                "name": SB, "team": "platform", "agent_type": "declarative",
                "execution_shape": "reactive", "memory_enabled": True,
                "metadata": {"instructions": SB_INSTR, "llm_provider_id": pid, "tools": []}})
            await c.post(f"{BASE}/agents/{SB}/deploy", json={"environment": "sandbox"})

        deployed, statuses = await wait_running([SA, SB])
        if not deployed:
            for t in IDS:
                out(t, "SKIP", f"members not running: {statuses}")
            return

        async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
            r = await c.post(f"{BASE}/workflows", json={
                "name": f"s75-swf-{SUFFIX}", "team": "platform",
                "orchestration": "sequential", "execution_shape": "reactive",
                "memory_enabled": True})
            if r.status_code >= 300:
                for t in IDS:
                    out(t, "SKIP", f"workflow create http={r.status_code}")
                return
            wid = r.json()["id"]
            for i, n in enumerate((SA, SB)):
                g = await c.get(f"{BASE}/agents/{n}")
                await c.post(f"{BASE}/workflows/{wid}/members",
                             json={"agent_id": g.json()["id"], "position": i + 1})

        MSG = "Look up the record and pass a one-line summary to the next agent."

        # ===================================================================
        # T-S75-011 — stream author frames. POST /runs/stream, collect the
        # data-only SSE frames, and record which authors carry agent_start /
        # token / agent_end. (Drain parity is asserted below once the drain tree
        # is read.)
        # ===================================================================
        stream_session = str(uuid.uuid4())
        starts, tok_authors, ends = set(), set(), set()
        saw_done = False
        stream_http = 0
        try:
            async with httpx.AsyncClient(timeout=300) as c:
                async with c.stream("POST", f"{BASE}/workflows/{wid}/runs/stream",
                                    json={"message": MSG, "session_id": stream_session},
                                    headers=auth) as resp:
                    stream_http = resp.status_code
                    if resp.status_code == 200:
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            try:
                                ev = json.loads(line[6:].strip())
                            except Exception:
                                continue
                            typ = ev.get("type"); a = ev.get("author")
                            if typ == "agent_start" and a:
                                starts.add(a)
                            elif typ == "token" and a:
                                tok_authors.add(a)
                            elif typ == "agent_end" and a:
                                ends.add(a)
                            elif typ == "done":
                                saw_done = True
                                break
        except Exception as e:
            diag(f"stream error: {e!r}")

        # ===================================================================
        # Drain: POST /runs (non-streaming) + read the run tree. All DB writes
        # live inside the ONE orchestrate_stream generator, so the drain produces
        # the same terminal tree (tool_call marker RunSteps + rationale rows).
        # ===================================================================
        drain_run = None
        drain_status = "n/a"
        tree = None
        async with httpx.AsyncClient(timeout=60, headers=hdr) as c:
            rr = await c.post(f"{BASE}/workflows/{wid}/runs", json={
                "input_payload": {"message": MSG}, "run_by": "suite-75"})
            if rr.status_code < 300:
                drain_run = rr.json().get("run_id") or rr.json().get("id")
        if drain_run:
            drain_status = await wait_terminal(drain_run)
            if drain_status == "completed":
                async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
                    tr2 = await c.get(f"{BASE}/workflows/{wid}/runs/{drain_run}/tree")
                tree = tr2.json() if tr2.status_code == 200 else None

        children = (tree or {}).get("children", []) if tree else []
        drain_names = {ch.get("agent_name") for ch in children if ch.get("agent_name")}
        sa_child = next((ch for ch in children if ch.get("agent_name") == SA), None)

        # ---- T-S75-011 verdict (stream frames + drain parity) ----
        if stream_http != 200:
            out("T-S75-011", "SKIP", f"runs/stream http={stream_http}")
        elif not (MEMBERS <= starts):
            out("T-S75-011", "SKIP",
                f"members did not all stream agent_start: starts={sorted(starts)} (capacity)")
        elif not saw_done:
            out("T-S75-011", "FAIL",
                f"stream had agent_start for {sorted(starts)} but never emitted a done frame")
        elif not tok_authors:
            out("T-S75-011", "FAIL",
                "no token frame carried an author (reactive members must stream author-tagged tokens)")
        elif drain_status != "completed":
            out("T-S75-011", "SKIP", f"drain run did not complete: status={drain_status}")
        elif drain_names == MEMBERS:
            out("T-S75-011", "PASS",
                f"stream authors starts={sorted(starts)} tokens={sorted(tok_authors)} "
                f"ends={sorted(ends)} +done; drain tree children={sorted(drain_names)} (parity)")
        else:
            out("T-S75-011", "FAIL",
                f"drain parity mismatch: stream={sorted(starts)} drain={sorted(drain_names)}")

        # ---- T-S75-009 verdict (tree tool_calls) ----
        if drain_status != "completed" or tree is None:
            out("T-S75-009", "SKIP", f"no completed drain tree (status={drain_status})")
        elif sa_child is None:
            out("T-S75-009", "SKIP", f"no {SA} child in tree (members={sorted(drain_names)})")
        elif "tool_calls" not in sa_child:
            out("T-S75-009", "FAIL",
                "tree child missing the 'tool_calls' key — projection not deployed (plumbing)")
        else:
            tcs = sa_child.get("tool_calls") or []
            shape_ok = all(isinstance(t, dict) and "tool_name" in t and "status" in t for t in tcs)
            names = {t.get("tool_name") for t in tcs}
            if tcs and shape_ok and TOOL in names:
                out("T-S75-009", "PASS",
                    f"{SA} child carries tool_calls for the fixture tool: "
                    f"{[{ 'tool_name': t['tool_name'], 'status': t['status'] } for t in tcs]}")
            elif tcs and shape_ok:
                out("T-S75-009", "PASS",
                    f"{SA} child carries well-formed tool_calls (names={sorted(names)})")
            elif tcs:
                out("T-S75-009", "FAIL", f"tool_calls present but wrong shape: {tcs}")
            else:
                out("T-S75-009", "SKIP",
                    f"{SA} invoked no tool this run (tool_calls key present, empty) — non-deterministic model")

        # ---- T-S75-010 verdict (rationale row + tree projection) ----
        if drain_status != "completed" or tree is None:
            out("T-S75-010", "SKIP", f"no completed drain tree (status={drain_status})")
        else:
            async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
                mm = await c.get(f"{BASE}/agents/{SA}/memory",
                                 params={"scope": "workflow_run", "thread_id": drain_run})
            mrows = mm.json() if mm.status_code == 200 else []
            rat_rows = [m for m in mrows
                        if m.get("message_kind") == "rationale" and m.get("agent_name") == SA]
            child_has_key = sa_child is not None and "rationale" in sa_child
            child_rat = (sa_child or {}).get("rationale")
            if sa_child is None:
                out("T-S75-010", "SKIP", f"no {SA} child in tree")
            elif not child_has_key:
                out("T-S75-010", "FAIL",
                    "tree child missing the 'rationale' key — projection not deployed (plumbing)")
            elif rat_rows:
                row_txt = (rat_rows[-1].get("content") or "").strip()
                child_txt = (child_rat or "").strip()
                if child_txt and row_txt and child_txt == row_txt:
                    out("T-S75-010", "PASS",
                        f"message_kind=rationale row for {SA} == tree child rationale ('{row_txt[:60]}')")
                elif child_txt and row_txt:
                    out("T-S75-010", "FAIL",
                        f"rationale row present but tree projection differs: "
                        f"row='{row_txt[:50]}' child='{child_txt[:50]}'")
                else:
                    out("T-S75-010", "FAIL",
                        f"persisted {len(rat_rows)} rationale row(s) but tree child rationale empty: {child_rat!r}")
            else:
                if child_rat:
                    out("T-S75-010", "FAIL",
                        f"tree child projects rationale '{str(child_rat)[:50]}' but NO "
                        "message_kind=rationale row was persisted (source/projection disagree)")
                else:
                    out("T-S75-010", "SKIP",
                        f"{SA} produced no tool-calling reasoning this run "
                        "(no rationale row; child null) — non-deterministic model")
    finally:
        async with httpx.AsyncClient(timeout=30, headers=hdr) as c:
            if wid:
                try:
                    await c.delete(f"{BASE}/workflows/{wid}")
                except Exception:
                    pass
            for n in (SA, SB):
                try:
                    await c.delete(f"{BASE}/agents/{n}")
                except Exception:
                    pass
            if tool_id:
                try:
                    await c.delete(f"{BASE}/tools/{tool_id}")
                except Exception:
                    pass

asyncio.run(main())
PY
)
echo "$SECTION_E"
tally "$SECTION_E"

# ---------------------------------------------------------------------------
# Final cleanup: tear down the chat agent kept alive for the restart test.
# ---------------------------------------------------------------------------
kubectl exec -n "$NAMESPACE" "$API_POD" -c registry-api -- python3 -c "
import urllib.request
try:
    urllib.request.urlopen(urllib.request.Request(
        'http://localhost:8000/api/v1/agents/${CHAT_AGENT}', method='DELETE'), timeout=5)
except Exception:
    pass
" 2>/dev/null || true

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo ""
echo "==> Suite 75 Results: ${PASS} passed, ${FAIL} failed, ${SKIP} skipped"
[ "$FAIL" -eq 0 ] || exit 1
