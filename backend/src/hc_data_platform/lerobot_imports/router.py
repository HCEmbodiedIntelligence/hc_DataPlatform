from __future__ import annotations

import asyncio
import json
from tempfile import SpooledTemporaryFile
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .capabilities import LeRobotCapabilities, LeRobotCapabilityRequest, detect_capabilities
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
from .robot_eligibility import ImportRobotOption, import_robots
from .service import LeRobotWebUploadService, read_bounded
from .source_archive import OriginalArchiveRequest, archive_files, stream_archive
from .source_browser import (
    OriginalEpisode,
    OriginalFileGrant,
    OriginalFilePage,
    OriginalSourceBrowser,
)
from .source_directory import OriginalDirectory, OriginalTextPreview, directory, text_preview

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports",
    tags=["lerobot-imports"],
)
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
_PART_MEMORY_SPOOL_BYTES = 8 * 1024**2
_MAX_PROXY_PART_BYTES = 600 * 1024**2


def get_service(request: Request) -> LeRobotWebUploadService:
    service = getattr(request.app.state, "lerobot_uploads", None)
    if service is None:
        raise problem(
            status=503,
            code="NATIVE_UPLOAD_STORE_UNAVAILABLE",
            title="原始文件上传服务未配置",
            detail="当前应用没有装配原始文件上传服务。",
        )
    return cast(LeRobotWebUploadService, service)


Service = Annotated[LeRobotWebUploadService, Depends(get_service)]


class NativeLabelsRequest(BaseModel):
    dataset_id: str = Field(min_length=1, max_length=128)
    labels: list[str] = Field(min_length=1, max_length=50)


@router.get(
    "/robot-options", response_model=list[ImportRobotOption], operation_id="listImportRobots"
)
def list_import_robots(
    project_id: str,
    region_code: str,
    organization_id: OrganizationHeader,
    auth: VerifiedAuth,
) -> list[ImportRobotOption]:
    from .configuration import connections

    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    with connections()() as db:
        return import_robots(db, organization_id, project_id, region_code)


@router.post(":capabilities", operation_id="detectLeRobotImportCapabilities")
def get_import_capabilities(
    project_id: str,
    region_code: str,
    command: LeRobotCapabilityRequest,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    response: Response,
) -> LeRobotCapabilities:
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    response.headers["Cache-Control"] = "no-store"
    return detect_capabilities(command.info)


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


