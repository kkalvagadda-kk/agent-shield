# Data Model — Webhook Application Identity & Invoker Grants

Two migrations. Numbers confirmed free against the live repo (`research.md` §1): **0069** (additive schema), **0070** (data backfill). Both go in `services/registry-api/alembic/versions/`, matching the exact style of `0044_artifact_role_grants.py` (module-level `revision`/`down_revision` strings, `op.execute()` with raw SQL, idempotent guards, no Alembic autogenerate).

---

## Migration 0069 — `applications` table + widened `artifact_role_grants` constraints

**File:** `services/registry-api/alembic/versions/0069_applications_and_invoker_grants.py`

```python
"""Create applications table; widen artifact_role_grants for the 'invoker' role
and 'application' grantee type (Decision 30).

Additive only — does not touch the live gateway auth path (that cutover is a
separate code change, not a migration) and does not move any existing data
(migration 0070 does the webhook_clients backfill).

Revision ID: 0069
Revises: 0068
"""
from alembic import op

revision = "0069"
down_revision = "0068"


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
        team_name        VARCHAR(255) NOT NULL,
        name             VARCHAR(128) NOT NULL,
        secret_encrypted TEXT        NOT NULL,
        enabled          BOOLEAN     NOT NULL DEFAULT true,
        created_by       VARCHAR(255) NOT NULL,
        created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
        rotated_at       TIMESTAMPTZ NULL,
        CONSTRAINT uq_applications_team_name UNIQUE (team_name, name)
    )
    """)

    op.execute("""
    CREATE INDEX IF NOT EXISTS idx_applications_team ON applications(team_name)
    """)

    # --- Widen artifact_role_grants (migration 0044) -----------------------
    # Postgres has no "ADD CONSTRAINT IF NOT EXISTS" for CHECK constraints, so
    # guard with a catalog lookup instead (idempotent re-run safe, matching
    # this repo's migration convention of IF [NOT] EXISTS guards).
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_grantee_type'
        ) THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_grantee_type;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_grantee_type
            CHECK (grantee_type IN ('user', 'team', 'application'));
    END $$;
    """)

    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_role'
        ) THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_role;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_role
            CHECK (role IN ('agent-admin', 'approver', 'invoker'));
    END $$;
    """)


def downgrade() -> None:
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_role') THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_role;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_role
            CHECK (role IN ('agent-admin', 'approver'));
    END $$;
    """)
    op.execute("""
    DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_arg_grantee_type') THEN
            ALTER TABLE artifact_role_grants DROP CONSTRAINT ck_arg_grantee_type;
        END IF;
        ALTER TABLE artifact_role_grants ADD CONSTRAINT ck_arg_grantee_type
            CHECK (grantee_type IN ('user', 'team'));
    END $$;
    """)
    op.execute("DROP INDEX IF EXISTS idx_applications_team")
    op.execute("DROP TABLE IF EXISTS applications")
```

**Note on downgrade ordering:** the CHECK-narrowing downgrade steps run *before* any data referencing `role='invoker'` or `grantee_type='application'` would need to exist to violate them — this migration ships no data, so downgrade is always safe. Migration 0070 (below) must never be downgraded without first downgrading 0069, or its backfilled `invoker` rows would already violate the narrowed CHECK before the `DELETE` in 0070's own downgrade runs. Alembic's linear revision chain enforces this ordering automatically (0070 cannot downgrade past 0069 without downgrading 0069 too).

### Corresponding ORM model — `services/registry-api/models.py`

Insert immediately after the `WebhookClient` class (currently ends at line 1806, right before the `agent_events` section comment at line 1809) — same file region as the table it supersedes for new registrations, mirroring `WebhookClient`'s own field shape exactly (this repo's established pattern for a first-class secret-bearing CRUD resource; `artifact_role_grants` itself stays **raw-SQL-only**, per `research.md` §3 — no ORM model added there):

