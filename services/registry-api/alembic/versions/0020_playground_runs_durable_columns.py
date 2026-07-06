"""Add execution_shape, input_payload, trigger columns, and output_text to playground_runs.

Revision ID: 0020
Revises: 0019
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "playground_runs",
        sa.Column("execution_shape", sa.String(16), nullable=False, server_default="reactive"),
    )
    op.add_column(
        "playground_runs",
        sa.Column("input_payload", JSONB, nullable=True),
    )
    op.add_column(
        "playground_runs",
        sa.Column("trigger_type", sa.String(16), nullable=True),
    )
    op.add_column(
        "playground_runs",
        sa.Column("trigger_payload", JSONB, nullable=True),
    )
    op.add_column(
        "playground_runs",
        sa.Column("output_text", sa.Text, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("playground_runs", "output_text")
    op.drop_column("playground_runs", "trigger_payload")
    op.drop_column("playground_runs", "trigger_type")
    op.drop_column("playground_runs", "input_payload")
    op.drop_column("playground_runs", "execution_shape")
