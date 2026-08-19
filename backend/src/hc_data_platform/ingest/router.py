from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, TypeVar

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hc_data_platform.core.context import current_request_context, select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .manifest import (
    MAX_MANIFEST_BYTES,
    decode_bounded_json,
    parse_manifest_bytes,
    preflight_manifest,
    require_decimal_crc64_wire_values,
)
from .models import (
    CompletedPart,
    FailedPartV1,
    ManifestPreflightResultV1,
    PartAuthorization,
    RawObjectCommittedV1,
    RolloutManifestV1,
    UploadPart,
    UploadSession,
    UploadSessionGrant,
    UploadSessionListV1,
    UploadStatus,
)
from .ports import InMemoryObjectStorage
from .service import UploadSessionService

router = APIRouter(prefix="/api/v1", tags=["ingest"])
_configured_service: UploadSessionService | None = None


class CreateUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest: RolloutManifestV1
    part_numbers: list[int] = Field(default_factory=list, max_length=256)
    object_storage_uri: str | None = Field(default=None, min_length=1, max_length=2048)

    @model_validator(mode="before")
    @classmethod
    def validate_manifest_crc64_wire_values(cls, value: object) -> object:
        if isinstance(value, Mapping):
            require_decimal_crc64_wire_values(value.get("manifest"))
        return value


class PartNumbersRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_numbers: list[int] = Field(min_length=1, max_length=256)


class ResumeUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    part_numbers: list[int] = Field(default_factory=list, max_length=256)


class CompleteUploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parts: list[CompletedPart] = Field(min_length=1, max_length=10_000)


class RetryFailedPartsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    failures: list[FailedPartV1] = Field(min_length=1, max_length=256)


_MAX_CREATE_REQUEST_BYTES = MAX_MANIFEST_BYTES + 64 * 1024
_MAX_CONTROL_REQUEST_BYTES = 8 * 1024 * 1024
_RequestModelT = TypeVar("_RequestModelT", bound=BaseModel)


def require_auth_context(request: Request) -> AuthContext:
    """Consume the AuthContext installed by BE-02 authentication middleware."""

    auth = getattr(request.state, "auth_context", None)
    if auth is None:
        auth = getattr(request.state, "auth", None)
    if not isinstance(auth, AuthContext):
        raise problem(
            status=401,
            code="AUTH_CONTEXT_REQUIRED",
            title="Authentication required",
            detail="A verified authentication context is required.",
        )
    return auth


Auth = Annotated[AuthContext, Depends(require_auth_context)]


@lru_cache(maxsize=1)
def get_service() -> UploadSessionService:
    if _configured_service is not None:
        return _configured_service
    return UploadSessionService(InMemoryObjectStorage())


