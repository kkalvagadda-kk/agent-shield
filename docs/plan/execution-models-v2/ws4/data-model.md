# WS-4 Data Model — `webhook_clients` + `agent_triggers.auth_mode`

## Migration `00NN` (PROVISIONAL — next free after the spine)

```python
def upgrade():
    # dual-mode flag — existing triggers keep bearer-token auth; new webhook triggers use client_signed.
    op.execute("""
        ALTER TABLE agent_triggers
          ADD COLUMN IF NOT EXISTS auth_mode VARCHAR(16) NOT NULL DEFAULT 'token'
    """)
    op.execute("""
        ALTER TABLE agent_triggers
          ADD CONSTRAINT IF NOT EXISTS ck_agent_triggers_auth_mode
          CHECK (auth_mode IN ('token','client_signed'))
    """)  # guard: if the DB lacks IF NOT EXISTS on constraints, wrap in a pg_constraint existence check.

    # per-application webhook credentials, allowlisted per trigger.
    op.create_table(
        "webhook_clients",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("trigger_id", pg.UUID(as_uuid=True),
                  sa.ForeignKey("agent_triggers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("client_id", sa.String(128), nullable=False),
        sa.Column("secret_hash", sa.String(128), nullable=False),   # HMAC secret, hashed at rest
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("created_by", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("trigger_id", "client_id", name="uq_webhook_clients_trigger_client"),
        if_not_exists=True,
    )

def downgrade():
    op.drop_table("webhook_clients")
    op.execute("ALTER TABLE agent_triggers DROP CONSTRAINT IF EXISTS ck_agent_triggers_auth_mode")
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS auth_mode")
```

- Idempotent + guarded (`IF [NOT] EXISTS`, `pg_constraint` guard where needed), data-preserving.
- `ON DELETE CASCADE` — deleting a trigger removes its clients.
- `UNIQUE(trigger_id, client_id)` — a client-id is unique per trigger (the allowlist key).

## ORM

```python
class WebhookClient(Base):
    __tablename__ = "webhook_clients"
    id: Mapped[uuid.UUID] = mapped_column(_UUID, primary_key=True, server_default=text("gen_random_uuid()"))
    trigger_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_triggers.id", ondelete="CASCADE"),
                                                  nullable=False)
    client_id: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_by: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("trigger_id", "client_id", name="uq_webhook_clients_trigger_client"),)

# AgentTrigger gains:
auth_mode: Mapped[str] = mapped_column(String(16), nullable=False, server_default="token")
```

## Secret storage

- The gateway needs to recompute `HMAC_SHA256(secret, ...)`, so it needs the **secret**, not just a hash of
  the request. Store the secret **encrypted at rest** (reuse the platform's `auth_config` credential
  encryption from migration `0056`) and decrypt in the gateway — OR store an HMAC-derivable secret hash and
  keep the raw secret only in the encrypted column. `secret_hash` above is the **lookup/verify** value; the
  column name reflects "not plaintext." Reveal the raw secret to the user **once** at creation, never again.
- `agent_events.client_id` — the resolved client stamped on each accepted event. `agent_events` already has a
  flexible payload/status shape (`models.py:1673`); add `client_id VARCHAR NULL` in the same migration if a
  dedicated column is wanted, else stamp into the existing payload JSON. (Prefer the column for query/audit.)
