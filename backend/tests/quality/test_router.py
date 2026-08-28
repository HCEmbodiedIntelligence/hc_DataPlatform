from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from hc_data_platform.core.errors import ProblemException
from hc_data_platform.quality import (
    FindingSeverity,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityProfileV1,
    QualityStatus,
)
from hc_data_platform.quality.models import AutoQualityProblemV1
from hc_data_platform.quality.router import configure_quality_repository, router
from hc_data_platform.security.auth import AuthContext


class _Repository:
    def __init__(
        self,
        report: QcReportV1,
        problems: tuple[AutoQualityProblemV1, ...] = (),
    ) -> None:
        self.report = report
        self.problems = problems

    def put_profile(self, project_id: str, profile: QualityProfileV1) -> None:
        del project_id, profile

    def get_profile(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None:
        del project_id, profile_id, profile_version
        return None

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> QcReportV1 | None:
        if (project_id, region_code, rollout_id) != ("p1", "cn-hz", "rollout-1"):
            return None
        return self.report

    def list_problem_reports(
        self, *, project_id: str, region_code: str
    ) -> tuple[AutoQualityProblemV1, ...]:
        if (project_id, region_code) != ("p1", "cn-hz"):
            return ()
        return self.problems


def _report() -> QcReportV1:
    profile = QualityProfileV1(profile_id="quality-router", required_topics=frozenset())
    return QcReportV1.build(
        rollout_id="rollout-1",
        source_sha256="a" * 64,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=profile.content_sha256(),
        engine_version=profile.engine_version,
        start_ns=0,
        end_ns=1,
        status=QualityStatus.PASS,
        topic_metrics=(),
        findings=(),
    )


def _client() -> TestClient:
    configure_quality_repository(_Repository(_report()))
    app = FastAPI()

    @app.exception_handler(ProblemException)
    async def handle_problem(_request: Request, exc: ProblemException) -> JSONResponse:
        return JSONResponse(exc.problem.model_dump(mode="json"), status_code=exc.problem.status)

    @app.middleware("http")
    async def install_auth(request: Request, call_next: Any) -> Any:
        request.state.auth_context = AuthContext(
            subject_id="quality-router-reader",
            project_ids=frozenset({"p1"}),
            region_codes=frozenset({"cn-hz"}),
            roles=frozenset({"uploader"}),
        )
        return await call_next(request)

    app.include_router(router)
    return TestClient(app)


def test_rollout_quality_is_tenant_scoped_and_not_cacheable() -> None:
    response = _client().get("/api/v1/projects/p1/regions/cn-hz/rollouts/rollout-1/quality")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["status"] == "PASS"


def test_failed_quality_report_is_projected_into_unified_problem_data() -> None:
    profile = QualityProfileV1(
        profile_id="quality-router",
        required_topics=frozenset(),
    )
    report = QcReportV1.build(
        rollout_id="rollout-1",
        source_sha256="b" * 64,
        profile_id=profile.profile_id,
        profile_version=profile.profile_version,
        profile_sha256=profile.content_sha256(),
        engine_version=profile.engine_version,
        start_ns=1_725_000_000_000_000_000,
        end_ns=1_725_000_001_000_000_000,
        status=QualityStatus.REJECT,
        topic_metrics=(),
        findings=(
            QcFinding(
                code=QualityCode.CONSECUTIVE_FRAMES_MISSING,
                severity=FindingSeverity.ERROR,
                message="front camera has consecutive missing frames",
                topic="/camera/front",
                start_ns=1_725_000_000_100_000_000,
                end_ns=1_725_000_000_300_000_000,
                observed=12,
                threshold=2,
            ),
        ),
    )
    problem_row = AutoQualityProblemV1.from_report(
        report,
        session_id="upload-session-1",
        data_package_id="data-package-1",
        updated_at=datetime(2026, 8, 26, tzinfo=timezone.utc),
    )
    client = _client()
    configure_quality_repository(_Repository(report, (problem_row,)))

    response = client.get("/api/v1/projects/p1/regions/cn-hz/quality-problems")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["source"] == "AUTO_QC"
    assert body["items"][0]["session_id"] == "upload-session-1"
    assert body["items"][0]["start_ns"] == "1725000000100000000"
