# Bug: every tool call denied ("policy restriction on the search tool") — OPA user-identity floor + missing X-User-Sub

**Found/Fixed:** original fix `b328ddf` (registry-api `0.2.190→0.2.191`, declarative-runner
`0.1.48→0.1.49`, 2026-07-15). **Recurred on the EKS test-cluster 2026-07-20** as a
deploy-lag (cluster was still serving the pre-fix `0.2.190`); resolved there by deploying
registry-api `0.2.223`, which carries the fix. This doc is the postmortem that `b328ddf`
never wrote (it lived only in the commit message).

## Symptom

A `user_delegated` agent (e.g. `serper-agent-5`, "Agent that uses web tool") answers every
query with a polite fallback:

> "I'm sorry, I wasn't able to retrieve the weather data at this moment due to a **policy
> restriction on the search tool**. However, here's how you can quickly find it…"

Nothing errors anywhere. The agent runs `2/2`, the tool is granted, the API key is valid —
the agent just explains its "limitation." The only tell is an **~11 ms `TOOL web_search`
span** in the trace: a real Serper call takes hundreds of ms; 11 ms is a denial round-trip.

The agent-facing string is `graph_builder.py`'s `f"Tool '{name}' denied by policy:
{reason}"` — the LLM paraphrases it as "policy restriction."

## Root cause

**OPA was right; its INPUT was wrong.** WS-2 added a `user_identity_ok` floor: a
`user_delegated` agent's tool call is **denied** when `input.user_id` is empty (a missing
principal must never silently downgrade to the service identity — correct, fail-closed).

The caller's identity has to travel: Studio → registry-api → agent pod → SDK → OPA input.
Two hops must forward it:

1. **registry-api → pod:** the chat proxy must send `X-User-Sub` / `X-Agent-Team` headers
   (`services/registry-api/pod_stream.py::stream_pod_chat_frames`, forwarded only
   `if user_id:`). The proxy callers pass `run.user_id`.
2. **pod → OPA:** the runner's app-level dependency
   (`services/declarative-runner/main.py::_bind_user_context`) reads those headers into the
   `_current_user_context` ContextVar the SDK's `governed_tool` wrapper reads.

If either hop drops the identity, `user_id` reaches OPA as `""` and **every** governed tool
on a `user_delegated` agent is denied — a total outage of tool use that fails closed and
silent. Before the WS-2 floor this gap was harmless, which is why the header was never
missed.

### Why it recurred on the test-cluster (2026-07-20)

Not a code regression — a **deploy lag**. The cluster was still running registry-api
`0.2.190`, which **predates** `b328ddf` (`0.2.191`) and therefore never forwarded
`X-User-Sub`. Every `user_delegated` tool call denied. Confirmed by:

- Running image was `0.2.190` before the redeploy; `b328ddf`'s forwarding lands in `0.2.191+`.
- After deploying `0.2.223`: `grep -c x-user-sub /app/pod_stream.py` → `1` (forwarding
  present), all `serper-agent-5` `playground_runs` have `user_id` populated, and the runner
  pod (`0.1.59`) shows **zero** `OPA denied` entries.
- Direct reproduction against the pod **with** `x-user-sub` set → `web_search` returns real
  Serper results (`tool_call_start` risk=low → `tool_call_end` with organic results), no deny.

## Fix

`b328ddf` (already in `main` and in `webhook-improvements`):

- **registry-api:** `_proxy_agent_stream` / `stream_pod_chat_frames` take `user_id` +
  `user_team` and send them as `X-User-Sub` / `X-Agent-Team` to the pod's `/chat/stream`.
- **declarative-runner:** `_bind_user_context` is an **app-level FastAPI dependency** (not
  per-route, not middleware) so every entrypoint (`/chat`, `/chat/stream`, `/workflow-run`,
  `/resume/*`, `/run`) binds the ContextVar in the handler's own task — a new route cannot
  forget it. Absent header ⇒ `""` ⇒ deny for `user_delegated`, admit for `daemon`
  (`agent_class == "daemon"`). Correct in both directions.

On this cluster the fix was applied by **deploying registry-api `0.2.223`** (which contains
`b328ddf`) over the stale `0.2.190`.

## How to recognize it next time

- Symptom is a **polite fallback**, never an error — grep for `OPA denied tool=… reason=…`
  in the agent pod log, or look for a **sub-15ms** `web_search`/tool span in the trace.
- Check the running registry-api tag carries the forwarding:
  `kubectl exec <registry-api-pod> -c registry-api -- grep -c x-user-sub /app/pod_stream.py`
  → must be ≥1.
- Check the agent's runner tag has `_bind_user_context` (declarative-runner ≥ `0.1.49`).
- Reproduce in-pod: POST `/chat/stream` **with** `x-user-sub` set → tool should run for real;
  **without** it → the same denial. If with-header works and the real chat doesn't, the gap
  is registry-api not forwarding (old image, or an empty `run.user_id`).

## Lessons

- **A governance floor turns a latent plumbing gap into a total outage — silently.** The
  identity header was "optional" until `user_identity_ok` made it load-bearing; nothing
  flagged the newly-required field. When a policy gains a new required input, audit every
  producer of that input.
- **"Fixed in the branch" ≠ "fixed on the cluster."** The code carried `b328ddf` the whole
  time; the running image didn't. Always check the **running** tag against the fix's tag
  before concluding a bug is live — the deploy is the real gate (same lesson as the
  registry-api rollout-FATAL guard added in `deploy-eks.sh`).
- **The tell is in the trace.** An 11 ms tool span is a denial, not a call. Span durations
  are a cheap, reliable oracle for "did the tool actually run."
