# Conversations not saved when memory is off — save conflated with agent-recall

**Found/Fixed:** 2026-07-28 — branch `lifecycle-journey-suite`. Ships in registry-api
0.2.238 + declarative-runner 0.1.67. Reported while testing github-agent (memory off).

## Symptom

A user's conversation with an agent was not saved / not visible in History when the agent
had memory turned off (github-agent: `memory_enabled=False`, and 48 of 54 agents are off by
default). The runner tried to save and got `HTTP 400 "Memory is not enabled for agent …"`.

## Root cause (the design flaw)

The platform **conflated two different concerns** onto one flag:
- **Saving the conversation transcript** — the user's record, for History + rehydrate.
- **The agent recalling prior turns** — injecting the transcript as working memory.

`memory_enabled` gated **only the SAVE** (`routers/memory.py` `save_turn`). The runner's
recall (`_load_memory_context`) and the History reads were **not** gated — the "agent
doesn't recall when off" behavior was achieved implicitly by *saving nothing*. So turning
memory off didn't just stop recall, it stopped **persisting the conversation at all**, and
the user lost their history.

## Fix (decouple)

Save is now independent of recall:
1. **`save_turn`** no longer checks `memory_enabled` → the transcript is **always
   persisted**, so History + rehydrate work for every agent.
2. **`list_memory`** gains `for_agent_context`. The runner's `_load_memory_context` sets it
   on the single-agent recall read (`scope="agent"` only); when set **and** the agent has
   memory disabled, the endpoint returns `[]` so the agent does **not** recall. The user's
   History reads never set it, so the transcript is always visible.
3. **runner** passes `for_agent_context=true` on the `scope="agent"` load only — the shared
   workflow transcript (`scope="workflow_run"`) is a separate feature and stays ungated.

Net: `memory_enabled` now means exactly "does the agent use prior turns as memory," while
the conversation is always saved for the user.

## Transition note

Agents on an older runner don't send `for_agent_context`, so their recall read is ungated
until redeployed on 0.1.67 — a memory-off agent on an old runner would recall the
now-saved transcript within a session. Redeploy agents on 0.1.67 to close it (github-agent
was redeployed as part of this change).

## Follow-up (gap-ledgered)

- Memory defaults **off** (`schemas.py:86`) and the History dock gives no hint when an
  agent has recall disabled — a UX gap (users expect to at least see their own messages).
  Consider defaulting on, or surfacing the state.

## Tests

- `scripts/e2e/suite-91-memory-save-recall-decouple.sh` — on a memory-OFF agent: save
  succeeds (was 400), History read returns the transcript, `for_agent_context` read is
  empty. Verified live on github-agent after redeploy.
