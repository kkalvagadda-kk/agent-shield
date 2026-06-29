"""
0009_playground — playground_runs + playground_datasets tables

Adds tables needed for the Playground feature:
- playground_runs: tracks per-user agent test runs
- playground_datasets: named JSONB collections of test items
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "playground_runs",
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
        sa.Column("context", sa.Text(), nullable=False, server_default="playground"),
        sa.Column("sandbox", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("input_message", sa.Text(), nullable=True),
        sa.Column("langfuse_trace_id", sa.Text(), nullable=True),
        sa.Column(
            "started_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "completed_at", sa.TIMESTAMP(timezone=True), nullable=True
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default="running",
        ),
        sa.ForeignKeyConstraint(
            ["agent_version_id"],
            ["agent_versions.id"],
            ondelete="SET NULL",
        ),
    )
    op.create_index(
        "idx_playground_runs_user_id", "playground_runs", ["user_id"]
    )
    op.create_index(
        "idx_playground_runs_agent", "playground_runs", ["agent_name"]
    )

    op.create_table(
        "playground_datasets",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("owner_user_id", sa.Text(), nullable=False),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column(
            "items",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "idx_playground_datasets_owner",
        "playground_datasets",
        ["owner_user_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_playground_datasets_owner", table_name="playground_datasets")
    op.drop_table("playground_datasets")
    op.drop_index("idx_playground_runs_agent", table_name="playground_runs")
    op.drop_index("idx_playground_runs_user_id", table_name="playground_runs")
    op.drop_table("playground_runs")
