# Contract — `/chat/stream` memory behavior + dispatch body/headers

Covers the runner's `POST /chat` and `POST /chat/stream`, the registry→pod dispatch body, and how memory load/save wraps the stream. Symmetry rule: `/chat/stream` must load+save memory exactly like `/chat` (today it does neither — the core POC-0 bug).

## Dispatch body (registry → agent pod)

Both `routers/chat.py::_proxy_agent_stream` and `routers/playground.py::_real_agent_stream` POST to the pod's `/chat/stream`:
```json
{
  "message": "<user turn>",
  "thread_id": "<checkpoint key: session_id for chat, per-member id for workflow>",
  "conversation_id": "<transcript key: session_id for chat, parent_run_id for workflow>",
  "scope": "agent" | "workflow_run",
  "workflow_run_id": "<parent_run_id, workflow only>"
}
```
Headers: `x-user-sub: <sub>`, `x-agent-team: <team>`, `x-deployment-id: <deployment uuid>`, `x-agentshield-trace-id: <trace>` (existing), `x-agentshield-auto-approve: true` (existing, eval-runner only).

`thread_id` is the LangGraph checkpoint key (unchanged semantics). `conversation_id` is the transcript key. For chat the two are equal (`session_id`); for a workflow member they differ (checkpoint per-member vs shared transcript). Default: if `conversation_id` absent, the runner uses `thread_id`.

## Runner `ChatRequest` (declarative-runner/main.py)

```python
class ChatRequest(BaseModel):
    message: str = ""
    thread_id: str | None = None          # checkpoint key (LangGraph config)
    conversation_id: str | None = None    # transcript key; defaults to thread_id
    scope: str = "agent"                   # agent | workflow_run
    workflow_run_id: str | None = None
    metadata: dict | None = None
```

## `/chat/stream` handler behavior (the fix)

```
user_id       = header x-user-sub
team          = header x-agent-team
deployment_id = header x-deployment-id (or env AGENTSHIELD_DEPLOYMENT_ID)
conv_id       = req.conversation_id or req.thread_id

# 1. set user context WITH a reset token (leak fix, §6.3)
token = _current_user_context.set({...})
try:
    # 2. load prior transcript
    memory_context = await _load_memory_context(
        AGENT_NAME, conversation_id=conv_id, scope=req.scope,
        user_id=user_id, deployment_id=deployment_id)
    # 3. stream, accumulating assistant text
    async for chunk in workflow_executor.run_streamed(
            req.message, thread_id=req.thread_id, trace_id=trace_id,
            memory_context=memory_context):
        accumulate text_delta content
        yield chunk
finally:
    _current_user_context.reset(token)
# 4. save the turn (fire-and-forget, after stream completes)
asyncio.create_task(_save_memory_turn(
    AGENT_NAME, conversation_id=conv_id, scope=req.scope,
    workflow_run_id=req.workflow_run_id, user_id=user_id, deployment_id=deployment_id,
    author_agent_name=AGENT_NAME, user_msg=req.message, assistant_msg=accumulated[:4000]))
```

Notes:
- Memory load/save live at the handler layer (symmetric with `/chat`), NOT inside `run_streamed`; `run_streamed` only gains a `memory_context` param it injects as prior messages (mirrors `run()`).
- The accumulator parses the runner's own SSE frames for `event: text_delta` → `data.content` (same shape the runner already emits via `sdk/streaming.py`).
- Save is fire-and-forget so it never delays the client's stream close; failures are logged, never raised (matches `/chat`).

## Workflow member (scope='workflow_run')

- Load uses `scope='workflow_run'` → drops the agent_name filter → member sees peers' turns. Peer turns whose `agent_name != AGENT_NAME` are injected with a `[<agent_name>]: ` content prefix so the model attributes them.
- Save tags the row with `author_agent_name = AGENT_NAME`, `workflow_run_id`, `message_kind='agent_output'`.

## SSE frames (unchanged)

No new SSE event types in this slice. `text_delta` / `done` / `error` / `approval_requested` are as-is. Per-agent SSE attribution routing is POC-2 (deferred). `docs/experience/playground.md` gets a note that chat now threads across turns and workflow members share a transcript.
