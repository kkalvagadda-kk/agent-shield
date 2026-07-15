"""WS-2 T014 — configurable daemon-approval reviewer role per trigger.

Adds `agent_triggers.approver_role` — the reviewer scope a daemon trigger's
approvals route to. WS-2 T011 derives an approval's reviewer scope as
`getattr(trig, "approver_role", None) or "agent:reviewer"`; this migration
turns that forward-compat `getattr` into a real, persisted column so a
daemon trigger's approver role is configurable at arm/edit time
(producers = routers/triggers.py, routers/composite_workflows.py).

Nullable — legacy triggers backfill lazily with NULL, and T011 falls back to
the `"agent:reviewer"` default for them. New/edited triggers set it explicitly.
Idempotent (`IF [NOT] EXISTS`), single statement, data-preserving.
"""

from alembic import op

revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS approver_role VARCHAR(256)")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS approver_role")
