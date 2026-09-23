"""Production PostgreSQL adapters for publication manifests and rollout readiness."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Sequence
from typing import Any
from uuid import UUID, uuid4

from hc_data_platform.core.context import current_request_context
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
                from hc_data_platform.continuous_recordings.lineage import materialize_rollout

                for rollout in manifest.rollouts:
                    materialize_rollout(
                        cursor,
                        project_id=manifest.project_id,
                        dataset_id=manifest.dataset_id,
                        rollout_id=rollout.rollout_id,
                        source_sha256=rollout.source_mcap_sha256,
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
                self._materialize_dataset_release(cursor, existing)
                self._finalize_episode_versions(cursor, existing)
                self._notify_dataset_owner(cursor, manifest)
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
    def _release_version_id(dataset_version: str) -> str:
        digest = hashlib.sha256(dataset_version.encode("utf-8")).hexdigest()[:32]
        return f"version_release_{digest}"

    @classmethod
    def _materialize_dataset_release(
        cls,
        cursor: Any,
        manifest: PublishedDatasetManifestV1,
    ) -> None:
        """Create the P06 business projection without copying Lance data.

        The child rows are small metadata projections. Their content references
        the same immutable Lance snapshot selected by ``base_lance_version``;
        publishing therefore adds no second copy of the aligned data objects.
        """

        cursor.execute("SELECT to_regclass('dataset_registry.dataset_versions') IS NOT NULL")
        registry_available = cursor.fetchone()
        if registry_available is None or not bool(registry_available[0]):
            # The lower-level publication contract is also used by isolated
            # workers that intentionally do not install the P05/P06 projection.
            return

        base_token = manifest.base_lance_version
        if base_token.startswith("version_lance_"):
            source_version_id = base_token
        else:
            normalized = base_token.removeprefix("lance-v").removeprefix("v")
            source_version_id = f"version_lance_{normalized}"
        release_version_id = cls._release_version_id(manifest.dataset_version)
        cursor.execute(
            """
            SELECT organization_id, region_code, version_document
            FROM dataset_registry.dataset_versions
            WHERE project_id = %s AND dataset_id = %s AND version_id = %s
              AND version_scope = 'INTERNAL'
            ORDER BY organization_id, region_code
            """,
            (manifest.project_id, manifest.dataset_id, source_version_id),
        )
        sources = cursor.fetchall()
        if not sources:
            raise problem(
                status=409,
                code="PUBLICATION_BASE_PROJECTION_MISSING",
                title="Publication base snapshot is unavailable",
                detail="The selected Lance snapshot has no internal Dataset projection.",
                details={"base_lance_version": manifest.base_lance_version},
            )

        publication_manifest_id = f"publication-{manifest.content_hash[:32]}"
        for organization_id, region_code, raw_document in sources:
            document = (
                json.loads(raw_document) if isinstance(raw_document, str) else dict(raw_document)
            )
            document.update(
                {
                    "version_id": release_version_id,
                    "display_version": manifest.dataset_version,
                    "created_at": manifest.created_at.isoformat(),
                    "published_at": manifest.created_at.isoformat(),
                    "version_token": f"publication:{manifest.content_hash}",
                }
            )
            summary = dict(document["manifest"])
            summary.update(
                {
                    "manifest_id": publication_manifest_id,
                    "sha256": manifest.content_hash,
                    "entry_count": str(len(manifest.rollouts)),
                }
            )
            document["manifest"] = summary
            cursor.execute(
                """
                INSERT INTO dataset_registry.dataset_versions (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    display_version, version_kind, version_status, created_at, published_at,
                    version_document, version_scope
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, 'READY', %s, %s, %s::jsonb,
                    'DATASET_RELEASE'
                )
                ON CONFLICT (
                    organization_id, project_id, region_code, dataset_id, version_id
                ) DO NOTHING
                """,
                (
                    organization_id,
                    manifest.project_id,
                    region_code,
                    manifest.dataset_id,
                    release_version_id,
                    manifest.dataset_version,
                    str(document["kind"]),
                    manifest.created_at,
                    manifest.created_at,
                    json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            cls._copy_release_children(
                cursor,
                organization_id=str(organization_id),
                project_id=manifest.project_id,
                region_code=str(region_code),
                dataset_id=manifest.dataset_id,
                source_version_id=source_version_id,
                release_version_id=release_version_id,
                publication_manifest_id=publication_manifest_id,
                publication_hash=manifest.content_hash,
            )
            cls._promote_dataset_current_release(
                cursor,
                organization_id=str(organization_id),
                project_id=manifest.project_id,
                region_code=str(region_code),
                dataset_id=manifest.dataset_id,
                release_version_id=release_version_id,
                display_version=manifest.dataset_version,
                version_kind=str(document["kind"]),
                published_at=manifest.created_at,
                manifest_sha256=manifest.content_hash,
            )

    @staticmethod
    def _promote_dataset_current_release(
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        release_version_id: str,
        display_version: str,
        version_kind: str,
        published_at: Any,
        manifest_sha256: str,
    ) -> None:
        """Point the dataset summary at the newest explicit business release."""

        cursor.execute(
            """
            SELECT dataset_document, version, updated_at, activity_at
            FROM dataset_registry.datasets
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s
            FOR UPDATE
            """,
            (organization_id, project_id, region_code, dataset_id),
        )
        row = cursor.fetchone()
        if row is None:
            return
        document = json.loads(row[0]) if isinstance(row[0], str) else dict(row[0])
        current = document.get("current_ready_version")
        if isinstance(current, dict) and current.get("version_id") == release_version_id:
            return
        if isinstance(current, dict) and current.get("version_id"):
            cursor.execute(
                """
                SELECT published_at
                FROM dataset_registry.dataset_versions
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND dataset_id = %s AND version_id = %s
                  AND version_scope = 'DATASET_RELEASE'
                """,
                (
                    organization_id,
                    project_id,
                    region_code,
                    dataset_id,
                    str(current["version_id"]),
                ),
            )
            current_row = cursor.fetchone()
            if current_row is not None and current_row[0] > published_at:
                return

        next_version = int(row[1]) + 1
        effective_updated_at = max(row[2], published_at)
        effective_activity_at = max(row[3], published_at)
        document.update(
            {
                "current_ready_version": {
                    "version_id": release_version_id,
                    "display_version": display_version,
                    "kind": version_kind,
                    "status": "READY",
                    "published_at": published_at.isoformat(),
                    "manifest_sha256": manifest_sha256,
                },
                "updated_at": effective_updated_at.isoformat(),
                "activity_at": effective_activity_at.isoformat(),
                "etag": f'"v{next_version}"',
            }
        )
        cursor.execute(
            """
            UPDATE dataset_registry.datasets
            SET version = %s, dataset_document = %s::jsonb,
                updated_at = %s, activity_at = %s
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s
            """,
            (
                next_version,
                json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                effective_updated_at,
                effective_activity_at,
                organization_id,
                project_id,
                region_code,
                dataset_id,
            ),
        )

    @staticmethod
    def _copy_release_children(
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        dataset_id: str,
        source_version_id: str,
        release_version_id: str,
        publication_manifest_id: str,
        publication_hash: str,
    ) -> None:
        identity = (
            release_version_id,
            organization_id,
            project_id,
            region_code,
            dataset_id,
            source_version_id,
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_content_projections (
                organization_id, project_id, region_code, dataset_id, version_id,
                content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
                operational_revision, content_document
            )
            SELECT organization_id, project_id, region_code, dataset_id, %s,
                   content_snapshot_id, content_snapshot_hash, %s, %s,
                   'publication:' || %s,
                   jsonb_set(
                     jsonb_set(
                       jsonb_set(content_document, '{version_id}', to_jsonb(%s::text)),
                       '{manifest,manifest_id}', to_jsonb(%s::text)
                     ),
                     '{manifest,sha256}', to_jsonb(%s::text)
                   )
            FROM dataset_registry.dataset_version_content_projections
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
              AND dataset_id = %s AND version_id = %s
            ON CONFLICT DO NOTHING
            """,
            (
                release_version_id,
                publication_manifest_id,
                publication_hash,
                publication_hash,
                release_version_id,
                publication_manifest_id,
                publication_hash,
                organization_id,
                project_id,
                region_code,
                dataset_id,
                source_version_id,
            ),
        )
        copies = (
            (
                "dataset_version_episodes",
                "episode_id, storage_region_code, revision_id, ordinal, started_at, "
                "started_at_ns, included, success_state, task, robot_id, review_status, "
                "review_finding_count, has_finding, change_type",
                "episode_document",
            ),
            (
                "dataset_version_episode_revisions",
                "episode_id, revision_id, ordinal",
                "revision_document",
            ),
            (
                "dataset_version_schema_summaries",
                "schema_snapshot_id, channel_count",
                "schema_document",
            ),
            (
                "dataset_version_schema_details",
                "schema_snapshot_id, channel_count",
                "schema_document",
            ),
            (
                "dataset_version_source_provenance",
                "provenance_id, storage_region_code, upload_id, source_id, source_display_name, "
                "source_manifest_id, registered_at",
                "provenance_document",
            ),
            (
                "dataset_version_capacity_facts",
                "capacity_state, calculated_at",
                "capacity_document",
            ),
            (
                "dataset_version_manifest_entries",
                "entry_id, episode_id, revision_id, entry_role, size_bytes",
                "entry_document",
            ),
            (
                "dataset_version_required_storage",
                "object_id, object_role, size_bytes, safe_locator",
                "storage_document",
            ),
        )
        for table, columns, document_column in copies:
            cursor.execute(
                f"""
                INSERT INTO dataset_registry.{table} (
                    organization_id, project_id, region_code, dataset_id, version_id,
                    {columns}, {document_column}
                )
                SELECT organization_id, project_id, region_code, dataset_id, %s,
                       {columns},
                       jsonb_set({document_column}, '{{version_id}}', to_jsonb(%s::text))
                FROM dataset_registry.{table}
                WHERE organization_id = %s AND project_id = %s AND region_code = %s
                  AND dataset_id = %s AND version_id = %s
                ON CONFLICT DO NOTHING
                """,
                (release_version_id, *identity),
            )

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

    @staticmethod
    def _notify_dataset_owner(cursor: Any, manifest: PublishedDatasetManifestV1) -> None:
        """Persist the owner inbox fact in the publication transaction.

        Publication can also be invoked by project-only automation against the lower-level
        publishing contract.  Without an exact organization and region there is no safe way
        to select one product dataset owner, so those invocations remain auditable but do not
        manufacture an account notification. Owner identifiers that are not exact account
        UUIDs are rejected instead of being guessed into account identities.
        """

        context = current_request_context()
        if context.organization_id is None or context.region_code is None:
            return
        cursor.execute(
            """
            SELECT owner_id
            FROM dataset_registry.datasets
            WHERE organization_id = %s
              AND project_id = %s
              AND region_code = %s
              AND dataset_id = %s
            """,
            (
                context.organization_id,
                manifest.project_id,
                context.region_code,
                manifest.dataset_id,
            ),
        )
        row = cursor.fetchone()
        if row is None:
            return
        try:
            recipient_id = UUID(str(row[0]))
        except ValueError:
            return

        resource_id = f"{manifest.dataset_id}:{manifest.dataset_version}"
        if len(resource_id) > 512:
            resource_id = f"sha256:{manifest.content_hash}"
        event_identity = "\0".join(
            (
                manifest.project_id,
                manifest.dataset_id,
                manifest.dataset_version,
                manifest.content_hash,
            )
        )
        event_key = (
            f"dataset-publication:v1:{hashlib.sha256(event_identity.encode('utf-8')).hexdigest()}"
        )
        cursor.execute("SELECT set_config('app.subject_id', %s, true)", (str(recipient_id),))
        try:
            cursor.execute(
                """
                INSERT INTO access_control.account_notifications (
                    notification_id, recipient_id, kind, organization_id, project_id,
                    access_request_id, resource_type, resource_id, event_key, state
                ) VALUES (
                    %s::uuid, %s::uuid, 'DATASET_VERSION_PUBLISHED', %s, %s,
                    NULL, 'DATASET_VERSION', %s, %s, 'UNREAD'
                )
                ON CONFLICT (recipient_id, event_key) DO NOTHING
                """,
                (
                    str(uuid4()),
                    str(recipient_id),
                    context.organization_id,
                    manifest.project_id,
                    resource_id,
                    event_key,
                ),
            )
        except Exception:
            # PostgreSQL has aborted the transaction, so restoring session variables would
            # only mask the notification failure.  The caller rolls manifest and lineage back.
            raise
        else:
            cursor.execute(
                "SELECT set_config('app.subject_id', %s, true)",
                (context.subject_id or "",),
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
