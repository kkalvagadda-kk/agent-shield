# Schedules kept firing on deleted, archived and never-published artifacts

**Found:** 2026-07-28 (reported: *"I see schedules being triggered even when the agents are deleted/stopped"*)
**Fixed:** 2026-07-29 — registry-api `0.2.247`, scheduler `0.1.2`, event-gateway `0.1.5`, migration `0076`

## Symptom

Measured on the EKS cluster: **37 triggers armed on dead artifacts.**

| Artifacts | State | Cron |
|---|---|---|
| `s71-sequential-*`, `s71-conditional-*`, `s71-handoff-*`, `s71-supervisor-*`, `s71-wf-*`, `s70-wf-*`, `s34-wf-*` (8) | **archived** | `0 0 * * *` / `0 9 * * 1` |
| `trigger-demo-flow` | **draft** — never published | `*/15 * * * *` |
| 13 triggers on soft-deleted agents | **deprecated** | various |

A never-published draft workflow had been firing every fifteen minutes for days. Its runs all
failed, and its `error_message` was empty, so it was invisible as well as pointless.

## Root cause

**Nothing disarmed a trigger, and nothing on the read side cared.**

```python
# routers/agents.py::delete_agent — soft delete
agent.status = "deprecated"     # deployments -> 'terminating'
                                # agent_triggers: untouched

# routers/composite_workflows.py::archive_workflow — three lines
wf.status = "archived"          # agent_triggers: untouched
```

And the scheduler's query filtered on `t.enabled` **alone** — no `a.status`, no `w.status`. So
artifact liveness had no bearing whatsoever on whether its schedule fired. Same for the
event-gateway's `_TRIGGER_SQL`: a deleted agent's webhook stayed live.

### The e2e suites were the main producer

`s71-*`, `s70-*`, `s34-*` are suite fixtures. Their cleanup archives the workflow and
soft-deletes the agent — which, by design, **left the trigger armed**. Every suite run minted
more. Running the suites during this session added ~15 more, which is the claim proven rather
than argued.

### Demonstrated in one click

The Claude-in-Chrome journey's **leg 8 — the cleanup step** — produced it live. The confirm
modal says it outright:

> Delete agent "cic-journey-sched"? This **soft-deletes** it (status → deprecated) and removes
> it from the active list.

Immediately afterwards:

```
cic-journey-sched   agent_status=deprecated   schedule  0 * * * *   enabled=True
```

The step meant to tidy up was manufacturing the defect.

## Fix

**Write side is the control.** `trigger_lifecycle.disarm_triggers(db, *, agent_id=|workflow_id=,
reason=)` — one definition, keyword-only so a workflow id can never silently disarm an agent's
triggers, running in the **caller's transaction** so the disarm and the status change commit
together. Called from:

| Call site | Reason |
|---|---|
| `delete_agent` | `agent deleted` |
| `quarantine_agent` | `agent quarantined` |
| `archive_workflow` | `workflow archived` |

Quarantine matters more than it looks: the pod is deliberately left running for forensics, so
disarming the triggers is the only thing standing between "quarantined" and "still executing on
a timer".

**Read side is defence in depth**, in **both** consumers — `scheduler/main.py` and
`event-gateway/webhook_auth.py`. They are separate services; filtering one and not the other is
the bandaid, and would have left every deleted agent's webhook live. A filtered-out trigger is
simply not found, so the gateway's existing `_DENY` path returns the uniform 401 — no new
branch, no enumeration oracle.

**Disarm, never delete.** `enabled=false` + `disabled_reason` + `disabled_at` (migration
`0076`), which also reaps the 37 existing rows. Reversible, and it keeps the record of what was
armed.

**Re-arming is an explicit human act.** Reactivating an artifact does **not** restore its
triggers — a revoke must lock the door, not leave it ajar pending an un-delete. A human
`PATCH {enabled: true}` clears `disabled_reason`, because a stale explanation sitting on an
armed trigger is read as current and is worse than none.

**Deliberately NOT called from undeploy/suspend.** Undeploy is reversible infrastructure, not
artifact death; disarming there would silently lose schedules across a redeploy. "Armed but not
deployed to production" is `resolve_dispatch_target`'s job, and it refuses with a readable
reason — see `trigger-dispatch-environment-mismatch.md`.

## Definition of "live"

Agents `status='active'`; workflows `status='published'`. **Draft workflows are included in the
reap on purpose** — an unpublished workflow has never passed the eval gate (Decision 20), so a
cron firing it unattended is precisely the state being eliminated. Note this is stricter than
`internal.py:194`, which rejects only `archived`; the difference is recorded as a follow-up.

## Tests — `scripts/e2e/suite-95-trigger-lifecycle-disarm.sh`

Red against `0.2.246`:

```
FAIL T-S95-001 DELETE agent …     delete=204 before_enabled=True after_enabled=True reason=None
FAIL T-S95-002 ARCHIVE workflow … archive=204 enabled=True reason=None
FAIL T-S95-003 QUARANTINE agent … quarantine=200 enabled=True reason=None
FAIL T-S95-005 re-enable clears the reason …
PASS T-S95-006 NEGATIVE CONTROL: a LIVE agent's trigger stays armed
```

`T-S95-000` is a **source parity grep** over both consumers. It exists because `T-S95-004`
asserts the read filter using a *copy* of the scheduler's query embedded in the driver — a copy
passes whether or not the real scheduler still has the predicate, i.e. it would be testing the
test. The grep pins the actual source. (Caught while reviewing why 004 went green against
unfixed code.)

The probe is deliberately **schema-tolerant**: run against a database without `0076` and the
cases fail on the real defect ("enabled stayed true after delete") rather than crashing on a
missing column. A crash tells you the column is absent; an assertion tells you the trigger is
still armed, which is the thing under test.

## Lessons

- **A soft delete is a lifecycle event, not a flag flip.** Everything that keys off "is this
  artifact alive" has to be told. Grep for the artifact's id, not just its status column.
- **When two services read the same table, a filter in one is not a fix.** The scheduler and
  the gateway had to change together.
- **A cleanup step that produces the defect is worse than no cleanup**, because it runs
  constantly and looks responsible. The suites had been quietly filling the scheduler for weeks.
- **A test that mirrors the implementation is testing itself.** `T-S95-004` embedded a copy of
  the query it was meant to verify and passed against broken code. Parity greps exist for
  exactly this.
