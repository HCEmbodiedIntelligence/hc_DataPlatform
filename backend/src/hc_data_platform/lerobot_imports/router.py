from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header

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
