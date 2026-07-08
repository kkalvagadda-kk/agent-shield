"""Create published_artifacts, published_versions, production_deployments tables.

Revision ID: 0036
Revises: 0035
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0036"
down_revision = "0035"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS published_artifacts (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        name TEXT NOT NULL,
        type TEXT NOT NULL CHECK (type IN ('agent', 'workflow', 'tool', 'skill')),
        description TEXT,
        source_id UUID,
        team TEXT NOT NULL,
        created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        UNIQUE(name, type)
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS published_versions (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_id UUID NOT NULL REFERENCES published_artifacts(id),
        version_label TEXT NOT NULL,
        config_snapshot JSONB NOT NULL DEFAULT '{}',
        source_version_id UUID,
        promoted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        promoted_by TEXT,
        notes TEXT
    )
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS production_deployments (
        id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
        artifact_id UUID NOT NULL REFERENCES published_artifacts(id),
        version_id UUID NOT NULL REFERENCES published_versions(id),
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK (status IN ('pending', 'deploying', 'running', 'suspended', 'failed')),
        namespace TEXT,
        deployed_at TIMESTAMPTZ,
        suspended_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_published_versions_artifact
        ON published_versions(artifact_id)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_production_deployments_artifact
        ON production_deployments(artifact_id)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS production_deployments")
    op.execute("DROP TABLE IF EXISTS published_versions")
    op.execute("DROP TABLE IF EXISTS published_artifacts")
