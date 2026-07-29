#!/usr/bin/env bash
# suite-92-playground-hitl-resume-session-id.sh
#
# T-S92 — Playground HITL resume-stream resolves the run by session_id, not just PK.
#
# REGRESSION GUARD for the bug the Claude-in-Chrome lifecycle journey caught:
# after the F-F fix threaded a persisted `session_id` (so a chat's turns share one
# reloadable thread), the run's checkpoint/HITL thread_id = session_id, which DIVERGES
# from PlaygroundRun.id. But `resume_stream_playground_run` resolved the run by
# `PlaygroundRun.id == run_id` only. The frontend (PlaygroundPage.handleHitlDecided)
# passes the THREAD id (= session_id), so every HITL resume-after-approve returned
# 404 "Playground run not found" -> the approved run never resumed -> "Stream
# connection lost", no final answer. See docs/bugs/playground-hitl-resume-session-id-404.md.
#
# Discriminating assertion: with a run whose session_id != id, resume-stream on the
# session_id must get PAST the run lookup. Pre-fix -> 404 "Playground run not found".
# Post-fix -> 404 "No decided approval found for this run" (run resolved; no approval seeded).
set -euo pipefail

NS="${NS:-agentshield-platform}"
POD="$(kubectl -n "$NS" get pod -l app.kubernetes.io/name=registry-api -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || true)"
[ -z "$POD" ] && POD="$(kubectl -n "$NS" get pod 2>/dev/null | grep -i registry-api | grep Running | head -1 | awk '{print $1}')"
[ -z "$POD" ] && { echo "no running registry-api pod found in $NS"; exit 1; }
echo "registry-api pod: $POD"

kubectl -n "$NS" exec -i "$POD" -c registry-api -- python3 - <<'PY'
import asyncio, uuid, httpx
from sqlalchemy import text
from db import AsyncSessionLocal

BASE = "http://localhost:8000/api/v1"
RUN_ID  = str(uuid.uuid4())
SESS_ID = str(uuid.uuid4())          # thread_id, distinct from the PK — the F-F reality
BOGUS   = str(uuid.uuid4())
AGENT   = "suite92-nonexistent-agent"

async def seed():
    async with AsyncSessionLocal() as s:
        await s.execute(text("""
            INSERT INTO playground_runs
              (id, session_id, user_id, agent_name, context, sandbox, status,
               execution_shape, eval_mode)
            VALUES
              (:id, :sess, 'suite92-user', :agent, 'playground', true, 'completed',
               'ephemeral', 'live')
        """), {"id": RUN_ID, "sess": SESS_ID, "agent": AGENT})
        await s.commit()

async def cleanup():
    async with AsyncSessionLocal() as s:
        await s.execute(text("DELETE FROM playground_runs WHERE id = :id"), {"id": RUN_ID})
        await s.commit()

def get(path):
    return httpx.get(BASE + path, timeout=15)

async def main():
    await seed()
    failures = []
    try:
        # T-S92-001 — resolve by session_id (the regression). Must get PAST the run lookup.
        r = get(f"/playground/runs/{SESS_ID}/resume-stream?_n=1")
        detail = (r.json().get("detail") if r.headers.get("content-type","").startswith("application/json") else r.text)
        if detail == "Playground run not found":
            failures.append(f"T-S92-001 FAIL: session_id lookup still 404s 'Playground run not found' (status={r.status_code}) — the bug is back")
        elif detail == "No decided approval found for this run":
            print("T-S92-001 PASS: resume-stream resolved run by session_id (reached approval lookup)")
        else:
            print(f"T-S92-001 PASS (resolved): status={r.status_code} detail={detail!r} (not 'Playground run not found')")

        # T-S92-002 — the PK path still resolves (no regression for durable/non-chat runs).
        r = get(f"/playground/runs/{RUN_ID}/resume-stream?_n=1")
        detail = (r.json().get("detail") if r.headers.get("content-type","").startswith("application/json") else r.text)
        if detail == "Playground run not found":
            failures.append("T-S92-002 FAIL: PK lookup no longer resolves the run")
        else:
            print(f"T-S92-002 PASS: PK lookup resolves (detail={detail!r})")

        # T-S92-003 — negative: an id matching no run must still 404 'Playground run not found'.
        r = get(f"/playground/runs/{BOGUS}/resume-stream?_n=1")
        detail = (r.json().get("detail") if r.headers.get("content-type","").startswith("application/json") else r.text)
        if r.status_code == 404 and detail == "Playground run not found":
            print("T-S92-003 PASS: unknown id 404s 'Playground run not found'")
        else:
            failures.append(f"T-S92-003 FAIL: unknown id should 404 'Playground run not found', got {r.status_code} {detail!r}")
    finally:
        await cleanup()

    if failures:
        print("\n".join(failures))
        raise SystemExit(1)
    print("suite-92 GREEN")

asyncio.run(main())
PY
