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

## Impact

- An operator cannot tell success from failure without navigating away and back.
- The natural recovery is to click Publish again. Whether that creates a second `publish_requests`
  row is **untested** — worth checking before this is fixed.
- It compounds
  [`publish-does-not-create-a-production-deployment`](publish-does-not-create-a-production-deployment.md):
  that bug sends the operator to Publish, and this one denies them confirmation that they did it.

## Suggested fix (not implemented)

1. Toast on 202: *"Publish requested — pending review in Admin → Publish Queue."* Name where it
   went; the queue is not somewhere the operator would think to look.
2. Flip the button to a disabled **"Pending review"** state immediately, without waiting for a
   reload, so the button itself carries the state.
3. Confirm the double-click behaviour before shipping either — if a second click enqueues a
   duplicate, that is the more urgent half.

## Why it is documented and not fixed here

Out of scope for the change that found it (the schedules workstream), and the right fix needs the
double-click question answered first — which is a behavioural investigation, not a UI tweak.
Recorded so it is a known gap rather than a surprise. Listed in the gap ledger at the head of
`docs/testing/manual-ui-e2e-test-plan.md`.

## Lessons

1. **The mutations that most need feedback are the ones whose effect is furthest away.** Local,
   instantly-visible changes can afford silence. A request that lands in another team's queue
   cannot.
2. **"State visible after reload" is not feedback.** Nobody reloads to check whether their click
   worked; they click again.
