from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from urllib.parse import urlsplit
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.core.dbapi import normalize_postgres_dsn, psycopg_connection_factory
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.core.migrations import apply_migrations
from hc_data_platform.ingest.raw_sources import (
    CommittedRawSourceGraph,
    PostgresRawSourceRepository,
    RawIngestJob,
    RawIngestJobType,
    RawSource,
    RawSourceEpisode,
    RawSourceFormat,
)
from hc_data_platform.quality import (
    FindingSeverity,
    ImageObservation,
    QcFinding,
    QcReportV1,
    QualityCode,
    QualityEngine,
    QualityProfileV1,
    QualityStatus,
)
from hc_data_platform.quality.postgres import PostgresQualityRepository
from hc_data_platform.quality.reclassify import reclassify_scope

from .test_quality import _input

pytestmark = pytest.mark.integration


def test_policy_reclassification_is_scoped_idempotent_and_preserves_history(
    isolated_dsn: str,
) -> None:
    with psycopg.connect(isolated_dsn) as connection:
        connection.execute(
            """INSERT INTO registry.organization_projects
               (organization_id, project_id, display_name) VALUES ('org-a', 'project-a', 'QC')"""
        )
    token = bind_request_context(
        RequestContext(
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-test",
        )
    )
    try:
        connect = psycopg_connection_factory(isolated_dsn)
        repository = PostgresQualityRepository(connect)
        previous = QualityProfileV1(
            profile_id="policy",
            required_topics={"/camera/front"},
            engine_version="be06-qc/1",
        )
        repository.put_profile("project-a", previous)
        data = _input(
            images={
                "/camera/front": (
                    ImageObservation(timestamp_ns=0, luma_mean=0, fingerprint="dark"),
                )
            }
        )
        original = QualityEngine().evaluate(data, previous)
        assert original.status is QualityStatus.REJECT
        repository.put_report(project_id="project-a", region_code="cn-test", report=original)
        repository.put_report(project_id="project-a", region_code="cn-other", report=original)
        scope = dict(organization_id="org-a", project_id="project-a", region_code="cn-test")
        assert reclassify_scope(connect, **scope, apply=False) == {
            "applied": False,
            "reports": 1,
            "released": 1,
            "risks": 0,
            "profiles": 1,
        }
        assert (
            repository.get_report(project_id="project-a", region_code="cn-test", rollout_id="r1")
            == original
        )
        assert reclassify_scope(connect, **scope, apply=True)["released"] == 1
        updated = repository.get_report(
            project_id="project-a", region_code="cn-test", rollout_id="r1"
        )
        assert updated is not None and updated.status is QualityStatus.PASS
        assert updated.engine_version == "be06-qc/2" and updated.profile_version == 2
        assert (
            repository.get_report(project_id="project-a", region_code="cn-other", rollout_id="r1")
            == original
        )
        assert reclassify_scope(connect, **scope, apply=True)["reports"] == 0
        with connect() as connection:
            row = connection.execute(
                """SELECT report_json FROM qc_reports
                   WHERE project_id=%s AND region_code=%s AND report_sha256=%s""",
                ("project-a", "cn-test", original.content_sha256),
            ).fetchone()
            assert QcReportV1.model_validate(row[0]) == original
        future = previous.model_copy(update={"profile_version": 3, "engine_version": "be06-qc/2"})
        repository.put_profile("project-a", future)
        with pytest.raises(ProblemException) as conflict:
            repository.put_report(
                project_id="project-a",
                region_code="cn-test",
                report=QualityEngine().evaluate(data, future),
                expected_previous_report_sha256=original.content_sha256,
            )
        assert conflict.value.problem.code == "QUALITY_SUMMARY_CHANGED"
        assert (
            repository.get_report(project_id="project-a", region_code="cn-test", rollout_id="r1")
            == updated
        )
    finally:
        reset_request_context(token)


