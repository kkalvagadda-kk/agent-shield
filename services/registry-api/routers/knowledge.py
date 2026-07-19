"""Public Knowledge Base / RAG router (POC-4 T-011).

Prefix `/api/v1/knowledge-bases`. Team-scoped CRUD for Knowledge Bases + Sources +
the agent↔KB binding, plus a team-scoped test-retrieval box. Registered in
`main.py`.

Identity & tenancy (S5):
  The caller's identity comes from the platform's standard optional-JWT +
  `X-User-Sub`/`X-User-Team` seam (the same seam agents.py / tools.py / catalog.py
  use) — a real Studio caller carries a Keycloak JWT; an in-cluster/e2e caller
  carries the `X-User-*` headers. The **team is always resolved from the caller's
  identity, never from a request body** (contracts/endpoints.md). Every read/write
  is filtered to that team, so one team can never see or touch another's KB.

Ingest is scheduled fire-and-forget via `BackgroundTasks` → `ingest.ingest_source`
(F-5): upload returns 201 immediately with a `pending` source; the row advances
pending→indexing→ready|failed as ingest runs.
"""
from __future__ import annotations

import logging
import uuid
from typing import Optional

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Header,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel
from sqlalchemy import delete, func, select, text as sa_text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from auth_middleware import get_optional_user
from db import get_db
from embedding_client import embed
from ingest import ingest_source
from models import (
    Agent,
    AgentKnowledgeBinding,
    AgentTool,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeSource,
    Tool,
)
from schemas import (
    BindingResponse,
    ChunkResponse,
    KBHit,
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    SearchRequest,
    SearchResponse,
    SourceResponse,
)
from store_factory import get_blob_store, get_vector_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/knowledge-bases", tags=["knowledge"])

# POC supports text/markdown/pdf; DOCX etc. deferred (gap ledger).
_SUPPORTED_CTYPES = {"text/plain", "text/markdown", "application/pdf"}
_SUPPORTED_EXTS = (".txt", ".md", ".markdown", ".pdf")

# Seeded platform tool name the binding PUT idempotently attaches to the agent.
_KNOWLEDGE_SEARCH_TOOL = "knowledge_search"


class KnowledgeBaseUpdate(BaseModel):
    """PATCH body — all fields optional; `team` is never patchable (server-side)."""

    name: Optional[str] = None
    description: Optional[str] = None


class AgentKBRef(BaseModel):
    """One KB an agent is bound to — the reverse-lookup row the agent config UI
    reads to pre-select which knowledge bases the agent already searches."""

    kb_id: uuid.UUID
    name: str


# ---------------------------------------------------------------------------
# Identity / tenancy
# ---------------------------------------------------------------------------
async def _resolve_caller(
    user: dict | None,
    x_user_sub: str | None,
    x_user_team: str | None,
    db: AsyncSession,
) -> tuple[str, str]:
    """Resolve (sub, team) from the caller's IDENTITY — never from a body.

    JWT sub wins (real Studio); otherwise `X-User-Sub` (in-cluster/e2e). The team
    is resolved from `user_team_assignments` by that sub, falling back to the
    `X-User-Team` header when the caller has no assignment row (dev/e2e identities).
    401 if there is no identity at all; 403 if no team can be resolved.
    """
    sub = (user or {}).get("sub") or x_user_sub
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    row = await db.execute(
        sa_text(
            "SELECT team_name FROM user_team_assignments WHERE user_sub = :sub LIMIT 1"
        ),
        {"sub": sub},
    )
    team = row.scalar_one_or_none() or x_user_team
    if not team:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No team assignment found for caller",
        )
    return sub, team


async def _get_kb_or_404(kb_id: uuid.UUID, team: str, db: AsyncSession) -> KnowledgeBase:
    """Load a KB scoped to `team`. A KB in another team 404s (no cross-team leak)."""
    kb = (
        await db.execute(
            select(KnowledgeBase).where(
                KnowledgeBase.id == kb_id, KnowledgeBase.team == team
            )
        )
    ).scalar_one_or_none()
    if kb is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return kb


async def _kb_response(db: AsyncSession, kb: KnowledgeBase) -> KnowledgeBaseResponse:
    """Build the response with computed source_count / ready_count / attached_agents."""
    statuses = (
        await db.execute(
            select(KnowledgeSource.status).where(KnowledgeSource.kb_id == kb.id)
        )
    ).scalars().all()
    source_count = len(statuses)
    ready_count = sum(1 for s in statuses if s == "ready")

    attached = (
        await db.execute(
            select(Agent.name)
            .join(AgentKnowledgeBinding, AgentKnowledgeBinding.agent_id == Agent.id)
            .where(AgentKnowledgeBinding.kb_id == kb.id)
        )
    ).scalars().all()

    return KnowledgeBaseResponse(
        id=kb.id,
        team=kb.team,
        name=kb.name,
        description=kb.description,
        created_by=kb.created_by,
        created_at=kb.created_at,
        updated_at=kb.updated_at,
        source_count=source_count,
        ready_count=ready_count,
        attached_agents=list(attached),
    )


