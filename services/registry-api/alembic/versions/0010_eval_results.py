"""
0010_eval_results — eval_runs + eval_run_results tables

Adds tables for the Eval Runner feature:
- eval_runs: tracks evaluation runs against a dataset
- eval_run_results: per-item result rows with judge score
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "eval_runs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column(
            "agent_version_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "dataset_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="pending",
        ),
        sa.Column("total_items", sa.Integer(), nullable=True),
        sa.Column("passed_count", sa.Integer(), nullable=True),
        sa.Column("failed_count", sa.Integer(), nullable=True),
        sa.Column("overall_score", sa.Float(), nullable=True),
        sa.Column("started_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["playground_datasets.id"],
            ondelete="RESTRICT",
        ),
    )
    op.create_index("idx_eval_runs_user_id", "eval_runs", ["user_id"])

    op.create_table(
        "eval_run_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "eval_run_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("dataset_item_idx", sa.Integer(), nullable=False),
        sa.Column("input_message", sa.Text(), nullable=True),
        sa.Column("response", sa.Text(), nullable=True),
        sa.Column("judge_score", sa.Float(), nullable=True),
        sa.Column("judge_reasoning", sa.Text(), nullable=True),
        sa.Column("passed", sa.Boolean(), nullable=True),
        sa.Column("langfuse_trace_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_eval_run_results_eval_run_id",
        "eval_run_results",
        ["eval_run_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_eval_run_results_eval_run_id", table_name="eval_run_results"
    )
    op.drop_table("eval_run_results")
    op.drop_index("idx_eval_runs_user_id", table_name="eval_runs")
    op.drop_table("eval_runs")
