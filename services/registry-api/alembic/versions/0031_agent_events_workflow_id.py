"""Add workflow_id to agent_events

Revision ID: 0031
Revises: 0030

Adds a nullable workflow_id UUID column to agent_events so the event gateway
can record which composite workflow was targeted when an event is matched to a
workflow trigger. Idempotent upgrade; guarded downgrade.
"""
from alembic import op

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS workflow_id UUID")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_events DROP COLUMN IF EXISTS workflow_id")
