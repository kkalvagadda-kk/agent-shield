# 016 — Playground HITL "Stream connection lost": resume-stream 404s on session_id

**Date:** 2026-07-28 · **Surface:** Playground sandbox HITL (Claude-in-Chrome journey leg 12)
**Verdict:** F-F (session_id threading) regression — `resume_stream_playground_run` resolved the
run by PK only; the frontend passes the thread id (= session_id) → `404 Playground run not found`
→ approved run never resumed. Fixed in registry-api `0.2.239`. See
`docs/bugs/playground-hitl-resume-session-id-404.md` + `scripts/e2e/suite-92-…`.

## Expected chain (what should happen)
1. Playground run calls high-risk `cic-echo-tool` → graph interrupts, HITL parks **inline**.
2. User clicks **Approve** → `POST /playground/approvals/{id}/decide` records `approved`.
3. Frontend opens `GET /playground/runs/{thread}/resume-stream` → registry-api reads the decided
   approval, proxies a streaming **resume** to the agent pod → tokens flow → final answer bubble.

## Symptom (named the wrong layer)
"Stream connection lost" toast + no final answer. Reads like a transport/SSE problem or the
short-TTL OIDC token refresh severing the EventSource. It was neither.

## Investigation

**1. Is the approval recorded? (registry-api log)**
```
kubectl -n agentshield-platform logs <registry-api-pod> -c registry-api --since=3m \
  | grep -iE "decide_playground_approval|resume"
# → decide_playground_approval: id=fb75… decision=approved thread_id=3dc6bde0-…   (200 OK)
# → GET /api/v1/playground/runs/3dc6bde0-…/resume-stream?_n=2  404 Not Found      ← smoking gun
```
Approval persisted; the **resume-stream 404'd**.

**2. Did the runner resume? (agent-pod log)**
```
kubectl -n agents-platform logs <agent-pod> -c <agent-container> --since=3m | grep -vE "/health|/ready"
# → HITL approval record created … context=playground risk=high
# → (nothing after — NO second tool_call, NO psycopg/checkpointer error, NO traceback)
```
Rules out the deferred checkpointer-pool concurrency bug (doc 015). The runner simply was never
re-driven.

**3. Is the route missing, or is the lookup failing? (deployed OpenAPI + direct call)**
```
kubectl -n … exec <registry-api> -c registry-api -- python3 -c \
 'import httpx;print([p for p in httpx.get("http://localhost:8000/openapi.json").json()["paths"] if "resume-stream" in p])'
# → ['/api/v1/playground/runs/{run_id}/resume-stream', '/api/v1/agents/{name}/chat/{run_id}/resume-stream']  (route EXISTS)

# hit it directly with the thread id:
# → 404 {"detail":"Playground run not found"}     (route runs; the RUN lookup fails)
```

**4. Is the id a run_id or a session_id? (DB)**
```
select count(*) from playground_runs where id::text = '3dc6bde0-…';          -- → 0
select count(*) from playground_runs where session_id::text = '3dc6bde0-…';  -- → 1
select id, session_id from playground_runs where agent_name='cic-journey-agent';
--   id=5893f807-…  session_id=3dc6bde0-…    ← id != session_id (F-F made them diverge)
```

**Root cause:** `resume_stream_playground_run` did
`select(PlaygroundRun).where(PlaygroundRun.id == parsed_id)` and `thread_id = run_id`. Pre-F-F,
`thread_id == run_id == PlaygroundRun.id`. F-F (`0.2.235`) set the checkpoint/HITL
`thread_id = run.session_id`, so the frontend (which needs the thread id to find the Approval)
passes `session_id`, which is not any `PlaygroundRun.id` → 404.

## Fix
Resolve by PK **or** `session_id`, derive `thread_id` the same way the dispatch path does:
```python
select(PlaygroundRun)
  .where(or_(PlaygroundRun.id == parsed_id, PlaygroundRun.session_id == run_id))
  .order_by(PlaygroundRun.started_at.desc())
...
thread_id = run.session_id or str(run.id)
```

## Guard
`suite-92-playground-hitl-resume-session-id.sh`: seed a run with `session_id != id`; assert
`/runs/{session_id}/resume-stream` resolves past the run lookup (not "Playground run not found").
Fails on `0.2.238`, passes on `0.2.239`.

## Playbook takeaway
"Stream connection lost" after a HITL approve → **don't assume transport/auth**. Check the
`resume-stream` request's HTTP status in the registry-api access log first. A 404 there means the
run/thread id the frontend sent doesn't resolve server-side — check `playground_runs.id` vs
`.session_id` for divergence.
