"""ONE definition of what happens to an artifact's triggers when the artifact dies.

WHY THIS MODULE EXISTS
----------------------
Nothing disarmed a trigger. `routers/agents.py::delete_agent` soft-deletes (status ->
'deprecated'), `routers/composite_workflows.py::archive_workflow` sets 'archived', and
neither touched `agent_triggers`. The scheduler filtered on `t.enabled` alone, so a
deleted agent's cron kept firing — 37 such triggers were live on the cluster, including
a never-published DRAFT workflow firing every 15 minutes for days.

The Claude-in-Chrome journey demonstrated it in one click: leg 8's UI delete produced
`deprecated` agent + `enabled=True` hourly schedule. The cleanup step was the factory.

TWO SIDES, DELIBERATELY
-----------------------
This is the WRITE side: liveness gates arming at the moment the artifact dies, so the
database cannot hold an armed trigger on a dead artifact. The READ side (a status
predicate in the scheduler query and the event-gateway's trigger lookup) is
defence-in-depth for the next lifecycle path that forgets to call this. Patching only
the read side would have been the bandaid: the gateway and the scheduler are separate
services, and fixing one leaves the other armed.

DISARM FOR REVERSIBLE TRANSITIONS; DELETE SCHEDULES ON AGENT DELETE
------------------------------------------------------------------
`enabled = false` plus a reason is the default: reversible, and it keeps the record of
what was armed. Re-arming is an explicit human act — reactivating an artifact does NOT
restore its triggers. A revoke must lock the door, not leave it ajar pending an
un-delete. Archive and quarantine use this and nothing else.

Agent DELETE additionally REMOVES the agent's `schedule` triggers
(`delete_schedule_triggers`). Deletion ends the artifact's life, and a disarmed
schedule on a deleted agent is inert — it cannot fire (disarmed in this same
transaction, and T-S95-004 proves the scheduler ignores it regardless) — so keeping it
only adds noise to an operations page that exists to surface schedules that MATTER.
63 of 100 schedule rows on the live cluster were this. Webhook triggers are exempt:
`webhook_clients.trigger_id` is ON DELETE CASCADE, so removing one would silently
destroy the applications registered against it.

NOT CALLED FROM UNDEPLOY/SUSPEND, on purpose. Undeploy is reversible infrastructure,
not artifact death; disarming there would silently lose schedules across a redeploy.
"Armed but not deployed to production" is handled by
`agent_endpoints.resolve_dispatch_target`, which refuses with a readable reason.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def disarm_triggers(
    db: AsyncSession,
    *,
    agent_id=None,
    workflow_id=None,
    reason: str,
) -> int:
    """Disable every armed trigger on ONE artifact. Returns how many were disarmed.

    Keyword-only, and exactly one of `agent_id` / `workflow_id` — the same explicit
    discrimination the DB CHECK `ck_agent_triggers_target` enforces on the rows. A
    single positional "id" would let a workflow id silently disarm an agent's
    triggers, which is the class of bug this repo keeps paying for.

    Runs in the CALLER'S transaction: it must commit or roll back together with the
    status change that motivated it, or the two can disagree — an agent marked
    deprecated with live triggers is exactly the state being eliminated.
    """
    if (agent_id is None) == (workflow_id is None):
        raise ValueError(
            "disarm_triggers requires exactly one of agent_id / workflow_id — "
            f"got agent_id={agent_id!r}, workflow_id={workflow_id!r}"
        )

    column = "agent_id" if agent_id is not None else "workflow_id"
    target = agent_id if agent_id is not None else workflow_id

    result = await db.execute(
        text(
            f"""
            UPDATE agent_triggers
               SET enabled = false,
                   disabled_reason = :reason,
                   disabled_at = :now
             WHERE {column} = :target
               AND enabled
            """
        ),
        {"reason": reason, "now": datetime.now(timezone.utc), "target": target},
    )
    count = result.rowcount or 0
    if count:
        logger.info(
            "disarmed %d trigger(s) on %s=%s — %s", count, column, target, reason
        )
    return count


async def delete_schedule_triggers(db: AsyncSession, *, agent_id) -> int:
    """Remove an agent's SCHEDULE triggers outright. Returns how many were deleted.

    WHY DELETE HERE WHEN THE MODULE RULE IS "DISARM, NEVER DELETE"
    -------------------------------------------------------------
    Disarming is right for *reversible* transitions — archive, quarantine — where the
    artifact may come back and the operator needs to know what was armed. Deletion is
    the end of the artifact's life, and a disarmed schedule on a deleted agent is not
    an operations concern: `delete_agent` disarms in the same transaction and
    T-S95-004 proves the scheduler ignores the row even if someone force-re-arms it.
    So it is inert, and inert rows on an operations page are noise — 63 of the 100
    schedule rows on the live cluster were exactly this.

    SCHEDULES ONLY, DELIBERATELY. `webhook_clients.trigger_id` is
    `ON DELETE CASCADE`, so deleting a webhook trigger silently destroys the
    applications registered against it and their credentials. Webhook triggers keep
    the disarm treatment; this function will not touch them.

    RUN LINKAGE IS LEFT ALONE. `agent_runs.trigger_id` is an unconstrained column,
    not a foreign key, so deleting the trigger strands the value rather than
    cascading. That is the better trade: the run row still records WHICH trigger
    fired it, which is the forensic question worth answering, and nulling it would
    destroy that to tidy a reference nothing enforces. A future migration adding an
    FK here must reckon with these orphans — that is the cost, stated.

    Not called from archive or quarantine. Quarantine is incident response, and
    destroying a schedule's configuration mid-incident removes evidence.
    """
    result = await db.execute(
        text(
            """
            DELETE FROM agent_triggers
             WHERE agent_id = :agent_id
               AND trigger_type = 'schedule'
            """
        ),
        {"agent_id": agent_id},
    )
    count = result.rowcount or 0
    if count:
        logger.info("deleted %d schedule trigger(s) on agent_id=%s", count, agent_id)
    return count


def apply_trigger_update(trigger, body) -> None:
    """Apply a PATCH body to a trigger row — the ONE place an update is interpreted.

    Both `routers/triggers.py` (agent) and `routers/composite_workflows.py` (workflow)
    PATCH the SAME table with the SAME `AgentTriggerUpdate` body, and each had its own
    copy of the setattr loop. The copies had already drifted: the agent handler cleared
    the disarm record on re-enable, the workflow handler did not — so re-enabling a
    workflow schedule left `disabled_reason` populated and the Schedules page rendered
    "disabled because the workflow was archived" beside a live, enabled schedule. A
    stale explanation reads as a current one.

    Arm state and `enabled` are the SAME field. There is no `armed` column, so a body
    carrying `armed` would be silently dropped by `exclude_none` and answer 200 for a
    write that never happened — which is exactly what the Schedules page's Disarm
    button did until this change. Callers disarm by setting `enabled=False`.
    """
    for field, value in body.model_dump(exclude_none=True).items():
        setattr(trigger, field, value)

    # Re-enabling is a HUMAN's deliberate act, so it clears the system disarm record.
    # A lifecycle disarm (`disarm_triggers` above) and an author's pause are the same
    # column; the reason is what tells them apart, and it must not outlive the disarm.
    if getattr(body, "enabled", None) is True:
        trigger.disabled_reason = None
        trigger.disabled_at = None

    trigger.updated_at = datetime.now(timezone.utc)
