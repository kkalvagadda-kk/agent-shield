# Claude-in-Chrome Schedule-Failure Journey — browser-plugin acceptance test

**What this is.** A human-watchable acceptance test for the schedule-dispatch fix: Claude drives a
**real, visible Chrome** (via the Claude-in-Chrome extension) through the exact sequence that
produced the original bug, against the deployed Studio. You watch it fail *legibly* — which is the
whole point. Tell me "run the Claude-in-Chrome schedule journey" and I execute it live, capturing a
screenshot at each **Assert**.

Companion to `claude-in-chrome-journey.md` (the 22-leg lifecycle run). This one is short and
targeted: 7 legs, ~5 minutes, one defect class.

---

## The defect this exists to catch

A scheduled run for `deamon-agent-test` failed every hour. The deployment overview showed:

```
   Next Fire   7/27/2026, 11:00:00 PM            [ Failing ]
   Last Run
   No runs yet.
```

A red badge, directly above a card claiming nothing had run, and **no reason anywhere on screen**.
Three separate faults stacked:

| # | Fault | Fix under test |
|---|---|---|
| 1 | Admission checked "is ANY deployment running?" while dispatch hardcoded `-production` — a sandbox-only agent passed the door then DNS-failed | `resolve_dispatch_target` owns both; refusal names the environment |
| 2 | The runs card read by **deployment** (FKs always NULL on trigger runs) while the badge read by **agent** | both now read by **trigger** |
| 3 | `error_message` was in the payload and never rendered | rendered on Last Run, Recent Runs, and under the badge |

Plus: alerts configured `on` with no email address rendered as a reassuring green **"On"** while
`alerting.py` silently returned at its `if not trigger.alert_email` guard.

**The test is not "does it work" — it is "when it can't work, does it say so."**

---

## How it runs (when you say go)
1. **Target Studio URL** — the deployed gateway
   (`https://k8s-envoygat-envoyage-6676b8bb93-7541836717beafbe.elb.us-west-2.amazonaws.com`).
   I open a NEW tab (never reuse yours) and navigate there.
2. **Login** — if it redirects to Keycloak I sign in as `platform-admin` (password via the
   credential flow — I never type it myself), or you're already logged in.
3. **Fixture** — one agent, created and deployed **through the UI** (legs 1-2). Nothing is
   pre-seeded: the bug lived in the path a user actually walks, so the test walks it.
4. I drive the legs in order, pausing on any failed Assert to show you the screenshot.

_Do_ = a browser action. _Assert_ = something I read off the page and verify. The plugin locates
elements by visible text / vision, so targets are described by their label.

---

## Legs

### Leg 1 — Create a daemon agent
- Do: navigate to `/agents/new`.
- Do: click **No-code**.
- Do: type `cip-sched-<timestamp>` into the agent-name field (placeholder "my-agent").
- Do: set the agent to run **autonomously / as a daemon** (the class selector), execution shape
  **durable**.
- Do: click **Create Agent**.
- Assert: the agents list contains the new agent.

### Leg 2 — Deploy to SANDBOX ONLY ⭐
This is the fixture. Do **not** deploy to production — the entire defect lives in the gap between
"deployed somewhere" and "deployed where triggers dispatch".
- Do: navigate to `/agents/<agent>`.
- Do: click **Deploy** → **Deploy to sandbox** → **Deploy**.
- Do: wait up to ~2 min for the deployment to reach **running**.
- Assert: a sandbox deployment is listed as **running**.
- Assert: there is **no production deployment** for this agent.

### Leg 3 — Arm an hourly schedule, alerts on, no email ⭐
Two things set up at once: the schedule that will fail, and the alert config that silently
notifies nobody.
- Do: on the agent page open **Settings**.
- Do: add a **schedule** trigger with cron `0 * * * *`, timezone UTC, and a job-spec message
  (e.g. `{"message": "scheduled check"}`).
- Do: turn **failure alerts ON** and leave the alert email **blank**.
- Do: **Save**.
- Do: **reload** the page and reopen Settings.
- Assert: the schedule persisted — cron `0 * * * *` is still there (save→reload→survived).

### Leg 4 — Fire the schedule through the real door
The scheduler fires on the hour; we don't wait for the clock. I POST the same cluster-internal
endpoint the scheduler calls on a tick — same door, same body, same code path.
- Do: `POST /api/v1/internal/runs/start` with
  `{agent_name, trigger_type: "schedule", trigger_id, run_by: "serviceaccount:scheduler"}`.
