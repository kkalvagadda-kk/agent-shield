"""
PII anonymization mapping store.

Writes Presidio anonymization mappings to the pii_mappings table so that
safety-orchestrator can de-anonymize agent outputs for the same session.
The original_text is stored as-is here; callers should encrypt it using
the application-layer encryption key before passing it in.
"""

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import Column, Index, String, Text
from sqlalchemy.dialects.postgresql import TIMESTAMP, UUID
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped
from sqlalchemy import select, delete


class _Base(DeclarativeBase):
    pass


class PiiMapping(_Base):
    __tablename__ = "pii_mappings"
    __table_args__ = (
        Index("idx_pii_mappings_session_id", "session_id"),
        Index("idx_pii_mappings_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[str] = mapped_column(String(256), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(128), nullable=False)
    original_text: Mapped[str] = mapped_column(Text, nullable=False)
    anonymized_text: Mapped[str] = mapped_column(Text, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime] = mapped_column(TIMESTAMP(timezone=True), nullable=False)


class PiiStore:
    def __init__(self, database_url: str, ttl_hours: int = 24) -> None:
        self._engine = create_async_engine(database_url, pool_pre_ping=True)
        self._session_factory = async_sessionmaker(self._engine, expire_on_commit=False)
        self._ttl_hours = ttl_hours

    async def store_mapping(
        self,
        session_id: str,
        agent_name: str,
        original_text: str,
        anonymized_text: str,
        entity_type: str,
    ) -> None:
        expires = datetime.now(timezone.utc) + timedelta(hours=self._ttl_hours)
        mapping = PiiMapping(
            id=uuid.uuid4(),
            session_id=session_id,
            agent_name=agent_name,
            original_text=original_text,
            anonymized_text=anonymized_text,
            entity_type=entity_type,
            expires_at=expires,
        )
        async with self._session_factory() as session:
            session.add(mapping)
            await session.commit()

    async def get_mappings(self, session_id: str, agent_name: str) -> list[PiiMapping]:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            result = await session.execute(
                select(PiiMapping).where(
                    PiiMapping.session_id == session_id,
                    PiiMapping.agent_name == agent_name,
                    PiiMapping.expires_at > now,
                )
            )
            return list(result.scalars().all())

    async def purge_expired(self) -> None:
        now = datetime.now(timezone.utc)
        async with self._session_factory() as session:
            await session.execute(delete(PiiMapping).where(PiiMapping.expires_at <= now))
            await session.commit()

    async def aclose(self) -> None:
        await self._engine.dispose()
