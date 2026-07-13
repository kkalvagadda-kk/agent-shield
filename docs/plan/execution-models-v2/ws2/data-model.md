# WS-2 Data Model — armed-by capture (one small migration)

## Migration `00NN` (PROVISIONAL number — next free after WS-0/WS-1 land)

```python
# add agent_triggers.armed_by — the authorizing human who armed a daemon trigger.
def upgrade():
    op.execute("ALTER TABLE agent_triggers ADD COLUMN IF NOT EXISTS armed_by VARCHAR(256)")
def downgrade():
    op.execute("ALTER TABLE agent_triggers DROP COLUMN IF EXISTS armed_by")
```

- Idempotent (`IF [NOT] EXISTS`), data-preserving, single statement.
- Nullable — pre-existing triggers backfill lazily (an un-armed legacy trigger has `armed_by=NULL`; audit
  shows "unknown armer" rather than blocking). New arms always set it.

## ORM edit

`models.py` `AgentTrigger` (~`:1602`) — add after `input_payload`:

```python
armed_by: Mapped[str | None] = mapped_column(String(256), nullable=True)  # authorizing human (daemon)
```

## Columns WS-2 relies on (already present — verified 2026-07-12)

| Column | Table | Line | Use |
|---|---|---|---|
| `run_by` | `agent_runs` | `models.py:1506` | Principal of the run — service identity (daemon trigger-run) or user (interactive/user_delegated). **No new column.** |
| `trigger_type` | `agent_runs` | `models.py:1503` | Distinguishes trigger-run (`schedule`/`webhook`/`workflow`) from interactive (`manual`) for the identity floor. |
| `agent_class` | `agents` / `workflows` | WS-0 `0058` | The authority axis `user_identity_ok` reads. |
| `agent_identities` (service identity) | table | migration (service identity) | Source of the daemon service principal. |

## Optional (NOT in WS-2 unless audit requires persistence)

`approvals.reviewer_scope VARCHAR NULL` — the reviewer role a daemon approval was routed to. **Deferred:**
scope is derivable at read time from the run's `agent_class` + the trigger's approver-role config, so WS-2
does **not** add it. Add only if the audit trail must persist the routed scope independently of config drift.

## `principal_display` (derived, not stored)

```
daemon trigger-run:        f"service:{agent_name} on behalf of {trigger.armed_by or 'unknown'}"
daemon workflow member:    f"workflow:{workflow_name} (service) on behalf of {trigger.armed_by or 'unknown'}"
user_delegated / chat:     the user's display (caller identity)
```
