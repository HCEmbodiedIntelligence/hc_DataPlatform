from __future__ import annotations

from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, Field

from hc_data_platform.core.context import select_request_scope
from hc_data_platform.core.errors import problem
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.scope import ScopeGuard

from .models import (
    CompletedPart,
    PartAuthorization,
    RawObjectCommittedV1,
    RolloutManifestV1,
    UploadPart,
    UploadSession,
    UploadSessionGrant,
)
from .ports import InMemoryObjectStorage
from .service import UploadSessionService

router = APIRouter(prefix="/api/v1", tags=["ingest"])
_configured_service: UploadSessionService | None = None


class CreateUploadRequest(BaseModel):
    manifest: RolloutManifestV1
    part_numbers: list[int] = Field(default_factory=list, max_length=10_000)


class PartNumbersRequest(BaseModel):
    part_numbers: list[int] = Field(min_length=1, max_length=10_000)


class ResumeUploadRequest(BaseModel):
    part_numbers: list[int] = Field(default_factory=list, max_length=10_000)


class CompleteUploadRequest(BaseModel):
    parts: list[CompletedPart] = Field(min_length=1, max_length=10_000)


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
    "/projects/{project_id}/regions/{region_code}/upload-sessions",
    response_model=UploadSessionGrant,
    status_code=201,
)
def create_upload_session(
    project_id: str,
    region_code: str,
    request: CreateUploadRequest,
    auth: Auth,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
) -> UploadSessionGrant:
    _authorize(auth, project_id, region_code)
    if request.manifest.project_id != project_id:
        raise problem(
            status=409,
            code="PROJECT_PATH_MISMATCH",
            title="Project path mismatch",
            detail="The path project and manifest project must match.",
        )
    result = get_service().create_upload(
        manifest=request.manifest,
        region_code=region_code,
        idempotency_key=idempotency_key,
        part_numbers=request.part_numbers,
    )
    response.headers["Cache-Control"] = "no-store"
    return result


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
)
def renew_upload_authorizations(
    project_id: str,
    region_code: str,
    session_id: str,
    request: PartNumbersRequest,
    auth: Auth,
    response: Response,
) -> list[PartAuthorization]:
    _authorize_and_get(auth, project_id, region_code, session_id)
    result = get_service().renew_part_authorizations(session_id, request.part_numbers)
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


@router.post(
    "/projects/{project_id}/regions/{region_code}/upload-sessions/{session_id}:complete",
    response_model=UploadSession,
)
def complete_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    request: CompleteUploadRequest,
    auth: Auth,
) -> UploadSession:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().complete_upload(session_id, request.parts)


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
)
def resume_upload_session(
    project_id: str,
    region_code: str,
    session_id: str,
    request: ResumeUploadRequest,
    auth: Auth,
    response: Response,
) -> UploadSessionGrant:
    _authorize_and_get(auth, project_id, region_code, session_id)
    result = get_service().resume_upload(session_id, request.part_numbers)
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
)
def commit_manifest(
    project_id: str,
    region_code: str,
    session_id: str,
    manifest: RolloutManifestV1,
    auth: Auth,
) -> RawObjectCommittedV1:
    _authorize_and_get(auth, project_id, region_code, session_id)
    return get_service().commit_manifest(session_id=session_id, manifest=manifest)


def _authorize(auth: AuthContext, project_id: str, region_code: str) -> None:
    auth.require_role("uploader", "admin")
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