def configure_ingest_service(service: UploadSessionService) -> None:
    global _configured_service
    _configured_service = service
    get_service.cache_clear()


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-manifests:preflight",
    response_model=ManifestPreflightResultV1,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": RolloutManifestV1.model_json_schema()}},
        }
    },
)
async def preflight_upload_manifest(
    project_id: str,
    region_code: str,
    request: Request,
    auth: Auth,
) -> ManifestPreflightResultV1:
    _authorize(auth, project_id, region_code)
    body = await _read_limited_body(request, MAX_MANIFEST_BYTES)
    result = parse_manifest_bytes(body)
    if result.manifest.project_id != project_id:
        raise problem(
            status=409,
            code="PROJECT_PATH_MISMATCH",
            title="Project path mismatch",
            detail="The path project and manifest project must match.",
        )
    return result


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions",
    response_model=UploadSessionGrant,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CreateUploadRequest.model_json_schema()}},
        }
    },
)
async def create_upload_session(
    project_id: str,
    region_code: str,
    request: Request,
    auth: Auth,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> UploadSessionGrant:
    _authorize(auth, project_id, region_code)
    payload = await _read_limited_json(request, _MAX_CREATE_REQUEST_BYTES)
    try:
        create_request = CreateUploadRequest.model_validate(payload)
    except ValidationError as exc:
        raise _request_validation_problem(exc) from exc
    preflight_manifest(create_request.manifest)
    if create_request.manifest.project_id != project_id:
        raise problem(
            status=409,
            code="PROJECT_PATH_MISMATCH",
            title="Project path mismatch",
            detail="The path project and manifest project must match.",
        )
    result = get_service().create_upload(
        manifest=create_request.manifest,
        region_code=region_code,
        idempotency_key=idempotency_key,
        part_numbers=create_request.part_numbers,
        object_storage_uri=create_request.object_storage_uri,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/projects/{project_id}/regions/{region_code}/upload-sessions",
    response_model=UploadSessionListV1,
)
def list_upload_sessions(
    project_id: str,
    region_code: str,
    auth: Auth,
    status: UploadStatus | None = None,
    data_package_id: str | None = Query(default=None, min_length=1, max_length=128),
    limit: int = Query(default=50, ge=1, le=100),
) -> UploadSessionListV1:
    _authorize(auth, project_id, region_code)
    return get_service().list_sessions(
        project_id,
        region_code,
        status=status,
        data_package_id=data_package_id,
        limit=limit,
    )


@router.get(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}",
    response_model=UploadSession,
)
def get_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    auth: Auth,
) -> UploadSession:
    _authorize(auth, project_id, region_code)
    return _scoped_session(project_id, region_code, session_id)


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:renew",
    response_model=list[PartAuthorization],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": PartNumbersRequest.model_json_schema()}},
        }
    },
)
async def renew_upload_authorizations(
    project_id: str,
    region_code: str,
    session_id: str,
    request: Request,
    auth: Auth,
    response: Response,
) -> list[PartAuthorization]:
    _authorize_and_get(auth, project_id, region_code, session_id)
    command = _validate_request_model(
        PartNumbersRequest,
        await _read_limited_json(request, _MAX_CONTROL_REQUEST_BYTES),
    )
    result = get_service().renew_part_authorizations(session_id, command.part_numbers)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/parts",
    response_model=list[UploadPart],
)
def list_uploaded_parts(
    project_id: str,
    region_code: str,
    session_id: str,
    auth: Auth,
) -> list[UploadPart]:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().list_parts(session_id)


@router.get(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}/manifest",
    response_model=ManifestPreflightResultV1,
)
def get_upload_manifest(
    project_id: str,
    region_code: str,
    session_id: str,
    auth: Auth,
) -> ManifestPreflightResultV1:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().get_manifest_preflight(session_id)


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:complete",
    response_model=UploadSession,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": CompleteUploadRequest.model_json_schema()}},
        }
    },
)
async def complete_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    request: Request,
    auth: Auth,
) -> UploadSession:
    _authorize_and_get(auth, project_id, region_code, session_id)
    command = _validate_request_model(
        CompleteUploadRequest,
        await _read_limited_json(request, _MAX_CONTROL_REQUEST_BYTES),
    )
    return get_service().complete_upload(session_id, command.parts)


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:retry-parts",
    response_model=list[PartAuthorization],
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": RetryFailedPartsRequest.model_json_schema()}
            },
        }
    },
)
async def retry_failed_upload_parts(
    project_id: str,
    region_code: str,
    session_id: str,
    request: Request,
    auth: Auth,
    response: Response,
) -> list[PartAuthorization]:
    _authorize_and_get(auth, project_id, region_code, session_id)
    command = _validate_request_model(
        RetryFailedPartsRequest,
        await _read_limited_json(request, _MAX_CONTROL_REQUEST_BYTES),
    )
    result = get_service().retry_failed_parts(session_id, command.failures)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:pause",
    response_model=UploadSession,
)
def pause_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    auth: Auth,
) -> UploadSession:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().pause_upload(session_id)


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:resume",
    response_model=UploadSessionGrant,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": ResumeUploadRequest.model_json_schema()}},
        }
    },
)
async def resume_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    request: Request,
    auth: Auth,
    response: Response,
) -> UploadSessionGrant:
    _authorize_and_get(auth, project_id, region_code, session_id)
    command = _validate_request_model(
        ResumeUploadRequest,
        await _read_limited_json(request, _MAX_CONTROL_REQUEST_BYTES),
    )
    result = get_service().resume_upload(session_id, command.part_numbers)
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:cancel",
    response_model=UploadSession,
)
def cancel_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    auth: Auth,
) -> UploadSession:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().cancel_upload(session_id)


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:commit-manifest",
    response_model=RawObjectCommittedV1,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {"application/json": {"schema": RolloutManifestV1.model_json_schema()}},
        }
    },
)
async def commit_manifest(
    project_id: str,
    region_code: str,
    session_id: str,
    request: Request,
    auth: Auth,
) -> RawObjectCommittedV1:
    _authorize_and_get(auth, project_id, region_code, session_id)
    manifest = parse_manifest_bytes(await _read_limited_body(request, MAX_MANIFEST_BYTES)).manifest
    return get_service().commit_manifest(
        session_id=session_id,
        manifest=manifest,
        actor_id=auth.subject_id,
        request_id=current_request_context().request_id,
    )


