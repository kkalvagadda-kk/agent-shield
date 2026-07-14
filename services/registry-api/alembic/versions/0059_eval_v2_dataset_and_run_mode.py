"""Eval v2 E-0 — mode discriminator on datasets + eval runs (composite plumbing).

Turns evaluation storage from response-only into a **mode-aware** shape without
any behavior change. Two additive discriminators + composite-score inputs:

  playground_datasets   (the *authoring* discriminator)
    - mode           VARCHAR(16) NOT NULL DEFAULT 'reactive'
                     CHECK mode IN (reactive,durable,scheduled,webhook,workflow)
    - schema_version SMALLINT     NOT NULL DEFAULT 1  (item schema can evolve
                     without a data migration)

  eval_runs             (the *interpretation* discriminator, resolved from the
                         executable at launch; must match dataset.mode)
    - mode              VARCHAR(16) NOT NULL DEFAULT 'reactive'  (same CHECK)
    - dimension_weights JSONB        NULL  (per-dimension weights for composite)
    - pass_threshold    NUMERIC(4,3) NULL  (per-run override of EVAL_PASS_THRESHOLD)

Back-compat: every pre-existing dataset / eval run is a valid `reactive` row
after this migration (server_default backfills atomically; an explicit UPDATE
belt-and-braces backfills any NULLs a partial run could have left). Idempotent +
guarded (IF NOT EXISTS on columns/constraints) so it is safe to re-run on a
partially-migrated DB. Data-preserving.
"""

from alembic import op

revision = "0059"
down_revision = "0058"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- playground_datasets.mode + schema_version -------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            -- ADD COLUMN with DEFAULT backfills every existing row atomically, so
            -- NOT NULL cannot fail on a populated table.
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_datasets' AND column_name = 'mode'
            ) THEN
                ALTER TABLE playground_datasets
                    ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'reactive';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_datasets' AND column_name = 'schema_version'
            ) THEN
                ALTER TABLE playground_datasets
                    ADD COLUMN schema_version SMALLINT NOT NULL DEFAULT 1;
            END IF;

            -- Belt-and-braces: any row a partial run could have left NULL reads reactive.
            UPDATE playground_datasets SET mode = 'reactive' WHERE mode IS NULL;
            UPDATE playground_datasets SET schema_version = 1 WHERE schema_version IS NULL;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_playground_datasets_mode'
            ) THEN
                ALTER TABLE playground_datasets ADD CONSTRAINT ck_playground_datasets_mode
                    CHECK (mode IN ('reactive','durable','scheduled','webhook','workflow'));
            END IF;
        END $$;
        """
    )

    # --- eval_runs.mode + dimension_weights + pass_threshold ---------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'mode'
            ) THEN
                ALTER TABLE eval_runs
                    ADD COLUMN mode VARCHAR(16) NOT NULL DEFAULT 'reactive';
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'dimension_weights'
            ) THEN
                ALTER TABLE eval_runs ADD COLUMN dimension_weights JSONB;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'pass_threshold'
            ) THEN
                ALTER TABLE eval_runs ADD COLUMN pass_threshold NUMERIC(4,3);
            END IF;

            UPDATE eval_runs SET mode = 'reactive' WHERE mode IS NULL;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_eval_runs_mode'
            ) THEN
                ALTER TABLE eval_runs ADD CONSTRAINT ck_eval_runs_mode
                    CHECK (mode IN ('reactive','durable','scheduled','webhook','workflow'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Drop constraints then columns, guarded so a partial down is safe.
    op.execute(
        """
        DO $$
        BEGIN
            ALTER TABLE eval_runs DROP CONSTRAINT IF EXISTS ck_eval_runs_mode;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'pass_threshold'
            ) THEN
                ALTER TABLE eval_runs DROP COLUMN pass_threshold;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'dimension_weights'
            ) THEN
                ALTER TABLE eval_runs DROP COLUMN dimension_weights;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_runs' AND column_name = 'mode'
            ) THEN
                ALTER TABLE eval_runs DROP COLUMN mode;
            END IF;

            ALTER TABLE playground_datasets DROP CONSTRAINT IF EXISTS ck_playground_datasets_mode;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_datasets' AND column_name = 'schema_version'
            ) THEN
                ALTER TABLE playground_datasets DROP COLUMN schema_version;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'playground_datasets' AND column_name = 'mode'
            ) THEN
                ALTER TABLE playground_datasets DROP COLUMN mode;
            END IF;
        END $$;
        """
    )
