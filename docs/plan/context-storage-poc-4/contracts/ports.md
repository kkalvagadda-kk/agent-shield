# Contracts — `BlobStore` & `VectorStore` ports + embedding client

Mirrors the POC-0 `ConversationStore` seam: callers depend on a `Protocol`, and
`store_factory` is the **only** construction choke point. New backend = new class + a factory
env value, zero caller change.

Files:
- `services/registry-api/blob_store.py` — `BlobStore` Protocol + `MinioBlobStore`.
- `services/registry-api/vector_store.py` — `VectorStore` Protocol + `PgVectorStore`.
- `services/registry-api/embedding_client.py` — thin HTTP client for the sidecar.
- `services/registry-api/store_factory.py` — add `get_blob_store()`, `get_vector_store()`.

Shared constant: `EMBEDDING_DIM = 384` (define once in `embedding_client.py`, import where
needed). A vector whose `len != EMBEDDING_DIM` is a programming error → `ValueError`.

---

## `BlobStore` (blob_store.py)

```python
from typing import Protocol

class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str:
        """Store bytes at `key`. Returns the key. Creates the bucket on first use
        (head_bucket → create_bucket on 404). Idempotent overwrite."""

    async def get(self, key: str) -> bytes:
        """Fetch bytes at `key`. Raises KeyError if the object does not exist."""


class MinioBlobStore:
    """S3/MinIO adapter (boto3). The ONLY place that talks to object storage."""
    def __init__(self) -> None:
        # endpoint/creds/bucket from env (see below). boto3 client built lazily,
        # path-style addressing (MinIO requires it), region 'us-east-1'.
        ...
    async def put(self, key: str, data: bytes, content_type: str | None = None) -> str: ...
    async def get(self, key: str) -> bytes: ...
```

Env (registry-api pod; wired in T-021 chart edits):
| Env | Default | Source |
|---|---|---|
| `BLOB_STORE_ENDPOINT` | `http://agentshield-minio.agentshield-platform.svc.cluster.local:9000` | minio-raw Service |
| `BLOB_STORE_BUCKET` | `knowledge-sources` | created on first put |
| `BLOB_STORE_ACCESS_KEY` | — | `minio-credentials` secret `root-user` |
| `BLOB_STORE_SECRET_KEY` | — | `minio-credentials` secret `root-password` |

Impl notes: boto3 is blocking, so wrap `client.put_object`/`get_object` in
`loop.run_in_executor` (same idiom as `judge.py:_invoke_bedrock_sync`). `get` maps boto3
`NoSuchKey`/`404` → `KeyError`.

---

## `VectorStore` (vector_store.py) — the S5 enforcement point

```python
from typing import Protocol, TypedDict
from sqlalchemy.ext.asyncio import AsyncSession

class ChunkToIndex(TypedDict):
    source_id: str
    chunk_index: int
    content: str
    embedding: list[float]        # len == EMBEDDING_DIM

class SearchHit(TypedDict):
    chunk_id: str
    source_id: str
    content: str
    score: float                  # cosine similarity in [0,1]

class VectorStore(Protocol):
    async def index(
        self, db: AsyncSession, *, team: str, kb_id: str,
        chunks: list[ChunkToIndex],
    ) -> int:
        """Insert chunk rows (content + embedding) for (team, kb_id). Returns rows written.
        `team`/`kb_id` are stamped onto every row — never taken from a chunk dict."""

    async def search(
        self, db: AsyncSession, *, team: str, kb_id: str,
        query_embedding: list[float], k: int = 5,
    ) -> list[SearchHit]:
        """Top-k chunks by cosine similarity, SCOPED to (team, kb_id).
        `team` and `kb_id` are REQUIRED keyword args — there is no overload that
        omits them and no 'search all' path. Fail-closed: an empty/None team or
        kb_id raises ValueError (never a broad query)."""
```

`PgVectorStore` implementation contract:
- `index`: parameterized `INSERT INTO knowledge_chunks (id, kb_id, team, source_id,
  chunk_index, content, embedding) VALUES (…, :embedding::vector)` per chunk (or executemany),
  with `team`/`kb_id` bound from the args, `embedding` formatted `"[f,f,…]"` like
  `memory.search_memory:281`. Validates `len(embedding)==EMBEDDING_DIM` before binding.
- `search`: the query is **exactly**:
  ```sql
  SELECT id, source_id, content,
         1 - (embedding <=> :q::vector) AS score
  FROM knowledge_chunks
  WHERE team = :team AND kb_id = :kb_id      -- MANDATORY, non-optional predicate (S5)
    AND embedding IS NOT NULL
  ORDER BY embedding <=> :q::vector
  LIMIT :k
  ```
  Guards: `if not team or not kb_id: raise ValueError` **before** building SQL (fail-closed).
- **Keyword fallback** (pgvector absent, `embedding` column missing): `search` catches the
  "column embedding does not exist" error, `db.rollback()`, and runs a degraded
  `ILIKE`/`plainto_tsquery` scan **still scoped by `team AND kb_id`** (S5 holds in the
  fallback too), returning hits with `score=0.0`. On EKS pgvector is present so this path is
  dev-only; it exists so retrieval degrades rather than 500s (mirrors `memory.search_memory`).

**S5 assertion surface for tests:** call `search(team="A", kb_id=<B's kb>)` → must return `[]`
because no chunk has both `team='A'` and that `kb_id`; and `search(team="A", kb_id=<A's kb>)`
returns A's chunks. suite-77 asserts both at the store layer and via the API.

---

## `embedding_client.py`

```python
EMBEDDING_DIM = 384

async def embed(texts: list[str]) -> list[list[float]]:
    """POST the batch to the embedding sidecar; return one 384-vector per text,
    order-preserving. Raises RuntimeError on non-200 or a dim mismatch (fail-loud;
    a silent bad embedding would poison retrieval)."""
```
Env: `EMBEDDING_SIDECAR_URL` (default
`http://agentshield-embedding-sidecar.agentshield-platform.svc.cluster.local:8000`). Uses
`httpx.AsyncClient`. Both the ingest pipeline and the internal search endpoint call this — the
one embedding seam, so query and document vectors always come from the same model.

---

## `store_factory.py` additions

```python
_BLOB: BlobStore | None = None
_VEC: VectorStore | None = None

def get_blob_store() -> BlobStore:
    global _BLOB
    if _BLOB is None:
        backend = os.getenv("BLOB_STORE", "minio")
        if backend != "minio": raise ValueError(f"Unknown BLOB_STORE={backend!r}")
        _BLOB = MinioBlobStore()
    return _BLOB

def get_vector_store() -> VectorStore:
    global _VEC
    if _VEC is None:
        backend = os.getenv("VECTOR_STORE", "pgvector")
        if backend != "pgvector": raise ValueError(f"Unknown VECTOR_STORE={backend!r}")
        _VEC = PgVectorStore()
    return _VEC
```
(`MinioBlobStore` holds a reusable boto3 client → cache the instance; `PgVectorStore` is
stateless like `PostgresConversationStore` and takes the request `AsyncSession` per call, so
caching it is also fine.)

**No-orphan check:** `get_blob_store` called by the source-upload handler + ingest; `get_vector_store`
called by ingest (`index`) + the internal search endpoint (`search`); `embed` called by ingest
+ internal search. Grep each in T-021's done-gate.
