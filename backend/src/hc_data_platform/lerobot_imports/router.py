from __future__ import annotations

import asyncio
import json
from tempfile import SpooledTemporaryFile
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, Field

from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.router import get_service as get_ingest_service
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .configuration import NativeConfiguration
from .models import (
    AuthorizeLeRobotPartsV1,
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
    LeRobotAssetCompletedV1,
    LeRobotImportAcceptedV1,
    LeRobotImportGrantV1,
    LeRobotPartGrantV1,
    LeRobotSourceFileV1,
    lerobot_part_plan,
)
from .processing import (
    NativeImportProgress,
    StartStoredProcessing,
    get_progress,
    list_progress,
    retry_processing,
    start_stored_processing,
)
from .resolutions import EpisodeResolution, ResolveEpisode, get_resolution, resolve_episode
from .service import LeRobotWebUploadService, read_bounded
from .source_browser import (
    OriginalEpisode,
    OriginalFileGrant,
    OriginalFilePage,
    OriginalSourceBrowser,
)

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports",
    tags=["lerobot-imports"],
)
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
_PART_MEMORY_SPOOL_BYTES = 8 * 1024**2
_MAX_PROXY_PART_BYTES = 600 * 1024**2


def get_service() -> LeRobotWebUploadService:
    from .configuration import validate_target

    ingest = get_ingest_service()
    return LeRobotWebUploadService(
        ingest.storage, raw_sources=ingest.raw_sources, target_validator=validate_target
    )


Service = Annotated[LeRobotWebUploadService, Depends(get_service)]


class NativeLabelsRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    labels: list[str] = Field(min_length=1, max_length=50)


