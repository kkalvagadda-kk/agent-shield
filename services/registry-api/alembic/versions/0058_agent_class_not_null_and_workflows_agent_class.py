"""agent_class NOT NULL on both executables (agents + workflows) — WS-0 M3.

Before v2, `agents.agent_class` was nullable with no default: the deploy path
(manifest_builder.py) coalesced NULL -> 'user_delegated' at read time, so the
daemon class was never actually reachable end-to-end and a garbage value could
slip in. `workflows` had no class column at all (D1 needs one).

This migration makes the class un-droppable on BOTH executables:
  - backfill existing NULL agents.agent_class -> 'user_delegated' (preserves the
    exact coalesce behavior the removed deploy-time default provided),
  - agents.agent_class: DEFAULT 'user_delegated' + NOT NULL + CHECK,
  - workflows.agent_class: new column, NOT NULL DEFAULT 'user_delegated' + CHECK.

NOT NULL + DEFAULT + CHECK together make illegal states unrepresentable: the
deploy-time coalesce becomes dead code (removed in the same slice), and OPA's
class-based flow (WS-2) can trust the value. Idempotent + guarded so it is safe
to re-run on a partially-migrated DB.
"""

from alembic import op

revision = "0058"
down_revision = "0057"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- agents.agent_class: backfill -> default -> not null -> check -------------
    op.execute(
        """
        DO $$
        BEGIN
            -- 1. Backfill NULLs to the value the removed deploy-time coalesce used.
            UPDATE agents SET agent_class = 'user_delegated' WHERE agent_class IS NULL;

            -- 2. Explicit default so a raw INSERT omitting the column still lands valid.
            ALTER TABLE agents ALTER COLUMN agent_class SET DEFAULT 'user_delegated';

            -- 3. NOT NULL (all rows are non-NULL after the backfill).
            ALTER TABLE agents ALTER COLUMN agent_class SET NOT NULL;

            -- 4. CHECK (guarded — skip if it already exists).
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_agents_agent_class'
            ) THEN
                ALTER TABLE agents ADD CONSTRAINT ck_agents_agent_class
                    CHECK (agent_class IN ('user_delegated','daemon'));
            END IF;
        END $$;
        """
    )

    # --- workflows.agent_class: new column (default backfills atomically) ---------
    op.execute(
        """
        DO $$
        BEGIN
            -- 5. ADD COLUMN with DEFAULT backfills every existing row, so NOT NULL cannot fail.
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'workflows' AND column_name = 'agent_class'
            ) THEN
                ALTER TABLE workflows
                    ADD COLUMN agent_class VARCHAR(32) NOT NULL DEFAULT 'user_delegated';
            END IF;

            -- 6. CHECK (guarded).
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_workflows_agent_class'
            ) THEN
                ALTER TABLE workflows ADD CONSTRAINT ck_workflows_agent_class
                    CHECK (agent_class IN ('user_delegated','daemon'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Data-preserving on the agents side: down does NOT re-NULL existing values,
    # it only relaxes the constraints.
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_agent_class;
            ALTER TABLE agents ALTER COLUMN agent_class DROP NOT NULL;
            ALTER TABLE agents ALTER COLUMN agent_class DROP DEFAULT;

            ALTER TABLE workflows DROP CONSTRAINT IF EXISTS ck_workflows_agent_class;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'workflows' AND column_name = 'agent_class'
            ) THEN
                ALTER TABLE workflows DROP COLUMN agent_class;
            END IF;
        END $$;
        """
    )
