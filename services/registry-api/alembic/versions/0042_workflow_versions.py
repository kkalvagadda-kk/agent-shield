"""Workflow versions: snapshot a workflow's composition for deployment + rollback.

Records the member agents, edges, orchestration mode, and execution shape at a
point in time so that a workflow deployment can reference a stable, immutable
revision rather than the live mutable workflow definition.

Revision ID: 0042
Revises: 0041
"""
from alembic import op

revision = "0042"
down_revision = "0041"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS workflow_versions (
        id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        workflow_id      UUID        NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        version_number   INTEGER     NOT NULL,
        members          JSONB       NOT NULL DEFAULT '[]',
        edges            JSONB       NOT NULL DEFAULT '[]',
        orchestration    VARCHAR(32) NOT NULL DEFAULT 'sequential',
        execution_shape  VARCHAR(16) NOT NULL DEFAULT 'durable',
        config           JSONB       NOT NULL DEFAULT '{}',
        eval_passed      BOOLEAN     NOT NULL DEFAULT false,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        created_by       VARCHAR(256),
        UNIQUE (workflow_id, version_number)
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_workflow_versions_workflow_id
        ON workflow_versions(workflow_id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_workflow_versions_workflow_id")
    op.execute("DROP TABLE IF EXISTS workflow_versions")
