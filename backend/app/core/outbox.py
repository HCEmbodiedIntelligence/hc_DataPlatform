from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, Integer, String, Text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.context import RequestContext
from app.core.db import Base
from app.core.ids import new_id


class OutboxRecord(Base):
    __tablename__ = "outbox_records"
    __table_args__ = (
        Index("ix_outbox_available_unpublished", "published_at", "available_at"),
        Index("ix_outbox_producer", "producer_context"),
    )

    outbox_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    producer_context: Mapped[str] = mapped_column(String(100), nullable=False)
    message_contract: Mapped[str] = mapped_column(String(200), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    effective_scope: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    payload_or_payload_ref: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    traceparent: Mapped[str | None] = mapped_column(String(512))
    correlation_id: Mapped[str | None] = mapped_column(String(128))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    broker_message_id: Mapped[str | None] = mapped_column(String(256))
    last_error_code: Mapped[str | None] = mapped_column(Text)


async def emit_event(
    session: AsyncSession,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, Any],
    ctx: RequestContext,
) -> str:
    event_id = new_id("outbox")
    now = datetime.now(UTC)
    session.add(
        OutboxRecord(
            outbox_id=event_id,
            producer_context=aggregate_type.split(".", 1)[0].lower(),
            message_contract=event_type,
            contract_version="1",
            aggregate_ref={"type": aggregate_type, "id": aggregate_id},
            effective_scope={
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            payload_or_payload_ref=payload,
            traceparent=None,
            correlation_id=ctx.request_id,
            occurred_at=now,
            available_at=now,
            attempt_count=0,
        )
    )
    await session.flush()
    return event_id
