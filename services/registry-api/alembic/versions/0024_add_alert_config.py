"""Add alert config columns to agent_triggers

Revision ID: 0024
Revises: 0023

Scheduled and event-driven agents can notify an operator when a run fails.
Adds:
  • alert_email      VARCHAR(255) nullable — recipient for failure alerts
  • alert_on_failure BOOLEAN default true  — whether to alert at all

Idempotent (IF NOT EXISTS) so re-runs against a partially-migrated DB are safe.
"""
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE agent_triggers "
        "ADD COLUMN IF NOT EXISTS alert_email VARCHAR(255)"
    )
    op.execute(
        "ALTER TABLE agent_triggers "
        "ADD COLUMN IF NOT EXISTS alert_on_failure BOOLEAN NOT NULL DEFAULT true"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS alert_on_failure")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS alert_email")
