"""Read-only runtime API for ready alignment fragments."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_read

from .models import AlignedFragmentManifestV1


class AlignmentManifestReader(Protocol):
    def get_ready_manifest(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> AlignedFragmentManifestV1 | None: ...


router = APIRouter(prefix="/api/v1", tags=["alignment"])
_repository: AlignmentManifestReader | None = None


def configure_alignment_repository(repository: AlignmentManifestReader) -> None:
    global _repository
    _repository = repository


@router.get(
    "/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/alignment",
    response_model=AlignedFragmentManifestV1,
)
def get_rollout_alignment(
    project_id: str,
    region_code: str,
    rollout_id: str,
    auth: VerifiedAuth,
) -> AlignedFragmentManifestV1:
    authorize_read(auth, project_id, region_code)
    if _repository is None:
        raise problem(
            status=503,
            code="ALIGNMENT_REPOSITORY_UNAVAILABLE",
            title="Alignment repository unavailable",
            detail="The alignment manifest repository is not configured.",
        )
    manifest = _repository.get_ready_manifest(
        project_id=project_id, region_code=region_code, rollout_id=rollout_id
    )
    if manifest is None:
        raise problem(
            status=404,
            code="ALIGNMENT_NOT_FOUND",
            title="Alignment not found",
            detail="No ready aligned fragment exists for this rollout.",
        )
    return manifest
