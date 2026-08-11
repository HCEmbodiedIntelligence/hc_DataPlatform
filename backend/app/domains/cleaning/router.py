"""HTTP surface for the 27 manual-cleaning OpenAPI operations."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Body, Depends, Header, Query, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.etag import check_if_match, compute_etag
from app.core.ids import new_id
from app.core.pagination import CursorParams, build_page
from app.platform.ports import DatasetVersionPort

from . import models, schemas
from .repository import scope_predicate
from .service import (
    CONTRACT_VERSION,
    CleaningService,
    _draft_projection,
    _iso,
    _issue_projection,
    _scope,
    envelope,
)


async def require_cleaning_headers(
    projectId: str,
    regionCode: str,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    project_header: Annotated[str, Header(alias="X-Project-Id")],
    region_header: Annotated[str, Header(alias="X-Region-Code")],
    client_version: Annotated[str, Header(alias="X-Client-Version")],
) -> None:
    del organization_id, client_version
    if project_header != projectId or region_header != regionCode:
        from app.core.errors import ForbiddenError

        raise ForbiddenError(code="SCOPE_MISMATCH")


def get_dataset_version_port() -> DatasetVersionPort:
    from .service import MissingDatasetVersionPort

    return cast(DatasetVersionPort, MissingDatasetVersionPort())


router = APIRouter(
    prefix="/api/v1/projects/{projectId}", dependencies=[Depends(require_cleaning_headers)]
)
Session = Annotated[AsyncSession, Depends(get_session)]
DatasetVersions = Annotated[DatasetVersionPort, Depends(get_dataset_version_port)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
ReadEpisode = Annotated[RequestContext, Depends(require("episode.read"))]
ReadIssue = Annotated[RequestContext, Depends(require("manual_issue.read"))]
CreateIssue = Annotated[RequestContext, Depends(require("manual_issue.create"))]
TriageIssue = Annotated[RequestContext, Depends(require("manual_issue.triage"))]
ResolveIssue = Annotated[RequestContext, Depends(require("manual_issue.resolve"))]
CreateCleaning = Annotated[
    RequestContext, Depends(require("manual_issue.triage", "cleaning.create"))
]
ReadCleaning = Annotated[RequestContext, Depends(require("cleaning.read"))]
EditCleaning = Annotated[RequestContext, Depends(require("cleaning.edit"))]
PreviewCleaning = Annotated[RequestContext, Depends(require("cleaning.preview"))]
SubmitCleaning = Annotated[RequestContext, Depends(require("cleaning.submit"))]
ReadReview = Annotated[RequestContext, Depends(require("dataset_version.read"))]


async def cleaning_page(
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    after: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    before: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, after=after, before=before)


Page = Annotated[CursorParams, Depends(cleaning_page)]


@router.get(
    "/dataset-versions/{versionId}/episodes/{episodeId}/viewer-bootstrap",
    operation_id="getViewerBootstrap",
)
async def get_viewer_bootstrap(versionId: str, episodeId: str, ctx: ReadEpisode) -> dict:
    return envelope(
        {
            "version_id": versionId,
            "episode_id": episodeId,
            "selected_revision_id": None,
            "duration_ns": "0",
            "streams": [],
            "allowed_actions": [],
        },
        ctx,
    )


@router.get("/dataset-versions/{versionId}/episodes", operation_id="listViewerEpisodes")
async def list_viewer_episodes(versionId: str, ctx: ReadEpisode, page: Page) -> dict:
    result = build_page([], page, ("ordinal", "episode_id"))
    result.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
    return result


@router.get(
    "/episode-revisions/{revisionId}/streams/{streamId}/window", operation_id="getStreamWindow"
)
async def get_stream_window(
    revisionId: str,
    streamId: str,
    start_ns: Annotated[int, Query(ge=0)],
    end_ns: Annotated[int, Query(gt=0)],
    ctx: ReadEpisode,
) -> dict:
    if start_ns >= end_ns:
        from app.core.errors import ValidationError

        raise ValidationError(code="INVALID_TIME_RANGE")
    return envelope(
        {
            "revision_id": revisionId,
            "stream_id": streamId,
            "start_ns": str(start_ns),
            "end_ns": str(end_ns),
            "gaps": [],
            "artifact_version": "1",
        },
        ctx,
    )


@router.post(
    "/episode-revisions/{revisionId}/streams/{streamId}/media-descriptors",
    operation_id="authorizeMediaDescriptor",
)
async def authorize_media_descriptor(
    revisionId: str,
    streamId: str,
    body: Annotated[dict[str, Literal["VIEW"]], Body()],
    session: Session,
    ctx: ReadEpisode,
) -> dict:
    del revisionId, body
    return envelope(
        {
            "descriptor_id": new_id("descriptor"),
            "stream_id": streamId,
            "kind": "MEDIA",
            "expires_at": _iso(datetime.now(UTC) + timedelta(minutes=5)),
            "media_url": "about:blank",
        },
        ctx,
    )


@router.get("/regions/{regionCode}/manual-issues:page", operation_id="getManualIssuesPage")
async def get_manual_issues_page(session: Session, ctx: ReadIssue) -> dict:
    counts = {
        status: str(
            await session.scalar(
                select(func.count())
                .select_from(models.ManualIssue)
                .where(
                    scope_predicate(models.ManualIssue, ctx), models.ManualIssue.status == status
                )
            )
            or 0
        )
        for status in ("OPEN", "IN_PROGRESS", "RESOLVED")
    }
    return envelope(
        {"counts": counts, "facets": {}, "allowed_actions": [], "snapshot_at": _iso()}, ctx
    )


@router.get("/regions/{regionCode}/manual-issues", operation_id="listManualIssues")
async def list_manual_issues(session: Session, ctx: ReadIssue, page: Page) -> dict:
    rows = list(
        (
            await session.scalars(
                select(models.ManualIssue)
                .where(scope_predicate(models.ManualIssue, ctx))
                .order_by(
                    models.ManualIssue.updated_at.desc(), models.ManualIssue.manual_issue_id.desc()
                )
                .limit(page.limit + 1)
            )
        ).all()
    )
    result = build_page(rows, page, ("updated_at", "manual_issue_id"))
    result["items"] = [_issue_projection(row, ctx) for row in result["items"]]
    result.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
    return result


@router.post(
    "/regions/{regionCode}/manual-issues", operation_id="createManualIssue", status_code=201
)
async def create_manual_issue(
    command: schemas.CreateManualIssueRequest,
    session: Session,
    ctx: CreateIssue,
    idempotency_key: IdempotencyKey,
    versions: DatasetVersions,
    response: Response,
) -> dict:
    result = await CleaningService(session, versions).create_issue(ctx, idempotency_key, command)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.get("/regions/{regionCode}/manual-issues/{issueId}", operation_id="getManualIssue")
async def get_manual_issue(
    issueId: str, session: Session, ctx: ReadIssue, response: Response
) -> dict:
    result = await CleaningService(session).issue(ctx, issueId)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/regions/{regionCode}/manual-issues/{issueId}:triage", operation_id="triageManualIssue"
)
async def triage_manual_issue(
    issueId: str,
    command: schemas.TriageManualIssueRequest,
    request: Request,
    session: Session,
    ctx: TriageIssue,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await CleaningService(session).triage_issue(
        ctx, idempotency_key, issueId, request, command
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/regions/{regionCode}/manual-issues/{issueId}/cleaning-drafts",
    operation_id="createCleaningDraftFromManualIssue",
    status_code=201,
)
async def create_cleaning_draft_from_manual_issue(
    issueId: str,
    request: Request,
    session: Session,
    ctx: CreateCleaning,
    idempotency_key: IdempotencyKey,
    command: Annotated[schemas.CreateDraftFromIssueRequest | None, Body()] = None,
) -> dict:
    if command is None:
        command = schemas.DraftFromIssueInitialRequest()
    return await CleaningService(session).create_draft_from_issue(
        ctx, idempotency_key, issueId, request, command
    )


@router.post(
    "/regions/{regionCode}/manual-issues/{issueId}:resolve", operation_id="resolveManualIssue"
)
async def resolve_manual_issue(
    issueId: str,
    command: schemas.ResolveManualIssueRequest,
    request: Request,
    session: Session,
    ctx: ResolveIssue,
    idempotency_key: IdempotencyKey,
    versions: DatasetVersions,
) -> dict:
    service = CleaningService(session, versions)

    async def action() -> dict[str, Any]:
        row = await service.repo.issue(ctx, issueId, lock=True)
        check_if_match(request, compute_etag(row.resource_version))
        if row.status != "IN_PROGRESS":
            from app.core.errors import VersionConflictError

            raise VersionConflictError(code="MANUAL_ISSUE_NOT_IN_PROGRESS")
        version = await versions.get_version(row.dataset_id, command.resolution_version_id, ctx)
        if not version or version.get("status") != "READY":
            from app.core.errors import VersionConflictError

            raise VersionConflictError(code="RESOLUTION_VERSION_INVALID")
        link = await session.scalar(
            select(models.ManualIssueDraftLink).where(
                models.ManualIssueDraftLink.manual_issue_id == issueId
            )
        )
        producer = str(version.get("producer_draft_id") or "")
        closure = (
            await session.scalar(
                select(models.CleaningDraftLineageClosure).where(
                    models.CleaningDraftLineageClosure.ancestor_draft_id == link.draft_id,
                    models.CleaningDraftLineageClosure.descendant_draft_id == producer,
                )
            )
            if link and producer
            else None
        )
        if closure is None:
            from app.core.errors import VersionConflictError

            raise VersionConflictError(code="OUTPUT_VERSION_NOT_IN_LINEAGE")
        row.status = "RESOLVED"
        row.resolution_version_id = command.resolution_version_id
        row.resolution_note = command.resolution_note
        row.producer_draft_id = producer
        row.root_issue_draft_id = link.draft_id
        row.lineage_depth = closure.depth
        row.resolved_at = datetime.now(UTC)
        row.resolved_by = ctx.actor_id
        row.resource_version += 1
        row.updated_at = datetime.now(UTC)
        row.updated_by = ctx.actor_id
        await session.flush()
        return envelope(_issue_projection(row, ctx), ctx)

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="resolveManualIssue",
        request_value=command,
        target_type="manual_issue",
        target_id=issueId,
        events=("manual_issue.resolved",),
        action=action,
    )


@router.post(
    "/regions/{regionCode}/manual-issues/{issueId}/preview-descriptors",
    operation_id="authorizeManualIssuePreview",
)
async def authorize_manual_issue_preview(
    issueId: str,
    body: Annotated[dict[str, Literal["ISSUE_INSPECTION"]], Body()],
    session: Session,
    ctx: ReadIssue,
) -> dict:
    await CleaningService(session).repo.issue(ctx, issueId)
    del body
    return envelope(
        {
            "descriptor": {
                "descriptor_id": new_id("descriptor"),
                "issue_id": issueId,
                "expires_at": _iso(datetime.now(UTC) + timedelta(minutes=5)),
                "media_url": "about:blank",
            }
        },
        ctx,
    )


@router.get("/regions/{regionCode}/cleaning-drafts:summary", operation_id="getCleaningDraftSummary")
async def get_cleaning_draft_summary(session: Session, ctx: ReadCleaning) -> dict:
    count = await session.scalar(
        select(func.count())
        .select_from(models.CleaningDraft)
        .where(scope_predicate(models.CleaningDraft, ctx))
    )
    return envelope(
        {
            "scope_counts": {
                "EDITING": str(count or 0),
                "COMMITTED": "0",
                "RETURNED": "0",
                "REVIEWING": "0",
            },
            "metrics": {},
            "jobs": [],
            "as_of": _iso(),
        },
        ctx,
    )


@router.get("/regions/{regionCode}/cleaning-drafts", operation_id="listCleaningDrafts")
async def list_cleaning_drafts(session: Session, ctx: ReadCleaning, page: Page) -> dict:
    rows = list(
        (
            await session.scalars(
                select(models.CleaningDraft)
                .where(scope_predicate(models.CleaningDraft, ctx))
                .order_by(
                    models.CleaningDraft.updated_at.desc(), models.CleaningDraft.draft_id.desc()
                )
                .limit(page.limit + 1)
            )
        ).all()
    )
    result = build_page(rows, page, ("updated_at", "draft_id"))
    result["items"] = [_draft_projection(row) for row in result["items"]]
    result["query_signature"] = new_id("query")
    result.update(scope=_scope(ctx), request_id=ctx.request_id, contract_version=CONTRACT_VERSION)
    return result


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/summary",
    operation_id="getCleaningDraftSummaryById",
)
async def get_cleaning_draft_summary_by_id(
    draftId: str, session: Session, ctx: ReadCleaning
) -> dict:
    return await CleaningService(session).draft(ctx, draftId)


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/events", operation_id="listCleaningDraftEvents"
)
async def list_cleaning_draft_events(
    draftId: str, session: Session, ctx: ReadCleaning, page: Page
) -> dict:
    await CleaningService(session).repo.draft(ctx, draftId)
    return envelope({"events": []}, ctx)


@router.get("/regions/{regionCode}/jobs", operation_id="listJobsByResources")
async def list_jobs_by_resources(ctx: ReadCleaning) -> dict:
    return envelope({"jobs": []}, ctx)


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/bootstrap",
    operation_id="getCleaningDraftBootstrap",
)
async def get_cleaning_draft_bootstrap(draftId: str, session: Session, ctx: ReadCleaning) -> dict:
    row = await CleaningService(session).repo.draft(ctx, draftId)
    edl = await session.get(
        models.CleaningEdlRevision, {"draft_id": draftId, "edl_revision": row.current_edl_revision}
    )
    return envelope(
        {
            "draft": _draft_projection(row),
            "origin": {"origin_type": row.origin_type},
            "base": {"version_id": row.base_version_id, "revision_id": row.base_revision_id},
            "streams": [],
            "edl": edl.canonical_document if edl else {"operations": []},
            "successor_composition": None,
            "active_preview": None,
            "active_commit": None,
            "review_feedback": None,
            "lease": None,
            "allowed_actions": [],
        },
        ctx,
    )


@router.post(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/edit-sessions",
    operation_id="acquireCleaningEditLease",
    status_code=201,
)
async def acquire_cleaning_edit_lease(
    draftId: str,
    command: schemas.AcquireLeaseRequest,
    session: Session,
    ctx: EditCleaning,
    idempotency_key: IdempotencyKey,
) -> dict:
    service = CleaningService(session)
    session_id = new_id("edit_session")

    async def action() -> dict[str, Any]:
        await service.repo.draft(ctx, draftId, lock=True)
        now = datetime.now(UTC)
        lease = models.CleaningEditSession(
            session_id=session_id,
            draft_id=draftId,
            principal_id=ctx.actor_id,
            client_instance_id=command.client_instance_id,
            lease_revision=1,
            etag=compute_etag(1),
            expires_at=now + timedelta(minutes=5),
        )
        session.add(lease)
        await session.flush()
        return envelope(
            {
                "session_id": session_id,
                "draft_id": draftId,
                "etag": lease.etag,
                "lease_revision": "1",
                "holder_summary": {"id": ctx.actor_id, "display_name": ctx.actor_id},
                "expires_at": _iso(lease.expires_at),
                "read_only": False,
            },
            ctx,
        )

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="acquireCleaningEditLease",
        request_value=command,
        target_type="cleaning_draft",
        target_id=draftId,
        events=("cleaning.draft.updated",),
        action=action,
    )


@router.post(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/edit-sessions/{sessionId}:renew",
    operation_id="renewCleaningEditLease",
)
async def renew_cleaning_edit_lease(
    draftId: str,
    sessionId: str,
    command: schemas.RenewLeaseRequest,
    request: Request,
    session: Session,
    ctx: EditCleaning,
) -> dict:
    await CleaningService(session).repo.draft(ctx, draftId)
    lease = await session.get(models.CleaningEditSession, sessionId, with_for_update=True)
    if lease is None or lease.draft_id != draftId:
        from app.core.errors import NotFoundError

        raise NotFoundError(code="EDIT_SESSION_NOT_FOUND")
    check_if_match(request, lease.etag)
    if lease.released_at:
        from app.core.errors import GoneError

        raise GoneError(code="EDIT_SESSION_RELEASED")
    lease.lease_revision += 1
    lease.etag = compute_etag(lease.lease_revision)
    lease.expires_at = datetime.now(UTC) + timedelta(minutes=5)
    await session.flush()
    return envelope(
        {
            "session_id": sessionId,
            "draft_id": draftId,
            "etag": lease.etag,
            "lease_revision": str(lease.lease_revision),
            "holder_summary": {"id": ctx.actor_id, "display_name": ctx.actor_id},
            "expires_at": _iso(lease.expires_at),
            "read_only": False,
        },
        ctx,
    )


@router.delete(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/edit-sessions/{sessionId}",
    operation_id="releaseCleaningEditLease",
    status_code=204,
)
async def release_cleaning_edit_lease(
    draftId: str, sessionId: str, request: Request, session: Session, ctx: EditCleaning
) -> Response:
    await CleaningService(session).repo.draft(ctx, draftId)
    lease = await session.get(models.CleaningEditSession, sessionId, with_for_update=True)
    if lease and not lease.released_at:
        check_if_match(request, lease.etag)
        lease.released_at = datetime.now(UTC)
        await session.flush()
    return Response(status_code=204)


@router.put("/regions/{regionCode}/cleaning-drafts/{draftId}/edl", operation_id="saveCleaningEdl")
async def save_cleaning_edl(
    draftId: str,
    command: schemas.SaveCleaningEdlRequest,
    request: Request,
    session: Session,
    ctx: EditCleaning,
    response: Response,
) -> dict:
    result = await CleaningService(session).save_edl(ctx, draftId, request, command)
    response.headers["ETag"] = result["data"]["draft"]["etag"]
    return result


@router.post(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/previews",
    operation_id="createCleaningPreview",
    status_code=202,
)
async def create_cleaning_preview(
    draftId: str,
    command: schemas.CreateCleaningPreviewRequest,
    request: Request,
    session: Session,
    ctx: PreviewCleaning,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await CleaningService(session).create_preview(
        ctx, idempotency_key, draftId, request, command
    )
    response.headers["Location"] = f"/api/v1/jobs/{result['job']['job_id']}"
    return result


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/previews/{previewId}",
    operation_id="getCleaningPreview",
)
async def get_cleaning_preview(
    draftId: str, previewId: str, session: Session, ctx: ReadCleaning
) -> dict:
    row = await CleaningService(session).repo.preview(draftId, previewId)
    return envelope(
        {
            "preview_id": row.preview_id,
            "status": row.status,
            "draft_id": row.draft_id,
            "base_revision_id": row.base_revision_id,
            "edl_revision": str(row.edl_revision),
            "operation_hash": row.operation_hash,
            "job_id": row.job_id,
            "source_to_output_map": row.source_to_output_map,
            "created_at": _iso(row.created_at),
            "expires_at": _iso(row.expires_at),
        },
        ctx,
    )


@router.post(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/commits",
    operation_id="commitCleaningDraft",
    status_code=202,
)
async def commit_cleaning_draft(
    draftId: str,
    command: schemas.CommitCleaningDraftRequest,
    request: Request,
    session: Session,
    ctx: SubmitCleaning,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await CleaningService(session).commit(ctx, idempotency_key, draftId, request, command)
    response.headers["Location"] = f"/api/v1/jobs/{result['job']['job_id']}"
    return result


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/commits/{commitId}",
    operation_id="getCleaningCommit",
)
async def get_cleaning_commit(
    draftId: str, commitId: str, session: Session, ctx: ReadCleaning
) -> dict:
    row = await CleaningService(session).repo.commit(draftId, commitId)
    return envelope(
        {
            "commit_id": row.commit_id,
            "status": row.status,
            "draft_id": row.draft_id,
            "preview_id": row.preview_id,
            "job_id": row.job_id,
            "created_at": _iso(row.created_at),
            "output_revisions": [],
            "output_version": None,
            "materialization_status": "NOT_STARTED",
            "successor_composition_hash": row.successor_composition_hash,
        },
        ctx,
    )


@router.get(
    "/regions/{regionCode}/cleaning-drafts/{draftId}/review-findings",
    operation_id="getCleaningReviewFindings",
)
async def get_cleaning_review_findings(draftId: str, session: Session, ctx: ReadReview) -> dict:
    await CleaningService(session).repo.draft(ctx, draftId)
    return envelope({"feedback": None}, ctx)
