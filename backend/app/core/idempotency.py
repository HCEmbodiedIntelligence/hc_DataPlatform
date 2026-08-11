from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.encoders import jsonable_encoder
from sqlalchemy import JSON, DateTime, Index, Integer, String, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base
from app.core.errors import VersionConflictError
from app.core.ids import new_id


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint("scope", "idempotency_key", name="uq_idempotency_scope_key"),
        Index("ix_idempotency_state_expires", "state", "expires_at"),
    )

    record_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    scope: Mapped[str] = mapped_column(String(1024), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    response_schema_version: Mapped[str | None] = mapped_column(String(64))
    response_body_or_resource_ref: Mapped[Any | None] = mapped_column(JSON)
    async_job_id: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_active_guard = asyncio.Lock()
_active_keys: set[tuple[str, str]] = set()


def _scope_key(scope: Any) -> str:
    if isinstance(scope, str):
        return scope
    if is_dataclass(scope):
        scope = asdict(scope)
    if isinstance(scope, Mapping):
        return json.dumps(scope, sort_keys=True, separators=(",", ":"), default=str)
    return str(scope)


async def with_idempotency(
    session: AsyncSession,
    key: str,
    scope: Any,
    fn: Callable[[], Awaitable[Any] | Any],
) -> Any:
    scope_key = _scope_key(scope)
    guard_key = (scope_key, key)
    async with _active_guard:
        if guard_key in _active_keys:
            raise VersionConflictError(
                code="IDEMPOTENCY_IN_PROGRESS",
                message="A request with this idempotency key is already in progress.",
                retryable=True,
            )
        _active_keys.add(guard_key)

    try:
        existing = await session.scalar(
            select(IdempotencyRecord).where(
                IdempotencyRecord.scope == scope_key,
                IdempotencyRecord.idempotency_key == key,
            )
        )
        if existing:
            if existing.state == "COMPLETED":
                return existing.response_body_or_resource_ref
            raise VersionConflictError(
                code="IDEMPOTENCY_IN_PROGRESS",
                message="A request with this idempotency key is already in progress.",
                retryable=True,
            )

        now = datetime.now(UTC)
        record = IdempotencyRecord(
            record_id=new_id("idempotency"),
            scope=scope_key,
            idempotency_key=key,
            state="IN_PROGRESS",
            created_at=now,
            locked_until=now + timedelta(minutes=2),
            expires_at=now + timedelta(days=1),
        )
        try:
            async with session.begin_nested():
                session.add(record)
                await session.flush()
        except IntegrityError as exc:
            raise VersionConflictError(
                code="IDEMPOTENCY_IN_PROGRESS",
                message="A request with this idempotency key is already in progress.",
                retryable=True,
            ) from exc

        value = fn()
        if inspect.isawaitable(value):
            value = await value
        stable = jsonable_encoder(value)
        record.state = "COMPLETED"
        record.response_body_or_resource_ref = stable
        record.completed_at = datetime.now(UTC)
        record.locked_until = None
        await session.flush()
        return value
    finally:
        async with _active_guard:
            _active_keys.discard(guard_key)
