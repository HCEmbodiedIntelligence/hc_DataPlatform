"""Read-only runtime API for immutable MCAP verification reports."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import RawVerificationReportV1


class VerificationReportReader(Protocol):
    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> RawVerificationReportV1 | None: ...


router = APIRouter(prefix="/api/v1", tags=["verification"])
_repository: VerificationReportReader | None = None


def configure_verification_repository(repository: VerificationReportReader) -> None:
    global _repository
    _repository = repository


@router.get(
    "/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/raw-verification",
    response_model=RawVerificationReportV1,
)
def get_raw_verification(
    project_id: str,
    region_code: str,
    rollout_id: str,
    auth: VerifiedAuth,
) -> RawVerificationReportV1:
    authorize_scope(auth, project_id, "upload.read", region_code)
    if _repository is None:
        raise problem(
            status=503,
            code="VERIFICATION_REPOSITORY_UNAVAILABLE",
            title="Verification repository unavailable",
            detail="The verification report repository is not configured.",
        )
    report = _repository.get_report(
        project_id=project_id, region_code=region_code, rollout_id=rollout_id
    )
    if report is None:
        raise problem(
            status=404,
            code="RAW_VERIFICATION_NOT_FOUND",
            title="Raw verification not found",
            detail="No immutable verification report exists for this rollout.",
        )
    return report
