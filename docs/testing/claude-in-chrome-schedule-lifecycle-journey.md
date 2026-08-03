# Claude-in-Chrome Schedule-Lifecycle Journey — browser-plugin acceptance test

**What this is.** A human-watchable acceptance test for the *whole* life of a schedule: created in
the UI, armed on a sandbox-only agent (where it cannot fire), promoted to production (where it
can), operated from the Schedules screen, and disarmed. Claude drives a **real, visible Chrome**
via the Claude-in-Chrome extension against the deployed Studio. Say "run the Claude-in-Chrome
schedule lifecycle journey" and I execute it live, screenshotting each **Assert**.

Sibling to `claude-in-chrome-schedule-failure-journey.md`, and deliberately the other half of it:

| | that journey | **this journey** |
|---|---|---|
| Thesis | *when a schedule cannot work, does the screen say so?* | *does the screen agree with what will actually fire — in both environments — and can an operator change it?* |
| Shape | one agent, sandbox only, 8 legs | one agent through **sandbox → production**, plus the cross-artifact screen, 13 legs |
| Fixture | a schedule that must fail | a schedule that must fail, **then must succeed after a real promotion** |

Both matter. A test that only proves failure-reporting passes just as well on a platform where
*nothing* fires — which is exactly the trap `will_fire` exists to close, and why leg 8 below is the
load-bearing one.

---

## The defect classes this exists to catch

Every row is a bug that actually shipped here.

| # | Class | Signature on screen |
|---|---|---|
| 1 | **Dispatch environment mismatch.** Admission asked "is ANY deployment running?"; dispatch hardcoded `-production`. A sandbox-only agent passed the door, then DNS-failed. 1,197 runs. | `Errno -2` / "Name or service not known" anywhere |
| 2 | **Health derived from history, not config.** A schedule that can never fire read *healthy* until something failed; after a fix, the badge stayed red for up to an hour. | badge disagrees with current config |
| 3 | **A remedy that does not resolve the cause.** Twice: "deploy to production" was unreachable from the UI, then "Publish" was reachable but INSUFFICIENT (it creates a catalog listing, not a running deployment) — so the operator completed it and got the same message back. | advice naming a control that does not exist, or one that completes and changes nothing |
| 4 | **Zombie schedules.** Deleting an artifact left its cron armed. 37 were live, one firing every 15 min for days. | a deleted agent's schedule still counting down |
| 5 | **A write that writes nothing.** Disarm PATCHed an undeclared field: 200 OK, success toast, no change. | state reverts after reload |
| 6 | **A fabricated field.** `armed_at` was synthesised from `created_at`, so every row read *Armed* — including ones the lifecycle gate had just disarmed. | "Armed" beside "this schedule is disabled" |
| 7 | **A version marker that lies.** The Sidebar build tag drifted 9 versions and reported the wrong live build. | Sidebar version ≠ deployed image tag |

**The test is: what the screen claims and what the scheduler will do must be the same thing.**

---

## Before you start

