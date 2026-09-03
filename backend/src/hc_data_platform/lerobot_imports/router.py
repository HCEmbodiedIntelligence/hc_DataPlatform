from __future__ import annotations

import asyncio
from tempfile import SpooledTemporaryFile
from typing import Annotated, BinaryIO, cast

from fastapi import APIRouter, Depends, Header, Query, Request, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.ingest.router import get_service as get_ingest_service
from hc_data_platform.security.http import VerifiedAuth

from .models import (
    AuthorizeLeRobotPartsV1,
    CommitLeRobotImportV1,
    CompleteLeRobotAssetV1,
    CreateLeRobotImportV1,
    LeRobotAssetCompletedV1,
    LeRobotImportAcceptedV1,
    LeRobotImportGrantV1,
    LeRobotPartGrantV1,
)
from .service import LeRobotWebUploadService

router = APIRouter(
    prefix="/api/v1/projects/{project_id}/regions/{region_code}/lerobot-imports",
    tags=["lerobot-imports"],
)
OrganizationHeader = Annotated[str, Header(alias="X-Organization-Id", min_length=1, max_length=128)]
_PART_MEMORY_SPOOL_BYTES = 8 * 1024**2
_MAX_PROXY_PART_BYTES = 600 * 1024**2


def get_service() -> LeRobotWebUploadService:
    ingest = get_ingest_service()
    return LeRobotWebUploadService(ingest.storage, raw_sources=ingest.raw_sources)


Service = Annotated[LeRobotWebUploadService, Depends(get_service)]


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
            body.write(chunk)
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
