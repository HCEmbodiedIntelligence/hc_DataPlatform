from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Select, and_, asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import NotFoundError
from app.core.pagination import CursorParams, decode_cursor
from app.domains.ingest import models


def scope_predicate(model: Any, ctx: RequestContext) -> Any:
    return and_(
        model.organization_id == ctx.organization_id,
        model.project_id == ctx.project_id,
        model.region_code == ctx.region_code,
    )


def apply_keyset(
    statement: Select[Any],
    params: CursorParams,
    primary: Any,
    identity: Any,
    *,
    descending: bool = True,
) -> Select[Any]:
    token = params.after or params.before
    reverse = params.before is not None
    effective_desc = descending != reverse
    if token:
        parts = decode_cursor(token)
        if primary.key not in parts or identity.key not in parts:
            from app.core.errors import ValidationError

            raise ValidationError(code="INVALID_CURSOR", message="The page cursor is invalid.")
        first = parts[primary.key]
        second = parts[identity.key]
        if isinstance(primary.type, DateTime) and isinstance(first, str):
            first = datetime.fromisoformat(first.replace("Z", "+00:00"))
        compare = (primary < first) if effective_desc else (primary > first)
        tie = (identity < second) if effective_desc else (identity > second)
        statement = statement.where(or_(compare, and_(primary == first, tie)))
    order = desc if effective_desc else asc
    return statement.order_by(order(primary), order(identity)).limit(params.limit + 1)


def restore_keyset_order[ModelT](rows: list[ModelT], params: CursorParams) -> list[ModelT]:
    if params.before is None:
        return rows
    page_rows = list(reversed(rows[: params.limit]))
    return page_rows + rows[params.limit : params.limit + 1]


class IngestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def source(
        self, ctx: RequestContext, source_id: str, *, lock: bool = False
    ) -> models.DataSource:
        stmt = select(models.DataSource).where(
            scope_predicate(models.DataSource, ctx), models.DataSource.source_id == source_id
        )
        if lock:
            stmt = stmt.with_for_update()
        value = await self.session.scalar(stmt)
        if value is None:
            raise NotFoundError(
                code="DATA_SOURCE_NOT_FOUND", message="The data source was not found."
            )
        return value

    async def upload(
        self, ctx: RequestContext, upload_id: str, *, lock: bool = False
    ) -> models.UploadSession:
        stmt = select(models.UploadSession).where(
            scope_predicate(models.UploadSession, ctx), models.UploadSession.upload_id == upload_id
        )
        if lock:
            stmt = stmt.with_for_update()
        value = await self.session.scalar(stmt)
        if value is None:
            raise NotFoundError(
                code="UPLOAD_SESSION_NOT_FOUND", message="The upload session was not found."
            )
        return value

    async def object(self, upload_id: str, object_id: str) -> models.UploadObject:
        value = await self.session.scalar(
            select(models.UploadObject).where(
                models.UploadObject.upload_id == upload_id,
                models.UploadObject.upload_object_id == object_id,
            )
        )
        if value is None:
            raise NotFoundError(
                code="UPLOAD_OBJECT_NOT_FOUND", message="The upload object was not found."
            )
        return value

    async def run(self, upload_id: str, run_id: str) -> models.VerificationRun:
        value = await self.session.scalar(
            select(models.VerificationRun).where(
                models.VerificationRun.upload_id == upload_id,
                models.VerificationRun.verification_run_id == run_id,
            )
        )
        if value is None:
            raise NotFoundError(
                code="VERIFICATION_RUN_NOT_FOUND", message="The verification run was not found."
            )
        return value

    async def current_quarantine(
        self, upload_id: str, *, lock: bool = False
    ) -> models.Quarantine | None:
        stmt = (
            select(models.Quarantine)
            .where(
                models.Quarantine.upload_id == upload_id,
                models.Quarantine.disposition.in_(("OPEN", "REVERIFY_REQUESTED")),
            )
            .order_by(models.Quarantine.created_at.desc())
            .limit(1)
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)
