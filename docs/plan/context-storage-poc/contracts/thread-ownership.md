# Contract — Thread / session ownership check (§6.3, S6)

**Guarantee:** a caller may only bind to a `session_id`/`thread_id` they own. A `session_id` first used by user A cannot be replayed by user B to read A's conversation. Fail-closed: ambiguous identity → do not bind to a shared session.

## Where enforced (the edge)

`services/registry-api/routers/chat.py` — `start_chat` and `start_deployment_chat` (POST handlers), and the stream handlers already check `run.user_id == caller.sub`. The NEW check is on **session binding at POST time**, since that is where a client-supplied `session_id` first enters.

## Rule

On POST, `user_sub = caller["sub"]`:
1. If `body.session_id` is empty → mint a fresh `session_id = uuid4()` (no binding to prove; safe).
2. If `body.session_id` is supplied → look for prior ownership:
   ```python
   owner = (await db.execute(
       select(PlaygroundRun.user_id)
       .where(PlaygroundRun.session_id == body.session_id)
       .limit(1)
   )).scalar_one_or_none()
   if owner is not None and owner != user_sub:
       raise HTTPException(403, "Not your session.")
   ```
   (A prior run under that session by the SAME user → allowed continuation. No prior run → first use, allowed, and this POST establishes ownership by writing a run with `user_id=user_sub`.)
3. Fail-closed (S6): if `user_sub` is empty/unauthenticated, never bind to a supplied `session_id` — mint a fresh one.

The transcript is keyed by `conversation_id = session_id`, and the memory rows carry `user_id`, so a downstream memory read constrained by `user_id` is the second line of defense. But the primary guard is this edge check — the runner's memory read is service-to-service and unauthenticated by design.

## Playground

`routers/playground.py` create-run path: the run is created with `user_id = caller.sub` and a `session_id`. The same ownership rule applies when a `session_id` is supplied on run creation. (Playground is sandbox/single-user in practice; the check is cheap and closes the same class of leak.)

## Test (suite T-S75-003)

1. User A: `POST /agents/{name}/chat` with `session_id=S`, chats, memory persists.
2. User B (different JWT): `POST /agents/{name}/chat` with `session_id=S` → **403 "Not your session."**
3. User B with no `session_id` → gets a fresh session, cannot see A's turns.
