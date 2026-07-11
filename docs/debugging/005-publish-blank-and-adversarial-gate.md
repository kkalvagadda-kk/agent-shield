# 005 — Publish blanks the app; risky agents can never be published

**Date:** 2026-07-09
**Surfaces:** Studio Playground "Publish Agent"; `POST /agents/{name}/publish`
**Symptom (user words):** "The moment I click Publish I get a blank screen, and I don't
see the agent in the approval list." Reported as a regression from recent HITL work.
Follow-up: "I'm pushing agents only after evals passed. evals passed + mark version
passed + publish. Are you thinking I did something different?"

## TL;DR

Two independent defects stacked, and the second one masqueraded as user error:

1. **Blank screen** — a real crash. Publish returns **422** whose `detail` is an
   **object** (`{error:"adversarial_eval_not_passed", version_number:4}`). The frontend
   did `toast.error(detail)`; passing an object to a React child throws *"Objects are
   not valid as a React child"*, and the `<Toaster>` lived **outside** the route
   `ErrorBoundary`, so the whole app blanked instead of showing a toast.
2. **The 422 itself** — a pre-existing **gate with no producer**, NOT the recent HITL
   changes. Publish has *two* gates; the second (`adversarial_eval_passed`) fires only
   for agents with a high/critical-risk tool, and **nothing in the product ever set it**.

## Root cause 1 — object error → crashed toast → blank app

- **Where:** `studio/src/pages/PlaygroundPage.tsx` publish `onError`; `studio/src/App.tsx`.
- **Problem:** FastAPI `HTTPException(detail={...})` serializes `detail` as a JSON
  object. `toast.error(object)` renders the object as a React child → throw. React
  error boundaries **don't** catch event-handler/async throws directly, but the toast
  render happens in the Toaster's tree — which was mounted *outside* the boundary that
  wrapped `<Routes>`. A throw there unmounts the whole app → blank.
- **Fix:** `publishErrorMessage(err, fallback)` extracts a safe string (special-cases
  the known error codes); wrap `<Toaster>` in its own `<ErrorBoundary fallback={null}>`
  so a bad toast degrades silently instead of blanking the app. `ErrorBoundary` gained
  an optional `fallback?: ReactNode` prop (studio 0.1.113).

## Root cause 2 — adversarial-eval gate had no producer

- **Where:** `services/registry-api/routers/agents.py` publish handler.
  - L508: `if not target_version.eval_passed → 422 eval_not_passed`
  - L513–522: compute `has_risky` from the version's tools + assigned tools' risk_level;
    if `has_risky and not target_version.adversarial_eval_passed → 422
    adversarial_eval_not_passed`.
- **Problem:** The user's flow was **correct** — they marked the version passed
  (`eval_passed=true`) and published. But "Mark Version Passed" (`PlaygroundPage`) only
  ever PATCHed `eval_passed`. There was **no UI, no eval runner, and no other path** that
  set `adversarial_eval_passed`. serper-agent-4's `web_search` tool is **high-risk**, so
  `has_risky=true` and the second gate was always active. Confirmed DB state:
  `v3/v4 eval_passed=True, adversarial_eval_passed=False`. Result: **publishing any
  high-risk agent was impossible via the product.** The gate shipped in migration `0012`
  (~6 months earlier) with no producer — a classic orphan gate. The backend PATCH
  endpoint (`versions.py:203`) already accepted the field; only the UI never sent it.
- **Fix (chosen: manual mark, symmetric with `eval_passed`):** add an explicit
  **"Mark Adversarial Passed"** button to the Playground promote panel that PATCHes
  `adversarial_eval_passed=true` — kept as a **separate** button from the ordinary eval
  mark so the red-team sign-off stays visible (not bundled into publish). `patchVersion`
  api-client + `AgentVersion` type now carry `adversarial_eval_passed` (studio 0.1.114).
  Deferred (gap ledger): a real automated red-team eval runner that sets the flag on pass.

## Why "it looks related to your changes" was a red herring

The recent work was all HITL (approvals, context, multi-tool). The publish gate and its
missing producer predate it by months. What the HITL-era code *did* introduce was the
**blank** (the object-detail toast crash), which made a long-standing 422 look new and
catastrophic. Lesson: a "new" catastrophic symptom can be a **new failure-rendering** of
an **old** error. Separate "what crashed" from "what returned the error."

## Investigation trail (commands)

```
# 1. Read the exact gate + error strings
sed -n '505,522p' services/registry-api/routers/agents.py

# 2. Query real version state (which gate is unsatisfied?)
kubectl exec -n agentshield-platform <registry-api> -c registry-api -- python3 -c "
  ... SELECT version_number, eval_passed, adversarial_eval_passed
      FROM agent_versions JOIN agents ... WHERE name='serper-agent-4' ..."
# → v3/v4 eval_passed=True but adversarial_eval_passed=False   ← the smoking gun

# 3. Find the producer (there is none)
grep -rn adversarial services/registry-api services/eval-runner sdk   # gate + PATCH, no runner
grep -n adversarial studio/src/pages/PlaygroundPage.tsx               # error-message only, no setter
```

## Verification

- Backend gate already covered by `suite-17` **T-S17-006** (risky version:
  `adversarial_eval_passed=false → 422`, then `true → 202`).
- New frontend guard: `studio/src/pages/PlaygroundPage.test.tsx` — "Mark Adversarial
  Passed" PATCHes `{adversarial_eval_passed:true}`, and the ordinary eval mark does **not**
  smuggle the adversarial flag (2 tests, green).
- `npm run typecheck` clean.

## Generalized principles

- **Orphan gate smell:** a boolean requirement in a write path with no code that ever
  sets it true = a dead-end for every user who hits it. When adding a gate, ship its
  producer in the same change (Definition of Done "no orphan code" applies to *gates*,
  not just symbols).
- **Never render a server error object directly.** API `detail` can be a string *or* an
  object. Always pass a string to toast/JSX. Keep global sinks (toaster, portals) inside
  an error boundary so one bad render can't blank the app.
- **Believe the user, then diagnose.** "You must have skipped eval" was wrong and
  dismissive; the DB showed the eval *was* passed. Query state before assigning blame.
