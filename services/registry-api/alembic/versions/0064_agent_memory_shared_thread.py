"""0064 — agent_memory shared-workflow-thread columns + atomic-index backstop.

POC-1 (context storage): scope/workflow_run_id/message_kind for the shared workflow
transcript, a (thread_id, scope, message_index) read index, and a
UNIQUE(thread_id, message_index) backstop for the S4 atomic-index fix. Idempotent +
data-preserving; up/down/up round-trips.

Chains onto 0063 (exec-v2 `tools_side_effecting_and_run_eval_mode`), which is the current
Alembic head after rebasing this branch onto main. This is the migration-sequencing fix
from docs/design/context-storage-vs-exec-v2-merge-notes.md (decision 1): context-storage
lands after exec-v2's chain, so revision=0064 / down_revision=0063.
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Additive columns (guarded).
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS workflow_run_id UUID")
    op.execute(
        "ALTER TABLE agent_memory "
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(16) NOT NULL DEFAULT 'agent'"
    )
    op.execute(
        "ALTER TABLE agent_memory "
        "ADD COLUMN IF NOT EXISTS message_kind VARCHAR(16) NOT NULL DEFAULT 'agent_output'"
    )

    # 2. CHECK constraints (guarded).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_memory_scope') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT ck_agent_memory_scope
                    CHECK (scope IN ('agent','workflow_run'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_memory_message_kind') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT ck_agent_memory_message_kind
                    CHECK (message_kind IN ('user','agent_output','rationale'));
            END IF;
        END $$;
        """
    )

    # 3. Read index for the workflow-scoped ordered transcript read.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_thread_scope "
        "ON agent_memory (thread_id, scope, message_index)"
    )

    # 4. Pre-flight de-dup, THEN the UNIQUE backstop (see §4). Renumbers any
    #    pre-existing duplicate (thread_id, message_index) rows deterministically so
    #    the constraint can be added without data loss.
    op.execute(
        """
        DO $$
        BEGIN
            -- Renumber duplicates within a thread by created_at, id (stable order).
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (PARTITION BY thread_id ORDER BY message_index, created_at, id) - 1 AS rn
                FROM agent_memory
            )
            UPDATE agent_memory m
               SET message_index = r.rn
              FROM ranked r
             WHERE m.id = r.id
               AND m.message_index <> r.rn
               AND EXISTS (  -- only touch threads that actually have a collision
                   SELECT 1 FROM agent_memory d
                   WHERE d.thread_id = m.thread_id
                   GROUP BY d.thread_id, d.message_index
                   HAVING count(*) > 1
               );

            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_agent_memory_thread_msg') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT uq_agent_memory_thread_msg
                    UNIQUE (thread_id, message_index);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS uq_agent_memory_thread_msg")
    op.execute("DROP INDEX IF EXISTS idx_agent_memory_thread_scope")
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS ck_agent_memory_message_kind")
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS ck_agent_memory_scope")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS message_kind")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS scope")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS workflow_run_id")
