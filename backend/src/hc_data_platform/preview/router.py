from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Response
from fastapi.responses import Response as FastApiResponse

from hc_data_platform.core.context import current_request_context, select_request_scope
from hc_data_platform.core.errors import ProblemDetails, problem
from hc_data_platform.security.http import VerifiedAuth, authorize_read

from .audit import (
    InMemoryPreviewAuditRecorder,
    PreviewAuditRecorder,
    PreviewDescriptorAuditEvent,
)
from .memory import (
    HmacUrlSigner,
    InMemoryPreviewArtifactStore,
    InMemoryPreviewRepository,
)
from .models import (
    PreviewDescriptorV1,
    PreviewJobDescriptorV1,
    PreviewPendingDescriptorV1,
    PreviewRequestV1,
    PreviewScopeV1,
)
from .service import PreviewControlPlaneService

router = APIRouter(prefix="/api/v1/previews", tags=["preview"])

_service = PreviewControlPlaneService(
    repository=InMemoryPreviewRepository(),
    store=InMemoryPreviewArtifactStore(),
    signer=HmacUrlSigner(),
)
_audit_recorder: PreviewAuditRecorder = InMemoryPreviewAuditRecorder()


def configure_preview_service(service: PreviewControlPlaneService) -> None:
    """Install the API control plane. The type intentionally has no encoder port."""

    global _service
    _service = service


def configure_preview_audit_recorder(recorder: PreviewAuditRecorder) -> None:
    global _audit_recorder
    _audit_recorder = recorder


@router.post(
    "/sessions",
    response_model=None,
    status_code=201,
    responses={
        201: {
            "description": "Durable preview is READY",
            "model": PreviewDescriptorV1,
        },
        202: {
            "description": "Preview generation is queued or running",
            "headers": {
                "Retry-After": {
                    "description": "Seconds before bounded polling",
                    "schema": {"type": "integer", "minimum": 1, "maximum": 60},
                }
            },
            "model": PreviewPendingDescriptorV1,
        }
    },
)
async def create_preview_session(
    request: PreviewRequestV1,
    response: Response,
    auth: VerifiedAuth,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    region_code: Annotated[str, Header(alias="X-Region-Code")],
) -> PreviewDescriptorV1 | PreviewPendingDescriptorV1:
    authorize_read(auth, request.project_id, region_code, organization_id)
    scope = PreviewScopeV1(
        organization_id=organization_id,
        project_id=request.project_id,
        region_code=region_code,
    )
    result = await _service.create_session(scope, request)
    if isinstance(result, PreviewPendingDescriptorV1):
        response.status_code = 202
        response.headers["Retry-After"] = str(result.retry_after_seconds)
        return result
    _record_descriptor_issue(result, actor_id=auth.subject_id, operation="CREATED")
    return result


@router.get("/jobs/{job_id}", response_model=PreviewJobDescriptorV1)
def get_preview_job(
    job_id: str,
    auth: VerifiedAuth,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    region_code: Annotated[str, Header(alias="X-Region-Code")],
    project_id: str = Query(min_length=1),
) -> PreviewJobDescriptorV1:
    authorize_read(auth, project_id, region_code, organization_id)
    return _service.get_job(
        PreviewScopeV1(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        ),
        job_id,
    )


@router.get("/sessions/{session_id}", response_model=PreviewDescriptorV1)
def get_preview_session(
    session_id: str,
    auth: VerifiedAuth,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    region_code: Annotated[str, Header(alias="X-Region-Code")],
    project_id: str = Query(min_length=1),
) -> PreviewDescriptorV1:
    authorize_read(auth, project_id, region_code, organization_id)
    descriptor = _service.get_session(
        PreviewScopeV1(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        ),
        session_id,
    )
    _record_descriptor_issue(descriptor, actor_id=auth.subject_id, operation="REFRESHED")
    return descriptor


def _record_descriptor_issue(
    descriptor: PreviewDescriptorV1,
    *,
    actor_id: str,
    operation: Literal["CREATED", "REFRESHED"],
) -> None:
    context = current_request_context()
    _audit_recorder.append_descriptor_issue(
        PreviewDescriptorAuditEvent(
            project_id=descriptor.project_id,
            region_code=context.region_code,
            actor_id=actor_id,
            request_id=context.request_id,
            session_id=descriptor.session_id,
            dataset_id=descriptor.dataset_id,
            rollout_id=descriptor.rollout_id,
            camera_id=descriptor.camera_id,
            view_mode=descriptor.view_mode.value,
            operation=operation,
            grant_expires_at=descriptor.signed_url_expires_at,
        )
    )


@router.get(
    "/sessions/{session_id}/media/index.m3u8",
    response_class=FastApiResponse,
    responses={
        200: {
            "description": "Small signed playlist whose members are direct object-store URLs",
            "content": {"application/vnd.apple.mpegurl": {}},
        },
        403: {"model": ProblemDetails, "description": "Invalid or expired capability"},
        404: {"model": ProblemDetails, "description": "Preview session not found"},
    },
)
def get_preview_playlist(
    session_id: str,
    expires: int = Query(gt=0),
    sig: str = Query(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$"),
    organization_id: str = Query(min_length=1),
    project_id: str = Query(min_length=1),
    region_code: str = Query(min_length=1),
) -> FastApiResponse:
    # This endpoint is the bearer-to-HLS capability bridge. The scope values select
    # RLS before the session-bound HMAC is verified; changing any value cannot reveal
    # a row because the UUID session is tenant-local and the signature is session-bound.
    select_request_scope(
        project_id,
        region_code,
        organization_id=organization_id,
    )
    playlist = _service.signed_playlist(
        PreviewScopeV1(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
        ),
        session_id=session_id,
        expires=expires,
        signature=sig,
    )
    return FastApiResponse(
        content=playlist,
        media_type="application/vnd.apple.mpegurl",
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


def local_media_url(object_key: str) -> str:
    """Used only by the explicit local artifact-store adapter."""

    if not object_key.startswith("derived/previews/"):
        raise problem(
            status=404,
            code="PREVIEW_MEDIA_NOT_FOUND",
            title="Preview media not found",
            detail="The requested preview object does not exist.",
        )
    return f"/api/v1/previews/local-media/{quote(object_key, safe='')}"
