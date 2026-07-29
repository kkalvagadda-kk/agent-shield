# Playground: every response merged into one bubble (no per-turn / reasoning split)

**Found/Fixed:** 2026-07-28 — branch `lifecycle-journey-suite`. Issue 2 of the three
reported Playground defects. Ships in sdk 0.2.9 → declarative-runner 0.1.66 (backend) +
studio 0.1.169 (frontend); tags bumped at Issue-2 deploy.

## Symptom

On the Playground, a single agent's whole run rendered as ONE assistant bubble —
reasoning, pre-tool text, and the post-tool answer all concatenated together, with no
separation of the model's reasoning from its answer ("all the response appended to the
same response instead of separate messages with proper reasoning information").

## Root cause (structural, two layers)

1. **The SSE stream had no message boundary and no reasoning channel.** `stream_events`
   (`sdk/agentshield_sdk/streaming.py` — the ONE emitter both custom-SDK agents *and*
   declarative agents use, via `runner.py` and `workflow_executor.run_streamed`) emitted a
   flat sequence of `text_delta` with only `tool_call_start/end` between. It never signalled
   when a NEW model turn began, and — because it flattened every streamed content block with
   `block.get("text","")` — Bedrock/Claude extended-thinking blocks (whose text lives under
   `reasoning_content.text` / `thinking`, not `text`) were silently **dropped**.
2. **ChatPane appended everything to the last bubble.** With no boundary event, the
   hand-rolled reducer (`ChatPane.tsx`) only ever appended `text_delta` onto the last
   assistant bubble and opened a new bubble only on user send — so a multi-turn run
   collapsed into one.

## Fix (the class-fix)

Emit explicit boundaries at the source and route them through the shared reducer:

- **Backend (`streaming.py`):** emit `message_start` on every `on_chat_model_start` (one per
  LLM turn → one bubble), and split reasoning blocks out as their own `reasoning` event
  (answer text still `text_delta`). One place → both agent types get it. The registry-api
  proxy (`_real_agent_stream`) already forwards any event type transparently, so no
  translation change was needed.
- **Frontend (`ChatPane.tsx`):** on `message_start` open a new assistant bubble via the
  **shared** `openAuthorBubble` primitive from `lib/chatStream.ts` (which no-ops on an empty
  open bubble, so no blank bubbles stack); on `reasoning` accumulate a distinct `reasoning`
  slot rendered as its own dimmed block. Backward-compatible: an older agent pod that emits
  no `message_start` degrades to the previous single-bubble behavior.

Reasoning-as-its-own-block is contingent on the Bedrock model actually returning reasoning
tokens; when it doesn't, the message-boundary bubble-splitting still applies (recorded in
the gap ledger).

## Tests

- `sdk/tests/test_streaming.py` — fakes the graph event stream; asserts `message_start` per
  turn + a separate `reasoning` frame that never leaks into `text_delta` (all absent
  pre-fix).
- `studio/src/components/playground/ChatPane.test.tsx` — "each LLM turn opens its own bubble;
  reasoning renders separately" drives `message_start`/`reasoning`/`text_delta` frames and
  asserts two distinct bubbles (`getByText("FIRST")` fails on a merged blob) + the reasoning
  block. Fails against pre-fix ChatPane.
- Real multi-step run rendering ≥2 bubbles (deployed agent) is Playwright journey leg 4
  (Phase 2) — gap-ledgered until it runs on the cluster.