```python
# ---------------------------------------------------------------------------
# applications — reusable webhook-sending identities (Decision 30)
#
# One row per real sending system, owned by exactly one team. Replaces the
# per-trigger WebhookClient as the credential that backs `client_signed`
# webhook auth — the trigger it may call is no longer stored here at all;
# that's an artifact_role_grants row with role='invoker' (see rbac.py).
# ---------------------------------------------------------------------------
class Application(Base):
    __tablename__ = "applications"
    __table_args__ = (
        UniqueConstraint("team_name", "name", name="uq_applications_team_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        _UUID, primary_key=True, server_default=_GEN_UUID
    )
    team_name: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    # Fernet token (crypto.encrypt_json), NOT a hash — same reasoning as
    # WebhookClient.secret_encrypted: the gateway must RECOMPUTE the HMAC to
    # verify a signature, so the raw secret must be recoverable.
    secret_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    # Kill switch — INDEPENDENT of any one artifact_role_grants row. Disabling
    # denies this application on every artifact it holds `invoker` on at once;
    # revoking one grant (a DELETE on the grants router) denies only that one
    # artifact. Read live on every gateway request — no cache.
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        _TSTZ, nullable=False, server_default=_NOW
    )
    rotated_at: Mapped[datetime | None] = mapped_column(_TSTZ, nullable=True)
```

No `relationship()` to `artifact_role_grants` — that table is raw-SQL-only and polymorphic (`grantee_id` is a bare `TEXT` column that can hold a user sub, a team name, or an application id depending on `grantee_type`), so an ORM relationship isn't representable without a discriminated-union hack this codebase deliberately avoids elsewhere (see `webhook_auth.py`'s own "explicit context parameter, not type-sniffing" principle).

---

## Migration 0070 — backfill `webhook_clients` → `applications` + `invoker` grants

**File:** `services/registry-api/alembic/versions/0070_backfill_webhook_clients_to_applications.py`

Exact logic, not "insert appropriate rows": for every `webhook_clients` row, resolve its trigger's owning artifact (`agent_id` or `workflow_id` — exactly one is set per the trigger's own `ck_agent_triggers_target` constraint) and that artifact's `team` column, then insert one `applications` row keyed on `(team, client_id)` and one `artifact_role_grants` row keyed on `(artifact_type, artifact_id, role='invoker', grantee_type='application', grantee_id=<new application id>)`. If two different teams' triggers independently registered the same `client_id` string, they become two distinct `applications` rows (per `(team_name, name)` uniqueness) — never merged, exactly per design doc §5.3.

