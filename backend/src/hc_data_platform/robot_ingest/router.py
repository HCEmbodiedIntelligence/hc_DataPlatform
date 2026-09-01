from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from hc_data_platform.security.http import VerifiedAuth

from .models import (
    AuthorizePartsCommand,
    CompleteAssetCommand,
    CreateRobotIngestIdentity,
    IdentityState,
    IssueCredentialCommand,
    PartAuthorizationGrant,
    RobotCredentialSummary,
    RobotIdentityEnvelope,
    RobotIdentityList,
    RobotIngestAttemptList,
    RobotIngestEpisodeResultList,
    RobotIngestStatistics,
    RobotIngestUploadEnvelope,
    RobotIngestUploadManifest,
    UpdateRobotIngestIdentity,
    UploadList,
)
from .service import RobotIngestService

router = APIRouter(prefix="/api/v1", tags=["robot-ingest"])
_service = RobotIngestService.in_memory()
_robot_bearer = HTTPBearer(auto_error=False, scheme_name="robotBearerAuth")


def configure_robot_ingest(service: RobotIngestService) -> None:
    global _service
    _service = service


def get_robot_ingest_service() -> RobotIngestService:
    return _service


def require_robot_token(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_robot_bearer)],
) -> str:
    # Token validity and principal type are deliberately checked by RobotIngestService,
    # not by the user JWT/session middleware.
    if credentials is None or credentials.scheme.lower() != "bearer":
        from hc_data_platform.core.errors import problem

        raise problem(
            status=401,
            code="ROBOT_CREDENTIAL_INVALID",
            title="Robot authentication failed",
            detail="A valid robot upload Bearer credential is required.",
        )
    return credentials.credentials.strip()


