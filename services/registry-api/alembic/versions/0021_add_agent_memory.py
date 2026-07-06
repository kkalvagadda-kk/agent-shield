"""Add agent_memory table

Revision ID: 0021
Revises: 0020
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_memory",
        sa.Column("id", UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("agent_name", sa.String(256), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("thread_id", sa.String(256), nullable=False),
        sa.Column("user_id", sa.String(256), nullable=True),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("message_index", sa.Integer, nullable=False),
        sa.Column("session_id", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "role IN ('user','assistant','system','tool')",
            name="ck_agent_memory_role",
        ),
    )
    op.create_index("ix_agent_memory_thread_msg", "agent_memory", ["thread_id", "message_index"])
    op.create_index("ix_agent_memory_agent_team", "agent_memory", ["agent_name", "team"])


def downgrade() -> None:
    op.drop_index("ix_agent_memory_agent_team")
    op.drop_index("ix_agent_memory_thread_msg")
    op.drop_table("agent_memory")
