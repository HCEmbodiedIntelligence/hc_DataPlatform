"""HTTP surface for the 28 dataset/version/review OpenAPI operations."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import write_audit
from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.idempotency import IdempotencyRecord
from app.core.ids import new_id
from app.core.outbox import emit_event
from app.core.pagination import CursorParams

from . import models, schemas
from .repository import scope_key
from .service import CONTRACT_VERSION, DatasetService, envelope


async def require_dataset_headers(
    projectId: str,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    project_header: Annotated[str, Header(alias="X-Project-Id")],
    region_code: Annotated[str, Header(alias="X-Region-Code")],
    client_version: Annotated[str, Header(alias="X-Client-Version")],
    accept: Annotated[str, Header(alias="Accept")],
) -> None:
    del organization_id, region_code, client_version, accept
    if project_header != projectId:
        from app.core.errors import ForbiddenError

        raise ForbiddenError(code="SCOPE_MISMATCH")


router = APIRouter(
    prefix="/api/v1/projects/{projectId}", dependencies=[Depends(require_dataset_headers)]
)
Session = Annotated[AsyncSession, Depends(get_session)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
ReadDataset = Annotated[RequestContext, Depends(require("dataset.read"))]
CreateDataset = Annotated[RequestContext, Depends(require("dataset.create"))]
ReadVersion = Annotated[RequestContext, Depends(require("dataset_version.read"))]
PublishVersion = Annotated[RequestContext, Depends(require("dataset_version.publish"))]
DownloadManifest = Annotated[RequestContext, Depends(require("dataset_version.download_manifest"))]
ReviewVersion = Annotated[RequestContext, Depends(require("dataset_version.review"))]
ReadEpisode = Annotated[RequestContext, Depends(require("episode.read"))]
ReadSchema = Annotated[RequestContext, Depends(require("data_schema.read"))]
ReadCapacity = Annotated[RequestContext, Depends(require("storage.overview.read"))]


async def dataset_page(
    limit: Annotated[Literal[10, 20, 50], Query()] = 20,
    after: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    before: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, after=after, before=before)


Page = Annotated[CursorParams, Depends(dataset_page)]


@router.get("/datasets:page-capabilities", operation_id="getDatasetsPageCapabilities")
async def get_datasets_page_capabilities(ctx: ReadDataset) -> dict:
    return envelope(
        {
            "scope": {
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            "authorization_revision": "1",
            "allowed_actions": ["CREATE_DATASET"] if "dataset.create" in ctx.capabilities else [],
            "blocked_reasons": [],
        },
        ctx,
    )


@router.get("/datasets:facets", operation_id="getDatasetFacets")
async def get_dataset_facets(ctx: ReadDataset) -> dict:
    return envelope(
        {
            "scope": {
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            "normalized_filters": {},
            "robots": [],
            "robot_models": [],
            "tasks": [],
            "scenes": [],
            "asset_states": [],
            "storage_classes": [],
            "channels": [],
        },
        ctx,
    )


@router.get("/datasets:summary", operation_id="summarizeDatasets")
async def summarize_datasets(session: Session, ctx: ReadDataset) -> dict:
    count = await session.scalar(
        select(__import__("sqlalchemy").func.count())
        .select_from(models.Dataset)
        .where(models.Dataset.scope_key == scope_key(ctx))
    )
    return envelope(
        {
            "scope": {
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            "dataset_count": str(count or 0),
            "episode_count": "0",
            "pending_review_version_count": "0",
            "returned_version_count": "0",
            "actionable_draft_count": "0",
            "normalized_filters": {},
        },
        ctx,
    )


@router.get("/datasets", operation_id="listDatasets")
async def list_datasets(session: Session, ctx: ReadDataset, page: Page) -> dict:
    return await DatasetService(session).list_datasets(ctx, page)


@router.post("/datasets", operation_id="createDataset", status_code=status.HTTP_201_CREATED)
async def create_dataset(
    command: schemas.CreateDatasetRequest,
    session: Session,
    ctx: CreateDataset,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await DatasetService(session).create_dataset(ctx, idempotency_key, command)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/dataset-list-exports",
    operation_id="createDatasetManifestListExport",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_dataset_manifest_list_export(
    command: schemas.CreateDatasetManifestListExportRequest,
    session: Session,
    ctx: ReadDataset,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    service = DatasetService(session)
    job_id = new_id("job")

    async def action() -> dict[str, Any]:
        return {
            "job": {
                "job_id": job_id,
                "kind": "DATASET_MANIFEST_LIST_EXPORT",
                "status": "QUEUED",
                "etag": '"rv-1"',
            },
            "scope": {
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            "request_id": ctx.request_id,
            "contract_version": CONTRACT_VERSION,
        }

    result = await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="createDatasetManifestListExport",
        request_value=command,
        target_type="export_job",
        target_id=job_id,
        events=("export.job.created",),
        action=action,
    )
    response.headers["Location"] = f"/api/v1/jobs/{job_id}"
    return result


def _find_job_projection(value: Any, job_id: str) -> dict[str, Any] | None:
    if isinstance(value, dict):
        if value.get("job_id") == job_id:
            return value
        for nested in value.values():
            found = _find_job_projection(nested, job_id)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_job_projection(nested, job_id)
            if found is not None:
                return found
    return None


@router.post(
    "/export-jobs/{jobId}/download-authorizations",
    operation_id="authorizeExportDownload",
)
async def authorize_export_download(
    jobId: str,
    session: Session,
    ctx: ReadDataset,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict[str, Any]:
    records = list(
        (
            await session.scalars(
                select(IdempotencyRecord).where(
                    IdempotencyRecord.scope.contains(f'"project_id":"{ctx.project_id}"')
                )
            )
        ).all()
    )
    job = next(
        (
            found
            for record in records
            if (
                found := _find_job_projection(
                    record.response_body_or_resource_ref,
                    jobId,
                )
            )
            is not None
        ),
        None,
    )
    if job is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(code="EXPORT_JOB_NOT_FOUND")
    if job.get("status") != "SUCCEEDED":
        from app.core.errors import VersionConflictError

        raise VersionConflictError(code="EXPORT_RESULT_NOT_READY")

    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return envelope(
            {
                "grant_id": new_id("event"),
                "export_job_id": jobId,
                "expires_at": "2099-01-01T00:00:00Z",
                "transport": "STREAM",
                "allowed_actions": ["DOWNLOAD"],
                "blocked_reasons": [],
            },
            ctx,
        )

    result = await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="authorizeExportDownload",
        request_value={"job_id": jobId},
        target_type="export_job",
        target_id=jobId,
        events=("export.result.downloaded",),
        action=action,
    )
    response.headers["Cache-Control"] = "private, no-store"
    return result


@router.get(
    "/datasets/{datasetId}/bootstrap", operation_id="getDatasetBootstrap", response_model=None
)
async def get_dataset_bootstrap(
    datasetId: str,
    session: Session,
    ctx: ReadDataset,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict | Response:
    result = await DatasetService(session).dataset_bootstrap(ctx, datasetId)
    etag = result["data"]["dataset"]["etag"]
    response.headers["ETag"] = etag
    if if_none_match and etag in {part.strip() for part in if_none_match.split(",")}:
        return Response(status_code=304, headers={"ETag": etag})
    return result


@router.get("/datasets/{datasetId}/versions", operation_id="listDatasetVersions")
async def list_dataset_versions(
    datasetId: str, session: Session, ctx: ReadVersion, page: Page
) -> dict:
    return await DatasetService(session).list_versions(ctx, datasetId, page)


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/episodes", operation_id="listVersionEpisodes"
)
async def list_version_episodes(
    datasetId: str, versionId: str, session: Session, ctx: ReadEpisode, page: Page
) -> dict:
    service = DatasetService(session)
    await service.repo.version(ctx, datasetId, versionId)
    rows = list(
        (
            await session.scalars(
                select(models.DatasetVersionRevision)
                .where(models.DatasetVersionRevision.version_id == versionId)
                .order_by(
                    models.DatasetVersionRevision.ordinal, models.DatasetVersionRevision.episode_id
                )
                .limit(page.limit + 1)
            )
        ).all()
    )
    result = __import__("app.core.pagination", fromlist=["build_page"]).build_page(
        rows, page, ("ordinal", "episode_id")
    )
    result["items"] = [
        {
            "scope": {
                "organization_id": ctx.organization_id,
                "project_id": ctx.project_id,
                "region_code": ctx.region_code,
            },
            "dataset_id": datasetId,
            "version_id": versionId,
            "episode_id": row.episode_id,
            "selected_revision": {"revision_id": row.revision_id, "ordinal": row.ordinal},
            "included": True,
            "success_state": "UNKNOWN",
            "task": None,
            "robot_id": None,
        }
        for row in result["items"]
    ]
    result.update(
        snapshot_id=new_id("snapshot"),
        scope={
            "organization_id": ctx.organization_id,
            "project_id": ctx.project_id,
            "region_code": ctx.region_code,
        },
        request_id=ctx.request_id,
        contract_version=CONTRACT_VERSION,
    )
    await write_audit(
        session,
        "episode.viewed",
        "dataset_version",
        versionId,
        "SUCCEEDED",
        ctx,
        {"operation_id": "listVersionEpisodes"},
    )
    await emit_event(
        session,
        "episode.viewed",
        "dataset_version",
        versionId,
        {"operation_id": "listVersionEpisodes"},
        ctx,
    )
    return result


async def _generic_version(
    session: AsyncSession, ctx: RequestContext, dataset_id: str, version_id: str, kind: str
) -> dict:
    return await DatasetService(session).generic_version_read(ctx, dataset_id, version_id, kind)


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/schema-summary",
    operation_id="getDatasetVersionSchemaSummary",
)
async def get_dataset_version_schema_summary(
    datasetId: str, versionId: str, session: Session, ctx: ReadSchema
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "schema-summary")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/source-provenance",
    operation_id="listDatasetVersionSourceProvenance",
)
async def list_dataset_version_source_provenance(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "source-provenance")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/capacity-facts",
    operation_id="getDatasetVersionCapacityFacts",
)
async def get_dataset_version_capacity_facts(
    datasetId: str, versionId: str, session: Session, ctx: ReadCapacity
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "capacity-facts")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/bootstrap",
    operation_id="getVersionBootstrap",
    response_model=None,
)
async def get_version_bootstrap(
    datasetId: str,
    versionId: str,
    session: Session,
    ctx: ReadVersion,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict | Response:
    row = await DatasetService(session).repo.version(ctx, datasetId, versionId)
    response.headers["ETag"] = row.etag
    if if_none_match and row.etag in {part.strip() for part in if_none_match.split(",")}:
        return Response(status_code=304, headers={"ETag": row.etag})
    return await _generic_version(session, ctx, datasetId, versionId, "bootstrap")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/episode-revisions/{revisionId}",
    operation_id="getVersionRevision",
)
async def get_version_revision(
    datasetId: str, versionId: str, revisionId: str, session: Session, ctx: ReadVersion
) -> dict:
    await DatasetService(session).repo.version(ctx, datasetId, versionId)
    row = await session.get(models.EpisodeRevision, revisionId)
    if row is None:
        from app.core.errors import NotFoundError

        raise NotFoundError(code="REVISION_NOT_FOUND")
    return envelope(
        {
            "dataset_id": datasetId,
            "version_id": versionId,
            "episode_id": row.episode_id,
            "revision_id": row.revision_id,
            "content_sha256": row.content_sha256,
            "started_at_ns": str(row.start_ns),
            "duration_ns": str(row.end_ns - row.start_ns),
            "streams": row.streams,
        },
        ctx,
    )


@router.get("/datasets/{datasetId}/versions/{versionId}/diff", operation_id="getVersionDiff")
async def get_version_diff(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "diff")


@router.post(
    "/datasets/{datasetId}/versions/{versionId}/diff-jobs",
    operation_id="createVersionDiffJob",
    status_code=202,
)
async def create_version_diff_job(
    datasetId: str,
    versionId: str,
    command: schemas.DiffJobRequest,
    session: Session,
    ctx: ReadVersion,
    idempotency_key: IdempotencyKey,
) -> dict:
    await DatasetService(session).repo.version(ctx, datasetId, versionId)
    job_id = new_id("job")
    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return {
            "job": {"job_id": job_id, "kind": "VERSION_DIFF", "status": "QUEUED", "etag": '"rv-1"'}
        }

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="createVersionDiffJob",
        request_value=command,
        target_type="dataset_version",
        target_id=versionId,
        events=("dataset.updated",),
        action=action,
    )


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/manifest", operation_id="getVersionManifest"
)
async def get_version_manifest(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    result = await _generic_version(session, ctx, datasetId, versionId, "manifest")
    await write_audit(
        session,
        "dataset_version.manifest.viewed",
        "dataset_version",
        versionId,
        "SUCCEEDED",
        ctx,
        {"operation_id": "getVersionManifest"},
    )
    await emit_event(
        session,
        "dataset_version.manifest.viewed",
        "dataset_version",
        versionId,
        {"operation_id": "getVersionManifest"},
        ctx,
    )
    return result


@router.post(
    "/datasets/{datasetId}/versions/{versionId}/manifest-jobs",
    operation_id="createManifestJob",
    status_code=202,
)
async def create_manifest_job(
    datasetId: str,
    versionId: str,
    command: schemas.ManifestJobRequest,
    request: Request,
    session: Session,
    ctx: PublishVersion,
    idempotency_key: IdempotencyKey,
) -> dict:
    row = await DatasetService(session).repo.version(ctx, datasetId, versionId, lock=True)
    from app.core.etag import check_if_match

    check_if_match(request, row.etag)
    job_id = new_id("job")
    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return {
            "job": {
                "job_id": job_id,
                "kind": "VERSION_MANIFEST",
                "status": "QUEUED",
                "etag": '"rv-1"',
            }
        }

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="createManifestJob",
        request_value=command,
        target_type="dataset_version",
        target_id=versionId,
        events=("dataset_version.raw.registered",),
        action=action,
    )


@router.post(
    "/datasets/{datasetId}/versions/{versionId}/manifest-download-authorizations",
    operation_id="authorizeManifestDownload",
)
async def authorize_manifest_download(
    datasetId: str,
    versionId: str,
    command: schemas.DownloadAuthorizationRequest,
    session: Session,
    ctx: DownloadManifest,
    idempotency_key: IdempotencyKey,
) -> dict:
    await DatasetService(session).repo.version(ctx, datasetId, versionId)
    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return envelope(
            {
                "authorization_id": new_id("authorization"),
                "purpose": command.purpose,
                "expires_at": "2099-01-01T00:00:00Z",
                "url": "about:blank",
            },
            ctx,
        )

    events = (
        (
            "dataset_version.manifest_download.requested",
            "dataset_version.manifest_download.completed",
        )
        if command.purpose == "VIEW"
        else ("dataset_version.raw_download.requested", "dataset_version.raw_download.completed")
    )
    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="authorizeManifestDownload",
        request_value=command,
        target_type="dataset_version",
        target_id=versionId,
        events=events,
        action=action,
    )


@router.get("/datasets/{datasetId}/versions/{versionId}/schema", operation_id="getVersionSchema")
async def get_version_schema(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "schema")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/required-storage",
    operation_id="listRequiredStorage",
)
async def list_required_storage(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "required-storage")


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/operational-inventory",
    operation_id="listOperationalInventory",
)
async def list_operational_inventory(
    datasetId: str, versionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await _generic_version(session, ctx, datasetId, versionId, "operational-inventory")


@router.post(
    "/datasets/{datasetId}/versions/{versionId}/review-checks",
    operation_id="runVersionReviewChecks",
)
async def run_version_review_checks(
    datasetId: str,
    versionId: str,
    command: schemas.ReviewChecksRequest,
    request: Request,
    session: Session,
    ctx: ReviewVersion,
) -> dict:
    del command
    return await DatasetService(session).review_checks(ctx, datasetId, versionId, request)


@router.post(
    "/datasets/{datasetId}/versions/{versionId}:approve",
    operation_id="approveVersionReview",
    status_code=202,
)
async def approve_version_review(
    datasetId: str,
    versionId: str,
    command: schemas.ApproveReviewCommand,
    request: Request,
    session: Session,
    ctx: ReviewVersion,
    idempotency_key: IdempotencyKey,
) -> dict:
    return await DatasetService(session).approve(
        ctx, idempotency_key, datasetId, versionId, request, command
    )


@router.post(
    "/datasets/{datasetId}/versions/{versionId}:return", operation_id="returnVersionReview"
)
async def return_version_review(
    datasetId: str,
    versionId: str,
    command: schemas.ReturnReviewCommand,
    request: Request,
    session: Session,
    ctx: ReviewVersion,
    idempotency_key: IdempotencyKey,
) -> dict:
    return await DatasetService(session).return_review(
        ctx, idempotency_key, datasetId, versionId, request, command
    )


@router.get(
    "/datasets/{datasetId}/versions/{versionId}/review-decisions/{reviewDecisionId}",
    operation_id="getVersionReviewDecision",
)
async def get_version_review_decision(
    datasetId: str, versionId: str, reviewDecisionId: str, session: Session, ctx: ReadVersion
) -> dict:
    return await DatasetService(session).decision_detail(
        ctx, datasetId, versionId, reviewDecisionId
    )


@router.post("/datasets/{datasetId}/deletion-checks", operation_id="preflightDatasetDeletion")
async def preflight_dataset_deletion(
    datasetId: str,
    command: schemas.DeletionPreflightRequest,
    session: Session,
    ctx: ReadDataset,
    idempotency_key: IdempotencyKey,
) -> dict:
    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return await service.deletion_preflight(ctx, datasetId, None, command)

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="preflightDatasetDeletion",
        request_value=command,
        target_type="dataset",
        target_id=datasetId,
        events=("dataset.deletion.requested", "dataset.deletion.completed"),
        action=action,
    )


@router.post(
    "/datasets/{datasetId}/versions/{versionId}/deletion-checks",
    operation_id="preflightVersionDeletion",
)
async def preflight_version_deletion(
    datasetId: str,
    versionId: str,
    command: schemas.DeletionPreflightRequest,
    session: Session,
    ctx: ReadVersion,
    idempotency_key: IdempotencyKey,
) -> dict:
    service = DatasetService(session)

    async def action() -> dict[str, Any]:
        return await service.deletion_preflight(ctx, datasetId, versionId, command)

    return await service._write(
        ctx=ctx,
        key=idempotency_key,
        operation_id="preflightVersionDeletion",
        request_value=command,
        target_type="dataset_version",
        target_id=versionId,
        events=("dataset_version.deletion.requested", "dataset_version.deletion.completed"),
        action=action,
    )
