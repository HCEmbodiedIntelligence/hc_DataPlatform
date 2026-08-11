from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    create_async_engine,
)
from sqlalchemy.ext.asyncio import (
    async_sessionmaker as _async_sessionmaker,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


def _database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://platform:platform@localhost:5432/platform",
    )


def create_engine(url: str | None = None) -> AsyncEngine:
    target = url or _database_url()
    options: dict[str, object] = {"pool_pre_ping": True}
    if target.startswith("sqlite"):
        options.pop("pool_pre_ping", None)
    return create_async_engine(target, **options)


engine = create_engine()
async_sessionmaker = _async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
SessionLocal = async_sessionmaker


async def get_session() -> AsyncIterator[AsyncSession]:
    """One transaction per request; failures always roll the whole unit back."""

    async with SessionLocal() as session:
        try:
            async with session.begin():
                yield session
        except BaseException:
            await session.rollback()
            raise
