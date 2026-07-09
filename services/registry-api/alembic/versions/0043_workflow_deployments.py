"""Workflow deployments: logical deployment records for versioned workflows.

Mirrors the agent sandbox `deployments` table but for workflows.  No real pod
is created — the deploy-controller treats these as logical routing targets.
Also scopes agent_runs to the workflow deployment that produced them via a new
workflow_deployment_id FK on agent_runs.

Revision ID: 0043
Revises: 0042
"""
from alembic import op

revision = "0043"
down_revision = "0042"

_STATUS_VALUES = (
    "('pending','deploying','running','failed','rolled_back','terminated',"
    "'gate_failed','suspending','suspended','terminating')"
)


def upgrade() -> None:
    op.execute(f"""
    CREATE TABLE IF NOT EXISTS workflow_deployments (
        id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        workflow_id      UUID        NOT NULL REFERENCES workflows(id) ON DELETE CASCADE,
        version_id       UUID        NOT NULL REFERENCES workflow_versions(id),
        name             VARCHAR(256),
        environment      VARCHAR(64) NOT NULL DEFAULT 'sandbox',
        status           VARCHAR(32) NOT NULL DEFAULT 'pending',
        replicas         INTEGER     NOT NULL DEFAULT 1,
        ttl_hours        INTEGER,
        deployed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
        suspended_at     TIMESTAMPTZ,
        terminated_at    TIMESTAMPTZ,
        error_message    TEXT,
        deployed_by      VARCHAR(256),
        previous_version_id UUID REFERENCES workflow_versions(id),
        CONSTRAINT ck_workflow_deployments_env
            CHECK (environment IN ('production','staging','canary','sandbox')),
        CONSTRAINT ck_workflow_deployments_status
            CHECK (status IN {_STATUS_VALUES})
    )
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_workflow_deployments_workflow_id
        ON workflow_deployments(workflow_id)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_workflow_deployments_status
        ON workflow_deployments(status)
    """)

    # Scope agent_runs to the workflow deployment that produced them.
    op.execute("""
    ALTER TABLE agent_runs
        ADD COLUMN IF NOT EXISTS workflow_deployment_id UUID
            REFERENCES workflow_deployments(id)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_runs_workflow_deployment_id
        ON agent_runs(workflow_deployment_id)
        WHERE workflow_deployment_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_workflow_deployment_id")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS workflow_deployment_id")
    op.execute("DROP INDEX IF EXISTS idx_workflow_deployments_status")
    op.execute("DROP INDEX IF EXISTS idx_workflow_deployments_workflow_id")
    op.execute("DROP TABLE IF EXISTS workflow_deployments")
