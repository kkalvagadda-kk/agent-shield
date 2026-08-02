# Publish gives no feedback — the only signal is a badge you see after reloading

**Found** 2026-08-02 (Claude-in-Chrome schedule-lifecycle journey, leg 8) · **Status: OPEN — documented, not fixed**

## Symptom

Clicking **Publish** on the agent page produces:

- no toast
- no navigation
- no spinner or button state change
- no visible row anywhere the operator is currently looking

`POST /api/v1/agents/{name}/publish` answers **202 Accepted** and creates a `publish_requests` row,
but the page does not say so. The only evidence is the **"Pending Review"** badge beside the agent
name — which renders on the *next* load of that page, so an operator who clicks Publish and stays
put sees nothing at all.

I misread it myself during the journey: clicked Publish, saw nothing change, checked the database,
found no production deployment, and concluded the click had done nothing. It had. I had to look at
`publish_requests` and a re-rendered badge to find out.

## Root cause

Not yet traced to a line — recorded from the outside. Every other mutation in Studio toasts
(`toast.success` on trigger disarm, agent update, deploy, schedule delete). Publish is the
exception, and it is the one with the **longest gap between action and visible consequence**: the
request lands in an admin queue on a different page, under a nav section (`ADMIN → Publish Queue`)
that is collapsed by default and invisible to non-admins.

The asymmetry is what makes it costly: cheap, instantly-visible actions announce themselves; the
one that starts a multi-party workflow does not.

## Impact — and the duplicate is real, not hypothetical

- An operator cannot tell success from failure without navigating away and back.
- The natural recovery is to click Publish again, and **that enqueues a duplicate.**
  `routers/agents.py::publish_agent` runs its checks (agent exists, no critical-risk tool, eval
  gate) and then constructs `PublishRequest(...)` + `db.add(pr)` unconditionally — there is no
  query for an existing `pending_review` row and no uniqueness constraint behind it.

  Confirmed against the live EKS cluster, read-only:

  ```
  agents with MULTIPLE pending publish requests:
      ('s89-1785209863-agent', 2)
      ('s89-1785211853-agent', 2)
  total pending: 12
  ```

  Two of twelve pending requests are duplicates. This has already happened, unprompted, in normal
  use — which is the strongest evidence that the silence drives the retry.

- The duplicates then land on a **reviewer**: approving one leaves its twin sitting in the queue
  pointing at the same artifact and version, and nothing marks it superseded.
- It compounds
  [`publish-does-not-create-a-production-deployment`](publish-does-not-create-a-production-deployment.md):
  that bug sends the operator to Publish, and this one denies them confirmation that they did it.

## Suggested fix (not implemented) — in this order

1. **Server first: make the duplicate impossible.** `publish_agent` should return the EXISTING
   pending request (200/202, idempotent) rather than adding a second one, or refuse with 409. This
   is the half that corrupts state, and it is correct regardless of what the UI does — a silent
   toast is a UI defect, a duplicated queue row is a data defect that a human then has to
   adjudicate. A partial unique index on `(asset_id) WHERE status = 'pending_review'` would make it
   structurally impossible rather than merely guarded.
2. Toast on 202: *"Publish requested — pending review in Admin → Publish Queue."* Name where it
   went; the queue is not somewhere the operator would think to look.
3. Flip the button to a disabled **"Pending review"** state immediately, without waiting for a
   reload, so the button itself carries the state.

Existing duplicates need a decision too — dedupe them, or teach the queue to collapse/supersede
requests for the same artifact+version. Not obviously safe to do automatically.

## Why it is documented and not fixed here

Out of scope for the change that found it (the schedules workstream): step 1 changes publish
semantics and touches the reviewer workflow, which deserves its own change and its own regression
test rather than riding along with a dispatch-message fix. Recorded so it is a known gap rather
than a surprise. Listed in the gap ledger at the head of
`docs/testing/manual-ui-e2e-test-plan.md`.

## Lessons

1. **The mutations that most need feedback are the ones whose effect is furthest away.** Local,
   instantly-visible changes can afford silence. A request that lands in another team's queue
   cannot.
2. **"State visible after reload" is not feedback.** Nobody reloads to check whether their click
   worked; they click again.
