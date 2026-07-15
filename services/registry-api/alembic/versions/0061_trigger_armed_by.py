"""WS-2 T005 — capture the authorizing human who armed a daemon trigger.

Adds `agent_triggers.armed_by` — the sub of the human who armed (created) a
trigger. A daemon trigger-run stamps `agent_runs.run_by` with the agent's
service identity, but audit + approval must still read WHO authorized the
standing arm: "service:X on behalf of {armed_by}". Set at trigger-arm time
(producer = routers/triggers.py, routers/composite_workflows.py).

Nullable — pre-existing triggers backfill lazily (an un-armed legacy trigger
has `armed_by=NULL`; audit shows "unknown armer" rather than blocking). New
arms always set it. Idempotent (`IF [NOT] EXISTS`), single statement,
data-preserving.
"""

from alembic import op

revision = "0061"
down_revision = "0060"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS armed_by VARCHAR(256)")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS armed_by")
