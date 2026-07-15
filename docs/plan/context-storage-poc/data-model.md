# Data Model — Context Storage POC-0/1

Only `agent_memory` changes in this slice. No new tables (user_profiles, knowledge_* are later POCs).

---

## 1. `agent_memory` — existing columns (as-built, `models.py` L1779-1805)

| Column | Type | Null | Notes |
|---|---|---|---|
| `id` | UUID PK | no | server default gen |
| `agent_name` | String(256) | no | author of the row |
| `team` | String(128) | no | |
| `thread_id` | String(256) | no | **transcript key** (= `conversation_id` at write time) |
| `user_id` | String(256) | yes | Keycloak sub; **now the per-user scope key** |
| `role` | String(16) | no | CHECK `role IN ('user','assistant','system','tool')` |
| `content` | Text | no | raw (S2 scan deferred to Tighten) |
| `message_index` | Integer | no | monotonic per `thread_id` after this slice |
| `session_id` | String(256) | yes | |
| `deployment_id` | UUID | yes | |
| `created_at` | TIMESTAMPTZ | no | |
| `expires_at` | TIMESTAMPTZ | yes | |

Existing indexes: `ix_agent_memory_thread_msg(thread_id, message_index)`, `ix_agent_memory_agent_team(agent_name, team)`.

---

## 2. New columns (this slice)

| Column | Type | Null | Default | Constraint |
|---|---|---|---|---|
| `workflow_run_id` | UUID | yes | NULL | set for `scope='workflow_run'` rows (= parent workflow run id) |
| `scope` | VARCHAR(16) | no | `'agent'` | CHECK `scope IN ('agent','workflow_run')` |
| `message_kind` | VARCHAR(16) | no | `'agent_output'` | CHECK `message_kind IN ('user','agent_output','rationale')` |

New index: `idx_agent_memory_thread_scope(thread_id, scope, message_index)` — serves the workflow-scoped ordered read.
New constraint: `uq_agent_memory_thread_msg UNIQUE(thread_id, message_index)` — the S4 correctness backstop.

**Semantics.**
- A **lone agent chat** turn: `scope='agent'`, `workflow_run_id=NULL`, `agent_name`=the agent, `message_kind` = `user` for the human turn / `agent_output` for the reply.
- A **workflow member** turn: `scope='workflow_run'`, `workflow_run_id`=parent run id, `thread_id`=shared `conversation_id`, `agent_name`=the member, `message_kind='agent_output'`.
- `message_kind='rationale'` is a valid value now (schema-ready) but no writer emits it in this slice — the Haiku summarizer is deferred (plan §10).

---

## 3. Migration `0064` DDL

File: `services/registry-api/alembic/versions/0064_agent_memory_shared_thread.py`.
`revision = "0064"`, `down_revision = "0063"` (**re-verify head at implementation time**; if exec-v2 took 0064, renumber + re-chain). Idempotent, data-preserving.