@pytest.fixture
def isolated_dsn() -> Iterator[str]:
    base_dsn = os.environ.get("HC_TEST_POSTGRES_DSN")
    if not base_dsn:
        pytest.skip("HC_TEST_POSTGRES_DSN is not set")
    base_dsn = normalize_postgres_dsn(base_dsn)
    database = f"hc_quality_raw_{uuid4().hex[:12]}"
    with psycopg.connect(base_dsn, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
    dsn = urlsplit(base_dsn)._replace(path=f"/{database}").geturl()
    try:
        asyncio.run(apply_migrations(dsn))
        yield dsn
    finally:
        with psycopg.connect(base_dsn, autocommit=True) as admin:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(database)))


def test_problem_sources_resolve_native_episodes_within_the_report_scope(isolated_dsn: str) -> None:
    with psycopg.connect(isolated_dsn) as connection:
        connection.execute(
            """INSERT INTO registry.organization_projects
               (organization_id, project_id, display_name)
               VALUES ('org-a', 'project-a', 'Raw test A'),
                      ('org-b', 'project-a', 'Raw test B')"""
        )
    token = bind_request_context(
        RequestContext(
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-test",
            subject_id="quality-test",
            request_id="quality-raw-test",
        )
    )
    try:
        connect = psycopg_connection_factory(isolated_dsn)
        raw_sources = PostgresRawSourceRepository(connect)
        for organization, region, import_id in (
            ("org-a", "cn-test", "native-a"),
            ("org-b", "cn-test", "other-organization"),
            ("org-a", "cn-other", "other-region"),
        ):
            scope = dict(organization_id=organization, project_id="project-a", region_code=region)
            raw_sources.register_committed(
                CommittedRawSourceGraph(
                    source=RawSource(
                        **scope,
                        raw_source_id=import_id,
                        upload_id=import_id,
                        source_format=RawSourceFormat.LEROBOT_V3,
                        source_format_version="v3.0",
                        manifest_key=f"raw/{import_id}/manifest.json",
                        storage_prefix=f"raw/{import_id}",
                        content_hash="a" * 64,
                        file_count=1,
                        total_bytes=1024,
                    ),
                    episodes=tuple(
                        RawSourceEpisode(
                            **scope,
                            raw_source_id=import_id,
                            episode_id=f"episode-{index}",
                            source_episode_index=index,
                        )
                        for index in (0, 7)
                    ),
                    job=RawIngestJob(
                        **scope,
                        raw_source_id=import_id,
                        job_id=f"job-{import_id}",
                        job_type=RawIngestJobType.LEROBOT_IMPORT,
                        adapter_name="lerobot_v3",
                    ),
                )
            )
        repository = PostgresQualityRepository(connect)
        profile = QualityProfileV1(profile_id="quality-raw", required_topics=frozenset())
        repository.put_profile("project-a", profile)
        for rollout_id in ("episode-0", "episode-7", "no-source"):
            repository.put_report(
                project_id="project-a",
                region_code="cn-test",
                report=QcReportV1.build(
                    rollout_id=rollout_id,
                    source_sha256="a" * 64,
                    profile_id=profile.profile_id,
                    profile_version=profile.profile_version,
                    profile_sha256=profile.content_sha256(),
                    engine_version=profile.engine_version,
                    start_ns=0,
                    end_ns=100,
                    status=QualityStatus.REJECT,
                    topic_metrics=(),
                    findings=(
                        QcFinding(
                            code=QualityCode.CONSECUTIVE_FRAMES_MISSING,
                            severity=FindingSeverity.ERROR,
                            message="Missing frames",
                            topic="camera/front",
                            start_ns=0,
                            end_ns=100,
                            observed=12,
                            threshold=2,
                        ),
                    ),
                ),
            )
        rows = {
            row.rollout_id: row
            for row in repository.list_problem_reports(
                project_id="project-a",
                region_code="cn-test",
            )
        }
        assert set(rows) == {"episode-0", "episode-7", "no-source"}
        for index in (0, 7):
            row = rows[f"episode-{index}"]
            assert row.session_id is None
            assert row.source_import_id == "native-a"
            assert row.source_episode_index == index
            assert row.data_package_id == row.rollout_id
        assert rows["no-source"].source_import_id is None
        assert rows["no-source"].source_episode_index is None
        assert repository.list_problem_reports(project_id="project-a", region_code="cn-other") == ()
    finally:
        reset_request_context(token)