async def _read_limited_body(request: Request, limit: int) -> bytes:
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != "application/json":
        raise problem(
            status=415,
            code="MANIFEST_CONTENT_TYPE_INVALID",
            title="Unsupported content type",
            detail="Manifest requests require application/json.",
        )
    content_length = request.headers.get("content-length")
    if content_length is not None:
        try:
            declared = int(content_length)
        except ValueError as exc:
            raise problem(
                status=400,
                code="CONTENT_LENGTH_INVALID",
                title="Content-Length is invalid",
                detail="Content-Length must be a non-negative integer.",
            ) from exc
        if declared < 0 or declared > limit:
            raise problem(
                status=413,
                code="MANIFEST_TOO_LARGE",
                title="Manifest request is too large",
                detail=f"The request body must not exceed {limit} bytes.",
            )
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise problem(
                status=413,
                code="MANIFEST_TOO_LARGE",
                title="Manifest request is too large",
                detail=f"The request body must not exceed {limit} bytes.",
            )
    return bytes(body)


async def _read_limited_json(request: Request, limit: int) -> object:
    body = await _read_limited_body(request, limit)
    return decode_bounded_json(body, max_bytes=limit)


def _request_validation_problem(exc: ValidationError) -> Exception:
    return problem(
        status=422,
        code="REQUEST_VALIDATION_FAILED",
        title="Request validation failed",
        detail="The upload request does not satisfy the API contract.",
        details={"errors": exc.errors(include_url=False, include_input=False)[:50]},
    )


def _validate_request_model(
    model: type[_RequestModelT],
    payload: object,
) -> _RequestModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        raise _request_validation_problem(exc) from exc


def _authorize(auth: AuthContext, project_id: str, region_code: str) -> None:
    auth.require_role("uploader", "admin", project_id=project_id)
    ScopeGuard.require(auth, project_id, region_code)
    select_request_scope(project_id, region_code)


def _authorize_and_get(
    auth: AuthContext,
    project_id: str,
    region_code: str,
    session_id: str,
) -> UploadSession:
    _authorize(auth, project_id, region_code)
    return _scoped_session(project_id, region_code, session_id)


def _scoped_session(project_id: str, region_code: str, session_id: str) -> UploadSession:
    session = get_service().get_session(session_id)
    if session.project_id != project_id or session.region_code != region_code:
        raise problem(
            status=404,
            code="UPLOAD_SESSION_NOT_FOUND",
            title="Upload session not found",
            detail="The upload session does not exist in this scope.",
        )
    return session