```python
"""0064 — agent_memory shared-workflow-thread columns + atomic-index backstop.

POC-1 (context storage): scope/workflow_run_id/message_kind for the shared workflow
transcript, a (thread_id, scope, message_index) read index, and a
UNIQUE(thread_id, message_index) backstop for the S4 atomic-index fix. Idempotent +
data-preserving; up/down/up round-trips.
"""
from alembic import op

revision = "0064"
down_revision = "0063"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Additive columns (guarded).
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS workflow_run_id UUID")
    op.execute(
        "ALTER TABLE agent_memory "
        "ADD COLUMN IF NOT EXISTS scope VARCHAR(16) NOT NULL DEFAULT 'agent'"
    )
    op.execute(
        "ALTER TABLE agent_memory "
        "ADD COLUMN IF NOT EXISTS message_kind VARCHAR(16) NOT NULL DEFAULT 'agent_output'"
    )

    # 2. CHECK constraints (guarded).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_memory_scope') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT ck_agent_memory_scope
                    CHECK (scope IN ('agent','workflow_run'));
            END IF;
            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_agent_memory_message_kind') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT ck_agent_memory_message_kind
                    CHECK (message_kind IN ('user','agent_output','rationale'));
            END IF;
        END $$;
        """
    )

    # 3. Read index for the workflow-scoped ordered transcript read.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_agent_memory_thread_scope "
        "ON agent_memory (thread_id, scope, message_index)"
    )

    # 4. Pre-flight de-dup, THEN the UNIQUE backstop (see §4). Renumbers any
    #    pre-existing duplicate (thread_id, message_index) rows deterministically so
    #    the constraint can be added without data loss.
    op.execute(
        """
        DO $$
        BEGIN
            -- Renumber duplicates within a thread by created_at, id (stable order).
            WITH ranked AS (
                SELECT id,
                       row_number() OVER (PARTITION BY thread_id ORDER BY message_index, created_at, id) - 1 AS rn
                FROM agent_memory
            )
            UPDATE agent_memory m
               SET message_index = r.rn
              FROM ranked r
             WHERE m.id = r.id
               AND m.message_index <> r.rn
               AND EXISTS (  -- only touch threads that actually have a collision
                   SELECT 1 FROM agent_memory d
                   WHERE d.thread_id = m.thread_id
                   GROUP BY d.thread_id, d.message_index
                   HAVING count(*) > 1
               );

            IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_agent_memory_thread_msg') THEN
                ALTER TABLE agent_memory ADD CONSTRAINT uq_agent_memory_thread_msg
                    UNIQUE (thread_id, message_index);
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS uq_agent_memory_thread_msg")
    op.execute("DROP INDEX IF EXISTS idx_agent_memory_thread_scope")
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS ck_agent_memory_message_kind")
    op.execute("ALTER TABLE agent_memory DROP CONSTRAINT IF EXISTS ck_agent_memory_scope")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS message_kind")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS scope")
    op.execute("ALTER TABLE agent_memory DROP COLUMN IF EXISTS workflow_run_id")
```

> The de-dup `UPDATE` is written defensively; if the implementer prefers, gate step 4's renumber behind a cheaper existence check first (`SELECT 1 ... HAVING count(*)>1 LIMIT 1`) and skip the UPDATE entirely when clean. Either is acceptable — the invariant is: constraint is added and no row is deleted.

---

## 4. The UNIQUE-constraint data caveat

Historically `thread_id = run_id` (unique per turn) and each thread had a single author, so `(thread_id, message_index)` is already effectively unique in existing data. The pre-flight renumber in step 4 exists so the migration is safe even if a stray duplicate exists (e.g. from the old per-`(agent,thread)` allocation on a reused `thread_id`). It preserves every row (renumber, never delete) and is idempotent (only fires on threads with an actual collision).

---

## 5. `message_index` allocation SQL (runtime, `memory.py::save_turn`)

Replaces the unlocked read-then-write. Runs inside the request transaction; the advisory lock is released at commit:
```python
from sqlalchemy import text, select, func
await db.execute(
    text("SELECT pg_advisory_xact_lock(hashtextextended(:tid, 0))"),
    {"tid": thread_id},
)
max_idx = (await db.execute(
    select(func.max(AgentMemory.message_index)).where(AgentMemory.thread_id == thread_id)
)).scalar() or 0
# insert rows at message_index = max_idx + i + 1 (i over the turn's messages), with
# scope / workflow_run_id / message_kind set from the append() call.
```
Allocation is **per `thread_id`** (agent_name dropped from the predicate) so concurrent members of a shared conversation get a single monotonic sequence.

---

## 6. Validation rules

- `scope ∈ {agent, workflow_run}`; `message_kind ∈ {user, agent_output, rationale}` (DB CHECK + Pydantic pattern on the API).
- `scope='workflow_run'` ⇒ `workflow_run_id` SHOULD be non-null (not DB-enforced in the POC to keep the migration additive; the writer always sets it — recorded as a soft invariant).
- Read ordering is always `ORDER BY message_index` (never `created_at`) so the transcript is deterministic across pods.
- `user_id` scoping: `scope='agent'` reads constrain `user_id` when the caller provides it; `workflow_run` reads span authors within one run but never across `workflow_run_id`/`thread_id` (and never across users — a workflow run belongs to one initiating user).