Service = Annotated[RobotIngestService, Depends(get_robot_ingest_service)]
RobotToken = Annotated[str, Depends(require_robot_token)]
OrganizationId = Annotated[
    str,
    Header(
        alias="X-Organization-Id",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
]
RegionCode = Annotated[
    str,
    Header(
        alias="X-Region-Code",
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9._-]+$",
    ),
]


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/robot-ingest/uploads",
    response_model=RobotIngestUploadEnvelope,
    status_code=201,
    operation_id="createRobotIngestUpload",
)
def create_robot_upload(
    command: RobotIngestUploadManifest,
    response: Response,
    token: RobotToken,
    service: Service,
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    result = service.create_upload(token=token, manifest=command)
    response.headers["Location"] = f"/api/v1/robot-ingest/uploads/{result.data.upload_id}"
    response.headers["Idempotency-Replayed"] = "true" if result.resumed else "false"
    if result.resumed:
        response.status_code = 200
    return result


@router.get(
    "/robot-ingest/uploads/{upload_id}",
    response_model=RobotIngestUploadEnvelope,
    operation_id="getRobotIngestUpload",
)
def get_robot_upload(
    upload_id: str,
    response: Response,
    token: RobotToken,
    service: Service,
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.get_upload(token=token, upload_id=upload_id)


@router.post(
    "/robot-ingest/uploads/{upload_id}/assets/{asset_id}:authorize-parts",
    response_model=PartAuthorizationGrant,
    operation_id="authorizeRobotIngestParts",
)
def authorize_robot_upload_parts(
    upload_id: str,
    asset_id: str,
    command: AuthorizePartsCommand,
    response: Response,
    token: RobotToken,
    service: Service,
) -> PartAuthorizationGrant:
    _no_store(response)
    return service.authorize_parts(
        token=token,
        upload_id=upload_id,
        asset_id=asset_id,
        command=command,
    )


@router.post(
    "/robot-ingest/uploads/{upload_id}/assets/{asset_id}:complete",
    response_model=RobotIngestUploadEnvelope,
    operation_id="completeRobotIngestAsset",
)
def complete_robot_upload_asset(
    upload_id: str,
    asset_id: str,
    command: CompleteAssetCommand,
    response: Response,
    token: RobotToken,
    service: Service,
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.complete_asset(
        token=token,
        upload_id=upload_id,
        asset_id=asset_id,
        command=command,
    )


@router.post(
    "/robot-ingest/uploads/{upload_id}:commit",
    response_model=RobotIngestUploadEnvelope,
    operation_id="commitRobotIngestUpload",
)
def commit_robot_upload(
    upload_id: str,
    response: Response,
    token: RobotToken,
    service: Service,
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.commit_upload(token=token, upload_id=upload_id)


@router.post(
    "/robot-ingest/uploads/{upload_id}:pause",
    response_model=RobotIngestUploadEnvelope,
    operation_id="pauseRobotIngestUpload",
)
def pause_robot_upload(
    upload_id: str, response: Response, token: RobotToken, service: Service
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.pause_upload(token=token, upload_id=upload_id)


@router.post(
    "/robot-ingest/uploads/{upload_id}:resume",
    response_model=RobotIngestUploadEnvelope,
    operation_id="resumeRobotIngestUpload",
)
def resume_robot_upload(
    upload_id: str, response: Response, token: RobotToken, service: Service
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.resume_upload(token=token, upload_id=upload_id)


@router.post(
    "/robot-ingest/uploads/{upload_id}:cancel",
    response_model=RobotIngestUploadEnvelope,
    operation_id="cancelRobotIngestUpload",
)
def cancel_robot_upload(
    upload_id: str, response: Response, token: RobotToken, service: Service
) -> RobotIngestUploadEnvelope:
    _no_store(response)
    return service.cancel_upload(token=token, upload_id=upload_id)


@router.get(
    "/projects/{project_id}/robot-ingest/identities",
    response_model=RobotIdentityList,
    operation_id="listRobotIngestIdentities",
)
def list_robot_identities(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityList:
    _no_store(response)
    return service.list_identities(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities",
    response_model=RobotIdentityEnvelope,
    status_code=201,
    operation_id="createRobotIngestIdentity",
)
def create_robot_identity(
    project_id: str,
    command: CreateRobotIngestIdentity,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    _no_store(response)
    return service.create_identity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        command=command,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}",
    response_model=RobotIdentityEnvelope,
    operation_id="getRobotIngestIdentity",
)
def get_robot_identity(
    project_id: str,
    ingest_identity_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    _no_store(response)
    return service.get_identity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
    )


@router.patch(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}",
    response_model=RobotIdentityEnvelope,
    operation_id="updateRobotIngestIdentity",
)
def update_robot_identity(
    project_id: str,
    ingest_identity_id: str,
    command: UpdateRobotIngestIdentity,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    _no_store(response)
    return service.update_identity(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        command=command,
    )


def _set_identity_state(
    *,
    project_id: str,
    ingest_identity_id: str,
    state: IdentityState,
    response: Response,
    auth: object,
    organization_id: str,
    service: RobotIngestService,
) -> RobotIdentityEnvelope:
    from hc_data_platform.security.auth import AuthContext

    if not isinstance(auth, AuthContext):
        raise TypeError("verified auth context required")
    _no_store(response)
    return service.set_identity_state(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        state=state,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}:enable",
    response_model=RobotIdentityEnvelope,
    operation_id="enableRobotIngestIdentity",
)
def enable_robot_identity(
    project_id: str,
    ingest_identity_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    return _set_identity_state(
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        state=IdentityState.ENABLED,
        response=response,
        auth=auth,
        organization_id=organization_id,
        service=service,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}:disable",
    response_model=RobotIdentityEnvelope,
    operation_id="disableRobotIngestIdentity",
)
def disable_robot_identity(
    project_id: str,
    ingest_identity_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    return _set_identity_state(
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        state=IdentityState.DISABLED,
        response=response,
        auth=auth,
        organization_id=organization_id,
        service=service,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}/credentials",
    response_model=RobotIdentityEnvelope,
    status_code=201,
    operation_id="issueRobotIngestCredential",
)
def issue_robot_credential(
    project_id: str,
    ingest_identity_id: str,
    command: IssueCredentialCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    _no_store(response)
    return service.issue_credential(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        command=command,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}:rotate-credential",
    response_model=RobotIdentityEnvelope,
    status_code=201,
    operation_id="rotateRobotIngestCredential",
)
def rotate_robot_credential(
    project_id: str,
    ingest_identity_id: str,
    command: IssueCredentialCommand,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotIdentityEnvelope:
    _no_store(response)
    return service.issue_credential(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        command=command.model_copy(update={"revoke_previous": True}),
    )


@router.get(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}/credentials",
    response_model=list[RobotCredentialSummary],
    operation_id="listRobotIngestCredentials",
)
def list_robot_credentials(
    project_id: str,
    ingest_identity_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> tuple[RobotCredentialSummary, ...]:
    _no_store(response)
    return service.list_credentials(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
    )


@router.post(
    "/projects/{project_id}/robot-ingest/identities/{ingest_identity_id}/credentials/{credential_id}:revoke",
    response_model=RobotCredentialSummary,
    operation_id="revokeRobotIngestCredential",
)
def revoke_robot_credential(
    project_id: str,
    ingest_identity_id: str,
    credential_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    service: Service,
) -> RobotCredentialSummary:
    _no_store(response)
    return service.revoke_credential(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        ingest_identity_id=ingest_identity_id,
        credential_id=credential_id,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/uploads",
    response_model=UploadList,
    operation_id="listRobotIngestUploadHistory",
)
def list_robot_upload_history(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    region_code: RegionCode,
    service: Service,
    robot_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    collection_task_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    source_format: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> UploadList:
    _no_store(response)
    return service.list_robot_uploads(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        collection_task_id=collection_task_id,
        source_format=source_format,
        created_from=created_from,
        created_to=created_to,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/uploads/{upload_id}/episodes",
    response_model=RobotIngestEpisodeResultList,
    operation_id="listRobotIngestUploadEpisodes",
)
def list_robot_upload_episodes(
    project_id: str,
    upload_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    region_code: RegionCode,
    service: Service,
) -> RobotIngestEpisodeResultList:
    _no_store(response)
    return service.list_upload_episode_results(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        upload_id=upload_id,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/attempts",
    response_model=RobotIngestAttemptList,
    operation_id="listRobotIngestAttempts",
)
def list_robot_attempts(
    project_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    region_code: RegionCode,
    service: Service,
    robot_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
) -> RobotIngestAttemptList:
    _no_store(response)
    return service.list_attempts(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
    )


@router.get(
    "/projects/{project_id}/robot-ingest/robots/{robot_id}/statistics",
    response_model=RobotIngestStatistics,
    operation_id="getRobotIngestStatistics",
)
def get_robot_statistics(
    project_id: str,
    robot_id: str,
    response: Response,
    auth: VerifiedAuth,
    organization_id: OrganizationId,
    region_code: RegionCode,
    service: Service,
    collection_task_id: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    source_format: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
) -> RobotIngestStatistics:
    _no_store(response)
    return service.statistics(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        region_code=region_code,
        robot_id=robot_id,
        collection_task_id=collection_task_id,
        source_format=source_format,
        created_from=created_from,
        created_to=created_to,
    )