# ---------------------------------------------------------------------------
# Knowledge Bases
# ---------------------------------------------------------------------------
@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
async def create_kb(
    body: KnowledgeBaseCreate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseResponse:
    sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)

    exists = (
        await db.execute(
            select(KnowledgeBase.id).where(
                KnowledgeBase.team == team, KnowledgeBase.name == body.name
            )
        )
    ).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A knowledge base named '{body.name}' already exists for this team.",
        )

    kb = KnowledgeBase(
        team=team,
        name=body.name,
        description=body.description,
        created_by=sub,
    )
    db.add(kb)
    await db.commit()
    await db.refresh(kb)
    logger.info("create_kb: kb=%s team=%s name=%s", kb.id, team, kb.name)
    return await _kb_response(db, kb)


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_kbs(
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[KnowledgeBaseResponse]:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kbs = (
        await db.execute(
            select(KnowledgeBase)
            .where(KnowledgeBase.team == team)
            .order_by(KnowledgeBase.created_at.desc())
        )
    ).scalars().all()
    return [await _kb_response(db, kb) for kb in kbs]


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_kb(
    kb_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseResponse:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)
    return await _kb_response(db, kb)


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def patch_kb(
    kb_id: uuid.UUID,
    body: KnowledgeBaseUpdate,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> KnowledgeBaseResponse:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)

    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] and updates["name"] != kb.name:
        clash = (
            await db.execute(
                select(KnowledgeBase.id).where(
                    KnowledgeBase.team == team,
                    KnowledgeBase.name == updates["name"],
                    KnowledgeBase.id != kb.id,
                )
            )
        ).scalar_one_or_none()
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A knowledge base named '{updates['name']}' already exists for this team.",
            )
    for field, value in updates.items():
        setattr(kb, field, value)
    kb.updated_at = func.now()
    await db.commit()
    await db.refresh(kb)
    return await _kb_response(db, kb)


@router.delete("/{kb_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_kb(
    kb_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)
    # FK ON DELETE CASCADE removes sources + chunks (+ the vector rows). MinIO blobs
    # are left behind (orphan-blob GC deferred — gap ledger).
    await db.delete(kb)
    await db.commit()
    logger.info("delete_kb: kb=%s team=%s", kb_id, team)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Sources (ingestion)
# ---------------------------------------------------------------------------
def _is_supported(filename: str, content_type: str | None) -> bool:
    if (content_type or "").lower() in _SUPPORTED_CTYPES:
        return True
    return (filename or "").lower().endswith(_SUPPORTED_EXTS)


