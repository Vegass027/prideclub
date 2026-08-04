from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

# Загружаем .env
from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

load_dotenv()

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# URL из env (а не из alembic.ini)
database_url = os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL_SYNC")
if not database_url:
    raise RuntimeError("DATABASE_URL or DATABASE_URL_SYNC is required for migrations")

if database_url.startswith("postgresql+asyncpg://"):
    db_url = database_url
else:
    # Alembic в синхронном режиме использует sync драйвер
    db_url = database_url

config.set_main_option("sqlalchemy.url", db_url)

# Без autogenerate: миграции пишутся вручную по docs/06-data-model.md
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
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


async def run_migrations_online() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())