```python
"""Backfill webhook_clients rows into applications + invoker grants (Decision 30
§5.3 / §10 step 2). Idempotent — re-running skips (team_name, name) pairs that
already exist (ON CONFLICT DO NOTHING against uq_applications_team_name), and
skips (artifact, role, grantee) grants that already exist (ON CONFLICT DO
NOTHING against uq_arg_active_grant).

Preconditions: migration 0069 has run (applications table + widened
artifact_role_grants constraints must already exist).

Does NOT touch or drop webhook_clients — it stays in place, read-only-in-
intent for one release (services/registry-api/routers/webhook_clients.py's
write endpoints return 410 once the gateway cutover ships; GET keeps working
so pre-existing rows this migration is about to consume remain independently
inspectable during the rollback window).

Revision ID: 0070
Revises: 0069
"""
from alembic import op

revision = "0070"
down_revision = "0069"


def upgrade() -> None:
    # Pass 1: one applications row per distinct (team, client_id) pair that has
    # ever been registered in webhook_clients. DISTINCT ON + ORDER BY created_at
    # ASC keeps the EARLIEST secret/created_by when the same (team, client_id)
    # was registered on more than one trigger under the SAME team (still one
    # reusable identity, per design doc §5.3) — only a different TEAM produces
    # a second row, via the (team_name, name) uniqueness itself.
    op.execute("""
    INSERT INTO applications (team_name, name, secret_encrypted, enabled, created_by, created_at)
    SELECT DISTINCT ON (team_name, client_name)
        team_name, client_name, secret_encrypted, true,
        COALESCE(created_by, 'system:backfill-0070'), created_at
    FROM (
        SELECT
            wc.client_id AS client_name,
            wc.secret_encrypted,
            wc.created_by,
            wc.created_at,
            COALESCE(a.team, w.team) AS team_name
        FROM webhook_clients wc
        JOIN agent_triggers t ON t.id = wc.trigger_id
        LEFT JOIN agents a ON a.id = t.agent_id
        LEFT JOIN workflows w ON w.id = t.workflow_id
    ) source_rows
    WHERE team_name IS NOT NULL
    ORDER BY team_name, client_name, created_at ASC
    ON CONFLICT (team_name, name) DO NOTHING
    """)

    # Pass 2: one invoker grant per webhook_clients row's trigger artifact,
    # resolving applications.id via the (team, client_id) pair Pass 1 just
    # ensured exists (whether inserted this run or a prior run — a plain JOIN
    # against the now-current applications table, not a RETURNING set from
    # Pass 1, so re-running this migration after a partial prior run is safe).
    op.execute("""
    INSERT INTO artifact_role_grants (artifact_type, artifact_id, role, grantee_type, grantee_id, granted_by)
    SELECT DISTINCT
        sr.artifact_type,
        sr.artifact_id,
        'invoker',
        'application',
        app.id::text,
        'system:backfill-0070'
    FROM (
        SELECT
            t.agent_id, t.workflow_id,
            COALESCE(a.team, w.team) AS team_name,
            wc.client_id AS client_name,
            CASE WHEN t.agent_id IS NOT NULL THEN 'agent' ELSE 'workflow' END AS artifact_type,
            COALESCE(t.agent_id, t.workflow_id) AS artifact_id
        FROM webhook_clients wc
        JOIN agent_triggers t ON t.id = wc.trigger_id
        LEFT JOIN agents a ON a.id = t.agent_id
        LEFT JOIN workflows w ON w.id = t.workflow_id
    ) sr
    JOIN applications app
        ON app.team_name = sr.team_name AND app.name = sr.client_name
    WHERE sr.team_name IS NOT NULL
    ON CONFLICT (artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL
        DO NOTHING
    """)


def downgrade() -> None:
    # Remove ONLY the rows this migration could have produced (grantee_type=
    # 'application' AND granted_by='system:backfill-0070') — never touch grants
    # a human created through the new API after this migration ran, and never
    # touch applications a human created directly through POST /teams/{team}/
    # applications (created_by would not be 'system:backfill-0070' for those).
    op.execute("""
        DELETE FROM artifact_role_grants
        WHERE grantee_type = 'application' AND granted_by = 'system:backfill-0070'
    """)
    op.execute("""
        DELETE FROM applications WHERE created_by = 'system:backfill-0070'
    """)
```

**Why `ON CONFLICT (artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL DO NOTHING` is syntactically valid here:** this matches `uq_arg_active_grant`'s exact partial-unique-index definition from migration 0044 (`ON (artifact_id, role, grantee_type, grantee_id) WHERE revoked_at IS NULL`) — Postgres requires the `ON CONFLICT` target to match an existing unique index/constraint exactly, including its partial-index predicate, which this does.

**Verify counts before cutover** (per design doc §10 step 2 — "Verify counts match before cutover"), run in the registry-api pod:

```sql
SELECT count(*) FROM webhook_clients;                                   -- N
SELECT count(*) FROM artifact_role_grants
  WHERE grantee_type = 'application' AND granted_by = 'system:backfill-0070';  -- should be N (or fewer only if
                                                                                --  two webhook_clients rows shared
                                                                                --  the exact same (team, client_id, artifact) tuple)
```

---

## Widened CHECK constraints — before/after (migration 0044 baseline vs. this plan)

| Constraint | Before (0044) | After (0069) |
|---|---|---|
| `ck_arg_grantee_type` | `grantee_type IN ('user','team')` | `grantee_type IN ('user','team','application')` |
| `ck_arg_role` | `role IN ('agent-admin','approver')` | `role IN ('agent-admin','approver','invoker')` |
| `ck_arg_artifact_type` | `artifact_type IN ('agent','workflow')` | **unchanged** — an `application` is never itself an artifact, only a grantee |

`uq_arg_active_grant`, `idx_arg_lookup`, `idx_arg_grantee` — all three already cover `grantee_type='application'` and `role='invoker'` correctly with **zero index DDL changes**; they're generic over the column values, not enumerated per value.
