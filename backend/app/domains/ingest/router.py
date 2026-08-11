from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.context import RequestContext, require
from app.core.db import get_session
from app.core.pagination import CursorParams, cursor_params
from app.domains.ingest import schemas
from app.domains.ingest.service import IngestService


async def require_contract_headers(
    projectId: str,
    regionCode: str,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    client_version: Annotated[str, Header(alias="X-Client-Version")],
    accept_language: Annotated[str, Header(alias="Accept-Language")],
) -> None:
    del projectId, regionCode, organization_id, client_version, accept_language


router = APIRouter(
    prefix="/api/v1/projects/{projectId}/regions/{regionCode}",
    dependencies=[Depends(require_contract_headers)],
)

Session = Annotated[AsyncSession, Depends(get_session)]
ReadSource = Annotated[RequestContext, Depends(require("ingest_source.read"))]
ManageSource = Annotated[RequestContext, Depends(require("ingest_source.manage"))]
ReadUpload = Annotated[RequestContext, Depends(require("upload.read"))]
ManageUpload = Annotated[RequestContext, Depends(require("upload.manage"))]
Page = Annotated[CursorParams, Depends(cursor_params)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


async def source_cursor_params(
    limit: Annotated[Literal[10, 20, 50], Query()] = 20,
    after: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
    before: Annotated[str | None, Query(min_length=1, max_length=2048)] = None,
) -> CursorParams:
    return CursorParams(limit=limit, after=after, before=before)


SourcePage = Annotated[CursorParams, Depends(source_cursor_params)]


@router.get("/data-sources/page", operation_id="getDataSourcesPage", tags=["P02 DataSource"])
async def get_data_sources_page(session: Session, ctx: ReadSource, page: SourcePage) -> dict:
    return await IngestService(session).source_page(ctx, page)


@router.get("/data-sources", operation_id="listDataSources", tags=["P02 DataSource"])
async def list_data_sources(session: Session, ctx: ReadSource, page: SourcePage) -> dict:
    return await IngestService(session).list_sources(ctx, page)


@router.post(
    "/data-sources",
    operation_id="createDataSource",
    tags=["P02 DataSource"],
    status_code=status.HTTP_201_CREATED,
)
async def create_data_source(
    command: schemas.CreateDataSourceCommand,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).create_source(ctx, idempotency_key, command)
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.get(
    "/data-sources/{sourceId}",
    operation_id="getDataSource",
    tags=["P02 DataSource"],
    response_model=None,
)
async def get_data_source(
    sourceId: str,
    session: Session,
    ctx: ReadSource,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict | Response:
    row = await IngestService(session).get_source(ctx, sourceId)
    response.headers["ETag"] = row.etag
    if if_none_match and row.etag in {item.strip() for item in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": row.etag})
    from app.domains.ingest.service import envelope

    return envelope(row.projection, ctx)


@router.patch("/data-sources/{sourceId}", operation_id="updateDataSource", tags=["P02 DataSource"])
async def update_data_source(
    sourceId: str,
    command: schemas.UpdateDataSourceCommand,
    request: Request,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).update_source(
        ctx, idempotency_key, request, sourceId, command
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/data-sources/{sourceId}:rotate-credential",
    operation_id="rotateDataSourceCredential",
    tags=["P02 DataSource"],
)
async def rotate_data_source_credential(
    sourceId: str,
    command: schemas.RotateCredentialCommand,
    request: Request,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).rotate_credential(
        ctx, idempotency_key, request, sourceId, command
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/data-sources/{sourceId}:test-connection",
    operation_id="testDataSourceConnection",
    tags=["P02 DataSource"],
    status_code=status.HTTP_202_ACCEPTED,
)
async def test_data_source_connection(
    sourceId: str,
    command: schemas.TestConnectionCommand,
    request: Request,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).test_connection(
        ctx, idempotency_key, request, sourceId, command
    )
    job = result["job"]
    response.headers.update(
        {
            "ETag": job["etag"],
            "Location": f"/api/v1/jobs/{job['job_id']}",
            "Cache-Control": "no-store",
        }
    )
    return result


@router.post(
    "/data-sources/{sourceId}:enable", operation_id="enableDataSource", tags=["P02 DataSource"]
)
async def enable_data_source(
    sourceId: str,
    command: schemas.SourceStateCommand,
    request: Request,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).set_source_state(
        ctx, idempotency_key, request, sourceId, command, "ENABLED"
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/data-sources/{sourceId}:disable", operation_id="disableDataSource", tags=["P02 DataSource"]
)
async def disable_data_source(
    sourceId: str,
    command: schemas.SourceStateCommand,
    request: Request,
    session: Session,
    ctx: ManageSource,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).set_source_state(
        ctx, idempotency_key, request, sourceId, command, "DISABLED"
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.get("/upload-sessions", operation_id="listUploadSessions", tags=["P03 UploadSession"])
async def list_upload_sessions(session: Session, ctx: ReadUpload, page: Page) -> dict:
    return await IngestService(session).list_uploads(ctx, page)


@router.post(
    "/upload-sessions",
    operation_id="createUploadSession",
    tags=["P03 UploadSession"],
    status_code=status.HTTP_201_CREATED,
)
async def create_upload_session(
    command: schemas.CreateUploadSessionCommand,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).create_upload(ctx, idempotency_key, command)
    response.headers.update(
        {"ETag": result["data"]["upload_session"]["etag"], "Cache-Control": "private, no-store"}
    )
    return result


@router.get(
    "/upload-sessions:summary", operation_id="summarizeUploadSessions", tags=["P03 UploadSession"]
)
async def summarize_upload_sessions(session: Session, ctx: ReadUpload) -> dict:
    return await IngestService(session).summarize_uploads(ctx)


@router.get(
    "/upload-sessions:creation-options",
    operation_id="getUploadCreationOptions",
    tags=["P03 UploadSession"],
)
async def get_upload_creation_options(session: Session, ctx: ManageUpload) -> dict:
    return await IngestService(session).creation_options(ctx)


@router.get(
    "/upload-sessions/{uploadId}/bootstrap",
    operation_id="getUploadSessionBootstrap",
    tags=["P03 UploadSession", "P04 UploadDetail"],
    response_model=None,
)
async def get_upload_session_bootstrap(
    uploadId: str,
    session: Session,
    ctx: ReadUpload,
    response: Response,
    if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
) -> dict | Response:
    result = await IngestService(session).bootstrap(ctx, uploadId)
    etag = result["data"]["session"]["etag"]
    response.headers["ETag"] = etag
    if if_none_match and etag in {item.strip() for item in if_none_match.split(",")}:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers={"ETag": etag})
    return result


@router.post(
    "/upload-sessions/{uploadId}:renew-upload-authorization",
    operation_id="renewUploadAuthorization",
    tags=["P03 UploadSession"],
)
async def renew_upload_authorization(
    uploadId: str,
    command: schemas.RenewUploadAuthorizationCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).renew_authorization(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {"ETag": result["data"]["upload_session"]["etag"], "Cache-Control": "private, no-store"}
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:pause",
    operation_id="pauseUploadSession",
    tags=["P03 UploadSession", "P04 UploadDetail"],
)
async def pause_upload_session(
    uploadId: str,
    command: schemas.UploadStateCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).pause_upload(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers["ETag"] = result["data"]["etag"]
    return result


@router.post(
    "/upload-sessions/{uploadId}:resume",
    operation_id="resumeUploadSession",
    tags=["P03 UploadSession", "P04 UploadDetail"],
)
async def resume_upload_session(
    uploadId: str,
    command: schemas.ResumeUploadCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).resume_upload(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {"ETag": result["data"]["upload_session"]["etag"], "Cache-Control": "private, no-store"}
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:retry-failed-parts",
    operation_id="retryUploadParts",
    tags=["P03 UploadSession", "P04 UploadDetail"],
)
async def retry_upload_parts(
    uploadId: str,
    command: schemas.RetryUploadPartsCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).retry_parts(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {"ETag": result["data"]["session"]["etag"], "Cache-Control": "private, no-store"}
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:submit-manifest",
    operation_id="submitUploadManifest",
    tags=["P03 UploadSession", "P04 UploadDetail"],
    status_code=status.HTTP_202_ACCEPTED,
)
async def submit_upload_manifest(
    uploadId: str,
    command: schemas.SubmitUploadManifestCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).submit_manifest(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {
            "ETag": result["job"]["etag"],
            "Location": f"/api/v1/jobs/{result['job']['job_id']}",
            "Cache-Control": "no-store",
        }
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:retry-verification",
    operation_id="retryUploadVerification",
    tags=["P03 UploadSession", "P04 UploadDetail"],
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_upload_verification(
    uploadId: str,
    command: schemas.RetryVerificationCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).retry_verification(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {
            "ETag": result["job"]["etag"],
            "Location": f"/api/v1/jobs/{result['job']['job_id']}",
            "Cache-Control": "no-store",
        }
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:create-replacement",
    operation_id="createReplacementUpload",
    tags=["P03 UploadSession", "P04 UploadDetail"],
    status_code=status.HTTP_201_CREATED,
)
async def create_replacement_upload(
    uploadId: str,
    command: schemas.CreateReplacementUploadCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).create_replacement(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {"ETag": result["data"]["upload_session"]["etag"], "Cache-Control": "private, no-store"}
    )
    return result


@router.post(
    "/upload-sessions/{uploadId}:cancel",
    operation_id="cancelUploadSession",
    tags=["P03 UploadSession", "P04 UploadDetail"],
    status_code=status.HTTP_202_ACCEPTED,
)
async def cancel_upload_session(
    uploadId: str,
    command: schemas.CancelUploadCommand,
    request: Request,
    session: Session,
    ctx: ManageUpload,
    idempotency_key: IdempotencyKey,
    response: Response,
) -> dict:
    result = await IngestService(session).cancel_upload(
        ctx, idempotency_key, request, uploadId, command
    )
    response.headers.update(
        {
            "ETag": result["job"]["etag"],
            "Location": f"/api/v1/jobs/{result['job']['job_id']}",
            "Cache-Control": "no-store",
        }
    )
    return result


@router.get(
    "/upload-sessions/{uploadId}/objects",
    operation_id="listUploadObjects",
    tags=["P04 UploadDetail"],
)
async def list_upload_objects(uploadId: str, session: Session, ctx: ReadUpload, page: Page) -> dict:
    return await IngestService(session).list_objects(ctx, uploadId, page)


@router.get(
    "/upload-sessions/{uploadId}/objects/{objectId}/parts",
    operation_id="listUploadParts",
    tags=["P04 UploadDetail"],
)
async def list_upload_parts(
    uploadId: str, objectId: str, session: Session, ctx: ReadUpload, page: Page
) -> dict:
    return await IngestService(session).list_parts(ctx, uploadId, objectId, page)


@router.get(
    "/upload-sessions/{uploadId}/source-manifest",
    operation_id="getSourceUploadManifest",
    tags=["P04 UploadDetail"],
)
async def get_source_upload_manifest(uploadId: str, session: Session, ctx: ReadUpload) -> dict:
    return await IngestService(session).source_manifest(ctx, uploadId)


@router.get(
    "/upload-sessions/{uploadId}/source-manifest/nodes",
    operation_id="listSourceManifestNodes",
    tags=["P04 UploadDetail"],
)
async def list_source_manifest_nodes(
    uploadId: str,
    session: Session,
    ctx: ReadUpload,
    page: Page,
    parent_node_id: Annotated[str | None, Query(alias="parentNodeId")] = None,
    path_prefix: Annotated[str | None, Query(alias="pathPrefix")] = None,
) -> dict:
    return await IngestService(session).list_manifest_nodes(
        ctx, uploadId, page, parent_node_id, path_prefix
    )


@router.get(
    "/upload-sessions/{uploadId}/verification-runs",
    operation_id="listVerificationRuns",
    tags=["P04 UploadDetail"],
)
async def list_verification_runs(
    uploadId: str, session: Session, ctx: ReadUpload, page: Page
) -> dict:
    return await IngestService(session).list_runs(ctx, uploadId, page)


@router.get(
    "/upload-sessions/{uploadId}/verification-runs/{runId}/findings",
    operation_id="listVerificationFindings",
    tags=["P04 UploadDetail"],
)
async def list_verification_findings(
    uploadId: str,
    runId: str,
    session: Session,
    ctx: ReadUpload,
    page: Page,
    severity: str | None = None,
    stage_filter: Annotated[str | None, Query(alias="stage")] = None,
    code: str | None = None,
) -> dict:
    return await IngestService(session).list_findings(
        ctx, uploadId, runId, page, severity, stage_filter, code
    )


@router.get(
    "/upload-sessions/{uploadId}/events",
    operation_id="listUploadEvents",
    tags=["P04 UploadDetail"],
)
async def list_upload_events(
    uploadId: str,
    session: Session,
    ctx: ReadUpload,
    page: Page,
    event_level: Annotated[str | None, Query(alias="eventLevel")] = None,
) -> dict:
    return await IngestService(session).list_events(ctx, uploadId, page, event_level)
