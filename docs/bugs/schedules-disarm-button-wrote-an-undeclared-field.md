# The Schedules page's Disarm button answered 200 and wrote nothing

**Found** 2026-08-01 (writing `studio/e2e/schedules-page.spec.ts`) · **Fixed** 2026-08-01, registry-api `0.2.252` / studio `0.1.176`

## Symptom

Clicking **Disarm** on a row of `/schedules` showed the success toast — "Disarmed
{agent} — it will not fire again until re-armed" — and the row went back to **Armed**
on the next refetch. The schedule kept firing.

A second, quieter symptom on the same page: **deprecated agents rendered an "Armed"
pill directly beside "this schedule is disabled"** — the lifecycle gate had disarmed
them correctly, and the page said otherwise.

## Root cause

Both symptoms are the same design error: **the page was built against a data model
that does not exist.**

It assumed two independent concepts — the author's `enabled` pause switch, and an
operator's `armed_at` / `disarmed_at` arming gesture. The schema has one column,
`agent_triggers.enabled`, plus `disabled_reason` / `disabled_at` (migration 0076) to
record who turned it off and why. `armed_by` is real; `armed_at` never existed.

That single wrong premise produced both failures:

1. **The write went nowhere.** `disarmTrigger` PATCHed `{"armed": false}`.
   `AgentTriggerUpdate` declares no `armed` field, so Pydantic dropped it, the
   handler's `for field, value in body.model_dump(exclude_none=True)` loop iterated an
   empty dict, and the request answered **200 having changed nothing**. An undeclared
   field is indistinguishable from a successful write at the status-code level.

2. **The read was fabricated.** To feed `isArmed(t) => t.armed_at != null`,
   `GET /api/v1/schedules` selected `tl.created_at AS armed_at` — non-null for every
   trigger ever created, so every row read as armed.

Two controls for one column is what let (1) go unnoticed: the **Enabled** toggle
beside the Disarm button wrote the real field, so the page was never fully inert, and
the broken control looked like the working one.

### Why the tests were green

- **Vitest** mocks `registryApi`, and asserted `expect(api.disarmTrigger).toHaveBeenCalledWith(...)`.
  It was called. A mock cannot fail a call the server accepts and ignores.
- **suite-96** asserted the endpoint's JSON shape and `will_fire`, but never read
  `armed_at` and never performed a write.

Neither layer could see the seam. The defect lived exactly between them, which is why
it took a Playwright spec driving the real endpoint into the real render to surface it.

## Fix

**Arm state IS `enabled`.** Collapsed to one concept and one control rather than adding
an `armed` column to make a redundant button work — two booleans with one observable
behaviour ("does this fire?") is the same defect as one field with two meanings, only
inverted, and three of the four states would have been indistinguishable.

- `trigger_lifecycle.apply_trigger_update` — **one** interpreter of a trigger PATCH,
  used by both `routers/triggers.py` and `routers/composite_workflows.py`. They had
  **already drifted**: the agent handler cleared the disarm record on re-enable, the
  workflow handler did not, so re-enabling a workflow schedule left "disabled because
  the workflow was archived" printed beside a live row. A stale explanation is read as
  a current one.
- `GET /schedules` no longer emits `armed_at`; `ScheduleListItem` drops the field.
- `lib/triggerArm.ts` derives arm state from `enabled`.
- `disarmTrigger` / `disarmWorkflowTrigger` **deleted**, not left as unused exports.
  The Schedules page has one arm control — the toggle — which calls the
  enable/disable endpoints that move the real column.
- The demo fetch shim models the real PATCH body, so the prototype cannot demonstrate
  an interaction the API refuses.

If arm and enable ever become genuinely separate, they need real **columns** and an arm
endpoint that can refuse (409 when there is nothing deployed to dispatch to) — not a
derived timestamp.

## Regression tests

Both fail against the buggy code:

- `scripts/e2e/suite-96-schedules-endpoint.sh`
  - `T-S96-007` — PATCH `{"armed": false}`, then **re-read the row**: `enabled` must be
    unchanged. An undeclared field may not masquerade as a write. *(This is the
    assertion whose absence let the bug ship: the old suite would have accepted the
    200.)*
  - `T-S96-008` — disarm via `enabled` **persists** and flips `will_fire` on re-read.
  - `T-S96-009` — re-enabling clears `disarm_reason` / `disarmed_at`.
- `studio/e2e/schedules-page.spec.ts` — real endpoint → real render: a lifecycle-disarmed
  row shows **Disarmed** (not "Armed"), and a disarm **survives a page reload**.
  Verified non-vacuous by inverting the assertion and confirming it fails.
- `studio/src/lib/triggerArm.test.ts` — `isArmed` consults `enabled` and nothing else.

## Lessons

1. **A 200 is not a write.** Any test of a mutation must re-read the row. Every
   assertion here was on a status code or a mock call, and both were true.
2. **Two controls for one field hide each other.** The working toggle masked the dead
   button.
3. **If the server has to synthesise a field to satisfy the UI, the UI is modelling
   something that does not exist.** `armed_at` from `created_at` was the tell, and it
   was written before anyone asked whether the column existed.
4. **Shared table + shared request model ⇒ shared handler.** The two PATCH routes had
   already diverged on a rule only one of them implemented.
