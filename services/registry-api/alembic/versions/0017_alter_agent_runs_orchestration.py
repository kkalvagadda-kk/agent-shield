"""alter agent_runs — add orchestration fields

Add trigger_type, run_by, team, thread_id, parent_run_id, schedule_id,
trigger_id, trigger_payload, error_message.  Widen status CHECK to include
queued, awaiting_approval, cancelled.

Revision ID: 0017
Revises: 0016
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_runs",
        sa.Column(
            "trigger_type",
            sa.String(16),
            nullable=True,
            server_default="manual",
        ),
    )
    op.add_column("agent_runs", sa.Column("run_by", sa.String(255), nullable=True))
    op.add_column("agent_runs", sa.Column("team", sa.String(100), nullable=True))
    op.add_column("agent_runs", sa.Column("thread_id", sa.String(255), nullable=True))
    op.add_column(
        "agent_runs",
        sa.Column("parent_run_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("schedule_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_runs",
        sa.Column("trigger_id", UUID(as_uuid=True), nullable=True),
    )
    op.add_column("agent_runs", sa.Column("trigger_payload", JSONB, nullable=True))
    op.add_column("agent_runs", sa.Column("error_message", sa.Text(), nullable=True))

    op.create_foreign_key(
        "fk_agent_runs_parent",
        "agent_runs",
        "agent_runs",
        ["parent_run_id"],
        ["id"],
    )

    op.create_check_constraint(
        "ck_agent_runs_trigger_type",
        "agent_runs",
        "trigger_type IN ('manual', 'api', 'schedule', 'webhook')",
    )

    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        "status IN ('queued', 'running', 'completed', 'failed', 'blocked', 'awaiting_approval', 'cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_agent_runs_trigger_type", "agent_runs", type_="check")
    op.drop_constraint("fk_agent_runs_parent", "agent_runs", type_="foreignkey")
    op.drop_constraint("ck_agent_runs_status", "agent_runs", type_="check")
    op.create_check_constraint(
        "ck_agent_runs_status",
        "agent_runs",
        "status IN ('running', 'completed', 'failed', 'blocked')",
    )
    for col in (
        "trigger_type", "run_by", "team", "thread_id",
        "parent_run_id", "schedule_id", "trigger_id",
        "trigger_payload", "error_message",
    ):
        op.drop_column("agent_runs", col)
