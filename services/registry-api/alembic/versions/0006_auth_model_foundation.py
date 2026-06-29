"""auth model foundation: agent_class, publish_status, agent_identities

Adds two new columns to the agents table:
  - agent_class: 'daemon' | 'user_delegated' (K8s SA identity class)
  - publish_status: 'private' | 'pending_review' | 'published' (lifecycle gate)

These are separate from the existing 'status' column (active/archived/deprecated/quarantined),
which tracks operational state. publish_status tracks the authoring/sharing lifecycle.

Also adds agent_identities table to record K8s SA subjects provisioned at deploy time.

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-28
"""
from alembic import op
import sqlalchemy as sa
import sqlalchemy.dialects.postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    # ── agents: agent_class column ────────────────────────────────────────────
    op.add_column(
        "agents",
        sa.Column(
            "agent_class",
            sa.String(32),
            nullable=True,  # nullable to preserve existing rows
        ),
    )
    op.create_check_constraint(
        "ck_agents_agent_class",
        "agents",
        "agent_class IS NULL OR agent_class IN ('daemon', 'user_delegated')",
    )

    # ── agents: publish_status column ─────────────────────────────────────────
    op.add_column(
        "agents",
        sa.Column(
            "publish_status",
            sa.String(32),
            nullable=False,
            server_default="private",
        ),
    )
    op.create_check_constraint(
        "ck_agents_publish_status",
        "agents",
        "publish_status IN ('private', 'pending_review', 'published')",
    )
    op.create_index("idx_agents_publish_status", "agents", ["publish_status"])

    # ── agent_identities table ────────────────────────────────────────────────
    op.create_table(
        "agent_identities",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("agent_name", sa.String(128), nullable=False),
        sa.Column(
            "deployment_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
        # Full K8s SA subject: system:serviceaccount:{namespace}:{sa-name}
        sa.Column("sa_subject", sa.String(512), nullable=False),
        sa.Column("sa_namespace", sa.String(256), nullable=False),
        sa.Column(
            "provisioned_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["agent_name"], ["agents.name"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["deployment_id"], ["deployments.id"], ondelete="SET NULL"
        ),
    )
    op.create_index(
        "idx_agent_identities_agent_name",
        "agent_identities",
        ["agent_name"],
    )
    op.create_index(
        "idx_agent_identities_sa_subject",
        "agent_identities",
        ["sa_subject"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )


def downgrade():
    op.drop_index("idx_agent_identities_sa_subject", table_name="agent_identities")
    op.drop_index("idx_agent_identities_agent_name", table_name="agent_identities")
    op.drop_table("agent_identities")

    op.drop_index("idx_agents_publish_status", table_name="agents")
    op.drop_constraint("ck_agents_publish_status", "agents", type_="check")
    op.drop_column("agents", "publish_status")

    op.drop_constraint("ck_agents_agent_class", "agents", type_="check")
    op.drop_column("agents", "agent_class")
