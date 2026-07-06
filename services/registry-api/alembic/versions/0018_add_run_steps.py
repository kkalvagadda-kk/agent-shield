"""add run_steps table

Revision ID: 0018
Revises: 0017
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, TIMESTAMP

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "run_steps",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("run_id", UUID(as_uuid=True), sa.ForeignKey("agent_runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("step_number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="pending"),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("approval_id", UUID(as_uuid=True), sa.ForeignKey("approvals.id"), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.UniqueConstraint("run_id", "step_number", name="uq_run_steps_run_step"),
        sa.CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed', 'awaiting_approval', 'cancelled')",
            name="ck_run_steps_status",
        ),
    )
    op.create_index("idx_run_steps_run_id", "run_steps", ["run_id"])


def downgrade() -> None:
    op.drop_index("idx_run_steps_run_id")
    op.drop_table("run_steps")
