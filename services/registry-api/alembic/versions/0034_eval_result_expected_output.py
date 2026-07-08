"""Add expected_output to eval_run_results

Revision ID: 0034
Revises: 0033

Adds expected_output TEXT column to eval_run_results table.
Idempotent (IF NOT EXISTS guard).
"""
from alembic import op
import sqlalchemy as sa

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE eval_run_results
        ADD COLUMN IF NOT EXISTS expected_output TEXT
    """)


def downgrade() -> None:
    op.drop_column("eval_run_results", "expected_output")
