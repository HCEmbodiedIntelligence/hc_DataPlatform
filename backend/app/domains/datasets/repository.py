"""Scope-safe SQLAlchemy queries for dataset/version/review facts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Select, and_, asc, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import NotFoundError, ValidationError
from app.core.pagination import CursorParams, decode_cursor

from . import models


def scope_key(ctx: RequestContext) -> str:
    return f"org:{ctx.organization_id}/project:{ctx.project_id}/region:{ctx.region_code}"


def scope_predicate(model: Any, ctx: RequestContext) -> Any:
    if hasattr(model, "scope_key"):
        return model.scope_key == scope_key(ctx)
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
            raise ValidationError(code="INVALID_CURSOR", message="The page cursor is invalid.")
        first, second = parts[primary.key], parts[identity.key]
        if isinstance(primary.type, DateTime) and isinstance(first, str):
            first = datetime.fromisoformat(first.replace("Z", "+00:00"))
        statement = statement.where(
            or_(
                primary < first if effective_desc else primary > first,
                and_(primary == first, identity < second if effective_desc else identity > second),
            )
        )
    order = desc if effective_desc else asc
    return statement.order_by(order(primary), order(identity)).limit(params.limit + 1)


def restore_keyset_order[ModelT](rows: list[ModelT], params: CursorParams) -> list[ModelT]:
    if params.before is None:
        return rows
    return list(reversed(rows[: params.limit])) + rows[params.limit : params.limit + 1]


class DatasetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def dataset(
        self, ctx: RequestContext, dataset_id: str, *, lock: bool = False
    ) -> models.Dataset:
        stmt = select(models.Dataset).where(
            scope_predicate(models.Dataset, ctx), models.Dataset.dataset_id == dataset_id
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="DATASET_NOT_FOUND", message="The Dataset was not found.")
        return row

    async def version(
        self,
        ctx: RequestContext,
        dataset_id: str,
        version_id: str,
        *,
        lock: bool = False,
    ) -> models.DatasetVersion:
        if version_id.lower() in {"current", "latest"}:
            raise ValidationError(code="EXACT_VERSION_ID_REQUIRED")
        stmt = select(models.DatasetVersion).where(
            scope_predicate(models.DatasetVersion, ctx),
            models.DatasetVersion.version_id == version_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(
                code="VERSION_NOT_FOUND", message="The DatasetVersion was not found."
            )
        if row.dataset_id != dataset_id:
            raise NotFoundError(
                code="VERSION_DATASET_RELATIONSHIP_MISMATCH",
                message="The Version does not belong to the Dataset in the path.",
            )
        return row

    async def decision(
        self,
        ctx: RequestContext,
        version_id: str,
        decision_id: str,
    ) -> models.ReviewDecision:
        row = await self.session.scalar(
            select(models.ReviewDecision).where(
                models.ReviewDecision.scope_key == scope_key(ctx),
                models.ReviewDecision.output_version_id == version_id,
                models.ReviewDecision.review_decision_id == decision_id,
            )
        )
        if row is None:
            raise NotFoundError(code="REVIEW_DECISION_NOT_FOUND")
        return row

    async def findings(self, decision_id: str) -> list[models.ReviewFinding]:
        return list(
            (
                await self.session.scalars(
                    select(models.ReviewFinding)
                    .where(models.ReviewFinding.review_decision_id == decision_id)
                    .order_by(
                        models.ReviewFinding.created_at, models.ReviewFinding.review_finding_id
                    )
                )
            ).all()
        )
