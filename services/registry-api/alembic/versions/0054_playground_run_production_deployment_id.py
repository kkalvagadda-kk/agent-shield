"""Add production_deployment_id to playground_runs.

`playground_runs.deployment_id` FKs the sandbox `deployments` table. Production
chat runs target a `production_deployments` row (a different table), so stuffing
that id into `deployment_id` violates the FK and the INSERT 500s — production
consumer chat could never start. Mirror the `agent_runs` design: a dedicated
`production_deployment_id` column FKing `production_deployments`, so each context
writes to the column whose FK it actually satisfies. Illegal states (a production
id in a sandbox-FK column) become unrepresentable.
"""

import sqlalchemy as sa
from alembic import op

revision = "0054"
down_revision = "0053"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_runs'
                  AND column_name = 'production_deployment_id'
            ) THEN
                ALTER TABLE playground_runs
                    ADD COLUMN production_deployment_id uuid NULL;
                ALTER TABLE playground_runs
                    ADD CONSTRAINT fk_playground_runs_production_deployment
                    FOREIGN KEY (production_deployment_id)
                    REFERENCES production_deployments(id)
                    ON DELETE SET NULL;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_runs'
                  AND column_name = 'production_deployment_id'
            ) THEN
                ALTER TABLE playground_runs
                    DROP CONSTRAINT IF EXISTS fk_playground_runs_production_deployment;
                ALTER TABLE playground_runs
                    DROP COLUMN production_deployment_id;
            END IF;
        END $$;
        """
    )
