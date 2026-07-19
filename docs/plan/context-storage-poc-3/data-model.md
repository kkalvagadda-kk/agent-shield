# POC-3 — Data Model

## Table `user_profiles` (migration 0065)

Platform-level, keyed by `user_id` only (Keycloak JWT `sub`). Not per-deployment, not per-team.
One row per user. All five preset columns nullable (`NULL` = no preference for that dimension).

| Column | Type | Null? | Default | Notes |
|---|---|---|---|---|
| `user_id` | `text` | NO | — | **PRIMARY KEY**. JWT `sub`. |
| `response_length` | `text` | YES | — | CHECK ∈ enums.md or NULL |
| `tone` | `text` | YES | — | CHECK ∈ enums.md or NULL |
| `format` | `text` | YES | — | CHECK ∈ enums.md or NULL |
| `language` | `text` | YES | — | CHECK ∈ enums.md or NULL (`auto` allowed) |
| `expertise` | `text` | YES | — | CHECK ∈ enums.md or NULL |
| `updated_at` | `timestamptz` | NO | `now()` | Bumped on every upsert |

Divergence note: architecture §8 sketched a single `preferences JSONB` column; the authoritative
POC-3 §3.1 uses **typed columns**, which this plan follows (queryable, CHECK-guardable, and the
frontend maps 1:1). Recorded in `research.md` R1/R7.

## Migration DDL (`alembic/versions/0065_user_profiles.py`) — idempotent, copy the 0064 style

```python
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
```

## ORM model (`services/registry-api/models.py`)

Uses the file's existing helpers (`_TSTZ`, `_NOW`, `Text`, `Mapped`, `mapped_column`).

```python
class UserProfile(Base):
    __tablename__ = "user_profiles"

    user_id: Mapped[str] = mapped_column(Text, primary_key=True)
    response_length: Mapped[str | None] = mapped_column(Text, nullable=True)
    tone: Mapped[str | None] = mapped_column(Text, nullable=True)
    format: Mapped[str | None] = mapped_column(Text, nullable=True)
    language: Mapped[str | None] = mapped_column(Text, nullable=True)
    expertise: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(_TSTZ, nullable=False, server_default=_NOW)
```

## Pydantic schemas (`services/registry-api/preferences.py`)

`Literal` types give the 422-on-out-of-vocab for free.

```python
from typing import Literal, Optional
from datetime import datetime
from pydantic import BaseModel

ResponseLength = Literal["concise", "balanced", "detailed"]
Tone           = Literal["professional", "neutral", "casual"]
Format         = Literal["prose", "bulleted", "structured"]
Language       = Literal["auto", "en", "es", "fr", "de", "ja"]
Expertise      = Literal["beginner", "intermediate", "expert"]

class UserPreferencesUpdate(BaseModel):
    response_length: Optional[ResponseLength] = None
    tone:            Optional[Tone] = None
    format:          Optional[Format] = None
    language:        Optional[Language] = None
    expertise:       Optional[Expertise] = None

class UserPreferences(UserPreferencesUpdate):
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}
```
