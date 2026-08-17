"""PostgreSQL index for attempt-isolated aligned fragment manifests."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from hc_data_platform.core.errors import problem

from .models import AlignedFragmentManifestV1


class PostgresAlignmentRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def put_ready_manifest(
        self,
        *,
        project_id: str,
        region_code: str,
        manifest: AlignedFragmentManifestV1,
    ) -> None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO aligned_fragment_attempts (
                        project_id, region_code, rollout_id, source_sha256,
                        converter_version, attempt_id, content_sha256, schema_sha256,
                        row_count, staging_uri, staging_format, status, manifest_json
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'READY', %s::jsonb
                    )
                    ON CONFLICT (
                        project_id, rollout_id, source_sha256, converter_version, attempt_id
                    ) DO UPDATE SET
                        content_sha256 = EXCLUDED.content_sha256,
                        schema_sha256 = EXCLUDED.schema_sha256,
                        row_count = EXCLUDED.row_count,
                        staging_uri = EXCLUDED.staging_uri,
                        staging_format = EXCLUDED.staging_format,
                        status = EXCLUDED.status,
                        manifest_json = EXCLUDED.manifest_json,
                        updated_at = now()
                    WHERE aligned_fragment_attempts.status = 'WRITING'
                    """,
                    (
                        project_id,
                        region_code,
                        manifest.rollout_id,
                        manifest.source_sha256,
                        manifest.converter_version,
                        manifest.attempt_id,
                        manifest.content_sha256,
                        manifest.schema_sha256,
                        manifest.row_count,
                        manifest.staging_uri,
                        manifest.staging_format,
                        manifest.model_dump_json(),
                    ),
                )
                cursor.execute(
                    """
                    SELECT manifest_json FROM aligned_fragment_attempts
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND source_sha256 = %s AND converter_version = %s AND attempt_id = %s
                    """,
                    (
                        project_id,
                        region_code,
                        manifest.rollout_id,
                        manifest.source_sha256,
                        manifest.converter_version,
                        manifest.attempt_id,
                    ),
                )
                row = cursor.fetchone()
                persisted = None if row is None else self._model(row[0])
                if persisted != manifest:
                    raise problem(
                        status=409,
                        code="ALIGNMENT_ATTEMPT_IMMUTABLE",
                        title="Alignment attempt is immutable",
                        detail="This alignment attempt already exists with different content.",
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_ready_manifest(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> AlignedFragmentManifestV1 | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT manifest_json FROM aligned_fragment_attempts
                    WHERE project_id = %s AND region_code = %s AND rollout_id = %s
                      AND status = 'READY' AND manifest_json IS NOT NULL
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (project_id, region_code, rollout_id),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        return self._model(row[0])

    @staticmethod
    def _model(value: object) -> AlignedFragmentManifestV1:
        if isinstance(value, str):
            return AlignedFragmentManifestV1.model_validate_json(value)
        return AlignedFragmentManifestV1.model_validate(value)
