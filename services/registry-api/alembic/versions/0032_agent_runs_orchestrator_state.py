"""Add orchestrator_state to agent_runs

Revision ID: 0032
Revises: 0031

Adds a nullable orchestrator_state JSONB column to agent_runs. It holds the
durable checkpoint for a PARENT composite-workflow run when a member pauses for
HITL approval, so the run can resume and advance the tree after the approval is
decided. Idempotent upgrade; guarded downgrade.
"""
from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE agent_runs ADD COLUMN IF NOT EXISTS orchestrator_state JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS orchestrator_state")
