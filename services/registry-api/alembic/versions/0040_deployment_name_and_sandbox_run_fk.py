"""Add deployments.name + agent_runs.sandbox_deployment_id FK.

Gives each (sandbox) deployment a human name that becomes the primary
identifier in the deployment-overview UX, and scopes playground agent_runs to
the sandbox deployment that produced them (mirror of production_deployment_id).

Revision ID: 0040
Revises: 0039
"""
from alembic import op

revision = "0040"
down_revision = "0039"


def upgrade() -> None:
    # Human-facing deployment name (e.g. "simple-qa-bd28"). Nullable — legacy
    # rows fall back to the agent name in the UI.
    op.execute("""
    ALTER TABLE deployments
    ADD COLUMN IF NOT EXISTS name VARCHAR(256)
    """)

    # Scope playground runs to the sandbox deployment that produced them.
    op.execute("""
    ALTER TABLE agent_runs
    ADD COLUMN IF NOT EXISTS sandbox_deployment_id UUID
        REFERENCES deployments(id)
    """)
    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_agent_runs_sandbox_deployment
        ON agent_runs(sandbox_deployment_id)
        WHERE sandbox_deployment_id IS NOT NULL
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_agent_runs_sandbox_deployment")
    op.execute("ALTER TABLE agent_runs DROP COLUMN IF EXISTS sandbox_deployment_id")
    op.execute("ALTER TABLE deployments DROP COLUMN IF EXISTS name")
