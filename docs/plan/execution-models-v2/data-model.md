# WS-0 Data Model

WS-0 is deliberately **thin on schema**: one migration (`0058`) that makes `agent_class`
un-droppable on both executables. No new tables. `run_steps` is reused unchanged for
production durable steps (it is already polymorphic on `run_id`).

## Migration `0058` — `agent_class` NOT NULL on both executables (M3)

File: `services/registry-api/alembic/versions/0058_agent_class_not_null_and_workflows_agent_class.py`
- `revision = "0058"`
- `down_revision = "0057"`  ← verified head (research.md Correction 1)

Idempotent + guarded per CLAUDE.md migration rules. Runs inside one transaction.

### `upgrade()`

**agents.agent_class** (exists, currently `nullable=True`, no default, no check):
1. Backfill: `UPDATE agents SET agent_class = 'user_delegated' WHERE agent_class IS NULL;`
   (preserves today's deploy-time behavior exactly — the removed coalesce defaulted NULL→user_delegated).
2. `ALTER TABLE agents ALTER COLUMN agent_class SET DEFAULT 'user_delegated';`
3. `ALTER TABLE agents ALTER COLUMN agent_class SET NOT NULL;`
4. Add check (guarded — skip if it already exists):
   `ALTER TABLE agents ADD CONSTRAINT ck_agents_agent_class CHECK (agent_class IN ('user_delegated','daemon'));`

**workflows.agent_class** (new column):
5. `ALTER TABLE workflows ADD COLUMN IF NOT EXISTS agent_class VARCHAR(32) NOT NULL DEFAULT 'user_delegated';`
   (the `DEFAULT` backfills every existing row atomically → the `NOT NULL` add cannot fail).
6. Add check (guarded):
   `ALTER TABLE workflows ADD CONSTRAINT ck_workflows_agent_class CHECK (agent_class IN ('user_delegated','daemon'));`

Guard pattern for the checks (idempotent — the migration may be re-run on a partially-migrated DB):
```python
op.execute("""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agents_agent_class') THEN
    ALTER TABLE agents ADD CONSTRAINT ck_agents_agent_class
      CHECK (agent_class IN ('user_delegated','daemon'));
  END IF;
END $$;
""")
```

### `downgrade()`
- `ALTER TABLE agents DROP CONSTRAINT IF EXISTS ck_agents_agent_class;`
- `ALTER TABLE agents ALTER COLUMN agent_class DROP NOT NULL;`
- `ALTER TABLE agents ALTER COLUMN agent_class DROP DEFAULT;`
- `ALTER TABLE workflows DROP CONSTRAINT IF EXISTS ck_workflows_agent_class;`
- `ALTER TABLE workflows DROP COLUMN IF EXISTS agent_class;`
(Data-preserving on the agents side: down does not re-NULL existing values.)

### Why NOT NULL + DEFAULT + CHECK together (make illegal states unrepresentable)
- `NOT NULL` — a class can never be absent → the deploy-time coalesce (M3) is deletable.
- `DEFAULT 'user_delegated'` — a raw `INSERT` that omits the column (direct SQL / a legacy caller)
  still gets a valid, visible value, not NULL. This is the "explicit default persisted, never a silent
  deploy downgrade" the M3 test asserts.
- `CHECK (... IN (...))` — no run can carry a garbage class; OPA's class-based flow (WS-2) can trust it.

## ORM model edits (`services/registry-api/models.py`)

**`Agent` (`:78`) — tighten `agent_class` (`:166`):**
```python
# was: agent_class: Mapped[str | None] = mapped_column(String(32), nullable=True)
agent_class: Mapped[str] = mapped_column(
    String(32), nullable=False, server_default=text("'user_delegated'")
)
```
Add to `Agent.__table_args__` (`:80`):
```python
CheckConstraint("agent_class IN ('user_delegated','daemon')", name="ck_agents_agent_class"),
```
Also reword the stale comment at `:155` (`reactive (single-shot)`) to the R1 taxonomy
(reactive = ephemeral/in-request/synchronous; durable = checkpointed/parks+resumes/survives restart).

**`CompositeWorkflow` (`:316`) — new `agent_class`:**
```python
agent_class: Mapped[str] = mapped_column(
    String(32), nullable=False, server_default=text("'user_delegated'")
)
```
Add to `CompositeWorkflow.__table_args__` (`:318`):
```python
CheckConstraint("agent_class IN ('user_delegated','daemon')", name="ck_workflows_agent_class"),
```

### Mapper-configure verification (CLAUDE.md rule)
After the edits: `python3 -c "import models, sqlalchemy.orm as o; o.configure_mappers(); print('ok')"`
from `services/registry-api/` must print `ok`.

## `run_steps` — reused unchanged for production durable runs

No migration. `RunStep.run_id` (`models.py:1572`) is a bare UUID (polymorphic; no FK). The new
`POST /api/v1/internal/runs/{run_id}/step-update` (Task T4) writes `RunStep(run_id=<agent_run.id>, ...)`
respecting `UniqueConstraint(run_id, step_number)` — identical write shape to the sandbox
`playground.py:284` callback, just against `AgentRun` rows.

## Field reference (authoritative types — keep consistent across all tasks/contracts)

| field | table/model | type | nullable | default | check |
|---|---|---|---|---|---|
| `agent_class` | `agents` / `Agent` | `varchar(32)` | **NO** | `'user_delegated'` | `IN ('user_delegated','daemon')` |
| `agent_class` | `workflows` / `CompositeWorkflow` | `varchar(32)` | **NO** | `'user_delegated'` | `IN ('user_delegated','daemon')` |
| `execution_shape` | `agents` / `Agent` | `varchar(16)` | NO | `'reactive'` | (existing app-level `reactive|durable`) |
| `execution_shape` | `workflows` / `CompositeWorkflow` | `varchar(16)` | NO | `'durable'` | `ck_workflows_execution_shape` (existing) |

`agent_class` value domain is exactly `{user_delegated, daemon}` everywhere — Pydantic pattern
`^(daemon|user_delegated)$`, TS union `"user_delegated" | "daemon"`, SQL CHECK, OPA input. One vocabulary.
