"""Add pgvector content_embedding to agent_memory

Revision ID: 0022
Revises: 0021

Defensive: pgvector is not present on all Postgres images (e.g. stock Bitnami).
We probe pg_available_extensions BEFORE issuing CREATE EXTENSION — a failed
CREATE EXTENSION inside Alembic's transactional DDL would abort the entire
migration transaction. When pgvector is unavailable we skip the embedding
column + ivfflat index; memory CRUD and context load/save work without them.
Semantic search stays disabled until a pgvector-capable Postgres is provisioned.
"""
from alembic import op
import sqlalchemy as sa

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def _pgvector_available(conn) -> bool:
    return bool(
        conn.execute(
            sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'vector'")
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    if not _pgvector_available(conn):
        # pgvector cannot be installed on this image — skip vector column/index.
        # Semantic search is disabled until a pgvector-capable DB is provisioned.
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # Add the column directly as the vector type. (Adding it as bytea and then
    # ALTER ... TYPE vector USING content_embedding::vector fails with
    # "cannot cast type bytea to vector" — there is no bytea→vector cast.)
    op.execute("ALTER TABLE agent_memory ADD COLUMN IF NOT EXISTS content_embedding vector(1536)")
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_agent_memory_embedding ON agent_memory "
        "USING ivfflat (content_embedding vector_cosine_ops) WITH (lists = 100)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_agent_memory_embedding")
    conn = op.get_bind()
    has_col = conn.execute(
        sa.text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'agent_memory' AND column_name = 'content_embedding'"
        )
    ).scalar()
    if has_col:
        op.drop_column("agent_memory", "content_embedding")
