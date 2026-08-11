"""Scope-safe queries for ManualIssue and CleaningDraft aggregates."""

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


class CleaningRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def issue(
        self, ctx: RequestContext, issue_id: str, *, lock: bool = False
    ) -> models.ManualIssue:
        stmt = select(models.ManualIssue).where(
            scope_predicate(models.ManualIssue, ctx), models.ManualIssue.manual_issue_id == issue_id
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="MANUAL_ISSUE_NOT_FOUND")
        return row

    async def draft(
        self, ctx: RequestContext, draft_id: str, *, lock: bool = False
    ) -> models.CleaningDraft:
        stmt = select(models.CleaningDraft).where(
            scope_predicate(models.CleaningDraft, ctx), models.CleaningDraft.draft_id == draft_id
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="CLEANING_DRAFT_NOT_FOUND")
        return row

    async def preview(
        self, draft_id: str, preview_id: str, *, lock: bool = False
    ) -> models.CleaningPreview:
        stmt = select(models.CleaningPreview).where(
            models.CleaningPreview.draft_id == draft_id,
            models.CleaningPreview.preview_id == preview_id,
        )
        if lock:
            stmt = stmt.with_for_update()
        row = await self.session.scalar(stmt)
        if row is None:
            raise NotFoundError(code="CLEANING_PREVIEW_NOT_FOUND")
        return row

    async def commit(self, draft_id: str, commit_id: str) -> models.CleaningCommit:
        row = await self.session.scalar(
            select(models.CleaningCommit).where(
                models.CleaningCommit.draft_id == draft_id,
                models.CleaningCommit.commit_id == commit_id,
            )
        )
        if row is None:
            raise NotFoundError(code="CLEANING_COMMIT_NOT_FOUND")
        return row


class ReviewSuccessorDraftAdapter:
    """Transaction-participating local adapter called only by domain 02."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_review_return_successor_in_transaction(
        self,
        *,
        ctx: RequestContext,
        supersedes_draft_id: str,
        returned_from_version_id: str,
        returned_from_review_decision_id: str,
        editable_base_revision_id: str,
        composition_members: list[dict[str, Any]],
    ) -> str:
        from datetime import UTC, datetime

        from app.core.ids import new_id

        source = await CleaningRepository(self.session).draft(ctx, supersedes_draft_id, lock=True)
        draft_id = new_id("cleaning_draft")
        now = datetime.now(UTC)
        empty_hash = "sha256:" + __import__("hashlib").sha256(b"[]").hexdigest()
        self.session.add(
            models.CleaningDraft(
                draft_id=draft_id,
                organization_id=ctx.organization_id,
                project_id=ctx.project_id,
                region_code=ctx.region_code,
                origin_type="REVIEW_RETURN",
                dataset_id=source.dataset_id,
                base_version_id=returned_from_version_id,
                episode_id=source.episode_id,
                base_revision_id=editable_base_revision_id,
                status="EDITING",
                current_edl_revision=0,
                current_operation_hash=empty_hash,
                resource_version=1,
                created_at=now,
                created_by=ctx.actor_id,
                updated_at=now,
                updated_by=ctx.actor_id,
            )
        )
        self.session.add(
            models.ReviewReturnDraftLineage(
                draft_id=draft_id,
                supersedes_draft_id=supersedes_draft_id,
                returned_from_version_id=returned_from_version_id,
                returned_from_review_decision_id=returned_from_review_decision_id,
            )
        )
        canonical = sorted(composition_members, key=lambda item: int(item["source_ordinal"]))
        digest = (
            __import__("hashlib")
            .sha256(
                __import__("json").dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            )
            .hexdigest()
        )
        self.session.add(
            models.ReviewSuccessorComposition(
                draft_id=draft_id,
                schema_version=1,
                source_version_id=returned_from_version_id,
                editable_base_revision_id=editable_base_revision_id,
                composition_hash=f"sha256:{digest}",
            )
        )
        for item in canonical:
            self.session.add(
                models.ReviewSuccessorCompositionMember(
                    draft_id=draft_id,
                    source_ordinal=int(item["source_ordinal"]),
                    source_revision_id=item["source_revision_id"],
                    handling=item["handling"],
                )
            )
        self.session.add(
            models.CleaningEdlRevision(
                draft_id=draft_id,
                edl_revision=0,
                operation_hash=empty_hash,
                canonical_document={"operations": []},
                validation_result={"status": "PASSED", "issues": []},
                calculated_summary={"output_segment_count": "1"},
                author_id=ctx.actor_id,
                created_at=now,
            )
        )
        self.session.add(
            models.CleaningDraftLineageClosure(
                ancestor_draft_id=draft_id, descendant_draft_id=draft_id, depth=0, created_at=now
            )
        )
        ancestors = list(
            (
                await self.session.scalars(
                    select(models.CleaningDraftLineageClosure).where(
                        models.CleaningDraftLineageClosure.descendant_draft_id
                        == supersedes_draft_id
                    )
                )
            ).all()
        )
        for ancestor in ancestors:
            self.session.add(
                models.CleaningDraftLineageClosure(
                    ancestor_draft_id=ancestor.ancestor_draft_id,
                    descendant_draft_id=draft_id,
                    depth=ancestor.depth + 1,
                    created_at=now,
                )
            )
        await self.session.flush()
        return draft_id
