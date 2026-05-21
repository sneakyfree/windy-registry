"""alembic/env.py — async-compatible alembic environment.

Imports every model so target_metadata sees all tables, then uses
SQLAlchemy 2.0 + asyncpg for the actual upgrade/downgrade operations.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from windy_registry.config import get_settings
from windy_registry.database import Base
from windy_registry.models import (  # noqa: F401  (side-effect import)
    Author,
    Drop,
    DropVersion,
    Follow,
    Fork,
    Purchase,
    Rating,
    Refund,
    Tip,
    UserLibrary,
    WebhookDelivery,
    WebhookSubscription,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Override URL with our settings (env-driven).
_settings = get_settings()
if _settings.database_url:
    config.set_main_option("sqlalchemy.url", _settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online_async() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_migrations_online_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
