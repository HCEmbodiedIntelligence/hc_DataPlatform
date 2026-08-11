"""Annotation repository with active Coverage Key convergence."""

from __future__ import annotations

from typing import Any

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext
from app.core.errors import NotFoundError

from . import models


def scope_predicate(model: Any, ctx: RequestContext) -> Any:
    return and_(
        model.organization_id == ctx.organization_id,
        model.project_id == ctx.project_id,
        model.region_code == ctx.region_code,
    )


class AnnotationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def task(
        self, ctx: RequestContext, task_id: str, *, lock: bool = False
    ) -> models.AnnotationTask:
        stmt = select(models.AnnotationTask).where(
            scope_predicate(models.AnnotationTask, ctx), models.AnnotationTask.task_id == task_id
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="ANNOTATION_TASK_NOT_FOUND")
        return row

    async def active_by_coverage(
        self, ctx: RequestContext, key_hash: str, *, lock: bool = False
    ) -> models.AnnotationTask | None:
        stmt = select(models.AnnotationTask).where(
            scope_predicate(models.AnnotationTask, ctx),
            models.AnnotationTask.coverage_key_hash == key_hash,
            models.AnnotationTask.workflow_status.not_in(("APPROVED", "CANCELLED")),
        )
        if lock:
            stmt = stmt.with_for_update()
        return await self.session.scalar(stmt)

    async def draft(self, task_id: str, *, lock: bool = False) -> models.AnnotationDraft:
        stmt = select(models.AnnotationDraft).where(models.AnnotationDraft.task_id == task_id)
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="ANNOTATION_DRAFT_NOT_FOUND")
        return row

    async def submission(
        self, task_id: str, submission_id: str, *, lock: bool = False
    ) -> models.AnnotationSubmission:
        stmt = select(models.AnnotationSubmission).where(
            models.AnnotationSubmission.task_id == task_id,
            models.AnnotationSubmission.submission_id == submission_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="ANNOTATION_SUBMISSION_NOT_FOUND")
        return row
