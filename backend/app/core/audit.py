from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.catalog import AUDIT_EVENTS
from app.core.context import RequestContext
from app.core.db import Base
from app.core.errors import ServerError
from app.core.ids import new_id


class AuditRecord(Base):
    __tablename__ = "audit_records"
    __table_args__ = (
        Index(
            "ix_audit_scope_occurred", "organization_id", "project_id", "region_code", "occurred_at"
        ),
        Index("ix_audit_target", "target_type", "target_id"),
    )

    audit_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    event_name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(100), nullable=False)
    target_id: Mapped[str] = mapped_column(String(128), nullable=False)
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    organization_id: Mapped[str] = mapped_column(String(128), nullable=False)
    project_id: Mapped[str] = mapped_column(String(128), nullable=False)
    region_code: Mapped[str] = mapped_column(String(64), nullable=False)
    request_id: Mapped[str] = mapped_column(String(128), nullable=False)
    detail: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


_SENSITIVE_FRAGMENTS = (
    "token",
    "secret",
    "access_key",
    "accesskey",
    "signed_url",
    "signedurl",
    "object_key",
    "objectkey",
    "oss_key",
)
_OBJECT_KEY_NAMES = {"object_key", "object_key_prefix", "full_object_key", "oss_key"}


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(str(value).encode()).hexdigest()[:16]


def redact_detail(value: Any, key: str | None = None) -> Any:
    lowered = (key or "").lower()
    if (
        any(fragment in lowered for fragment in _SENSITIVE_FRAGMENTS)
        or lowered in _OBJECT_KEY_NAMES
    ):
        return {"redacted": True, "sha256_prefix": _fingerprint(value)}
    if isinstance(value, dict):
        return {str(k): redact_detail(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact_detail(item) for item in value]
    return value


async def write_audit(
    session: AsyncSession,
    event_name: str,
    target_type: str,
    target_id: str,
    outcome: str,
    ctx: RequestContext,
    detail: dict[str, Any] | None = None,
) -> str:
    if event_name not in AUDIT_EVENTS:
        raise ServerError(
            code="UNREGISTERED_AUDIT_EVENT",
            message="The audit event is not present in the canonical registry.",
        )
    audit_id = new_id("audit")
    session.add(
        AuditRecord(
            audit_id=audit_id,
            event_name=event_name,
            target_type=target_type,
            target_id=target_id,
            outcome=outcome,
            actor_id=ctx.actor_id,
            organization_id=ctx.organization_id,
            project_id=ctx.project_id,
            region_code=ctx.region_code,
            request_id=ctx.request_id,
            detail=redact_detail(detail) if detail else None,
            occurred_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return audit_id
