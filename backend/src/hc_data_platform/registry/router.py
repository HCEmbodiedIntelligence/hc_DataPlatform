"""Formal P14 robot-model router."""

from __future__ import annotations

import mimetypes
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from fastapi.responses import StreamingResponse

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.security.http import VerifiedAuth
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    AuthorizeRobotModelAssetPartsRequest,
    CompleteRobotModelAssetFileRequest,
    CreateRobotModelAssetUploadRequest,
    CreateRobotModelDraftRequest,
    CreateRobotModelRequest,
    PublishRobotModelVersionRequest,
    ReplaceRobotModelJointMappingsRequest,
    RobotAssetPartAuthorizationPage,
    RobotModelAssetDownloadAuthorization,
    RobotModelAssetPage,
    RobotModelAssetUploadEnvelope,
    RobotModelJointMappingPage,
    RobotModelPage,
    RobotModelPublishPreflightEnvelope,
    RobotModelVersionEnvelope,
)
from .service import RegistryService

router = APIRouter(prefix="/api/v1/organizations/{organization_id}", tags=["registry"])
_service = RegistryService.in_memory()
_MAX_ASSET_PART_BYTES = 16 * 1024**2

PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    status: {
        "description": "The registry request could not be completed.",
        "content": {
            "application/problem+json": {"schema": {"$ref": "#/components/schemas/ProblemDetails"}}
        },
    }
    for status in (401, 403, 404, 409, 412, 413, 415, 422, 503)
}


def configure_registry(service: RegistryService) -> None:
    global _service
    _service = service


def get_registry_service() -> RegistryService:
    return _service


ServiceDependency = Annotated[RegistryService, Depends(get_registry_service)]


def resolve_registry_project(
    organization_id: str,
    auth: VerifiedAuth,
) -> str:
    """Choose an authorization/audit project without making it resource identity."""

    candidates = sorted(
        {
            project_id
            for scoped_organization, project_id, _region in auth.organization_scope_triples
            if scoped_organization == organization_id
        }
    )
    if candidates:
        return candidates[0]

    from hc_data_platform.core.errors import problem

    raise problem(
        status=403,
        code="ORGANIZATION_SCOPE_DENIED",
        title="Organization access denied",
        detail="No authorized organization scope is available for this registry operation.",
    )


ProjectScope = Annotated[str, Depends(resolve_registry_project)]
IfMatch = Annotated[str, Header(alias="If-Match", min_length=1, max_length=256)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=256)]


def _request_id(request: Request) -> str:
    value = getattr(request.state, "request_id", None)
    return value if isinstance(value, str) and value else "request-id-unavailable"


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


def _select_project_scope(
    auth: VerifiedAuth, project_id: str, *, capability: str = "robot_model.read"
) -> None:
    """Bind PostgreSQL RLS to the authenticated registry project scope."""

    ScopeGuard.require(auth, project_id)
    auth.require_capability(capability, project_id)
    select_request_scope(project_id)