- Assert: the call returns **201** and a run id. _(A refusal is RECORDED, not raised — a 409 back
  to the scheduler would be a log line nobody reads. The row is what makes it visible.)_
- Assert: the returned run has status **failed**.

### Leg 5 — The overview explains itself ⭐⭐ (the headline assert)
- Do: navigate to `/agents/<agent>` and open the **sandbox deployment**.
- Assert: the page shows the **Next Fire** / **Failure Alerts** / **Last Run** cards (the scheduled
  overview resolved).
- Assert: the health badge reads **Failing**.
- Assert (**the fix**): a reason is visible on screen naming the cause —
  **"no running production deployment"** / "deployed to sandbox".
- Assert (**negative**): the text **"Name or service not known"** / "Errno -2" appears **nowhere**.
  A DNS error here means a URL was rebuilt at the point of use and the resolver was bypassed.
- Assert (**negative**): the words **"No runs yet"** appear **nowhere** on the page. A failing
  schedule that claims it has no runs is the original bug verbatim.
- Screenshot this leg — it is the before/after of the whole change.

### Leg 6 — Alert honesty
- Assert: the **Failure Alerts** card does **not** show a bare green **"On"**.
- Assert: it warns that alerts are on but **not delivered** / notify **nobody**, and points at
  Settings.

### Leg 7 — Fix the cause, watch it clear
Proves the message was actionable — the operator can act on what the screen told them.
- Do: on the agent page click **Deploy** → **Deploy to production** → **Deploy**.
- Do: wait for the production deployment to reach **running** (tolerant: if no pod capacity,
  record and stop here — legs 1-6 are the gate).
- Do: fire the schedule again (as in leg 4).
- Assert: the new run is **not** refused for the environment reason — the run is admitted and
  addressed. _(Whether the agent pod then completes the work is the few-pods boundary every other
  suite accepts; what we assert is that the door stopped refusing.)_
- Assert: the overview's **Last Run** now reflects the newer run.

### Leg 8 — Cleanup
- Do: delete the agent through its UI delete control.
- Assert: it is gone from the agents list.

---

## What this proves that the other layers cannot

| Layer | Covers | Blind to |
|---|---|---|
| `suite-94` (bash) | the door refuses correctly, the reason names the environment, the run is reachable by trigger | anything on a screen |
| `OverviewScheduled.test.tsx` (Vitest) | the component renders a reason when handed one | `registryApi` is mocked wholesale — cannot catch the component calling the wrong endpoint, or the field being dropped in serialization |
| `schedule-failure-reason.spec.ts` (Playwright) | the real wiring, headless | nobody watches it; a subtly confusing layout still passes |
| **this journey** | a human sees what an operator would see, in order, including whether the message is actually *actionable* (leg 7) | — |

Leg 5's negative asserts are the durable ones. "No runs yet" and "Errno -2" are each a specific
regression signature, not a vague quality bar.

## Notes — what I click, and what I won't
- **Authorized in-test actions — I DO perform these as written, because performing them IS the
  test:** creating the agent (leg 1), **Deploy** to sandbox (leg 2) and to production (leg 7),
  creating/saving the trigger (leg 3), firing `/internal/runs/start` (legs 4, 7), and **Delete**
  of the agent I created (leg 8). Treat every "Do: click …" as a green light for the real control.
- **What I won't do:** reuse your existing tabs (I open a fresh one), touch any agent or schedule
  this journey did not create, type a password myself (login goes through the credential flow), or
  click any side-effecting control that is not a leg step above.
- Each Assert produces a screenshot; the run can be captured as a GIF on request.
- Everything created is removed in leg 8, or left for your inspection if you prefer.

## Further ideas (still open)
- [ ] **Webhook twin.** The event-gateway shares this door and returns **202 to the sender** before
      dispatch is attempted, so a webhook against a sandbox-only agent tells the caller it
      succeeded. Worth a leg once we decide whether the gateway should 503 instead.
- [ ] **Zombie schedules.** Archived and draft workflows still have armed schedules firing (see
      `docs/design/todo/schedule-lifecycle-and-operations.md`, Finding 1). Not fixed here; a leg
      belongs with that work.
- [ ] _add your own …_
