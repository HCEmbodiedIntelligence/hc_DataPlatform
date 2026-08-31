from __future__ import annotations

import base64
import binascii
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Header
from fastapi.responses import FileResponse

from hc_data_platform.core.context import current_request_context
from hc_data_platform.security.http import VerifiedAuth, authorize_read

from .artifact_store import LocalAlignedMediaArtifactStore
from .audit import (
    AlignedMediaAuditRecorder,
    AlignedMediaAuthorizationAuditEvent,
    InMemoryAlignedMediaAuditRecorder,
)
from .memory import InMemoryAlignedMediaRepository
from .models import (
    AlignedMediaAuthorizationV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
)
from .ports import AlignedMediaArtifactStorePort
from .service import AlignedMediaAuthorizationService

router = APIRouter(prefix="/api/v1/aligned-media", tags=["aligned-media"])

_local_store = LocalAlignedMediaArtifactStore(Path("/tmp/hc-data/media"))
_service = AlignedMediaAuthorizationService(
    repository=InMemoryAlignedMediaRepository(),
    store=_local_store,
)
_store: AlignedMediaArtifactStorePort = _local_store
_audit: AlignedMediaAuditRecorder = InMemoryAlignedMediaAuditRecorder()


def configure_aligned_media(
    service: AlignedMediaAuthorizationService,
    store: AlignedMediaArtifactStorePort,
    audit: AlignedMediaAuditRecorder,
) -> None:
    global _service, _store, _audit
    _service = service
    _store = store
    _audit = audit


@router.post(
    "/authorize",
    operation_id="authorizeAlignedMedia",
    response_model=AlignedMediaAuthorizationV1,
    responses={
        409: {
            "description": "The aligned media artifact failed or is being abandoned.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        },
        425: {
            "description": "The artifact is absent, generating, or not yet dataset-committed.",
            "content": {
                "application/problem+json": {
                    "schema": {"$ref": "#/components/schemas/ProblemDetails"}
                }
            },
        },
    },
)
def authorize_aligned_media(
    selector: AlignedMediaSelectorV1,
    auth: VerifiedAuth,
    organization_id: Annotated[str, Header(alias="X-Organization-Id")],
    region_code: Annotated[str, Header(alias="X-Region-Code")],
) -> AlignedMediaAuthorizationV1:
    authorize_read(auth, selector.project_id, region_code, organization_id)
    descriptor = _service.authorize(
        AlignedMediaScopeV1(
            organization_id=organization_id,
            project_id=selector.project_id,
            region_code=region_code,
        ),
        selector,
    )
    context = current_request_context()
    _audit.append_authorization(
        AlignedMediaAuthorizationAuditEvent(
            project_id=selector.project_id,
            region_code=region_code,
            actor_id=auth.subject_id,
            request_id=context.request_id,
            artifact_id=descriptor.artifact_id,
            dataset_id=descriptor.dataset_id,
            rollout_id=descriptor.rollout_id,
            dataset_version=descriptor.dataset_version,
            camera_id=descriptor.camera_id,
            grant_expires_at=descriptor.expires_at,
        )
    )
    return descriptor


@router.get("/local/{object_key:path}", response_class=FileResponse, include_in_schema=False)
def get_local_aligned_media(object_key: str) -> FileResponse:
    resolver = getattr(_store, "resolve_local_object", None)
    try:
        padding = "=" * (-len(object_key) % 4)
        exact_key = base64.b64decode(
            f"{object_key}{padding}", altchars=b"-_", validate=True
        ).decode()
    except (binascii.Error, UnicodeDecodeError):
        exact_key = ""
    path = resolver(exact_key) if callable(resolver) and exact_key else None
    if path is None:
        from hc_data_platform.core.errors import problem

        raise problem(
            status=404,
            code="ALIGNED_MEDIA_NOT_FOUND",
            title="Aligned media not found",
            detail="The requested local media object does not exist.",
        )
    return FileResponse(path, media_type="video/mp4", filename="media.mp4")
