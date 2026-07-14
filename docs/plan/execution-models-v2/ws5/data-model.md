# WS-5 Data Model — `agent_versions.source_url` + `build_status`

## Migration `00NN` (PROVISIONAL — next free after the spine)

```python
def upgrade():
    op.execute("ALTER TABLE agent_versions ADD COLUMN IF NOT EXISTS source_url TEXT")
    op.execute("ALTER TABLE agent_versions ADD COLUMN IF NOT EXISTS build_status VARCHAR(16)")
    op.execute("""
        ALTER TABLE agent_versions
          ADD CONSTRAINT IF NOT EXISTS ck_agent_versions_build_status
          CHECK (build_status IS NULL OR build_status IN ('pending','building','succeeded','failed'))
    """)  # guard with a pg_constraint existence check if the DB lacks IF NOT EXISTS on constraints.

def downgrade():
    op.execute("ALTER TABLE agent_versions DROP CONSTRAINT IF EXISTS ck_agent_versions_build_status")
    op.execute("ALTER TABLE agent_versions DROP COLUMN IF EXISTS build_status")
    op.execute("ALTER TABLE agent_versions DROP COLUMN IF EXISTS source_url")
```

- Idempotent + guarded, data-preserving. Both columns **nullable** — pre-existing versions (built via the CLI
  + local Docker) have `source_url=NULL`, `build_status=NULL` and are unaffected. Browser-built versions carry
  both.
- `build_status` domain: `pending` (source saved, Job not yet spawned) → `building` (Kaniko running) →
  `succeeded` (image pushed) / `failed` (build error, logs retained).

## ORM

`models.py` `AgentVersion` (~`:516`) — add:

```python
source_url: Mapped[str | None] = mapped_column(Text, nullable=True)     # MinIO agent-source pointer
build_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
```

## MinIO object layout (`agent-source` bucket — reuse existing MinIO)

```
agent-source/{team}/{agent_name}/{version}/agent.py
```

- One object per version; `source_url` stores the pointer (bucket-relative key or a signed URL policy).
- Reuses the MinIO already deployed for Langfuse (per project memory) — no second object store.

## State machine (who writes `build_status`)

| Transition | Writer | Trigger |
|---|---|---|
| → `pending` | registry-api `POST /agents/{name}/builds` | source saved to MinIO |
| `pending` → `building` | build-service | Kaniko Job started |
| `building` → `succeeded` | build-service callback → registry-api | image pushed to internal registry → **auto-create `agent_version`** |
| `building` → `failed` | build-service callback → registry-api | Kaniko non-zero exit → logs retained, **no version, no deploy** (fail-closed) |

No `agent_version` row is created until `succeeded` — a failed build never produces a deployable version.
