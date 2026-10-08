"""Production PostgreSQL adapters for publication manifests and rollout readiness."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

from hc_data_platform.core.errors import problem

from .adapters import CatalogRolloutStateV1
from .models import DerivedStatus, PublishedDatasetManifestV1, QualityStatus
from .transactions import PublicationSideEffects

if TYPE_CHECKING:
    from psycopg import Cursor


class PostgresPublishedManifestRepository:
    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        side_effects: Callable[[Cursor[Any]], PublicationSideEffects],
    ) -> None:
        self._connection_factory = connection_factory
        self._side_effects = side_effects

    def create_immutable(self, manifest: PublishedDatasetManifestV1) -> PublishedDatasetManifestV1:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                effects = self._side_effects(cursor)
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
                effects.prepare_sources(manifest)
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
                effects.project_release(existing)
                self._finalize_episode_versions(cursor, existing)
                effects.notify_owner(manifest)
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
                if row is None and dataset_version.startswith("version_release_"):
                    # P05/P06 use a stable ID derived from the publication name.
                    # Resolve the same immutable publication without creating a second one.
                    cursor.execute(
                        """
                        SELECT manifest_json FROM publishing.dataset_versions
                        WHERE project_id = %s AND dataset_id = %s
                          AND 'version_release_' || substring(
                            encode(sha256(convert_to(dataset_version, 'UTF8')), 'hex'), 1, 32
                          ) = %s
                        """,
                        (project_id, dataset_id, dataset_version),
                    )
                    row = cursor.fetchone()
        finally:
            connection.close()
        if row is None:
            return None
        manifest = self._model(row[0])
        # Keep the requested public identity for export job URLs; the frozen hash,
        # source snapshot and publication assets still identify the original release.
        return manifest.model_copy(update={"dataset_version": dataset_version})

    @staticmethod
    def _model(value: object) -> PublishedDatasetManifestV1:
        if isinstance(value, str):
            return PublishedDatasetManifestV1.model_validate_json(value)
        return PublishedDatasetManifestV1.model_validate(value)

    @staticmethod
    def _finalize_episode_versions(cursor: Any, manifest: PublishedDatasetManifestV1) -> None:
        cursor.execute("SELECT to_regclass('publishing.episode_version_finalizations') IS NOT NULL")
        available = cursor.fetchone()
        if available is None or not bool(available[0]):
            return
        for rollout in manifest.rollouts:
            if rollout.annotation_task_id is None or rollout.annotation_submission_id is None:
                continue
            cursor.execute(
                """
                INSERT INTO publishing.episode_version_finalizations (
                    project_id, dataset_id, dataset_version, rollout_id,
                    annotation_task_id, annotation_submission_id, finalized_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (
                    manifest.project_id,
                    manifest.dataset_id,
                    manifest.dataset_version,
                    rollout.rollout_id,
                    rollout.annotation_task_id,
                    rollout.annotation_submission_id,
                    manifest.created_at,
                ),
            )


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
                           COALESCE(summary.status, 'PENDING'),
                           COALESCE(summary.profile_id, 'quality-not-run'),
                           COALESCE(summary.profile_version, 1),
                           attempt.manifest_json ->> 'profile_id',
                           (attempt.manifest_json ->> 'frequency_hz')::integer
                    FROM lance_rollout_lineage lineage
                    LEFT JOIN quality_rollout_summaries summary
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
                # Continuous recordings publish through the committed media/Lance
                # bundle receipt rather than aligned_fragment_attempts. Require
                # that independent receipt and its immutable QC before exposing
                # the rollout to publication; never infer readiness from upload.
                cursor.execute(
                    """
                    SELECT lineage.rollout_id,lineage.step_count,report.status,
                           report.report_document #>> '{quality_report,profile_id}',
                           (report.report_document #>> '{quality_report,profile_version}')::integer,
                           'continuous:' || lineage.schema_snapshot_id,
                           version.frequency_hz::integer
                      FROM lance_rollout_lineage lineage
                      JOIN ingest.recording_episode_processing episode
                        ON episode.organization_id=lineage.organization_id
                       AND episode.project_id=lineage.project_id
                       AND episode.recording_id || ':' || episode.episode_id=lineage.rollout_id
                       AND episode.dataset_id=lineage.dataset_id
                       AND episode.dataset_version=lineage.version_added
                       AND episode.status='READY'
                      JOIN ingest.recording_episode_qc_reports report
                        ON report.organization_id=episode.organization_id

                       AND report.project_id=episode.project_id
                       AND report.region_code=episode.region_code

                       AND report.recording_id=episode.recording_id
                       AND report.episode_id=episode.episode_id
                       AND report.report_id=episode.qc_report_id
                       AND report.report_document->>'source_sha256'=lineage.source_sha256
                      JOIN lance_dataset_versions version
                        ON version.organization_id=lineage.organization_id

                       AND version.project_id=lineage.project_id
                       AND version.dataset_id=lineage.dataset_id

                       AND version.version=lineage.version_added
                       AND version.lance_version=episode.lance_version
                     WHERE lineage.project_id=%s
                     AND lineage.dataset_id=%s
                     AND lineage.version_added<=%s
                       AND lineage.converter_version='continuous-mp4-sensor/1'
                       AND NOT EXISTS (SELECT 1 FROM aligned_fragment_attempts attempt
                           WHERE attempt.organization_id=lineage.organization_id

                             AND attempt.project_id=lineage.project_id
                             AND attempt.rollout_id=lineage.rollout_id
                             AND attempt.status='READY' AND attempt.manifest_json IS NOT NULL)
                     ORDER BY lineage.rollout_id
                    """,
                    (project_id, dataset_id, dataset_version),
                )
                rows.extend(cursor.fetchall())
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
