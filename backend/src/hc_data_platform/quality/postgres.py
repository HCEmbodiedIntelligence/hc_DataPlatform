"""PostgreSQL quality profile, immutable report, and latest-summary adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.core.errors import problem

from .models import QcReportV1, QualityProfileV1, QualitySummaryV1


class PostgresQualityRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_profile(self, project_id: str, profile: QualityProfileV1) -> None:
        connection = self._connection_factory()
        digest = profile.content_sha256()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO quality_profiles (
                        project_id, profile_id, profile_version, schema_version,
                        profile_sha256, profile_json
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (project_id, profile_id, profile_version) DO NOTHING
                    """,
                    (
                        project_id,
                        profile.profile_id,
                        profile.profile_version,
                        profile.schema_version,
                        digest,
                        profile.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT profile_sha256 FROM quality_profiles
                    WHERE project_id = %s AND profile_id = %s AND profile_version = %s
                    """,
                    (project_id, profile.profile_id, profile.profile_version),
                )
                row = cursor.fetchone()
                if row is None or str(row[0]) != digest:
                    raise problem(
                        status=409,
                        code="QUALITY_PROFILE_IMMUTABLE",
                        title="Quality profile is immutable",
                        detail="The profile version already exists with different content.",
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_profile(
        self, project_id: str, profile_id: str, profile_version: int
    ) -> QualityProfileV1 | None:
        row = self._fetchone(
            """
            SELECT profile_json FROM quality_profiles
            WHERE project_id = %s AND profile_id = %s AND profile_version = %s
            """,
            (project_id, profile_id, profile_version),
        )
        return None if row is None else QualityProfileV1.model_validate(row[0])

    def put_report(self, *, project_id: str, region_code: str, report: QcReportV1) -> None:
        summary = QualitySummaryV1.from_report(report)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO qc_reports (
                        report_sha256, project_id, region_code, rollout_id,
                        source_sha256, profile_id, profile_version, profile_sha256,
                        engine_version, schema_version, status, report_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb
                    ) ON CONFLICT (
                        project_id, region_code, rollout_id, source_sha256,
                        profile_id, profile_version, engine_version
                    ) DO NOTHING
                    """,
                    (
                        report.content_sha256,
                        project_id,
                        region_code,
                        report.rollout_id,
                        report.source_sha256,
                        report.profile_id,
                        report.profile_version,
                        report.profile_sha256,
                        report.engine_version,
                        report.schema_version,
                        report.status.value,
                        report.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT report_json FROM qc_reports
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND source_sha256 = %s AND profile_id = %s
                      AND profile_version = %s AND engine_version = %s
                    """,
                    (
                        project_id,
                        region_code,
                        report.rollout_id,
                        report.source_sha256,
                        report.profile_id,
                        report.profile_version,
                        report.engine_version,
                    ),
                )
                row = cursor.fetchone()
                if row is None or self._report(row[0]) != report:
                    raise problem(
                        status=409,
                        code="QUALITY_REPORT_IMMUTABLE",
                        title="Quality report is immutable",
                        detail="This evaluation identity already has different report content.",
                    )
                cursor.execute(
                    """
                    INSERT INTO quality_rollout_summaries (
                        project_id, region_code, rollout_id, source_sha256,
                        profile_id, profile_version, engine_version, status, report_sha256
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (project_id, region_code, rollout_id) DO UPDATE SET
                        source_sha256 = EXCLUDED.source_sha256,
                        profile_id = EXCLUDED.profile_id,
                        profile_version = EXCLUDED.profile_version,
                        engine_version = EXCLUDED.engine_version,
                        status = EXCLUDED.status,
                        report_sha256 = EXCLUDED.report_sha256,
                        updated_at = now()
                    """,
                    (
                        project_id,
                        region_code,
                        summary.rollout_id,
                        summary.source_sha256,
                        summary.profile_id,
                        summary.profile_version,
                        summary.engine_version,
                        summary.status.value,
                        summary.report_sha256,
                    ),
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> QcReportV1 | None:
        row = self._fetchone(
            """
            SELECT report.report_json
            FROM quality_rollout_summaries summary
            JOIN qc_reports report
              ON report.project_id = summary.project_id
             AND report.region_code = summary.region_code
             AND report.report_sha256 = summary.report_sha256
            WHERE summary.project_id = %s AND summary.region_code = %s
              AND summary.rollout_id = %s
            """,
            (project_id, region_code, rollout_id),
        )
        return None if row is None else self._report(row[0])

    def _fetchone(self, query: str, params: tuple[object, ...]) -> Any | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(query, params)
                return cursor.fetchone()
        finally:
            connection.close()

    @staticmethod
    def _report(value: object) -> QcReportV1:
        if isinstance(value, str):
            return QcReportV1.model_validate_json(value)
        return QcReportV1.model_validate(value)
