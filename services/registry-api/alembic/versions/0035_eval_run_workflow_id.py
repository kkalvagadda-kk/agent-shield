"""Add workflow_id to eval_runs

Revision ID: 0035
Revises: 0034

Adds workflow_id UUID column to eval_runs table so evals can target workflows.
Idempotent (IF NOT EXISTS guard).
"""
from alembic import op
import sqlalchemy as sa

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_runs
        ADD COLUMN IF NOT EXISTS workflow_id UUID
    """)


def downgrade() -> None:
    op.drop_column("eval_runs", "workflow_id")
