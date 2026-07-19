"""Embedding client — the single seam to the embedding sidecar (POC-4).

Both the ingest pipeline and the internal knowledge/search endpoint call `embed`,
so query and document vectors always come from the SAME model. Fail-loud: a
non-200 response or a dim mismatch raises `RuntimeError` (a silent bad embedding
would poison retrieval).

EMBEDDING_DIM = 384 (bge-small-en-v1.5) is the shared constant — defined ONCE here
and imported by PgVectorStore; it also matches migration 0067's `vector(384)` and
the embedding sidecar. It must never drift.
"""
from __future__ import annotations

import os

import httpx

# Shared constant — must match PgVectorStore (imports this) + migration 0067 +
# the embedding sidecar model output dim.
EMBEDDING_DIM = 384

EMBEDDING_SIDECAR_URL = os.getenv(
    "EMBEDDING_SIDECAR_URL",
    "http://agentshield-embedding-sidecar.agentshield-platform.svc.cluster.local:8000",
)

_TIMEOUT = float(os.getenv("EMBEDDING_SIDECAR_TIMEOUT", "60"))


async def embed(texts: list[str]) -> list[list[float]]:
    """POST the batch to the embedding sidecar; return one EMBEDDING_DIM-vector per
    text, order-preserving. Raises RuntimeError on non-200 or a dim/count mismatch
    (fail-loud)."""
    if not texts:
        return []

    url = EMBEDDING_SIDECAR_URL.rstrip("/") + "/embed"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(url, json={"texts": texts})
    except httpx.HTTPError as exc:
        raise RuntimeError(f"embedding sidecar request failed: {exc}") from exc

    if resp.status_code != 200:
        raise RuntimeError(
            f"embedding sidecar returned {resp.status_code}: {resp.text[:200]}"
        )

    data = resp.json()
    embeddings = data.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != len(texts):
        got = len(embeddings) if isinstance(embeddings, list) else "none"
        raise RuntimeError(
            f"embedding count mismatch: got {got} embeddings for {len(texts)} texts"
        )

    for i, vec in enumerate(embeddings):
        if not isinstance(vec, list) or len(vec) != EMBEDDING_DIM:
            got = len(vec) if isinstance(vec, list) else "non-list"
            raise RuntimeError(
                f"embedding dim mismatch at index {i}: expected {EMBEDDING_DIM}, got {got}"
            )

    # Order-preserving: the sidecar returns embeddings aligned to the input order.
    return embeddings
