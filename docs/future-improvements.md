# Future Improvements

## Langfuse Observations Not Appearing in Traces

**Status:** Deferred (2026-07-08)
**Symptom:** Traces are created and visible in Langfuse, but show "No observations in this trace." — no LLM call spans, no token counts, no cost data.

**Root cause (suspected):** `LangfuseCallbackHandler` is either not wired into the LangGraph `ainvoke` call inside the agent pod, or the agent pod's Langfuse env vars aren't reaching the callback handler at runtime. The trace shell is created by registry-api (`trace_create_run`), but the actual LLM observations must be emitted by the runner inside the pod via LangChain's callback mechanism.

**What's needed:**
1. Confirm agent pod has `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` in env
2. Wire `LangfuseCallbackHandler(trace_id=...)` into `graph.ainvoke(state, config={"callbacks": [handler]})` in `services/declarative-runner/workflow_executor.py`
3. Verify the callback handler's host/key match the deployed Langfuse instance
4. After fix: observations (generations, spans) should appear under each trace with model, tokens, cost

**Files to modify:**
- `services/declarative-runner/workflow_executor.py` — wire callback into ainvoke (2 call sites ~L690, ~L764)
- `services/declarative-runner/main.py` — ensure env vars are read and passed through
