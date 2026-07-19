# POC-4 Data Model

New migration: **`0067_knowledge_base_rag.py`** (down_revision `0066`). Idempotent and
guarded exactly like `0022` — probe `pg_available_extensions` before touching the `vector`
type so the migration applies on a stock-Postgres dev box (there the embedding column + ANN
index are skipped and retrieval degrades to keyword; surfaced, not silent).

Shared constant across migration + `PgVectorStore` + embedding-sidecar: **`EMBEDDING_DIM = 384`**
(`bge-small-en-v1.5`). This literal appears in exactly three places and must match.

---

## Tables

### `knowledge_bases` — a team-scoped collection of Sources
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | `server_default gen_random_uuid()` |
| `team` | varchar(128) NOT NULL | tenant key |
| `name` | varchar(256) NOT NULL | |
| `description` | text NULL | |
| `created_by` | varchar(256) NULL | Keycloak sub of creator |
| `created_at` | timestamptz NOT NULL | `server_default now()` |
| `updated_at` | timestamptz NOT NULL | `server_default now()` |

Indexes: `ix_knowledge_bases_team (team)`. Unique `uq_knowledge_bases_team_name (team, name)`
(a team can't have two KBs with the same name).

### `knowledge_sources` — one uploaded file + its ingestion lifecycle
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `kb_id` | uuid NOT NULL FK→`knowledge_bases.id` ON DELETE CASCADE | |
| `team` | varchar(128) NOT NULL | denormalized tenant key (matches parent KB) |
| `filename` | varchar(512) NOT NULL | original upload name |
| `blob_key` | varchar(1024) NOT NULL | MinIO object key `kb/{kb_id}/{source_id}/{filename}` |
| `content_type` | varchar(128) NULL | `text/plain`,`text/markdown`,`application/pdf` |
| `size_bytes` | integer NULL | |
| `status` | varchar(32) NOT NULL default `'pending'` | `pending\|indexing\|ready\|failed` (CHECK) |
| `error` | text NULL | failure reason when `status='failed'` |
| `chunk_count` | integer NOT NULL default `0` | filled on `ready` |
| `created_by` | varchar(256) NULL | |
| `created_at` | timestamptz NOT NULL | |

Indexes: `ix_knowledge_sources_kb (kb_id)`, `ix_knowledge_sources_team_kb (team, kb_id)`.
CHECK `ck_knowledge_sources_status status IN ('pending','indexing','ready','failed')`.

### `knowledge_chunks` — retrievable text segment + its embedding
| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `kb_id` | uuid NOT NULL FK→`knowledge_bases.id` ON DELETE CASCADE | |
| `team` | varchar(128) NOT NULL | denormalized tenant key — **the S5 predicate column** |
| `source_id` | uuid NOT NULL FK→`knowledge_sources.id` ON DELETE CASCADE | |
| `chunk_index` | integer NOT NULL | 0-based position within the Source |
| `content` | text NOT NULL | the chunk text (returned to the agent) |
| `embedding` | `vector(384)` NULL | **guarded**: added only if pgvector present |
| `created_at` | timestamptz NOT NULL | |

Indexes:
- `ix_knowledge_chunks_team_kb (team, kb_id)` — **always created** (composite tenant index;
  also backs the keyword fallback when pgvector is absent).
- `ix_knowledge_chunks_embedding` — **guarded**, created only when pgvector present:
  `USING hnsw (embedding vector_cosine_ops)`. (HNSW over IVFFlat: no `lists` tuning, good
  recall at POC scale, no train step. Falls back to `ivfflat … WITH (lists=100)` only if the
  deployed pgvector predates HNSW — the portable 17.6.0 image includes pgvector ≥0.7 which
  has HNSW, so HNSW is the path taken on EKS.)

### `agent_knowledge_bindings` — which KB an agent's `knowledge_search` is bound to
| Column | Type | Notes |
|---|---|---|
| `agent_id` | uuid NOT NULL FK→`agents.id` ON DELETE CASCADE | part of PK |
| `kb_id` | uuid NOT NULL FK→`knowledge_bases.id` ON DELETE CASCADE | part of PK |
| `team` | varchar(128) NOT NULL | the agent's team (denormalized; the join key the internal endpoint filters on) |
| `created_by` | varchar(256) NULL | |
| `created_at` | timestamptz NOT NULL | |

PK `(agent_id, kb_id)`. Index `ix_agent_knowledge_bindings_agent (agent_id)`. POC constraint:
one row per agent (the attach picker replaces any existing binding); multi-KB fan-out deferred.

---

## Migration `0067` structure (mirror 0022's guard)

```python
revision = "0067"; down_revision = "0066"

def _pgvector_available(conn) -> bool:
    return bool(conn.execute(sa.text(
        "SELECT 1 FROM pg_available_extensions WHERE name='vector'")).scalar())

def upgrade():
    op.create_table("knowledge_bases", ...)          # plain columns, always
    op.create_table("knowledge_sources", ...)        # with status CHECK
    op.create_table("knowledge_chunks", ...)         # WITHOUT the embedding column
    op.create_table("agent_knowledge_bindings", ...)
    op.create_index("ix_knowledge_chunks_team_kb", "knowledge_chunks", ["team","kb_id"])
    # ... other plain indexes ...
    conn = op.get_bind()
    if _pgvector_available(conn):
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute("ALTER TABLE knowledge_chunks ADD COLUMN IF NOT EXISTS embedding vector(384)")
        op.execute("CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding "
                   "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)")
    # else: semantic search disabled on this DB (keyword fallback in PgVectorStore)
```
All `create_table`/`create_index` use `IF NOT EXISTS` semantics (guarded or
`op.execute("CREATE TABLE IF NOT EXISTS …")` where the Alembic helper lacks it) so re-runs
are safe. `downgrade()` drops in reverse (`agent_knowledge_bindings`, `knowledge_chunks`,
`knowledge_sources`, `knowledge_bases`), dropping the ANN index first if the column exists.

---

## ORM models (`services/registry-api/models.py`)

Add `KnowledgeBase`, `KnowledgeSource`, `KnowledgeChunk`, `AgentKnowledgeBinding` mirroring
the columns above. The `embedding` column is mapped as a **deferred / optional** attribute
NOT touched by ORM inserts — chunk embedding is written via raw SQL in `PgVectorStore.index`
(pgvector's `vector` type isn't a native SQLAlchemy type here; the codebase already treats it
as raw SQL in `memory.search_memory`). So the `KnowledgeChunk` ORM class declares
`content, team, kb_id, source_id, chunk_index` but **not** `embedding` (kept out of the mapper
to avoid a custom type dependency); the vector column is read/written only through
`PgVectorStore`'s `text()` SQL. This keeps `sqlalchemy.orm.configure_mappers()` clean on a
stock DB where the column may not exist.

Relationships: `KnowledgeBase.sources` (1-many, cascade delete), `KnowledgeSource.chunks`
(1-many, cascade delete). No relationship onto `Agent` for the binding (queried directly) to
avoid touching the large `Agent` mapper.

**Verify after adding:** import the routers + `sqlalchemy.orm.configure_mappers()` succeeds
(CLAUDE.md §5).

---

## Data-flow invariants (S5 tenant isolation — the security crux)

1. Every `knowledge_sources` and `knowledge_chunks` row carries `team` copied from its parent
   `knowledge_bases.team` at write time (never from a request field).
2. `PgVectorStore.search(team, kb_id, …)` puts `WHERE team = :team AND kb_id = :kb_id` into
   **every** query — `(team, kb_id)` are **required positional args**, there is no overload
   that omits them and no "search all teams" code path.
3. The `knowledge_search` internal endpoint derives `team` from the pod's server-side header
   and `kb_id` from `agent_knowledge_bindings` filtered by that same `team` — a model prompt
   can influence neither. If no binding exists for `(agent, team)`, the endpoint returns an
   empty result with a clear "no knowledge base attached" note (fail-closed: never widen).
4. KB/source/chunk read APIs filter by the caller's team (`require_user` → `/me` team), so the
   Studio UI can't list another team's KBs.
