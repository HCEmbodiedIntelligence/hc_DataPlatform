from __future__ import annotations

import asyncio
import importlib
import logging
import os
import pkgutil
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

import app.domains
from alembic import context
from app.core.db import Base

config = context.config
if database_url := os.getenv("DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")


def import_models() -> None:
    for module_name in (
        "app.core.audit",
        "app.core.idempotency",
        "app.core.outbox",
        "app.domains.ingest.models",
    ):
        importlib.import_module(module_name)
    for item in pkgutil.iter_modules(app.domains.__path__):
        module_name = f"app.domains.{item.name}.models"
        try:
            importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                logger.warning("model import failed for %s: %s", module_name, type(exc).__name__)
        except Exception as exc:
            logger.warning("model import failed for %s: %s", module_name, type(exc).__name__)


import_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    section = config.get_section(config.config_ini_section, {})
    connectable = async_engine_from_config(section, prefix="sqlalchemy.", poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
