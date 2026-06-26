"""
Alembic async env.py for the AgentShield Registry API.

Uses SQLAlchemy's async engine so migrations run against the same
asyncpg driver that the application uses.  The DATABASE_URL is read
from application settings (config.py) rather than from alembic.ini,
so there is a single source of truth for the connection string.

Run modes
---------
  Offline  — generates SQL script without a live DB connection.
  Online   — connects to Postgres and executes migrations directly.
"""

import asyncio
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Import Base (triggers model registration in metadata) and settings.
# Both modules live next to env.py on sys.path because alembic.ini sets
# prepend_sys_path = .
# ---------------------------------------------------------------------------
from db import Base  # noqa: F401 — side-effect: registers all ORM models
import models  # noqa: F401 — ensures every model class is imported
from config import settings

# ---------------------------------------------------------------------------
# Alembic Config object — provides access to values in alembic.ini.
# ---------------------------------------------------------------------------
config = context.config

# Wire logging from the ini file (if running via `alembic` CLI).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override the sqlalchemy.url with the value from application settings so
# the migration always uses the same URL as the running service.
# Use DIRECT_DATABASE_URL if available (bypasses PgBouncer; required for
# DDL transactions and autogenerate introspection).
migration_url = settings.direct_database_url or settings.database_url
config.set_main_option("sqlalchemy.url", migration_url)

# ---------------------------------------------------------------------------
# Target metadata — Alembic uses this for autogenerate comparisons.
# ---------------------------------------------------------------------------
target_metadata = Base.metadata


# ---------------------------------------------------------------------------
# Offline mode — emit SQL to stdout/file without a live connection.
# ---------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """Run migrations without a database connection.

    Useful for generating a SQL script that a DBA can review and apply.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


# ---------------------------------------------------------------------------
# Online mode — connect to Postgres and apply migrations.
# ---------------------------------------------------------------------------
def do_run_migrations(connection) -> None:
    """Configure the migration context and run all pending migrations."""
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Create an async engine, hand a sync-compatible connection to Alembic."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: Alembic should never pool connections; each run gets a
        # fresh connection that is closed immediately after migrations finish.
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        # run_sync bridges the async connection into the synchronous Alembic API.
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
