"""Add production_deployment_id to agent_identities.

agent_identities.deployment_id FKs the sandbox `deployments` table. Production
agent pods live in `production_deployments` (a different table), so a production
identity cannot store its deployment id there without an FK violation — and today
it stores nothing, so production SA subjects never enter the OPA bundle and every
production tool call fails closed (agent_unauthenticated). Mirror the
playground_runs / agent_runs design: a dedicated production_deployment_id column
FKing production_deployments.
"""

import sqlalchemy as sa
from alembic import op

revision = "0055"
down_revision = "0054"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'agent_identities'
                  AND column_name = 'production_deployment_id'
            ) THEN
                ALTER TABLE agent_identities
                    ADD COLUMN production_deployment_id uuid NULL;
                ALTER TABLE agent_identities
                    ADD CONSTRAINT fk_agent_identities_production_deployment
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
                WHERE table_name = 'agent_identities'
                  AND column_name = 'production_deployment_id'
            ) THEN
                ALTER TABLE agent_identities
                    DROP CONSTRAINT IF EXISTS fk_agent_identities_production_deployment;
                ALTER TABLE agent_identities
                    DROP COLUMN production_deployment_id;
            END IF;
        END $$;
        """
    )