- **Target Studio URL** is an input, not a constant. Local:
  `https://agentshield.127.0.0.1.nip.io:8443` (needs `bash scripts/gateway-proxy.sh` running).
  EKS: the ELB gateway host. I open a **new tab** and navigate there — never reusing yours.
  *(The failure-journey doc hardcodes an EKS URL that is now stale; don't copy that.)*
- **Login**: if it redirects to Keycloak, **you sign in**. I never type a password.
- **Prerequisite — an LLM provider must exist for the team.** A fresh cluster has none
  (`seed-defaults.sh` does not create one), and agent creation needs `llm_provider_id`. Leg 11
  (Publish) additionally needs a provider whose credentials actually **work**, because publishing is
  eval-gated (Decision 20). With a placeholder credential, stop at leg 10 and record it.
- **Clean slate**: `GET /api/v1/schedules` should be empty, or contain only rows you recognise.
  Leg 0 checks this — a screen full of other people's leftovers makes every count assert
  meaningless.

_Do_ = a browser action. _Assert_ = something I read off the page. Targets are described by visible
label; the plugin locates them by text/vision.

---

## Legs

### Leg 0 — Clean slate + the build is what you think it is
- Do: navigate to `/schedules`.
- Assert: the empty state reads **"No schedules match this filter"**, or every listed row is one you
  expect. Record the starting count — later legs assert *deltas*.
- Assert: the Sidebar build marker equals the tag you deployed (`kubectl get deploy … -o jsonpath`
  on the studio image). **Class 7.** Everything after this is meaningless if the bundle is stale;
  this is the cheapest possible way to find that out first, and it has been wrong twice.

### Leg 1 — Create a daemon agent, through the UI
- Do: `/agents/new` → **No-code**.
- Do: name it `cip-lc-<timestamp>`; class **daemon** (runs autonomously), shape **durable**.
- Do: pick a **Model**. It is required — an agent with no provider can never complete a
  run, and the wizard used to accept one silently.
- Do: **Create Agent**.
- Assert: the agent appears in the agents list.
- Assert (**class 3**): the wizard's schedule notice names the **whole** route —
  Publish · **Publish Queue** · **Deploy Latest** — and says publishing alone only creates
  the catalog listing. Naming just "Publish" is reachable but insufficient, which is the
  worse failure: the operator completes it and is told to do it again.

### Leg 2 — Deploy to SANDBOX only ⭐
The fixture. The whole point is the gap between "deployed somewhere" and "deployed *where triggers
dispatch*".
- Do: `/agents/<agent>` → **Deploy** → **Deploy to sandbox** → **Deploy**.
- Do: wait up to ~2 min for **running**.
- Assert: a sandbox deployment is listed **running**.
- Assert: there is **no** production deployment.

### Leg 3 — Arm an hourly schedule, alerts on, no email
- Do: agent page → **Settings** → add a **schedule** trigger, cron `0 * * * *`, timezone UTC,
  job-spec `{"message": "scheduled check"}`.
- Do: failure alerts **ON**, alert email **blank**.
- Do: **Save**, then **reload** and reopen Settings.
- Assert: cron `0 * * * *` survived the reload. *(save → reload → assert; state living in the store
  but never reaching the DB is the single most repeated defect in this repo.)*
- Assert (**class 3**): Settings warns that this schedule cannot run yet, naming the cause.

### Leg 4 — The Schedules screen sees it, and calls it what it is ⭐
First cross-artifact assert — the screen must agree with the agent page.
- Do: navigate to `/schedules`.
- Assert: exactly **one** new row versus leg 0, naming the agent.
- Assert: **ARM STATE = Armed**, with `by <your sub>` underneath.
- Assert: **WILL FIRE = a warning**, and the reason names the **environment** —
  "no running production deployment" / "deployed to sandbox".
- Assert (**class 1, negative**): `Errno -2` / "Name or service not known" appears **nowhere**. A
  transport symptom here means a URL was rebuilt at the point of use and `resolve_dispatch_target`
  was bypassed.
- Assert: the amber **attention banner** count went up by exactly one — this row is switched on and
  blocked, which is precisely what the banner is for.
- Assert: the **Needs attention** filter contains it; **Will fire** does not.

### Leg 5 — Fire the schedule through the real door
The scheduler fires on the hour; we don't wait for the clock. Same endpoint it calls on a tick.
- Do: `POST /api/v1/internal/runs/start` with
  `{agent_name, trigger_type: "schedule", trigger_id, run_by: "serviceaccount:scheduler"}`.
- Assert: **201** with a run id. *(A refusal is RECORDED, not raised — a 409 back to the scheduler
  is a log line nobody reads. The row is what makes it visible.)*
- Assert: the run's status is **failed**.

### Leg 6 — Both screens explain the failure, and agree ⭐⭐
- Do: `/agents/<agent>` → open the **sandbox deployment**.
- Assert: the scheduled overview resolved (Next Fire / Failure Alerts / Last Run cards).
- Assert: health badge reads **Failing**.
- Assert (**class 2**): the reason on screen is the **current config** problem, not merely the last
  run's error.
- Assert (**negative**): **"No runs yet"** appears nowhere. A failing schedule claiming no runs is
  the original bug verbatim — the badge read by *agent*, the card read by *deployment*, and every
  trigger run has both deployment FKs NULL.
- Do: navigate to `/schedules`.
- Assert: the row's **LAST RUN** shows **failed** with the **same** reason. Two surfaces, one
  resolver — if they disagree, one of them is restating the predicate.

### Leg 7 — Alert honesty
- Assert: **Failure Alerts** does **not** show a bare green **"On"**.
- Assert: it warns alerts are on but **not delivered** / reach nobody, pointing at Settings.
  *(`alerting.py` returns at `if not trigger.alert_email`, so a green "On" claimed coverage that did
  not exist.)*

### Leg 8 — Reach production, and watch the verdict flip ⭐⭐⭐ (headline)
The load-bearing leg. Everything above is satisfied by a platform where nothing works;
**this** is the one that fails if `will_fire` has quietly become "always false".

**It is THREE steps, not one.** Publishing produces a catalog listing; the running
deployment is a separate action on a different page. The first run of this journey
assumed Publish was the whole thing and got a schedule that still would not fire —
see `docs/bugs/publish-does-not-create-a-production-deployment.md`.

- Do: agent page → **Publish** (eval-gated per Decision 20; the tooltip names where to
  run the eval). No toast fires — the only signal is the **Pending Review** badge on the
  next load. That silence is itself a known defect
  (`docs/bugs/publish-click-gives-no-feedback.md`) and a second click enqueues a
  **duplicate**, so click once.
- Do: **Admin ▸ Publish Queue** → **Promote to Catalog** → confirm.
- Assert: the queue reports eval provenance honestly — a version marked passed without a
  scored run shows **"No eval"**, not a fabricated score.
- Do: **Marketplace** → the artifact → **Deploy Latest**. ← the step everyone misses
- Do: wait for the production pod to reach **Running** (its own namespace,
  `production-{artifact}-{id8}`).
- Assert: `/schedules` now shows **WILL FIRE = Yes** for this row, `why_not` empty.
- Assert: **NEXT FIRE** shows a real countdown rather than `—`.
- Assert: the attention banner count returns to its leg-0 value.
- Assert (**class 2**): the health badge clears **without waiting for another run** — no
  new run row appeared, and it reads `degraded` (dispatchable, last run failed) rather
  than `failing` (cannot dispatch). Health is answered from config, not from history.
- Assert: the **Route to production** strip on the agent page shows all four steps done.
  Its last step reads `dispatch_error`, the same resolver the run door uses, so it cannot
  claim a schedule will fire when a fire would refuse.
- Do: fire the schedule again (as leg 5).
- Assert: the run is **not** refused for the environment reason.
- **Tolerance:** if the eval gate cannot pass (no working LLM credential) or there is no
  pod capacity, **stop and record it** — do not fake a pass. Legs 0-7 plus 9-12 still
  stand on their own.

### Leg 9 — Operate it from the Schedules screen: disarm, and prove it stuck ⭐⭐
**Class 5.** The old Disarm button PATCHed a field the API does not declare — 200 OK, success toast,
nothing written. Only a reload can tell a write that landed from one the server dropped.
- Assert: the row has exactly **one** arm control, and the ACTIONS column has **no** second
  Disarm button. *(Two controls for one column is how the dead one hid behind the working one.)*
- Do: click the arm toggle. Watch the **PATCH** in the network panel.
- Assert: ARM STATE flips to **Disarmed**; WILL FIRE becomes "this schedule is disabled"; NEXT FIRE
  clears.
- Do: **reload the page.**
- Assert: still **Disarmed**. *(This is the assert. The toast is not evidence.)*
- Assert: neighbouring rows are unchanged — the write hit one row.
- Do: click the toggle again to re-arm.
- Assert: **Armed**, WILL FIRE **Yes**, and **no stale disarm reason** is printed beside it. *(The
  workflow and agent PATCH handlers had drifted: only one cleared the disarm record on re-enable, so
  "disabled because the workflow was archived" survived next to a live schedule. A stale explanation
  is read as a current one.)*

### Leg 10 — Screen sanity: the filters partition, the banner does not cry wolf
- Assert: **All** = **Will fire** ∪ **Needs attention** ∪ **Disarmed**, with no row in two buckets
  and none missing from All.
- Do: disarm the row (leg 9), stay on the page.
- Assert: it moves to **Disarmed** and the attention count **drops**. *(A disarmed schedule is not
  firing *on purpose*. Counting it makes the badge alarm about its own success, and an alarm that
  cries wolf stops being read.)*
- Assert: the **Sidebar badge** equals the banner count. Two readers, one predicate — if they
  disagree, `needsAttention` has been restated somewhere.
- Do: re-arm.

### Leg 11 — Delete the artifact; the schedule must disarm itself ⭐⭐
**Class 4.** This is the leg that catches zombies, and the UI delete is exactly what produced 37 of
them in one click.
- Do: delete the agent through its **UI delete control** (confirm in the modal).
- Assert: it is gone from the agents list.
- Do: navigate to `/schedules`.
- Assert (**class 4**): the row is **still listed** — listing it *is* the feature; the zombies were
  invisible precisely because nothing listed them.
- Assert: **ARM STATE = Disarmed**, with the reason **"agent deleted"** and a date.
- Assert (**class 6, negative**): the pill does **not** read **Armed**. An "Armed" pill next to
  "agent deleted" means arm state is being derived from something that is not the trigger's own
  switch.
- Assert: it does **not** appear in the attention count — the lifecycle gate working as designed
  must not read as a problem.

### Leg 12 — Cleanup
- Do: delete the schedule row via the trash control (confirm).
- Assert: `/schedules` is back to its leg-0 count.
- Note: deleting an agent **soft-deletes** it (status → `deprecated`); it stays queryable for
  forensics. Only the trigger row is removed here.

---

## What this proves that the other layers cannot

| Layer | Covers | Blind to |
|---|---|---|
| `suite-94/95/96` (bash) | dispatch admissibility, lifecycle disarm, the endpoint's JSON, and that an undeclared field cannot masquerade as a write | anything on a screen; whether two surfaces *agree* |
| `SchedulesPage.test.tsx` (Vitest) | rendering per state, mutation routing by `artifact_kind` | `registryApi` is mocked wholesale — it asserted `disarmTrigger` **was called**, and it was. A mock cannot fail a call the server silently no-ops |
| `schedules-page.spec.ts` (Playwright) | real endpoint → real render, disarm survives reload, headless | nobody watches it; a correct-but-confusing screen still passes |
| **this journey** | a human sees the sandbox→production transition in order, and whether the verdict *flips* when the cause is fixed | — |

The **positive** asserts in leg 8 are the durable ones. Negative-only assertions cannot distinguish
"correct" from "nothing works" — which is the whole reason `will_fire` is computed from the same
predicate the scheduler reads rather than restated for the page.

## Notes — what I click, and what I won't
- **Authorized in-test actions — I DO perform these, because performing them IS the test:** create
  the agent (1), **Deploy** to sandbox (2), create/save the trigger (3), fire
  `/internal/runs/start` (5, 8), **Publish** (8), arm/disarm from the Schedules screen (9, 10),
  **Delete** the agent I created (11), delete its trigger row (12).
- **What I won't do:** reuse your tabs, touch any artifact this journey did not create, type a
  password, or click a side-effecting control that is not a leg above.
- Each Assert produces a screenshot; the run can be captured as a GIF on request.

## Further ideas (still open)
- [ ] **Workflow twin.** Every leg here has a composite-workflow counterpart, and workflow liveness
      is a *different* predicate (`status <> 'archived'`, not "published" — the stricter versions
      were each wrong once). A draft workflow's schedule currently reports `will_fire = Yes`; worth a
      leg once that is deliberately settled.
- [ ] **The webhook twin** from the sibling doc still applies: the gateway returns **202 to the
      sender** before dispatch is attempted, so a webhook against a sandbox-only agent tells the
      caller it succeeded.
- [ ] **Deploy-to-production via API.** Leg 8 covers the *Publish* leg (`production_deployments`).
      The other production leg — `deployments(environment='production')` — has no UI control, so it
      is untested here despite `resolve_dispatch_target` supporting both.
- [ ] _add your own …_
