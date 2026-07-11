"""Add deployment_id to playground_runs for HITL approval provenance.

A chat/playground run started against a specific deployment should record
which deployment it ran on. The HITL console joins approvals → run to show
the reviewer *who* requested the tool and on *which* deployment/environment,
instead of relying on fragile cross-table heuristics.
"""

import sqlalchemy as sa
from alembic import op

revision = "0051"
down_revision = "0050"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Idempotent: guard against re-run on an already-migrated DB.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_runs'
                  AND column_name = 'deployment_id'
            ) THEN
                ALTER TABLE playground_runs
                    ADD COLUMN deployment_id uuid NULL;
                ALTER TABLE playground_runs
                    ADD CONSTRAINT fk_playground_runs_deployment
                    FOREIGN KEY (deployment_id) REFERENCES deployments(id)
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
                  AND column_name = 'deployment_id'
            ) THEN
                ALTER TABLE playground_runs
                    DROP CONSTRAINT IF EXISTS fk_playground_runs_deployment;
                ALTER TABLE playground_runs
                    DROP COLUMN deployment_id;
            END IF;
        END $$;
        """
    )
