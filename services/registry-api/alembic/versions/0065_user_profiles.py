"""0065 — user_profiles (POC-3 response preferences).

Platform-level, one row per user (Keycloak JWT `sub` = text PK). Five nullable enum
columns hold the user's structured response presets (length / tone / format / language /
expertise); NULL on any column = "no preference" for that dimension. `updated_at` bumps
on every upsert. Guarded CHECK constraints mirror the app-layer Pydantic enums as defense
in depth (see contracts/enums.md).

Idempotent + data-preserving (CREATE TABLE IF NOT EXISTS, guarded pg_constraint adds);
up/down/up round-trips. Chains onto 0064 (agent_memory shared-workflow-thread), the current
Alembic head, so revision=0065 / down_revision=0064.
"""
from alembic import op

revision = "0065"
down_revision = "0064"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profiles (
            user_id         text PRIMARY KEY,
            response_length text,
            tone            text,
            format          text,
            language        text,
            expertise       text,
            updated_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # Guarded CHECK constraints (idempotent — mirrors 0064's pg_constraint guard).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_user_profiles_response_length') THEN
                ALTER TABLE user_profiles ADD CONSTRAINT ck_user_profiles_response_length
                    CHECK (response_length IS NULL OR response_length IN ('concise','balanced','detailed'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_user_profiles_tone') THEN
                ALTER TABLE user_profiles ADD CONSTRAINT ck_user_profiles_tone
                    CHECK (tone IS NULL OR tone IN ('professional','neutral','casual'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_user_profiles_format') THEN
                ALTER TABLE user_profiles ADD CONSTRAINT ck_user_profiles_format
                    CHECK (format IS NULL OR format IN ('prose','bulleted','structured'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_user_profiles_language') THEN
                ALTER TABLE user_profiles ADD CONSTRAINT ck_user_profiles_language
                    CHECK (language IS NULL OR language IN ('auto','en','es','fr','de','ja'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_user_profiles_expertise') THEN
                ALTER TABLE user_profiles ADD CONSTRAINT ck_user_profiles_expertise
                    CHECK (expertise IS NULL OR expertise IN ('beginner','intermediate','expert'));
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS user_profiles")
