# `memory_enabled=true` gives no cross-conversation recall — long-term memory is unwired scaffolding

**Found:** 2026-07-28, during the Claude-in-Chrome lifecycle journey (Leg 20, "memory RECALL: on vs off"), while fixing the memory-ON recall test the user asked to re-run.
**Fixed:** NOT fixed — this is a documented finding + gap. The *original* leg-20 blocker (a memory-ON agent could not even respond) is resolved separately; see "What was actually fixed" below. The deeper defect (no long-term memory) is recorded here and in the gap ledger.
**Deployed context:** declarative-runner `0.1.67` (the memory-decouple build), registry-api `0.2.242`, on EKS `agentshield-platform`.

## Symptom

The journey's Leg 20 asserts a clean decouple: a **memory-ON** agent recalls a fact stated in an
earlier turn; a **memory-OFF** agent does **not** recall, but the conversation is still **saved**.
Driving the real Playground against the deployed cluster, the observed behavior did **not** match:

| | within one conversation (same thread) | across conversations (new thread) |
|---|---|---|
| **memory-ON** (`cic-mem2`) | recalls "73" ✓ | **does NOT recall** ✗ ("I don't have access to any personal information about you") |
| **memory-OFF** (`cic-journey-agent`) | **also recalls "73"** | does NOT recall ✓ |

So the ON and OFF agents are **behaviorally identical**: both recall within a single chat, and
**neither** recalls across chats. The `memory_enabled` flag produced **no observable difference**
in the Playground, and there is **no long-term / cross-conversation memory at all**.

## Root cause (the design flaw, not the surface error)

`memory_enabled` was built as a *within-conversation history* toggle for one recall scope, on top of a
long-term (pgvector) memory path that is **scaffolding with no producer and no consumer**. Three layers:

1. **The Playground recall is ungated.** The declarative runner's memory read is gated on
   `memory_enabled` only for `scope == "agent"` (`services/registry-api/routers/memory.py:150`:
   `if for_agent_context and scope == "agent" and not agent.memory_enabled: return []`). The
   Playground/thread recall uses a different, **ungated** scope, so same-thread history is **always**
   injected — which is why the memory-OFF agent still "remembered" 73 inside one chat. The flag never
   entered that path.

2. **No cross-thread read even where the flag *is* honored.** `memory.load_context` hard-filters
   `AgentMemory.thread_id == thread_id`. There is **no cross-conversation predicate** — a different
   `thread_id`/`conversation_id` shares zero rows. So even the gated agent-scope recall cannot surface
   a fact from a *previous* conversation.

3. **The pgvector long-term path is inert end-to-end.** `agent_memory.content_embedding vector(1536)`
   exists (migration `0022`) and `POST /{name}/memory/search` exists (`routers/memory.py:198-218`),
   but:
   - **no writer** — `save_turn` never computes or stores an embedding, so `content_embedding` is
     NULL for every row and the search's `WHERE content_embedding IS NOT NULL` matches nothing;
   - **no real query vector** — search uses `query_embedding = [0.0]*1536  # placeholder`;
   - **no caller** — nothing in `services/` or `sdk/` ever calls `/memory/search`; the runner only
     ever calls `GET /memory` (thread-keyed list) and `POST /memory` (save).

   For **SDK-type** agents the flag is a complete no-op: the SDK never reads `memory_enabled` or loads
   transcript memory (its only "memory" is the LangGraph checkpointer, keyed by `thread_id`, for
   HITL/durability — not recall).

**Net:** the only thing `memory_enabled` can ever change is the agent-scope recall, and that scope is
thread-filtered with no working long-term store behind it. Cross-conversation recall is a **no-op for
every agent**, ON or OFF.

## What was actually fixed (the leg-20 test the user flagged)

The user's report was narrower: the memory-ON recall test failed because the seeded
`cic-journey-agent-mem` could not respond at all — "Could not resolve authentication method." Root
cause: it was seeded via the API **without** `llm_provider_id`, so its version snapshot carried
`llm_provider_id=None`, and deploying it injected **no** Bedrock LLM secret (the pod had no AWS env).
Retrofitting the version did not re-inject the secret.

Fix: recreate the agent through the **normal Studio no-code pipeline** (`cic-mem2`), which sets
`llm_provider_id` and makes deploy-controller inject the Bedrock creds
(`LLM_PROVIDER=bedrock`, `AWS_ACCESS_KEY_ID/SECRET`, `LLM_MODEL=us.anthropic.claude-sonnet-4-6`).
`cic-mem2` then responds and recalls **within a conversation**, and — validating the Issue-1 (F-F)
persistence fix — its transcript **auto-rehydrates after a page reload** (sidebar shows the thread; a
post-reload turn still recalls 73). That closes the *reported* failure.

It does **not** deliver the cross-conversation "long-term memory" that the word "memory" implies. That
is the gap above.

## Recommended class-fix (if long-term memory is intended)

Make the pgvector path real, and give the flag one clear meaning:
1. **Writer** — in `save_turn`, embed the turn (reuse the knowledge-base embedding sidecar) and store
   `content_embedding`.
2. **Real query embedding** — replace the `[0.0]*1536` placeholder in `/memory/search` with an actual
   embedding of the incoming message.
3. **Caller** — have `_load_memory_context` call `/memory/search` (cross-thread, agent-scoped, gated on
   `memory_enabled`) and merge the top-k long-term hits with the same-thread history.
4. **Decide the flag's contract** — either (a) `memory_enabled` governs *only* long-term
   cross-conversation recall (same-thread history stays always-on for both), or (b) it also gates
   same-thread recall in the Playground scope. Today (a) is half-built and (b) is not applied to the
   Playground scope. Pick one and make the Playground scope honor it, so ON vs OFF is actually testable.

Until then, Leg 20's ON/OFF decouple assertion is **not representable** against the running product and
must not be reported green.

## Cross-links

- Investigation that confirmed the code paths: same-session general-purpose agent trace of
  `memory_enabled` (files: `services/declarative-runner/main.py` `_load_memory_context` L448-506;
  `services/registry-api/routers/memory.py` gate L150, save L53-91, stubbed search L198-218;
  `services/registry-api/memory.py` `load_context`/`search_memory`; `sdk/agentshield_sdk/server.py`).
- Persistence fix that *does* work (Issue-1 / F-F): playground transcript rehydration on reload.
- Gap ledger: `docs/testing/manual-ui-e2e-test-plan.md`.
