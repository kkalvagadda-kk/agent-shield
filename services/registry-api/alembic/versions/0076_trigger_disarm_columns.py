"""Record WHY a trigger was disarmed, and reap the triggers armed on dead artifacts.

WHY
---
No lifecycle path disarmed a trigger. `routers/agents.py::delete_agent` soft-deletes
(status -> 'deprecated') and `routers/composite_workflows.py::archive_workflow` sets
status -> 'archived'; neither touched `agent_triggers`. The scheduler filtered on
`t.enabled` alone. So artifact liveness had NO bearing on whether its schedule fired.

Measured on the EKS cluster before this landed: 37 triggers armed on dead artifacts —
8 archived workflows firing daily, a DRAFT workflow (`trigger-demo-flow`) firing every
15 minutes for days, and 13 triggers on soft-deleted agents. Most were created by the
e2e suites, whose cleanup archives the workflow and soft-deletes the agent — which by
design left the trigger armed. The suites were a zombie factory.

Demonstrated live during the Claude-in-Chrome journey: leg 8's UI delete produced
`cic-journey-sched, deprecated, 0 * * * *, enabled=True` in one click. The CLEANUP
step was itself the factory.

WHAT
----
`disabled_reason` / `disabled_at` make the disarm auditable: an operator opening a
disabled schedule can see it was the system, and why, rather than assuming a colleague
turned it off. The write-side disarm (`trigger_lifecycle.disarm_triggers`) sets them;
re-enabling through the trigger PATCH clears them, because that is a human's explicit
act and must not read as a system disarm afterwards.

BACKFILL SEMANTICS — disable, never delete. Reversible, and it preserves the record of
what was armed. Agreed rule: reactivating an artifact does NOT re-arm its triggers; the
operator must turn them back on deliberately (fail closed — a revoke locks the door).

"live" per the agreed definition: agents `status='active'`; workflows
`status='published'`. Draft workflows are included in the reap on purpose — a workflow
that has never been published has never passed the eval gate (Decision 20), so a cron
firing it unattended is exactly the state this migration exists to end.

Idempotent: ADD COLUMN IF NOT EXISTS, and the UPDATEs match only rows still armed.

Revision ID: 0076
Revises: 0075
"""
from alembic import op

revision = "0076"
down_revision = "0075"


def upgrade() -> None:
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS disabled_reason TEXT")
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS disabled_at TIMESTAMPTZ")

    # Reap: agents that are not active.
    op.execute(
        """
        UPDATE agent_triggers t
           SET enabled = false,
               disabled_reason = 'backfill 0076: agent is ' || a.status,
               disabled_at = now()
          FROM agents a
         WHERE t.agent_id = a.id
           AND a.status <> 'active'
           AND t.enabled
        """
    )
    # Reap: workflows that are not published (covers draft AND archived).
    op.execute(
        """
        UPDATE agent_triggers t
           SET enabled = false,
               disabled_reason = 'backfill 0076: workflow is ' || w.status,
               disabled_at = now()
          FROM workflows w
         WHERE t.workflow_id = w.id
           AND w.status <> 'published'
           AND t.enabled
        """
    )


def downgrade() -> None:
    # Only the columns come back off. The disarm itself is NOT reverted: re-arming
    # triggers on dead artifacts is the defect, and a downgrade must not recreate it.
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS disabled_reason")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS disabled_at")
