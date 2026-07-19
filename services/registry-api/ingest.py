"""Knowledge-source ingest pipeline (POC-4 T-010).

`ingest_source(source_id)` is a fire-and-forget task scheduled by the source-upload
handler's `BackgroundTasks`. It opens its OWN `AsyncSessionLocal` (the request
session is already closed by the time it runs), walks the source through its
lifecycle, and is fail-loud: any exception flips the source to `failed` with a
non-null `error`, logs it, and ends the task cleanly (never re-raises into the
event loop).

Pipeline (F-5):
    pending → indexing
    BlobStore.get(blob_key) → extract_text (txt/md native, pdf via pypdf)
    → chunk_text(size=1000, overlap=150) → embed(chunks) → VectorStore.index
    → chunk_count + status=ready

The embedding is written through PgVectorStore (raw SQL, the vector(384) column);
`team`/`kb_id` are the required tenant scope stamped onto every chunk row — never
taken from a chunk dict (S5).
"""
from __future__ import annotations

import io
import logging
from typing import Optional

from sqlalchemy import delete, select

from db import AsyncSessionLocal
from embedding_client import embed
from models import KnowledgeChunk, KnowledgeSource
from store_factory import get_blob_store, get_vector_store

logger = logging.getLogger(__name__)

# Shared with the plan (§6). Defined here as the ingest-side chunking policy.
CHUNK_SIZE = 1000
CHUNK_OVERLAP = 150

# Cap the persisted error string so a huge stack/decoder message can't overflow
# the (Text) column or the SourceResponse.
_MAX_ERROR_LEN = 2000


def extract_text(filename: str, content_type: Optional[str], data: bytes) -> str:
    """Extract plain text from an uploaded Source.

    txt/md decode natively (utf-8, replacing undecodable bytes so a stray byte
    never fails ingest); pdf goes through pypdf. Type is decided by content_type
    first, then the filename extension (uploads via curl often carry a generic
    `application/octet-stream`). DOCX and other types are deferred (gap ledger).
    """
    name = (filename or "").lower()
    ctype = (content_type or "").lower()

    is_pdf = ctype == "application/pdf" or name.endswith(".pdf")
    if is_pdf:
        return _extract_pdf(data)

    # Everything else the POC supports (text/plain, text/markdown, .txt, .md) is
    # decoded natively. `errors="replace"` keeps a single bad byte from failing a
    # whole document.
    return data.decode("utf-8", errors="replace")


def _extract_pdf(data: bytes) -> str:
    from pypdf import PdfReader  # lazy import — only pdf uploads pay the cost

    reader = PdfReader(io.BytesIO(data))
    parts: list[str] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def chunk_text(
    text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP
) -> list[str]:
    """Sliding-window character chunks with overlap.

    Non-empty windows only (whitespace-only trailing windows are dropped), so a
    3-paragraph .txt yields ≥1 chunk. `overlap < size` is enforced so the window
    always advances (no infinite loop on a pathological config).
    """
    if size <= 0:
        raise ValueError("chunk size must be positive")
    if overlap >= size:
        overlap = size // 4  # defensive: keep the window advancing

    normalized = text.strip()
    if not normalized:
        return []

    step = size - overlap
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        window = normalized[start : start + size].strip()
        if window:
            chunks.append(window)
        start += step
    return chunks


async def ingest_source(source_id: str) -> None:
    """Extract → chunk → embed → index a single Source. Owns its session; fail-loud.

    Any exception sets status='failed' + error=str(exc) (logged) and the task ends
    cleanly — a background task must never let an exception escape into the loop.
    """
    async with AsyncSessionLocal() as db:
        source = (
            await db.execute(
                select(KnowledgeSource).where(KnowledgeSource.id == source_id)
            )
        ).scalar_one_or_none()
        if source is None:
            logger.warning("ingest_source: source %s not found — nothing to do", source_id)
            return

        team = source.team
        kb_id = str(source.kb_id)
        blob_key = source.blob_key
        filename = source.filename
        content_type = source.content_type

        # pending → indexing (visible to the polling UI immediately).
        source.status = "indexing"
        source.error = None
        await db.commit()

        try:
            blob = await get_blob_store().get(blob_key)
            text = extract_text(filename, content_type, blob)
            chunks = chunk_text(text)
            if not chunks:
                raise ValueError("no extractable text in source (0 chunks)")

            vectors = await embed(chunks)
            chunk_dicts = [
                {
                    "source_id": str(source_id),
                    "chunk_index": i,
                    "content": chunk,
                    "embedding": vectors[i],
                }
                for i, chunk in enumerate(chunks)
            ]

            # Idempotent re-ingest: clear any prior chunks for this source before
            # re-indexing (reprocess also clears, but a bare re-run must be safe too).
            # This is team+source scoped — the same DELETE the vectors live in.
            await db.execute(
                delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id)
            )

            written = await get_vector_store().index(
                db, team=team, kb_id=kb_id, chunks=chunk_dicts
            )

            source.chunk_count = written
            source.status = "ready"
            source.error = None
            await db.commit()
            logger.info(
                "ingest_source: source=%s kb=%s team=%s ready chunks=%d",
                source_id, kb_id, team, written,
            )
        except Exception as exc:  # fail-loud: record + log, never re-raise into the loop
            logger.exception("ingest_source: source=%s FAILED: %s", source_id, exc)
            await db.rollback()
            # Re-fetch on a clean tx and mark failed (the rollback expired `source`).
            failed = (
                await db.execute(
                    select(KnowledgeSource).where(KnowledgeSource.id == source_id)
                )
            ).scalar_one_or_none()
            if failed is not None:
                failed.status = "failed"
                failed.error = str(exc)[:_MAX_ERROR_LEN]
                failed.chunk_count = 0
                await db.commit()
