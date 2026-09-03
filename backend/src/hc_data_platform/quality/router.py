"""Tenant-scoped API for immutable quality profiles and reports."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import AutoQualityProblemListV1, AutoQualityProblemV1, QcReportV1, QualityProfileV1


class QualityRepository(Protocol):
    def put_profile(self, project_id: str, profile: QualityProfileV1) -> None: ...

    def get_profile(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None: ...

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> QcReportV1 | None: ...

    def list_problem_reports(
        self, *, project_id: str, region_code: str
    ) -> tuple[AutoQualityProblemV1, ...]: ...


router = APIRouter(prefix="/api/v1", tags=["quality"])
_repository: QualityRepository | None = None


def configure_quality_repository(repository: QualityRepository) -> None:
    global _repository
    _repository = repository


def _required_repository() -> QualityRepository:
    if _repository is None:
        raise problem(
            status=503,
            code="QUALITY_REPOSITORY_UNAVAILABLE",
            title="Quality repository unavailable",
            detail="The quality profile and report repository is not configured.",
        )
    return _repository


def _no_store(response: Response) -> None:
    """Quality profiles and reports are tenant-scoped operational evidence."""

    response.headers["Cache-Control"] = "no-store"


@router.post(
    "/projects/{project_id}/quality-profiles",
    response_model=QualityProfileV1,
    status_code=201,
)
def create_quality_profile(
    project_id: str,
    profile: QualityProfileV1,
    response: Response,
    auth: VerifiedAuth,
) -> QualityProfileV1:
    _no_store(response)
    authorize_scope(auth, project_id, "upload.manage")
    _required_repository().put_profile(project_id, profile)
    return profile


@router.get(
    "/projects/{project_id}/quality-profiles/{profile_id}/versions/{profile_version}",
    response_model=QualityProfileV1,
)
def get_quality_profile(
    project_id: str,
    profile_id: str,
    profile_version: int,
    response: Response,
    auth: VerifiedAuth,
) -> QualityProfileV1:
    _no_store(response)
    authorize_scope(auth, project_id, "upload.read")
    profile = _required_repository().get_profile(project_id, profile_id, profile_version)
    if profile is None:
        raise problem(
            status=404,
            code="QUALITY_PROFILE_NOT_FOUND",
            title="Quality profile not found",
            detail="The requested immutable quality profile version does not exist.",
        )
    return profile


@router.get(
    "/projects/{project_id}/regions/{region_code}/rollouts/{rollout_id}/quality",
    response_model=QcReportV1,
)
def get_rollout_quality(
    project_id: str,
    region_code: str,
    rollout_id: str,
    response: Response,
    auth: VerifiedAuth,
) -> QcReportV1:
    _no_store(response)
    authorize_scope(auth, project_id, "upload.read", region_code)
    report = _required_repository().get_report(
        project_id=project_id, region_code=region_code, rollout_id=rollout_id
    )
    if report is None:
        raise problem(
            status=404,
            code="QUALITY_REPORT_NOT_FOUND",
            title="Quality report not found",
            detail="No persisted quality report exists for this rollout.",
        )
    return report


@router.get(
    "/projects/{project_id}/regions/{region_code}/quality-problems",
    response_model=AutoQualityProblemListV1,
    operation_id="listAutoQualityProblems",
)
def list_auto_quality_problems(
    project_id: str,
    region_code: str,
    response: Response,
    auth: VerifiedAuth,
) -> AutoQualityProblemListV1:
    """Expose latest RISK/REJECT reports as read-only unified issue-center rows."""

    _no_store(response)
    authorize_scope(auth, project_id, "upload.read", region_code)
    items = _required_repository().list_problem_reports(
        project_id=project_id,
        region_code=region_code,
    )
    return AutoQualityProblemListV1(items=items, total=len(items))
