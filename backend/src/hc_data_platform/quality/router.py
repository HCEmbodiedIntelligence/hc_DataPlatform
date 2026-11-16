"""Tenant-scoped historical QC execution evidence; configuration belongs to schemes."""

from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Response

from hc_data_platform.core.errors import problem
from hc_data_platform.security.http import VerifiedAuth, authorize_scope

from .models import ProblemLocation, ActionGapSegment, AutoQualityProblemListV1, AutoQualityProblemV1, QcReportV1, QualityProfileV1


class QualityRepository(Protocol):
    def put_profile(self, project_id: str, profile: QualityProfileV1) -> None: ...

    def get_profile(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None: ...

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> QcReportV1 | None: ...

    def problem_locations(self, project_id, region_code, digest, code, recording_id, rollout_id
                          ) -> list[ProblemLocation] | None: ...

    def action_gaps(self, project_id: str, region_code: str, recording_id: str,
                    report_sha256: str) -> list[ActionGapSegment] | None: ...

    def list_problem_reports(
        self, *, project_id: str, region_code: str, include_discarded: bool = False
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
    deprecated=True,
)
def create_quality_profile(
    project_id: str,
    profile: QualityProfileV1,
    response: Response,
    auth: VerifiedAuth,
) -> QualityProfileV1:
    _no_store(response)
    authorize_scope(auth, project_id, "upload.manage")
    raise problem(
        status=409,
        code="CAPTURE_SCHEME_REQUIRED",
        title="Quality rules belong to the capture scheme",
        detail="请在采集方案中配置质检规则并发布版本；不再单独创建项目 QC 配置。",
    )


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
    include_discarded: bool = False,
) -> AutoQualityProblemListV1:
    """Expose latest RISK/REJECT reports as read-only unified issue-center rows."""

    _no_store(response)
    authorize_scope(auth, project_id, "upload.read", region_code)
    items = _required_repository().list_problem_reports(
        **({"include_discarded": True} if include_discarded else {}),
        project_id=project_id,
        region_code=region_code,
    )
    return AutoQualityProblemListV1(items=items, total=len(items))


@router.get(
    "/projects/{project_id}/regions/{region_code}/continuous-recordings/{recording_id}/quality-reports/{report_sha256}/action-gaps",
    response_model=list[ActionGapSegment], operation_id="getRecordingActionGapSegments",
)
def get_action_gaps(project_id: str, region_code: str, recording_id: str, report_sha256: str,
                    response: Response, auth: VerifiedAuth):
    _no_store(response)
    authorize_scope(auth, project_id, "upload.read", region_code)
    result = _required_repository().action_gaps(project_id, region_code, recording_id, report_sha256)
    if result is None:
        raise problem(status=404, code="ACTION_GAPS_NOT_FOUND", title="未找到这份报告的问题片段",
                      detail="请刷新问题数据后重试。")
    return result


@router.get("/projects/{project_id}/regions/{region_code}/quality-problems/{digest}/locations",
            response_model=list[ProblemLocation], operation_id="getQualityProblemLocations")
def get_problem_locations(project_id: str, region_code: str, digest: str, code: str,
                          response: Response, auth: VerifiedAuth, recording_id: str | None = None,
                          rollout_id: str | None = None):
    _no_store(response)
    authorize_scope(auth, project_id, "upload.read", region_code)
    locations = _required_repository().problem_locations(project_id, region_code, digest, code,
                                                          recording_id, rollout_id)
    if locations is None:
        raise problem(status=404, code="QC_REPORT_NOT_FOUND", title="未找到对应的质检报告",
                      detail="请刷新问题列表后再打开。")
    return locations