@router.post(
    "/{kb_id}/sources",
    response_model=SourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_source(
    kb_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)

    filename = file.filename or "upload"
    if not _is_supported(filename, file.content_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=(
                f"Unsupported file type '{file.content_type or filename}'. "
                "POC supports text/plain, text/markdown, application/pdf (.txt/.md/.pdf)."
            ),
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty.")

    source_id = uuid.uuid4()
    blob_key = f"kb/{kb_id}/{source_id}/{filename}"
    # Store the blob FIRST — if MinIO is unreachable we fail the request loudly
    # rather than persisting a source row that can never be ingested.
    await get_blob_store().put(blob_key, data, content_type=file.content_type)

    source = KnowledgeSource(
        id=source_id,
        kb_id=kb.id,
        team=team,  # from the KB/caller — never a request field
        filename=filename,
        blob_key=blob_key,
        content_type=file.content_type,
        size_bytes=len(data),
        status="pending",
        chunk_count=0,
        created_by=sub,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)

    # Fire-and-forget ingest (F-5). ingest_source opens its OWN session.
    background_tasks.add_task(ingest_source, str(source_id))
    logger.info("upload_source: source=%s kb=%s team=%s file=%s bytes=%d scheduled",
                source_id, kb_id, team, filename, len(data))
    return SourceResponse.model_validate(source)


@router.get("/{kb_id}/sources", response_model=list[SourceResponse])
async def list_sources(
    kb_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[SourceResponse]:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    await _get_kb_or_404(kb_id, team, db)
    rows = (
        await db.execute(
            select(KnowledgeSource)
            .where(KnowledgeSource.kb_id == kb_id)
            .order_by(KnowledgeSource.created_at.desc())
        )
    ).scalars().all()
    return [SourceResponse.model_validate(r) for r in rows]


async def _get_source_or_404(
    kb_id: uuid.UUID, source_id: uuid.UUID, team: str, db: AsyncSession
) -> KnowledgeSource:
    src = (
        await db.execute(
            select(KnowledgeSource).where(
                KnowledgeSource.id == source_id,
                KnowledgeSource.kb_id == kb_id,
                KnowledgeSource.team == team,
            )
        )
    ).scalar_one_or_none()
    if src is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    return src


@router.get(
    "/{kb_id}/sources/{source_id}/chunks",
    response_model=list[ChunkResponse],
)
async def get_chunks(
    kb_id: uuid.UUID,
    source_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[ChunkResponse]:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    await _get_kb_or_404(kb_id, team, db)
    src = await _get_source_or_404(kb_id, source_id, team, db)
    if src.status != "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Source is '{src.status}', chunks are available only when 'ready'.",
        )
    rows = (
        await db.execute(
            select(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == source_id)
            .order_by(KnowledgeChunk.chunk_index)
        )
    ).scalars().all()
    return [ChunkResponse.model_validate(r) for r in rows]


@router.post(
    "/{kb_id}/sources/{source_id}/reprocess",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SourceResponse,
)
async def reprocess_source(
    kb_id: uuid.UUID,
    source_id: uuid.UUID,
    background_tasks: BackgroundTasks,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> SourceResponse:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    await _get_kb_or_404(kb_id, team, db)
    src = await _get_source_or_404(kb_id, source_id, team, db)

    # Recover a stuck 'indexing' or a 'failed' source: drop its chunks (+ vectors),
    # reset to pending, re-schedule ingest.
    await db.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source_id))
    src.status = "pending"
    src.error = None
    src.chunk_count = 0
    await db.commit()
    await db.refresh(src)

    background_tasks.add_task(ingest_source, str(source_id))
    logger.info("reprocess_source: source=%s kb=%s team=%s re-scheduled", source_id, kb_id, team)
    return SourceResponse.model_validate(src)


@router.delete(
    "/{kb_id}/sources/{source_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_source(
    kb_id: uuid.UUID,
    source_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    await _get_kb_or_404(kb_id, team, db)
    src = await _get_source_or_404(kb_id, source_id, team, db)
    # Deleting the source cascades its chunks (+ vector rows) via FK ON DELETE
    # CASCADE. MinIO blob is left behind (orphan-blob GC deferred — gap ledger).
    await db.delete(src)
    await db.commit()
    logger.info("delete_source: source=%s kb=%s team=%s", source_id, kb_id, team)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Test retrieval (team-scoped; proves retrieval before attaching to an agent)
# ---------------------------------------------------------------------------
@router.post("/{kb_id}/search", response_model=SearchResponse)
async def test_retrieval(
    kb_id: uuid.UUID,
    body: SearchRequest,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> SearchResponse:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    await _get_kb_or_404(kb_id, team, db)

    query = (body.query or "").strip()
    if not query:
        return SearchResponse(hits=[])

    q_vec = (await embed([query]))[0]
    # team is the CALLER's — this box can only ever search the caller's team's KB (S5).
    hits = await get_vector_store().search(
        db, team=team, kb_id=str(kb_id), query_embedding=q_vec, k=body.k, query_text=query
    )
    if not hits:
        return SearchResponse(hits=[])

    filenames = await _source_filenames(db, [h["source_id"] for h in hits])
    return SearchResponse(
        hits=[
            KBHit(
                chunk_id=uuid.UUID(h["chunk_id"]),
                source_id=uuid.UUID(h["source_id"]),
                source_filename=filenames.get(h["source_id"], "unknown"),
                content=h["content"],
                score=h["score"],
            )
            for h in hits
        ]
    )


async def _source_filenames(db: AsyncSession, source_ids: list[str]) -> dict[str, str]:
    """Map source_id (str) → filename for the returned hits, one query."""
    if not source_ids:
        return {}
    unique = list({sid for sid in source_ids})
    rows = (
        await db.execute(
            select(KnowledgeSource.id, KnowledgeSource.filename).where(
                KnowledgeSource.id.in_([uuid.UUID(s) for s in unique])
            )
        )
    ).all()
    return {str(rid): fname for (rid, fname) in rows}


# ---------------------------------------------------------------------------
# Agent ↔ KB binding (the "attach knowledge_search" picker)
# ---------------------------------------------------------------------------
async def _ensure_knowledge_search_tool(db: AsyncSession, agent_id: uuid.UUID, added_by: str) -> None:
    """Idempotently attach the seeded `knowledge_search` tool to the agent.

    Attaching a KB wires the tool in one action (contracts/endpoints.md). If the
    platform tool has not been seeded yet, we warn but do NOT fail the binding —
    the binding is still valid and the tool attaches on the next PUT after seed.
    """
    tool_id = (
        await db.execute(select(Tool.id).where(Tool.name == _KNOWLEDGE_SEARCH_TOOL))
    ).scalar_one_or_none()
    if tool_id is None:
        logger.warning(
            "bind_agent: platform tool '%s' not seeded — binding recorded but tool not attached",
            _KNOWLEDGE_SEARCH_TOOL,
        )
        return
    stmt = (
        pg_insert(AgentTool)
        .values(agent_id=agent_id, tool_id=tool_id, added_by=added_by)
        .on_conflict_do_nothing(index_elements=[AgentTool.agent_id, AgentTool.tool_id])
    )
    await db.execute(stmt)


@router.put("/{kb_id}/agents/{agent_id}", response_model=BindingResponse)
async def bind_agent(
    kb_id: uuid.UUID,
    agent_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> BindingResponse:
    sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)

    agent = (
        await db.execute(
            select(Agent).where(Agent.id == agent_id, Agent.team == team)
        )
    ).scalar_one_or_none()
    if agent is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent not found")

    # An agent may be bound to MULTIPLE KBs — knowledge_search fans out across all
    # of them (routers/internal.py). Idempotent add: insert this (agent, kb) pair,
    # leaving any other KB bindings for the agent intact. Composite PK
    # (agent_id, kb_id) makes a re-bind a no-op via ON CONFLICT DO NOTHING.
    await db.execute(
        pg_insert(AgentKnowledgeBinding)
        .values(agent_id=agent_id, kb_id=kb.id, team=team, created_by=sub)
        .on_conflict_do_nothing(
            index_elements=[AgentKnowledgeBinding.agent_id, AgentKnowledgeBinding.kb_id]
        )
    )
    # Attaching a KB also ensures the knowledge_search tool is on the agent.
    await _ensure_knowledge_search_tool(db, agent_id, sub)
    await db.commit()
    logger.info("bind_agent: agent=%s (%s) → kb=%s team=%s", agent.name, agent_id, kb_id, team)
    return BindingResponse(agent_id=agent_id, agent_name=agent.name, kb_id=kb.id, team=team)


@router.get("/{kb_id}/agents", response_model=list[BindingResponse])
async def list_bound_agents(
    kb_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[BindingResponse]:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)
    rows = (
        await db.execute(
            select(AgentKnowledgeBinding.agent_id, Agent.name)
            .join(Agent, Agent.id == AgentKnowledgeBinding.agent_id)
            .where(AgentKnowledgeBinding.kb_id == kb.id, AgentKnowledgeBinding.team == team)
        )
    ).all()
    return [
        BindingResponse(agent_id=aid, agent_name=aname, kb_id=kb.id, team=team)
        for (aid, aname) in rows
    ]


@router.get("/agent-bindings/{agent_id}", response_model=list[AgentKBRef])
async def list_agent_knowledge_bases(
    agent_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[AgentKBRef]:
    """Reverse lookup: every KB this agent is bound to (team-scoped).

    The agent-config UI reads this to pre-select the agent's current knowledge
    bases. Returns [] when the agent has no bindings (fail-closed, not 404).
    """
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    rows = (
        await db.execute(
            select(KnowledgeBase.id, KnowledgeBase.name)
            .join(AgentKnowledgeBinding, AgentKnowledgeBinding.kb_id == KnowledgeBase.id)
            .where(
                AgentKnowledgeBinding.agent_id == agent_id,
                AgentKnowledgeBinding.team == team,
                KnowledgeBase.team == team,
            )
            .order_by(KnowledgeBase.name)
        )
    ).all()
    return [AgentKBRef(kb_id=kid, name=kname) for (kid, kname) in rows]


@router.delete(
    "/{kb_id}/agents/{agent_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def unbind_agent(
    kb_id: uuid.UUID,
    agent_id: uuid.UUID,
    x_user_sub: Optional[str] = Header(None, alias="X-User-Sub"),
    x_user_team: Optional[str] = Header(None, alias="X-User-Team"),
    user: dict | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    _sub, team = await _resolve_caller(user, x_user_sub, x_user_team, db)
    kb = await _get_kb_or_404(kb_id, team, db)
    # Unbind removes the KB scope (leaves the tool attached; the internal endpoint
    # then fail-closes to empty for this agent until a KB is bound again).
    await db.execute(
        delete(AgentKnowledgeBinding).where(
            AgentKnowledgeBinding.agent_id == agent_id,
            AgentKnowledgeBinding.kb_id == kb.id,
            AgentKnowledgeBinding.team == team,
        )
    )
    await db.commit()
    logger.info("unbind_agent: agent=%s kb=%s team=%s", agent_id, kb_id, team)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
