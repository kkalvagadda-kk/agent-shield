"""add skills table

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "skills",
        sa.Column("id", PG_UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("name", sa.String(256), nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("tool_ids", JSONB, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("status", sa.String(32), nullable=False, server_default=sa.text("'active'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.UniqueConstraint("name", "team", name="uq_skills_name_team"),
    )
    op.create_index("idx_skills_team", "skills", ["team"])


def downgrade():
    op.drop_index("idx_skills_team", "skills")
    op.drop_table("skills")