@router.get("/robots/{robot_id}/mcap-profile")
def get_mcap_profile(
    project_id: str,
    region_code: str,
    robot_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> McapProcessingConfig:
    from .mcap_processing import profile

    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    return profile((organization_id, project_id, region_code), robot_id)


@router.put("/robots/{robot_id}/mcap-profile")
def set_mcap_profile(
    project_id: str,
    region_code: str,
    robot_id: str,
    command: McapProcessingConfig,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
) -> McapProcessingConfig:
    from .mcap_processing import profile

    auth.require_organization_capability(organization_id, "robot.manage")
    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    return profile((organization_id, project_id, region_code), robot_id, command)


@router.post("/{import_id}:process-mcap")
def process_mcap(
    project_id: str,
    region_code: str,
    import_id: str,
    command: StartStoredProcessing,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
):
    from .mcap_processing import start

    authorize_scope(auth, project_id, "upload.manage", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    raw = browser.source(organization_id, project_id, region_code, import_id)
    if raw.source_format.value != "MCAP" or not raw.dataset_id:
        raise problem(
            status=422,
            code="MCAP_SOURCE_REQUIRED",
            title="需要 MCAP 原始数据",
            detail="请选择绑定数据集的 MCAP 原始数据。",
        )
    return start((organization_id, project_id, region_code), raw, browser.manifest(raw), command)


@router.get("/{import_id}/mcap-processing")
def mcap_progress(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
):
    from .mcap_processing import progress

    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    raw = browser.source(organization_id, project_id, region_code, import_id)
    return progress((organization_id, project_id, region_code), import_id, browser.manifest(raw))


@router.get("")
def list_native_imports(
    project_id: str,
    region_code: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    dataset_id: Annotated[str | None, Query(max_length=128)] = None,
    include_all_sources: bool = False,
    recording_id: Annotated[str | None, Query(max_length=128)] = None,
) -> list[NativeImportProgress]:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    items = list_progress(
        organization_id,
        project_id,
        region_code,
        limit=limit,
        offset=offset,
        dataset_id=dataset_id,
        include_all_sources=include_all_sources,
        recording_id=recording_id,
    )
    return (
        [
            service.delivery.enrich((organization_id, project_id, region_code), item)
            for item in items
        ]
        if service.delivery is not None
        else items
    )


@router.post(
    "/archive",
    operation_id="downloadOriginalArchive",
    response_class=StreamingResponse,
    responses={
        200: {"content": {"application/zip": {"schema": {"type": "string", "format": "binary"}}}}
    },
)
def download_original_archive(
    project_id: str,
    region_code: str,
    command: OriginalArchiveRequest,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
) -> StreamingResponse:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    authorize_scope(auth, project_id, "export.download", region_code, organization_id)
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    files = archive_files(browser, organization_id, project_id, region_code, command)
    stream = stream_archive(browser, files)
    return StreamingResponse(
        stream,
        media_type="application/zip",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="original-data.zip"',
            "X-Accel-Buffering": "no",
        },
        background=BackgroundTask(stream.close),
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


def _source_local_root() -> str | None:
    from hc_data_platform.core.config import get_settings

    settings = get_settings()
    if settings.environment == "local" and settings.object_store_provider == "s3":
        return settings.object_store_raw_browser_path or settings.object_store_browser_path
    return None


@router.get("/{import_id}/directory")
def browse_original_directory(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    response: Response,
    path: Annotated[str, Query(max_length=1024)] = "",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> OriginalDirectory:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    response.headers["Cache-Control"] = "no-store"
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    raw = browser.source(organization_id, project_id, region_code, import_id)
    return directory(
        browser, raw, path=path, offset=offset, limit=limit, local_root=_source_local_root()
    )


@router.get("/{import_id}/text-preview")
def preview_original_text(
    project_id: str,
    region_code: str,
    import_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
    response: Response,
    path: Annotated[str, Query(min_length=1, max_length=1024)],
) -> OriginalTextPreview:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    response.headers["Cache-Control"] = "no-store"
    browser = OriginalSourceBrowser(service.storage, service.raw_sources)
    return text_preview(
        browser, browser.source(organization_id, project_id, region_code, import_id), path
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
    service: Service,
) -> NativeImportProgress:
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    item = get_progress(organization_id, project_id, region_code, import_id)
    return (
        service.delivery.enrich((organization_id, project_id, region_code), item)
        if service.delivery is not None
        else item
    )


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


@router.get("/{import_id}/mcap-versions", operation_id="listMcapRetryVersions")
def mcap_versions(
    project_id: str,
    region_code: str,
    import_id: str,
    robot_id: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
):
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    raw = OriginalSourceBrowser(service.storage, service.raw_sources).source(
        organization_id, project_id, region_code, import_id
    )
    if raw.source_format.value != "MCAP":
        raise problem(
            status=422,
            code="MCAP_SOURCE_REQUIRED",
            title="需要 MCAP",
            detail="请选择 MCAP 原始文件",
        )
    from .configuration import connections

    with connections()() as db:
        # Only the batch's robot can be inspected using upload permissions.
        association = db.execute(
            """SELECT robot_id FROM ingest.mcap_processing_files
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            UNION SELECT authenticated_robot_id FROM ingest.robot_ingest_uploads
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            UNION SELECT robot_id FROM ingest.raw_sources
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s
            UNION SELECT robotics.robot_reference_owner(organization_id,'raw',project_id,
            region_code,raw_source_id,robot_id) FROM ingest.raw_sources
            WHERE organization_id=%s AND project_id=%s AND region_code=%s AND raw_source_id=%s""",
            (organization_id, project_id, region_code, import_id) * 4,
        ).fetchall()
        if robot_id not in {r[0] for r in association}:
            raise problem(
                status=403,
                code="ROBOT_CONFIG_SCOPE_DENIED",
                title="机器人不属于该批次",
                detail="只能查看本批次关联机器人的已发布配置",
            )
        rows = db.execute(
            """SELECT document FROM ingest.robot_processing_versions
            WHERE organization_id=%s AND robot_id=%s ORDER BY scheme_id,version DESC""",
            (organization_id, robot_id),
        ).fetchall()
    # Do not expose validation evidence from another project to upload-only users.
    return {
        "items": [
            {
                k: r[0][k]
                for k in ("scheme_id", "version", "content_sha256", "configuration", "name")
            }
            for r in rows
        ]
    }


@router.get("/{import_id}/mcap-channels", operation_id="inspectMcapChannels")
def mcap_channels(
    project_id: str,
    region_code: str,
    import_id: str,
    path: str,
    auth: VerifiedAuth,
    organization_id: OrganizationHeader,
    service: Service,
):
    authorize_scope(auth, project_id, "upload.read", region_code, organization_id)
    from hc_data_platform.continuous_recordings.mcap_native import inspect
    from hc_data_platform.robot_processing.router import sample

    return sample(
        service,
        organization_id,
        project_id,
        region_code,
        import_id,
        path,
        lambda source, evidence: {"inventory": inspect(source), "evidence": evidence},
    )
