"""0066 — drop the llm_providers.provider CHECK constraint.

Provider validity is now enforced at the Pydantic layer against LLM_PROVIDER_SPECS
(llm_provider_specs.py), so adding a provider (e.g. ollama) is a single registry
entry rather than a migration. The old two-value CHECK
(`provider IN ('anthropic','bedrock')`) would otherwise reject any new provider at
the DB layer, so it is dropped here.

Idempotent (guarded DROP / guarded re-ADD); up/down/up round-trips. Chains onto
0065 (user_profiles), the current head, so revision=0066 / down_revision=0065.
"""
from alembic import op

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_llm_providers_provider') THEN
                ALTER TABLE llm_providers DROP CONSTRAINT ck_llm_providers_provider;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    # Re-add the original two-value CHECK (guarded). Note: rows using providers
    # outside the original set (e.g. ollama) would violate this — acceptable for a
    # downgrade, which is a schema rollback.
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_llm_providers_provider') THEN
                ALTER TABLE llm_providers ADD CONSTRAINT ck_llm_providers_provider
                    CHECK (provider IN ('anthropic','bedrock'));
            END IF;
        END $$;
        """
    )
