"""
0011_agent_runs — agent_runs table (central invocation primitive)

Every agent invocation (production or playground) creates one row here.
Enables request-scoped observability: cost, latency, Langfuse trace linkage.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(256), nullable=False),
        sa.Column("agent_version_id", UUID(as_uuid=True), nullable=True),
        sa.Column("session_id", sa.String(256), nullable=True),
        sa.Column("user_id", sa.String(256), nullable=True),
        sa.Column("input", sa.Text, nullable=True),
        sa.Column("output", sa.Text, nullable=True),
        sa.Column("langfuse_trace_id", sa.String(256), nullable=True),
        sa.Column("cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("prompt_tokens", sa.Integer, nullable=True),
        sa.Column("completion_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("context", sa.String(32), nullable=False, server_default="production"),
        sa.Column("started_at", TIMESTAMP(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("completed_at", TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('running','completed','failed','blocked')",
            name="ck_agent_runs_status",
        ),
        sa.CheckConstraint(
            "context IN ('production','playground')",
            name="ck_agent_runs_context",
        ),
    )
    op.create_index("ix_agent_runs_agent_name", "agent_runs", ["agent_name"])
    op.create_index("ix_agent_runs_session_id", "agent_runs", ["session_id"])
    op.create_index("ix_agent_runs_started_at", "agent_runs", ["started_at"],
                    postgresql_ops={"started_at": "DESC"})


def downgrade() -> None:
    op.drop_index("ix_agent_runs_started_at", table_name="agent_runs")
    op.drop_index("ix_agent_runs_session_id", table_name="agent_runs")
    op.drop_index("ix_agent_runs_agent_name", table_name="agent_runs")
    op.drop_table("agent_runs")
