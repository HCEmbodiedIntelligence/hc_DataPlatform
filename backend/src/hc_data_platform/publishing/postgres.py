"""Production PostgreSQL adapters for publication manifests and rollout readiness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from hc_data_platform.core.errors import problem

from .adapters import CatalogRolloutStateV1
from .models import DerivedStatus, PublishedDatasetManifestV1, QualityStatus


class PostgresPublishedManifestRepository:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def create_immutable(self, manifest: PublishedDatasetManifestV1) -> PublishedDatasetManifestV1:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO publishing.dataset_versions (
                        project_id, dataset_id, dataset_version, base_lance_version,
                        content_hash, manifest_json, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (project_id, dataset_id, dataset_version) DO NOTHING
                    """,
                    (
                        manifest.project_id,
                        manifest.dataset_id,
                        manifest.dataset_version,
                        manifest.base_lance_version,
                        manifest.content_hash,
                        manifest.model_dump_json(),
                        manifest.created_at,
                    ),
                )
                cursor.execute(
                    """
                    SELECT manifest_json FROM publishing.dataset_versions
                    WHERE project_id = %s AND dataset_id = %s AND dataset_version = %s
                    """,
                    (manifest.project_id, manifest.dataset_id, manifest.dataset_version),
                )
                row = cursor.fetchone()
                existing = None if row is None else self._model(row[0])
                if existing is None or existing.content_hash != manifest.content_hash:
                    raise problem(
                        status=409,
                        code="DATASET_VERSION_IMMUTABLE",
                        title="Dataset version is immutable",
                        detail="A different manifest is already published under this version.",
                    )
                cursor.execute(
                    """
                    SELECT to_regprocedure(
                        'publishing.materialize_rollout_publication_lineage(text,text,text)'
                    ) IS NOT NULL
                    """
                )
                availability = cursor.fetchone()
                if availability is None or not bool(availability[0]):
                    raise problem(
                        status=503,
                        code="PUBLICATION_REGION_LINEAGE_UNAVAILABLE",
                        title="Publication region lineage unavailable",
                        detail="The rollout publication lineage migration is not available.",
                        retryable=True,
                    )
                cursor.execute(
                    """
                    SELECT expected_count, resolved_count
                    FROM publishing.materialize_rollout_publication_lineage(%s, %s, %s)
                    """,
                    (manifest.project_id, manifest.dataset_id, manifest.dataset_version),
                )
                lineage_counts = cursor.fetchone()
                expected = len(manifest.rollouts)
                if (
                    lineage_counts is None
                    or int(lineage_counts[0]) != expected
                    or int(lineage_counts[1]) != expected
                ):
                    raise problem(
                        status=409,
                        code="PUBLICATION_REGION_LINEAGE_UNRESOLVED",
                        title="Publication region lineage unresolved",
                        detail=(
                            "Every published rollout must resolve to one exact persisted region."
                        ),
                    )
            connection.commit()
            return existing
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get(
        self, *, project_id: str, dataset_id: str, dataset_version: str
    ) -> PublishedDatasetManifestV1 | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT manifest_json FROM publishing.dataset_versions
                    WHERE project_id = %s AND dataset_id = %s AND dataset_version = %s
                    """,
                    (project_id, dataset_id, dataset_version),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        return None if row is None else self._model(row[0])

    @staticmethod
    def _model(value: object) -> PublishedDatasetManifestV1:
        if isinstance(value, str):
            return PublishedDatasetManifestV1.model_validate_json(value)
        return PublishedDatasetManifestV1.model_validate(value)


class PostgresAnnotationTaskLocator:
    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def find_task_id(
        self,
        *,
        project_id: str,
        dataset_id: str,
        dataset_version: int,
        rollout_id: str,
    ) -> str | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT task_id FROM annotation.annotation_tasks
                    WHERE project_id = %s AND dataset_id = %s
                      AND dataset_version = %s AND rollout_id = %s
                    """,
                    (project_id, dataset_id, dataset_version, rollout_id),
                )
                row = cursor.fetchone()
        finally:
            connection.close()
        return None if row is None else str(row[0])


class PostgresCatalogRolloutState:
    """Join durable Lance lineage with the latest QC and alignment readiness facts."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def list_states(
        self, *, project_id: str, dataset_id: str, dataset_version: int
    ) -> Sequence[CatalogRolloutStateV1]:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT lineage.rollout_id,
                           lineage.step_count,
                           summary.status,
                           summary.profile_id,
                           summary.profile_version,
                           attempt.manifest_json ->> 'profile_id',
                           (attempt.manifest_json ->> 'frequency_hz')::integer
                    FROM lance_rollout_lineage lineage
                    JOIN quality_rollout_summaries summary
                      ON summary.project_id = lineage.project_id
                     AND summary.rollout_id = lineage.rollout_id
                    JOIN LATERAL (
                        SELECT manifest_json
                        FROM aligned_fragment_attempts candidate
                        WHERE candidate.project_id = lineage.project_id
                          AND candidate.rollout_id = lineage.rollout_id
                          AND candidate.status = 'READY'
                          AND candidate.manifest_json IS NOT NULL
                        ORDER BY candidate.updated_at DESC LIMIT 1
                    ) attempt ON true
                    WHERE lineage.project_id = %s AND lineage.dataset_id = %s
                      AND lineage.version_added <= %s
                    ORDER BY lineage.rollout_id
                    """,
                    (project_id, dataset_id, dataset_version),
                )
                rows = cursor.fetchall()
        finally:
            connection.close()
        return tuple(
            CatalogRolloutStateV1(
                project_id=project_id,
                dataset_id=dataset_id,
                dataset_version=dataset_version,
                rollout_id=str(row[0]),
                total_steps=int(row[1]),
                quality_status=QualityStatus(str(row[2])),
                derived_status=DerivedStatus.DERIVED_READY,
                quality_profile_version=f"{row[3]}:v{row[4]}",
                alignment_profile_version=str(row[5]),
                alignment_frequency_hz=int(row[6]),
            )
            for row in rows
        )