@router.get("/{import_id}/episodes/{episode_index}/resolution")
def get_episode_resolution(
    project_id: str,
    region_code: str,
    import_id: str,
    episode_index: int,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> EpisodeResolution:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    return get_resolution(organization_id, project_id, region_code, import_id, episode_index)


@router.post("/{import_id}/episodes/{episode_index}/resolution")
def resolve_native_episode(
    project_id: str,
    region_code: str,
    import_id: str,
    episode_index: int,
    command: ResolveEpisode,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> EpisodeResolution:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    return resolve_episode(
        organization_id, project_id, region_code, import_id, episode_index, command, auth.subject_id
    )


@router.post("/{import_id}:process")
def process_stored_source(
    project_id: str,
    region_code: str,
    import_id: str,
    command: StartStoredProcessing,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> NativeImportProgress:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    raw = browser.source(organization_id, project_id, region_code, import_id)
    if raw.source_format.value != "LEROBOT_V3" or not raw.dataset_id:
        raise problem(
            status=422,
            code="RAW_PROCESSING_FORMAT_UNSUPPORTED",
            title="此原始格式需要处理适配器",
            detail="该入口处理 LeRobot；带清单的 MCAP 请使用采集包处理入口。",
        )
    stored = browser.manifest(raw)
    info = json.loads(
        read_bounded(service.storage, f"{raw.storage_prefix}/meta/info.json", 1024**2)
    )
    try:
        manifest = CreateLeRobotImportV1(
            dataset_id=raw.dataset_id,
            collection_task_id=command.collection_task_id,
            robot_id=command.robot_id,
            processing_mode="PROCESS",
            info=info,
            files=tuple(
                LeRobotSourceFileV1(
                    path=item["path"],
                    size=item["size"],
                    part_count=lerobot_part_plan(item["size"])[1],
                )
                for item in stored["files"]
            ),
        )
    except ValueError as exc:
        raise problem(
            status=422,
            code="LEROBOT_PROCESSING_PROFILE_UNSUPPORTED",
            title="数据与处理规则不匹配",
            detail=str(exc),
        ) from exc
    return start_stored_processing(organization_id, project_id, region_code, import_id, manifest)


@router.get("")
def list_native_imports(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    dataset_id: Annotated[str | None, Query(max_length=128)] = None,
    include_all_sources: bool = False,
) -> list[NativeImportProgress]:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    return list_progress(
        organization_id,
        project_id,
        region_code,
        limit=limit,
        offset=offset,
        dataset_id=dataset_id,
        include_all_sources=include_all_sources,
    )


@router.get("/{import_id}/files")
def list_original_files(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> OriginalFilePage:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    raw = browser.source(organization_id, project_id, region_code, import_id)
    return browser.files(raw, offset=offset, limit=limit)


@router.get("/{import_id}/assets:read")
def authorize_original_file(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    download: bool = False,
) -> OriginalFileGrant:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    return browser.grant(
        browser.source(organization_id, project_id, region_code, import_id), path, download=download
    )


@router.get("/{import_id}/episodes/{episode_index}")
def get_original_episode(
    project_id: str,
    region_code: str,
    import_id: str,
    episode_index: int,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> OriginalEpisode:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    return browser.episode(
        browser.source(organization_id, project_id, region_code, import_id),
        episode_index,
    )


@router.get("/{import_id}/processing")
def get_native_processing(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> NativeImportProgress:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    return get_progress(organization_id, project_id, region_code, import_id)


@router.post("/{import_id}:retry")
def retry_native_processing(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> NativeImportProgress:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    return retry_processing(organization_id, project_id, region_code, import_id)


@router.get("/configuration")
def get_native_configuration(
    project_id: str,
    region_code: str,
    dataset_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> NativeConfiguration:
    from .configuration import processing_configuration

    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    return processing_configuration(project_id, region_code, dataset_id)


@router.post("/configuration")
def configure_native_labels(
    project_id: str,
    region_code: str,
    command: NativeLabelsRequest,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> NativeConfiguration:
    from .configuration import configure_labels

    authorize_scope(auth, project_id, "data_schema.publish", region_code, organization_id)
    return configure_labels(
        auth, organization_id, project_id, region_code, command.dataset_id, command.labels
    )


@router.post("", response_model=LeRobotImportGrantV1, status_code=201)
def begin_lerobot_import(
    project_id: str,
    region_code: str,
    manifest: CreateLeRobotImportV1,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> LeRobotImportGrantV1:
    return service.begin(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        manifest=manifest,
    )


@router.post(
    "/{import_id}/assets:authorize-parts",
    response_model=LeRobotPartGrantV1,
)
def authorize_lerobot_asset_parts(
    project_id: str,
    region_code: str,
    import_id: str,
    command: AuthorizeLeRobotPartsV1,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> LeRobotPartGrantV1:
    return service.authorize_parts(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        import_id=import_id,
        command=command,
    )


@router.put("/{import_id}/assets:upload-part", status_code=200)
async def upload_lerobot_asset_part(
    project_id: str,
    region_code: str,
    import_id: str,
    request: Request,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    dataset_id: Annotated[str, Query(min_length=1, max_length=128)],
    path: Annotated[str, Query(min_length=1, max_length=1024)],
    multipart_upload_id: Annotated[str, Query(min_length=1, max_length=2048)],
    part_number: Annotated[int, Query(ge=1, le=10_000)],
) -> Response:
    declared_length = request.headers.get("content-length")
    if declared_length is not None:
        try:
            parsed_length = int(declared_length)
        except ValueError as exc:
            raise problem(
                status=400,
                code="LEROBOT_PART_LENGTH_INVALID",
                title="LeRobot part length is invalid",
                detail="Content-Length must be a non-negative integer.",
            ) from exc
        if not 0 <= parsed_length <= _MAX_PROXY_PART_BYTES:
            raise problem(
                status=413,
                code="LEROBOT_PART_TOO_LARGE",
                title="LeRobot part is too large",
                detail="The proxied multipart body exceeds the platform transfer limit.",
            )

    received = 0
    with SpooledTemporaryFile(max_size=_PART_MEMORY_SPOOL_BYTES, mode="w+b") as body:
        async for chunk in request.stream():
            received += len(chunk)
            if received > _MAX_PROXY_PART_BYTES:
                raise problem(
                    status=413,
                    code="LEROBOT_PART_TOO_LARGE",
                    title="LeRobot part is too large",
                    detail="The proxied multipart body exceeds the platform transfer limit.",
                )
            await asyncio.to_thread(body.write, chunk)
        if declared_length is not None and received != int(declared_length):
            raise problem(
                status=400,
                code="LEROBOT_PART_LENGTH_MISMATCH",
                title="LeRobot part length does not match",
                detail="The received multipart body differs from Content-Length.",
            )
        body.seek(0)
        uploaded = await asyncio.to_thread(
            service.upload_part,
            auth=auth,
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            import_id=import_id,
            dataset_id=dataset_id,
            path=path,
            multipart_upload_id=multipart_upload_id,
            part_number=part_number,
            body=cast(BinaryIO, body),
            size=received,
        )
    return Response(
        status_code=200,
        headers={"ETag": uploaded.etag, "Cache-Control": "no-store"},
    )


@router.post(
    "/{import_id}/assets:complete",
    response_model=LeRobotAssetCompletedV1,
)
def complete_lerobot_asset(
    project_id: str,
    region_code: str,
    import_id: str,
    command: CompleteLeRobotAssetV1,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> LeRobotAssetCompletedV1:
    return service.complete_asset(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        import_id=import_id,
        command=command,
    )


@router.post("/{import_id}:commit", response_model=LeRobotImportAcceptedV1)
def commit_lerobot_import(
    project_id: str,
    region_code: str,
    import_id: str,
    command: CommitLeRobotImportV1,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> LeRobotImportAcceptedV1:
    return service.commit(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        import_id=import_id,
        command=command,
    )
