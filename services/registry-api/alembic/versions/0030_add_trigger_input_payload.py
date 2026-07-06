"""Add input_payload to agent_triggers (per-schedule job parameters)

Revision ID: 0030
Revises: 0029

A schedule trigger carries an optional JSON `input_payload` — the per-job
parameters fed to the agent on each fire. This lets ONE deployed agent (with
generic, reusable instructions) serve MANY scheduled jobs with different
parameters (agent_triggers.agent_id is not unique). Nullable; idempotent.
"""
from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS input_payload JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS input_payload")
