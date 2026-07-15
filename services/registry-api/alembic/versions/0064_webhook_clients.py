"""WS-4 T002 — per-application webhook credentials: `webhook_clients` + dual-mode auth.

Upgrades the webhook gateway from a single coarse per-trigger bearer token to
per-application client-id + allowlist + HMAC request signing. Four additive,
data-preserving changes:

1. `agent_triggers.auth_mode` — dual-mode flag, `'token' | 'client_signed'`.
   **Defaults to `'token'`** so every existing trigger keeps bearer-token auth
   untouched. NEW webhook triggers are also born `'token'` and UPGRADE to
   `'client_signed'` when their FIRST client is registered
   (`routers/webhook_clients.py::create_webhook_client`). Birthing them
   `'client_signed'` was tried and reverted: `client_signed` with an empty
   allowlist is a trigger that authenticates NOBODY, `auth_mode` is not settable
   through the trigger API (so there is no supported way back), and it 401s the
   `token` + `webhook_url` that the trigger-create response hands the caller.
   Flipping on first registration keeps the invariant `client_signed` ⟺ "at least
   one client exists". The gateway branches on the stored mode **explicitly** —
   never "try token, fall back to signed" (that priority fallthrough is the
   No-Bandaid anti-pattern). Proven by suite-76 T-S76-009/004/005.

2. `ck_agent_triggers_auth_mode` — CHECK pinning the two legal modes.

3. `webhook_clients` — the per-application credential + allowlist, keyed on
   `trigger_id` ALONE, which is why it serves **both** agent triggers and
   workflow triggers with no schema change (a workflow trigger is just an
   `agent_triggers` row with `workflow_id` set).

   **`secret_encrypted`, NOT `secret_hash`** — the data-model doc's `secret_hash`
   is unimplementable: the gateway must *recompute* `HMAC_SHA256(secret, ...)` to
   verify a signature, so it needs the raw secret back. A one-way hash cannot be
   reversed for that. The column holds a **reversible Fernet token** (`crypto.py`
   `encrypt_json`, keyed by `AGENTSHIELD_ENCRYPTION_KEY`), and the name says so
   honestly. Reveal-once is enforced at the API instead: the read schema
   (`WebhookClientResponse`) has no secret field at all, so a leak is
   unrepresentable rather than filtered. TEXT (not VARCHAR(128)) because a Fernet
   token is ~100+ chars and grows with the payload — a tight cap would truncate.

4. `agent_events.client_id` — the resolved client stamped on each accepted event
   (the audit reader's column; preferred over burying it in the payload JSON so
   it is queryable).

Idempotent (`IF [NOT] EXISTS` + a `pg_constraint` existence guard — note
`ADD CONSTRAINT IF NOT EXISTS` is **not** valid PostgreSQL, so the data-model
doc's version would fail). Up/down/up round-trips.
"""

from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Dual-mode flag. Existing rows take the 'token' default => no sender breaks.
    op.execute(
        "ALTER TABLE agent_triggers "
        "ADD COLUMN IF NOT EXISTS auth_mode VARCHAR(16) NOT NULL DEFAULT 'token'"
    )

    # 2. Pin the legal modes. `ADD CONSTRAINT IF NOT EXISTS` is not valid PostgreSQL,
    #    so guard on pg_constraint (house style, mirrors 0063).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_triggers_auth_mode'
            ) THEN
                ALTER TABLE agent_triggers
                    ADD CONSTRAINT ck_agent_triggers_auth_mode
                    CHECK (auth_mode IN ('token', 'client_signed'));
            END IF;
        END $$;
        """
    )

    # 3. Per-application credentials, allowlisted per trigger. ON DELETE CASCADE:
    #    deleting a trigger removes its clients. UNIQUE(trigger_id, client_id) is
    #    the allowlist key — and its index also serves the gateway's lookup, which
    #    always filters on trigger_id first, so no extra index is needed.
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS webhook_clients (
            id               UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            trigger_id       UUID         NOT NULL REFERENCES agent_triggers(id) ON DELETE CASCADE,
            client_id        VARCHAR(128) NOT NULL,
            secret_encrypted TEXT         NOT NULL,
            enabled          BOOLEAN      NOT NULL DEFAULT true,
            created_by       VARCHAR(256),
            created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT uq_webhook_clients_trigger_client UNIQUE (trigger_id, client_id)
        )
        """
    )

    # 4. The resolved client stamped on each accepted event (audit/query column).
    op.execute("ALTER TABLE agent_events ADD COLUMN IF NOT EXISTS client_id VARCHAR(128)")


def downgrade() -> None:
    op.execute("ALTER TABLE agent_events DROP COLUMN IF EXISTS client_id")
    op.execute("DROP TABLE IF EXISTS webhook_clients")
    op.execute("ALTER TABLE agent_triggers DROP CONSTRAINT IF EXISTS ck_agent_triggers_auth_mode")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS auth_mode")
