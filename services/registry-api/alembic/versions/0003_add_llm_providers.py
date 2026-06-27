"""Add llm_providers table and LLM fields on agents + deployments

Creates llm_providers (Fernet-encrypted credentials, team-scoped).
Adds llm_provider_id FK to agents.
Adds llm_secret_name, llm_env_keys, llm_provider_type, llm_provider_model
to deployments (populated at deploy time by Registry API before deploy-controller
picks up the pending record).

Tables changed:
  + llm_providers                      — new
  ~ agents.llm_provider_id             — new nullable FK → llm_providers.id
  ~ deployments.llm_secret_name        — new nullable varchar
  ~ deployments.llm_env_keys           — new nullable JSONB
  ~ deployments.llm_provider_type      — new nullable varchar
  ~ deployments.llm_provider_model     — new nullable varchar

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Create llm_providers table ────────────────────────────────────
    op.create_table(
        "llm_providers",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("provider", sa.String(32), nullable=False),
        sa.Column("default_model", sa.String(256), nullable=False),
        sa.Column("credentials_encrypted", sa.Text, nullable=False),
        sa.Column("team", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("name", "team", name="uq_llm_providers_name_team"),
        sa.CheckConstraint(
            "provider IN ('anthropic','bedrock')",
            name="ck_llm_providers_provider",
        ),
    )
    op.create_index("idx_llm_providers_team", "llm_providers", ["team"])

    # ── 2. Add llm_provider_id FK to agents ──────────────────────────────
    op.add_column(
        "agents",
        sa.Column(
            "llm_provider_id",
            UUID(as_uuid=True),
            sa.ForeignKey("llm_providers.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("idx_agents_llm_provider_id", "agents", ["llm_provider_id"])

    # ── 3. Add LLM fields to deployments ─────────────────────────────────
    op.add_column("deployments", sa.Column("llm_secret_name", sa.String(256), nullable=True))
    op.add_column("deployments", sa.Column("llm_env_keys", JSONB, nullable=True))
    op.add_column("deployments", sa.Column("llm_provider_type", sa.String(32), nullable=True))
    op.add_column("deployments", sa.Column("llm_provider_model", sa.String(256), nullable=True))


def downgrade() -> None:
    # Remove deployment columns
    op.drop_column("deployments", "llm_provider_model")
    op.drop_column("deployments", "llm_provider_type")
    op.drop_column("deployments", "llm_env_keys")
    op.drop_column("deployments", "llm_secret_name")

    # Remove agents FK
    op.drop_index("idx_agents_llm_provider_id", "agents")
    op.drop_column("agents", "llm_provider_id")

    # Drop llm_providers table
    op.drop_index("idx_llm_providers_team", "llm_providers")
    op.drop_table("llm_providers")
