"""Apply the new delete rule to the backlog: remove schedule triggers on deleted agents.

Revision ID: 0078
Revises: 0077
Create Date: 2026-08-02

WHY
---
`delete_agent` now REMOVES an agent's `schedule` triggers rather than only disarming
them (see trigger_lifecycle.delete_schedule_triggers). Rows created before that change
are still sitting there — 63 of the 100 schedule rows on the live cluster belong to
agents that were deleted long ago. Leaving them would mean the rule applies only to
agents deleted from today onward, and the Schedules page — an operations surface —
would stay 2/3 full of artifacts nobody can act on.

WHAT IT TOUCHES, EXACTLY
------------------------
    trigger_type = 'schedule'          -- webhooks are exempt, see below
    AND agent_id IS NOT NULL           -- workflow triggers are not in scope
    AND the owning agent.status = 'deprecated'   -- i.e. deleted

Nothing else. An armed schedule on a LIVE agent is untouched, a webhook trigger is
untouched, and a workflow's triggers are untouched.

WHY WEBHOOKS ARE EXEMT
----------------------
`webhook_clients.trigger_id` is ON DELETE CASCADE. Deleting a webhook trigger silently
destroys every application registered against it, along with their credentials. That is
real data belonging to whoever integrated with the hook, and it is not this migration's
to discard.

WHY THIS IS SAFE TO DELETE RATHER THAN DISARM
---------------------------------------------
Every row it removes is already inert: its agent is deprecated, so
`trigger_liveness.artifact_is_live` is false and BOTH read-side consumers (the scheduler
query and the event-gateway lookup) filter it out. suite-95's T-S95-004 asserts exactly
that — a force-re-armed trigger on a dead agent is invisible to the scheduler. So this
deletes rows that cannot fire and cannot be made to fire without first reactivating
their agent.

WHAT IS LOST
------------
The cron expression and input payload of schedules on already-deleted agents. If a
deprecated agent is ever reactivated, its schedules must be re-authored — which was
already true, since re-arming was always an explicit human act and the disarm record
was never restored automatically.

`agent_runs.trigger_id` values pointing at these rows are LEFT AS THEY ARE. The column
is not a foreign key, so nothing cascades and nothing breaks; the run row keeps its
record of which trigger fired it, which is the forensic question worth preserving.

IRREVERSIBLE. The downgrade cannot restore deleted rows and does not pretend to.
"""
from alembic import op

revision = "0078"
down_revision = "0077"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM agent_triggers t
         USING agents a
         WHERE t.agent_id = a.id
           AND t.trigger_type = 'schedule'
           AND a.status = 'deprecated'
        """
    )


def downgrade() -> None:
    """No-op. Deleted rows cannot be reconstructed.

    Stated rather than raised: a downgrade that raises would block rolling back the
    rest of a release for data this migration deliberately discarded. The schedules are
    gone; the rollback is still allowed to proceed.
    """
