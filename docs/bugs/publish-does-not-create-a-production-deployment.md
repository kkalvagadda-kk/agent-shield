# The dispatch refusal told operators to Publish; publishing does not make a schedule fire

**Found** 2026-08-02 (Claude-in-Chrome schedule-lifecycle journey, leg 8) · **Fixed** 2026-08-02, registry-api `0.2.253`

## Symptom

A scheduled agent's overview, Settings panel, and Schedules row all said:

> agent 'X' has no running production deployment — it is deployed to sandbox. Schedule and
> webhook triggers dispatch to production. **Publish the agent** (Studio: agent page → Publish;
> requires a passing eval) or deploy it to production via the API, then re-enable the trigger.

Following that advice exactly — Publish → admin approves → agent shows in the catalog — left
`will_fire` **still false**, with the **same message** telling the operator to publish an agent
that is already published.

## Root cause

**Publishing produces a catalog listing, not a running deployment.** Reaching production is three
steps, and the message named one:

| # | Step | Row it writes |
|---|---|---|
| 1 | Agent page → **Publish** | `publish_requests` (status `pending_review`) |
| 2 | Admin → Publish Queue → **Promote to Catalog** | `published_artifacts` |
| 3 | Marketplace → artifact → **Deploy Latest** | **`production_deployments`** ← the one that matters |

`routers/admin.py::approve_publish_request` never references `ProductionDeployment` — grep the
file, the symbol does not appear. And `ProductionDeployment.artifact_id` FKs to
`published_artifacts.id`, *not* to the agent, which is why step 2 cannot create it even in
principle: the artifact it would attach to is what step 2 is busy creating.

`resolve_dispatch_target` was right the whole time. A published-but-not-deployed agent genuinely
has no running production deployment. The defect was entirely in the remedy.

### Why this is worse than the bug it replaced

This is the third wording of the same sentence, and each fix moved one notch:

1. **"deploy the agent to production"** — *true of the API, unreachable from the UI.* Studio's
   Deploy button opens a "Deploy to sandbox" modal with no environment choice. The reader hunts for
   a button that does not exist. Fixed in `0.2.244`.
2. **"Publish the agent"** — *reachable, but insufficient.* Strictly worse in one respect: the
   operator does the named thing, watches it succeed, and receives the identical message. Advice
   that completes and changes nothing is harder to diagnose than advice that obviously cannot be
   followed.
3. **All three steps named.** ← this fix

The lesson is not "name a reachable control". It is **name a remedy that resolves the stated
cause** — and verify it does by following it.

## Fix

`agent_endpoints.py::resolve_dispatch_target` now emits:

> …publish it, approve it in Admin → Publish Queue, then deploy it from Marketplace → the artifact
> → Deploy Latest (all three are required — publishing alone only creates the catalog listing),
> then re-enable the trigger.

The parenthetical is deliberate: it pre-empts the exact misreading that produced the loop, for an
operator who has already published once and is re-reading the message.

## Regression test

`scripts/e2e/suite-96-schedules-endpoint.sh` **T-S96-010** — asserts the live `why_not` for a
sandbox-only agent names *all three* steps (publish · queue/approve · marketplace/deploy latest).
Fails against every earlier wording.

Deliberately asserts the **three concepts**, not the sentence: a message test pinned to exact
prose becomes a rename-detector that everyone learns to update without reading.

## Not changed, on purpose

**Stored `agent_runs.error_message` keeps the old wording forever.** The message is captured when a
run fails; improving the sentence must not rewrite history, or the record stops being a record.
This surfaced immediately — a schedule page showed the old text under LAST RUN while its live
`why_not` read "this schedule is disabled". Both correct: `dispatch_error` is current,
`last_error` is historical (Decision 32).

## Lessons

1. **Follow your own error message before shipping it.** Two of the three wordings would have died
   the first time anyone did.
2. **"Reachable" is not "sufficient."** A remedy that runs to completion and resolves nothing is a
   worse failure mode than one that visibly cannot be started.
3. **Three-step flows need the whole path in the message**, not the first step. The operator cannot
   see the other two: step 3 lives on a different page, under a different nav section, against a
   different table.
