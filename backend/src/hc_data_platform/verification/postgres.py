"""PostgreSQL persistence for immutable verification reports."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.core.errors import problem

from .models import RawVerificationReportV1


class PostgresVerificationRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_report(
        self, *, project_id: str, region_code: str, report: RawVerificationReportV1
    ) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO raw_verification_reports (
                        report_sha256, project_id, region_code, rollout_id,
                        source_sha256, object_key, status, report_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
                    ON CONFLICT (
                        project_id, region_code, rollout_id, source_sha256
                    ) DO NOTHING
                    """,
                    (
                        report.content_sha256,
                        project_id,
                        region_code,
                        report.rollout_id,
                        report.source_sha256,
                        report.object_key,
                        report.status.value,
                        report.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT report_json FROM raw_verification_reports
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND source_sha256 = %s
                    """,
                    (project_id, region_code, report.rollout_id, report.source_sha256),
                )
                row = cursor.fetchone()
                if row is None or self._model(row[0]) != report:
                    raise problem(
                        status=409,
                        code="RAW_VERIFICATION_IMMUTABLE",
                        title="Raw verification is immutable",
                        detail="This rollout and source hash already identify another report.",
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_report(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> RawVerificationReportV1 | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT report_json FROM raw_verification_reports
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                    ORDER BY created_at DESC LIMIT 1
                    """,
                    (project_id, region_code, rollout_id),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        return None if row is None else self._model(row[0])

    @staticmethod
    def _model(value: object) -> RawVerificationReportV1:
        if isinstance(value, str):
            return RawVerificationReportV1.model_validate_json(value)
        return RawVerificationReportV1.model_validate(value)
