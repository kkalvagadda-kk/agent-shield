"""asset_lifecycle: publish_requests, asset_grants, grant_audit, approval_authority, asset_visible

Adds the asset lifecycle tables for Phase 9.2:
  - publish_requests: tracks publish workflow submissions
  - asset_grants: records which teams have access to which assets
  - grant_audit: append-only log of grant actions
  - approval_authority: records who can approve publish requests per resource
  - asset_visible: SQL helper function for visibility checks

Also expands deployments.status to include 'gate_failed' (deploy-controller pre-flight gate).

Revision ID: 0007
Revises: 0006
Create Date: 2026-06-29
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    # ── publish_requests ──────────────────────────────────────────────────────
    op.create_table(
        "publish_requests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("submitted_by", sa.Text, nullable=False),
        sa.Column(
            "submitted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "status",
            sa.String(32),
            nullable=False,
            server_default=sa.text("'pending_review'"),
        ),
        sa.Column("highest_risk_level", sa.String(16), nullable=False),
        sa.Column(
            "dependency_declaration",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
        sa.Column("reviewed_by", sa.Text, nullable=True),
        sa.Column("reviewed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("review_notes", sa.Text, nullable=True),
        sa.CheckConstraint(
            "asset_type IN ('tool','agent','skill','workflow')",
            name="ck_publish_requests_asset_type",
        ),
        sa.CheckConstraint(
            "status IN ('pending_review','approved','rejected')",
            name="ck_publish_requests_status",
        ),
        sa.CheckConstraint(
            "highest_risk_level IN ('low','medium','high')",
            name="ck_publish_requests_risk_level",
        ),
    )
    op.create_index(
        "idx_publish_requests_asset",
        "publish_requests",
        ["asset_id", "status"],
    )

    # ── asset_grants ──────────────────────────────────────────────────────────
    op.create_table(
        "asset_grants",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("asset_type", sa.String(32), nullable=False),
        sa.Column("grantee_team", sa.Text, nullable=False),
        sa.Column("granted_by", sa.Text, nullable=False),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "asset_type IN ('tool','agent','skill','workflow')",
            name="ck_asset_grants_asset_type",
        ),
    )
    op.create_index(
        "idx_asset_grants_lookup",
        "asset_grants",
        ["asset_id", "grantee_team", "revoked_at", "expires_at"],
    )

    # ── grant_audit ──────────────────────────────────────────────────────────
    op.create_table(
        "grant_audit",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("admin_id", sa.Text, nullable=False),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("asset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("grantee_team", sa.Text, nullable=False),
        sa.Column(
            "timestamp",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "action IN ('created','revoked','expired')",
            name="ck_grant_audit_action",
        ),
    )

    # ── approval_authority ────────────────────────────────────────────────────
    op.create_table(
        "approval_authority",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("resource_type", sa.String(16), nullable=False),
        sa.Column("resource_id", sa.Text, nullable=False),
        sa.Column("approver_user_id", sa.Text, nullable=True),
        sa.Column("approver_role", sa.Text, nullable=True),
        sa.Column("granted_by", sa.Text, nullable=False),
        sa.Column(
            "granted_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.CheckConstraint(
            "resource_type IN ('agent','tool','skill')",
            name="ck_approval_authority_resource_type",
        ),
        sa.CheckConstraint(
            "approver_user_id IS NOT NULL OR approver_role IS NOT NULL",
            name="ck_approval_authority_approver",
        ),
    )
    op.create_index(
        "idx_approval_authority_resource",
        "approval_authority",
        ["resource_type", "resource_id", "revoked_at"],
    )

    # ── asset_visible function ────────────────────────────────────────────────
    op.execute(
        """
        CREATE OR REPLACE FUNCTION asset_visible(
          p_asset_id UUID, p_user_sub TEXT, p_user_team TEXT
        ) RETURNS BOOLEAN AS $$
          SELECT EXISTS (
            SELECT 1 FROM agents
            WHERE id = p_asset_id AND created_by = p_user_sub

            UNION ALL

            SELECT 1 FROM agents a
            JOIN asset_grants g ON g.asset_id = a.id
            WHERE a.id = p_asset_id
              AND a.publish_status = 'published'
              AND g.grantee_team = p_user_team
              AND g.revoked_at IS NULL
              AND (g.expires_at IS NULL OR g.expires_at > NOW())
          );
        $$ LANGUAGE sql STABLE;
        """
    )

    # ── deployments: expand status to include gate_failed ────────────────────
    op.drop_constraint("ck_deployments_status", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('pending','deploying','running','failed','rolled_back','terminated','gate_failed')",
    )


def downgrade():
    # Restore original deployments status constraint
    op.drop_constraint("ck_deployments_status", "deployments", type_="check")
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('pending','deploying','running','failed','rolled_back','terminated')",
    )

    # Drop asset_visible function
    op.execute("DROP FUNCTION IF EXISTS asset_visible(UUID, TEXT, TEXT)")

    # Drop tables in reverse order
    op.drop_index("idx_approval_authority_resource", table_name="approval_authority")
    op.drop_table("approval_authority")
    op.drop_table("grant_audit")
    op.drop_index("idx_asset_grants_lookup", table_name="asset_grants")
    op.drop_table("asset_grants")
    op.drop_index("idx_publish_requests_asset", table_name="publish_requests")
    op.drop_table("publish_requests")
