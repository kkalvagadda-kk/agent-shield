"""
AgentShield Registry API — async database engine and session factory.

Usage
-----
    from db import get_db, Base

    # In a FastAPI route:
    async def my_route(db: AsyncSession = Depends(get_db)):
        result = await db.execute(select(Agent))
        ...

    # In Alembic env.py:
    from db import Base
    target_metadata = Base.metadata
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import declarative_base

from config import settings

# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
# pool_pre_ping: evict stale connections that PgBouncer may have recycled.
# pool_size / max_overflow: tuned for a single registry-api replica; adjust
# if you scale the service horizontally (keep total < PgBouncer pool_size).
engine = create_async_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    echo=settings.log_level == "DEBUG",
)

# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
# expire_on_commit=False keeps ORM instances usable after commit, which
# matters for returning Pydantic models from response schemas.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this.
# ---------------------------------------------------------------------------
Base = declarative_base()

# Re-export AsyncSession so callers can use the type without importing SA directly.
__all__ = ["engine", "AsyncSessionLocal", "AsyncSession", "Base", "get_db"]


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a database session per request and ensure it is closed on exit.

    Usage::

        @router.get("/agents")
        async def list_agents(db: AsyncSession = Depends(get_db)):
            ...
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
