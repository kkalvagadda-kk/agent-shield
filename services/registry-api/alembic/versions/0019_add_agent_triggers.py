"""add agent_triggers table

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-04
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID, TIMESTAMP

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_triggers",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", UUID(as_uuid=True), sa.ForeignKey("agents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("trigger_type", sa.String(16), nullable=False),
        sa.Column("cron_expression", sa.String(100), nullable=True),
        sa.Column("timezone", sa.String(50), nullable=True, server_default="UTC"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("token_hash", sa.String(128), nullable=True),
        sa.Column("filter_conditions", JSONB, nullable=True),
        sa.Column("created_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.CheckConstraint(
            "trigger_type IN ('schedule', 'webhook')",
            name="ck_agent_triggers_type",
        ),
    )
    op.create_index("idx_agent_triggers_agent", "agent_triggers", ["agent_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_triggers_agent")
    op.drop_table("agent_triggers")
