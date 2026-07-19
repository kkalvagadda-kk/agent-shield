"""Single construction choke points for the storage ports (§4.1).

`get_conversation_store()` / `get_blob_store()` / `get_vector_store()` are the ONLY
places that pick a backend. Callers depend on the Protocols
(`ConversationStore` / `BlobStore` / `VectorStore`), never on a concrete adapter.
A future backend ships as a new class + a new env value here — zero caller change.
"""
from __future__ import annotations

import os

from blob_store import BlobStore, MinioBlobStore
from conversation_store import ConversationStore, PostgresConversationStore
from vector_store import PgVectorStore, VectorStore

# Cached singletons — MinioBlobStore holds a reusable boto3 client; PgVectorStore is
# stateless (takes the request AsyncSession per call), so caching it is fine too.
_BLOB: BlobStore | None = None
_VEC: VectorStore | None = None


def get_conversation_store() -> ConversationStore:
    backend = os.getenv("CONVERSATION_STORE", "postgres")
    if backend == "postgres":
        return PostgresConversationStore()
    raise ValueError(f"Unknown CONVERSATION_STORE={backend!r}")


def get_blob_store() -> BlobStore:
    global _BLOB
    if _BLOB is None:
        backend = os.getenv("BLOB_STORE", "minio")
        if backend != "minio":
            raise ValueError(f"Unknown BLOB_STORE={backend!r}")
        _BLOB = MinioBlobStore()
    return _BLOB


def get_vector_store() -> VectorStore:
    global _VEC
    if _VEC is None:
        backend = os.getenv("VECTOR_STORE", "pgvector")
        if backend != "pgvector":
            raise ValueError(f"Unknown VECTOR_STORE={backend!r}")
        _VEC = PgVectorStore()
    return _VEC
