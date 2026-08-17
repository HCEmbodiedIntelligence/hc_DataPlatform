from __future__ import annotations

from fastapi import APIRouter

from hc_data_platform.security.http import VerifiedAuth, authorize_read

from .memory import (
    HmacUrlSigner,
    InMemoryExclusionReader,
    InMemoryMediaEncoder,
    InMemoryPreviewCache,
    InMemoryStepReader,
)
from .models import PreviewDescriptorV1, PreviewRequestV1
from .service import PreviewService

router = APIRouter(prefix="/api/v1/previews", tags=["preview"])

_service = PreviewService(
    step_reader=InMemoryStepReader(),
    exclusions=InMemoryExclusionReader(),
    encoder=InMemoryMediaEncoder(),
    cache=InMemoryPreviewCache(),
    signer=HmacUrlSigner(),
)


def configure_preview_service(service: PreviewService) -> None:
    """Application composition hook for real Lance, FFmpeg, cache and signer adapters."""

    global _service
    _service = service


@router.post("/sessions", response_model=PreviewDescriptorV1, status_code=201)
def create_preview_session(
    request: PreviewRequestV1,
    auth: VerifiedAuth,
) -> PreviewDescriptorV1:
    authorize_read(auth, request.project_id)
    return _service.create(request)


@router.get("/sessions/{session_id}", response_model=PreviewDescriptorV1)
def get_preview_session(session_id: str, auth: VerifiedAuth) -> PreviewDescriptorV1:
    descriptor = _service.get(session_id)
    authorize_read(auth, descriptor.project_id)
    return descriptor
