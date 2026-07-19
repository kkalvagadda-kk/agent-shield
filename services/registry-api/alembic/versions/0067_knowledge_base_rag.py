"""0067 — Team Knowledge Base / RAG schema (POC-4).

Four tables backing team-scoped retrieval-augmented generation:
  knowledge_bases        — a team-scoped collection of Sources
  knowledge_sources      — one uploaded file + its ingestion lifecycle (status CHECK)
  knowledge_chunks       — retrievable text segment (+ a guarded pgvector embedding)
  agent_knowledge_bindings — which KB an agent's knowledge_search is bound to

The knowledge_chunks.embedding column is a pgvector `vector(384)` type
(bge-small-en-v1.5). pgvector is NOT present on every Postgres image (stock
Bitnami lacks it), and a failed `CREATE EXTENSION` inside Alembic's transactional
DDL aborts the whole migration. So — exactly like 0022 — we probe
pg_available_extensions BEFORE touching the vector type. When pgvector is
unavailable the embedding column + HNSW index are skipped and retrieval degrades
to the keyword ILIKE fallback in PgVectorStore (surfaced, not silent). The
composite tenant index ix_knowledge_chunks_team_kb is ALWAYS created (it also
backs the keyword fallback).

EMBEDDING_DIM = 384 is the shared constant across this migration, PgVectorStore,
and the embedding sidecar; it must never drift.

Idempotent: tables are guarded by an inspector existence check, indexes and the
guarded vector column/index use `IF NOT EXISTS`, so re-runs are safe.
`downgrade()` drops in reverse (bindings, chunks, sources, bases), dropping the
ANN index first. Chains onto 0066 (drop llm_providers CHECK), the current head.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0067"
down_revision = "0066"
branch_labels = None
depends_on = None

# Shared constant — must match PgVectorStore.EMBEDDING_DIM and the embedding sidecar.
EMBEDDING_DIM = 384

_UUID = postgresql.UUID(as_uuid=True)
_TSTZ = sa.TIMESTAMP(timezone=True)
_NOW = sa.text("now()")
_GEN_UUID = sa.text("gen_random_uuid()")


def _pgvector_available(conn) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    existing = set(sa.inspect(conn).get_table_names())

    if "knowledge_bases" not in existing:
        op.create_table(
            "knowledge_bases",
            sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
            sa.Column("team", sa.String(128), nullable=False),
            sa.Column("name", sa.String(256), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("created_by", sa.String(256), nullable=True),
            sa.Column("created_at", _TSTZ, nullable=False, server_default=_NOW),
            sa.Column("updated_at", _TSTZ, nullable=False, server_default=_NOW),
            sa.UniqueConstraint("team", "name", name="uq_knowledge_bases_team_name"),
        )

    if "knowledge_sources" not in existing:
        op.create_table(
            "knowledge_sources",
            sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
            sa.Column(
                "kb_id",
                _UUID,
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("team", sa.String(128), nullable=False),
            sa.Column("filename", sa.String(512), nullable=False),
            sa.Column("blob_key", sa.String(1024), nullable=False),
            sa.Column("content_type", sa.String(128), nullable=True),
            sa.Column("size_bytes", sa.Integer(), nullable=True),
            sa.Column(
                "status",
                sa.String(32),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column(
                "chunk_count", sa.Integer(), nullable=False, server_default=sa.text("0")
            ),
            sa.Column("created_by", sa.String(256), nullable=True),
            sa.Column("created_at", _TSTZ, nullable=False, server_default=_NOW),
            sa.CheckConstraint(
                "status IN ('pending','indexing','ready','failed')",
                name="ck_knowledge_sources_status",
            ),
        )

    if "knowledge_chunks" not in existing:
        # WITHOUT the embedding column — the pgvector vector(384) column is added
        # below only when pgvector is available (guarded, exactly like 0022).
        op.create_table(
            "knowledge_chunks",
            sa.Column("id", _UUID, primary_key=True, server_default=_GEN_UUID),
            sa.Column(
                "kb_id",
                _UUID,
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("team", sa.String(128), nullable=False),
            sa.Column(
                "source_id",
                _UUID,
                sa.ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("chunk_index", sa.Integer(), nullable=False),
            sa.Column("content", sa.Text(), nullable=False),
            sa.Column("created_at", _TSTZ, nullable=False, server_default=_NOW),
        )

    if "agent_knowledge_bindings" not in existing:
        op.create_table(
            "agent_knowledge_bindings",
            sa.Column(
                "agent_id",
                _UUID,
                sa.ForeignKey("agents.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "kb_id",
                _UUID,
                sa.ForeignKey("knowledge_bases.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("team", sa.String(128), nullable=False),
            sa.Column("created_by", sa.String(256), nullable=True),
            sa.Column("created_at", _TSTZ, nullable=False, server_default=_NOW),
        )

    # Indexes — always created (IF NOT EXISTS for idempotency on re-run).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_bases_team "
        "ON knowledge_bases (team)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_sources_kb "
        "ON knowledge_sources (kb_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_sources_team_kb "
        "ON knowledge_sources (team, kb_id)"
    )
    # The composite tenant index — ALWAYS created (S5 predicate + keyword fallback).
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_team_kb "
        "ON knowledge_chunks (team, kb_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_knowledge_bindings_agent "
        "ON agent_knowledge_bindings (agent_id)"
    )

    # Guarded pgvector column + ANN index — only when pgvector is installable.
    if _pgvector_available(conn):
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            "ALTER TABLE knowledge_chunks "
            f"ADD COLUMN IF NOT EXISTS embedding vector({EMBEDDING_DIM})"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS ix_knowledge_chunks_embedding "
            "ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)"
        )
    # else: semantic search disabled on this DB; PgVectorStore keyword fallback covers it.


def downgrade() -> None:
    # Drop in reverse dependency order. The ANN index is dropped first (guarded);
    # the vector column goes away with the table. All guarded with IF EXISTS.
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_embedding")
    op.execute("DROP TABLE IF EXISTS agent_knowledge_bindings")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP TABLE IF EXISTS knowledge_sources")
    op.execute("DROP TABLE IF EXISTS knowledge_bases")
