"""Eval v2 E-0 — per-dimension / trajectory / side-effect result store.

Adds the composite-score evidence columns to `eval_run_results`. All nullable,
no backfill: pre-existing rows read as response-only (they carry `judge_score`
= the composite, which stays the auto-promote gate input — unchanged).

  eval_run_results
    - dimension_scores JSONB   NULL  {response,trajectory,tool_call,side_effect,filter} 0..1
    - eval_detail      JSONB   NULL  the evidence (trajectory diffs, recorded
                                     side-effects, filter_reason, injection_result)
    - trigger_payload  JSONB   NULL  the event/job payload this item ran (scheduled/webhook)
    - matched          BOOLEAN NULL  webhook: did the filter match? (fast column)
    - run_id           UUID    NULL  soft FK -> playground_runs.id (deep-link to run tree)

`run_id` is a *soft* FK (no DB constraint) so a deleted playground run never
blocks result retention. Idempotent + guarded (IF NOT EXISTS). Data-preserving.
"""

from alembic import op

revision = "0060"
down_revision = "0059"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'dimension_scores'
            ) THEN
                ALTER TABLE eval_run_results ADD COLUMN dimension_scores JSONB;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'eval_detail'
            ) THEN
                ALTER TABLE eval_run_results ADD COLUMN eval_detail JSONB;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'trigger_payload'
            ) THEN
                ALTER TABLE eval_run_results ADD COLUMN trigger_payload JSONB;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'matched'
            ) THEN
                ALTER TABLE eval_run_results ADD COLUMN matched BOOLEAN;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'run_id'
            ) THEN
                ALTER TABLE eval_run_results ADD COLUMN run_id UUID;
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
                WHERE table_name = 'eval_run_results' AND column_name = 'run_id'
            ) THEN
                ALTER TABLE eval_run_results DROP COLUMN run_id;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'matched'
            ) THEN
                ALTER TABLE eval_run_results DROP COLUMN matched;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'trigger_payload'
            ) THEN
                ALTER TABLE eval_run_results DROP COLUMN trigger_payload;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'eval_detail'
            ) THEN
                ALTER TABLE eval_run_results DROP COLUMN eval_detail;
            END IF;
            IF EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_name = 'eval_run_results' AND column_name = 'dimension_scores'
            ) THEN
                ALTER TABLE eval_run_results DROP COLUMN dimension_scores;
            END IF;
        END $$;
        """
    )
