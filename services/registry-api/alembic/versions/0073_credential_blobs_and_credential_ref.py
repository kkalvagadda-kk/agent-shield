"""0073 — credential_blobs table + auth_configs.credential_ref + backfill (Decision 31).

WS-1 CredentialProvider seam (behavior-preserving). Relocates the durable credential
value from the inline column ``auth_configs.credentials_encrypted`` to the generic KV
table ``credential_blobs`` (keyed by ``CredentialRef.path``), while keeping the ref
pointer in ``auth_configs``.

Byte-identity: the backfill copies the existing Fernet ciphertext VERBATIM into
``credential_blobs.value_encrypted`` (same ``AGENTSHIELD_ENCRYPTION_KEY``, NO
re-encrypt), so every migrated credential resolves to the exact same bytes as before.

Idempotent + data-preserving:
  * ``CREATE TABLE IF NOT EXISTS`` / ``ADD COLUMN IF NOT EXISTS`` — safe to re-run.
  * The backfill INSERT is guarded by ``WHERE NOT EXISTS`` and the UPDATE by
    ``credential_ref IS NULL`` — a second run is a no-op.
  * ``credentials_encrypted`` is NOT dropped (dual-read window); its drop is a later
    migration. Downgrade drops the column then the table; the legacy column (which
    still holds the same ciphertext) is untouched, so no data is lost on the way down.

Data model: docs/plan/mcp-tool-source-phase4/data-model.md §1d.
"""
from alembic import op

revision = "0073"
down_revision = "0072"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. The generic KV value store for the pg-fernet backend.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS credential_blobs (
            path             VARCHAR(512) PRIMARY KEY,
            value_encrypted  TEXT NOT NULL,
            created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )

    # 2. The ref pointer on auth_configs (NULL = legacy row, read the old column).
    op.execute(
        "ALTER TABLE auth_configs ADD COLUMN IF NOT EXISTS credential_ref VARCHAR(512);"
    )

    # 3. Backfill: copy each existing Fernet blob VERBATIM into credential_blobs
    #    (path = 'auth-configs/{id}', value = the existing ciphertext — same key,
    #    no re-encrypt), guarded by NOT EXISTS so re-running is a no-op.
    op.execute(
        """
        INSERT INTO credential_blobs (path, value_encrypted)
        SELECT 'auth-configs/' || id::text, credentials_encrypted
        FROM auth_configs
        WHERE credentials_encrypted IS NOT NULL
          AND NOT EXISTS (
            SELECT 1 FROM credential_blobs cb
            WHERE cb.path = 'auth-configs/' || auth_configs.id::text
          );
        """
    )

    # 4. Point the ref at the freshly-backfilled blob for every migrated row.
    op.execute(
        """
        UPDATE auth_configs
        SET credential_ref = 'pg-fernet://credential-blobs/auth-configs/' || id::text
        WHERE credentials_encrypted IS NOT NULL AND credential_ref IS NULL;
        """
    )


def downgrade() -> None:
    # Preserve data on the way down: the ref column drop is safe (the values remain
    # in the legacy credentials_encrypted column, untouched); the table is dropped last.
    op.execute("ALTER TABLE auth_configs DROP COLUMN IF EXISTS credential_ref;")
    op.execute("DROP TABLE IF EXISTS credential_blobs;")