async def _read_asset_part(request: Request) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"", "application/octet-stream"}:
        from hc_data_platform.core.errors import problem

        raise problem(
            status=415,
            code="ROBOT_MODEL_ASSET_PART_CONTENT_TYPE_INVALID",
            title="Unsupported robot model asset part content type",
            detail="Robot model asset parts require application/octet-stream.",
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            from hc_data_platform.core.errors import problem

            raise problem(
                status=422,
                code="ROBOT_MODEL_ASSET_PART_LENGTH_INVALID",
                title="Robot model asset part length is invalid",
                detail="Content-Length must be a positive integer.",
            ) from exc
        if declared < 1 or declared > _MAX_ASSET_PART_BYTES:
            from hc_data_platform.core.errors import problem

            raise problem(
                status=413,
                code="ROBOT_MODEL_ASSET_PART_TOO_LARGE",
                title="Robot model asset part is too large",
                detail=f"A robot model asset part must be at most {_MAX_ASSET_PART_BYTES} bytes.",
            )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > _MAX_ASSET_PART_BYTES:
            from hc_data_platform.core.errors import problem

            raise problem(
                status=413,
                code="ROBOT_MODEL_ASSET_PART_TOO_LARGE",
                title="Robot model asset part is too large",
                detail=f"A robot model asset part must be at most {_MAX_ASSET_PART_BYTES} bytes.",
            )
    if not body:
        from hc_data_platform.core.errors import problem

        raise problem(
            status=422,
            code="ROBOT_MODEL_ASSET_PART_EMPTY",
            title="Robot model asset part is empty",
            detail="Robot model asset parts must contain at least one byte.",
        )
    return bytes(body)


@router.put(
    "/robot-model-assets/upload-part",
    operation_id="uploadRobotModelAssetPartToServer",
    status_code=200,
    response_class=Response,
    responses=PROBLEM_RESPONSES,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}
            },
        }
    },
)
async def upload_robot_model_asset_part_to_server(
    organization_id: str,
    token: Annotated[str, Query(min_length=1, max_length=16_384)],
    request: Request,
    service: ServiceDependency,
) -> Response:
    part = service.receive_authorized_asset_part(
        organization_id=organization_id,
        token=token,
        body=await _read_asset_part(request),
    )
    return Response(
        status_code=200,
        headers={
            "Cache-Control": "private, no-store",
            "ETag": f'"{part.etag}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/robot-model-assets/content",
    operation_id="downloadRobotModelAssetFromServer",
    response_class=StreamingResponse,
    responses=PROBLEM_RESPONSES,
)
def download_robot_model_asset_from_server(
    organization_id: str,
    token: Annotated[str, Query(min_length=1, max_length=16_384)],
    service: ServiceDependency,
) -> StreamingResponse:
    filename, chunks = service.open_authorized_asset_download(
        organization_id=organization_id,
        token=token,
    )
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return StreamingResponse(
        chunks,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get(
    "/robot-models",
    operation_id="listRobotModels",
    response_model=RobotModelPage,
    responses=PROBLEM_RESPONSES,
)
def list_robot_models(
    organization_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    q: str | None = Query(default=None, min_length=1, max_length=256),
) -> RobotModelPage:
    _select_project_scope(auth, project_id)
    _no_store(response)
    return service.list_robot_models(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        query=q,
        request_id=_request_id(request),
    )


@router.post(
    "/robot-models",
    operation_id="createRobotModel",
    status_code=201,
    response_model=RobotModelVersionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def create_robot_model(
    organization_id: str,
    command: CreateRobotModelRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> RobotModelVersionEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    result = service.create_robot_model(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/robot-model-versions/{result.data.id}"
    )
    return result


@router.get(
    "/robot-model-versions/{version_id}",
    operation_id="getRobotModelVersion",
    response_model=RobotModelVersionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def get_robot_model_version(
    organization_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotModelVersionEnvelope:
    _select_project_scope(auth, project_id)
    _no_store(response)
    result = service.get_robot_model_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        request_id=_request_id(request),
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.delete(
    "/robot-model-versions/{version_id}:discard-import",
    operation_id="discardRobotModelImport",
    status_code=204,
    responses=PROBLEM_RESPONSES,
)
def discard_robot_model_import(
    organization_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
    fallback_version_id: str | None = Query(default=None),
) -> None:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    service.discard_robot_model_import(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        fallback_version_id=fallback_version_id,
        request_id=_request_id(request),
    )


@router.post(
    "/robot-model-versions/{version_id}:create-draft",
    operation_id="createRobotModelDraft",
    status_code=201,
    response_model=RobotModelVersionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def create_robot_model_draft(
    organization_id: str,
    version_id: str,
    command: CreateRobotModelDraftRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> RobotModelVersionEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    result = service.create_robot_model_draft(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        source_version_id=version_id,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )
    response.headers["ETag"] = result.data.etag
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/robot-model-versions/{result.data.id}"
    )
    return result


@router.get(
    "/robot-model-versions/{version_id}/joint-mappings",
    operation_id="listRobotModelJointMappings",
    response_model=RobotModelJointMappingPage,
    responses=PROBLEM_RESPONSES,
)
def list_robot_model_joint_mappings(
    organization_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotModelJointMappingPage:
    _select_project_scope(auth, project_id)
    _no_store(response)
    return service.list_robot_model_joint_mappings(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.put(
    "/robot-model-versions/{version_id}/joint-mappings",
    operation_id="replaceRobotModelJointMappings",
    response_model=RobotModelVersionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def replace_robot_model_joint_mappings(
    organization_id: str,
    version_id: str,
    command: ReplaceRobotModelJointMappingsRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> RobotModelVersionEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    result = service.replace_robot_model_joint_mappings(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.post(
    "/robot-model-versions/{version_id}:preflight-publish",
    operation_id="preflightRobotModelPublish",
    response_model=RobotModelPublishPreflightEnvelope,
    responses=PROBLEM_RESPONSES,
)
def preflight_robot_model_publish(
    organization_id: str,
    version_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
) -> RobotModelPublishPreflightEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    return service.preflight_robot_model_publish(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
    )


@router.post(
    "/robot-model-versions/{version_id}:publish",
    operation_id="publishRobotModelVersion",
    response_model=RobotModelVersionEnvelope,
    responses=PROBLEM_RESPONSES,
)
def publish_robot_model_version(
    organization_id: str,
    version_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    if_match: IfMatch,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
    command: PublishRobotModelVersionRequest,
) -> RobotModelVersionEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    result = service.publish_robot_model_version(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        expected_etag=if_match,
        idempotency_key=idempotency_key,
        request_id=_request_id(request),
        command=command,
    )
    response.headers["ETag"] = result.data.etag
    return result


@router.post(
    "/robot-model-versions/{version_id}/upload-sessions",
    operation_id="createRobotModelAssetUploadSession",
    status_code=201,
    response_model=RobotModelAssetUploadEnvelope,
    responses=PROBLEM_RESPONSES,
)
def create_robot_model_asset_upload_session(
    organization_id: str,
    version_id: str,
    request: Request,
    response: Response,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    idempotency_key: IdempotencyKey,
    service: ServiceDependency,
    command: CreateRobotModelAssetUploadRequest,
) -> RobotModelAssetUploadEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    result = service.create_asset_upload(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        request_id=_request_id(request),
        idempotency_key=idempotency_key,
        command=command,
    )
    response.headers["Location"] = (
        f"/api/v1/organizations/{organization_id}/robot-model-asset-uploads/{result.data.upload_id}"
    )
    return result


@router.post(
    "/robot-model-asset-uploads/{upload_id}:authorize-parts",
    operation_id="authorizeRobotModelAssetParts",
    response_model=RobotAssetPartAuthorizationPage,
    responses=PROBLEM_RESPONSES,
)
def authorize_robot_model_asset_parts(
    organization_id: str,
    upload_id: str,
    command: AuthorizeRobotModelAssetPartsRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotAssetPartAuthorizationPage:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    return service.authorize_asset_parts(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        upload_id=upload_id,
        request_id=_request_id(request),
        command=command,
    )


@router.post(
    "/robot-model-asset-uploads/{upload_id}:complete-file",
    operation_id="completeRobotModelAssetUploadFile",
    response_model=RobotModelAssetUploadEnvelope,
    responses=PROBLEM_RESPONSES,
)
def complete_robot_model_asset_upload_file(
    organization_id: str,
    upload_id: str,
    command: CompleteRobotModelAssetFileRequest,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotModelAssetUploadEnvelope:
    _select_project_scope(auth, project_id, capability="robot_model.manage")
    _no_store(response)
    return service.complete_asset_upload_file(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        upload_id=upload_id,
        request_id=_request_id(request),
        command=command,
    )


@router.get(
    "/robot-model-versions/{version_id}/assets",
    operation_id="listRobotModelAssets",
    response_model=RobotModelAssetPage,
    responses=PROBLEM_RESPONSES,
)
def list_robot_model_assets(
    organization_id: str,
    version_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotModelAssetPage:
    _select_project_scope(auth, project_id)
    _no_store(response)
    return service.list_robot_model_assets(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        request_id=_request_id(request),
    )


@router.get(
    "/robot-model-versions/{version_id}/assets/{asset_id}/download",
    operation_id="authorizeRobotModelAssetDownload",
    response_model=RobotModelAssetDownloadAuthorization,
    responses=PROBLEM_RESPONSES,
)
def authorize_robot_model_asset_download(
    organization_id: str,
    version_id: str,
    asset_id: str,
    response: Response,
    request: Request,
    auth: VerifiedAuth,
    project_id: ProjectScope,
    service: ServiceDependency,
) -> RobotModelAssetDownloadAuthorization:
    _select_project_scope(auth, project_id)
    _no_store(response)
    return service.authorize_asset_download(
        auth=auth,
        organization_id=organization_id,
        project_id=project_id,
        version_id=version_id,
        asset_id=asset_id,
        request_id=_request_id(request),
    )
