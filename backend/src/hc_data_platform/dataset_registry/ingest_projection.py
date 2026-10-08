"""Atomic P05/P06 projections created from a successful ingest commit."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactV1,
)
from hc_data_platform.alignment.models import AlignmentInputV1
from hc_data_platform.core.context import current_request_context
from hc_data_platform.episode_store.catalog_models import CollectionVersion, EpisodeDerivedReady
from hc_data_platform.episode_store.models import CommitReceipt, PackageInput
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1
from hc_data_platform.workflow.models import (
    ContinuousEpisodeProjectionSourceV1,
    IngestProjectionSourceV1,
)

from .models import (
    DatasetIngestViewerTarget,
)
from .projection_models import (
    DatasetIngestProjectionConflict as DatasetIngestProjectionConflict,
)
from .projection_models import (
    DataValueKind as DataValueKind,
)
from .projection_models import (
    ProjectionFacts as _ProjectionFacts,
)
from .projection_models import (
    TagEpisodeProjectionSource as TagEpisodeProjectionSource,
)
from .projection_models import (
    _document as _document,
)
from .projection_models import (
    _numeric_sequence as _numeric_sequence,
)
from .projection_models import (
    _numeric_vector_payload as _numeric_vector_payload,
)
from .projection_models import (
    _operational_revision as _operational_revision,
)
from .projection_models import (
    _previous_id as _previous_id,
)
from .projection_models import (
    _revision_hash as _revision_hash,
)
from .projection_models import (
    _row_document as _row_document,
)
from .projection_models import (
    _stream_kind as _stream_kind,
)
from .projection_models import (
    _value_kind as _value_kind,
)
from .projection_models import (
    _version_id as _version_id,
)
from .projection_models import (
    _version_token as _version_token,
)
from .projection_models import (
    projection_target as _target,
)
from .projection_models import stable_id as _stable_id  # noqa: F401 - legacy fixture import
from .projection_writer import DatasetProjectionWriter


class PostgresDatasetIngestProjector:
    """Validate ingestion inputs and atomically publish their product projection."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        *,
        native_manifest_parser: Any = None,
        episode_packages: Any = None,
    ) -> None:
        self._connection_factory = connection_factory
        self._native_manifest_parser = native_manifest_parser
        self._episode_packages = episode_packages
        self.projections = DatasetProjectionWriter(
            files=episode_packages.files if episode_packages else None,
            steps=episode_packages.steps if episode_packages else None,
        )

    def _commit_matches(
        self,
        source: IngestProjectionSourceV1 | ContinuousEpisodeProjectionSourceV1,
        version: DatasetVersionRef | CollectionVersion,
        ready: DerivedReadyV1 | EpisodeDerivedReady,
    ) -> bool:
        if isinstance(version, CollectionVersion):
            if not isinstance(ready, EpisodeDerivedReady) or self._episode_packages is None:
                return False
            identity = ready.data.identity
            if (
                identity.scope != (source.organization_id, source.project_id, source.region_code)
                or identity.dataset_id != ready.dataset_id
                or identity.rollout_id != source.rollout_id
                or ready.data not in version.episodes
                or ready.collection_version_id != version.collection_version_id
                or ready.step_count != ready.data.frame_count
            ):
                return False
            stored: CollectionVersion = self._episode_packages.collections.get(
                ready.dataset_id, collection_version_id=version.collection_version_id
            )
            receipt: CommitReceipt = self._episode_packages.repository.get(
                identity, ready.data.data_version_id
            )
            package_input: PackageInput = self._episode_packages.files.open(
                identity, ready.data.data_version_id
            ).manifest.input
            return (
                stored == version
                and receipt.data == ready.data
                and package_input.source_sha256 == source.source_sha256
                and package_input.processor_version == ready.converter_version
            )
        return (
            isinstance(ready, DerivedReadyV1)
            and version.lance_version == ready.lance_version
            and len(version.committed_rollouts) == version.version
        )

    def project(
        self,
        *,
        source: IngestProjectionSourceV1,
        alignment: AlignmentInputV1,
        schema_snapshot_id: str,
        frequency_hz: float,
        version: DatasetVersionRef | CollectionVersion,
        ready: DerivedReadyV1 | EpisodeDerivedReady,
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> DatasetIngestViewerTarget:
        context = current_request_context()
        if (
            context.organization_id != source.organization_id
            or context.project_id != source.project_id
            or context.region_code != source.region_code
            or not context.service_identity
        ):
            raise DatasetIngestProjectionConflict(
                "dataset ingest projection requires the exact Worker scope"
            )
        if (
            alignment.rollout_id != source.rollout_id
            or alignment.source_sha256 != source.source_sha256
            or version.project_id != source.project_id
            or version.dataset_id != ready.dataset_id
            or version.version != ready.dataset_version
            or not self._commit_matches(source, version, ready)
            or version.schema_snapshot_id != schema_snapshot_id
            or version.frequency_hz != frequency_hz
            or ready.project_id != source.project_id
            or ready.rollout_id != source.rollout_id
            or ready.source_sha256 != source.source_sha256
            or ready.content_hash != version.content_hash
            or ready.step_count <= 0
            or source.rollout_id not in version.committed_rollouts
        ):
            raise DatasetIngestProjectionConflict(
                "dataset ingest projection lineage does not match the data commit"
            )

        target = _target(source, version, ready)
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (
                        "/".join(
                            (
                                source.organization_id,
                                source.project_id,
                                source.region_code,
                                ready.dataset_id,
                            )
                        ),
                    ),
                )
                existing = self.projections.existing_target(
                    cursor,
                    source=source,
                    version=version,
                    target=target,
                )
                if existing is not None:
                    connection.commit()
                    return existing
                preflight = (
                    self._native_manifest_parser.parse(source.manifest_key)
                    if source.lerobot is not None and self._native_manifest_parser is not None
                    else self._preflight(cursor, source)
                )
                if (
                    preflight.manifest.project_id != source.project_id
                    or preflight.manifest.rollout_id != source.rollout_id
                    or preflight.manifest.data_package_id != source.data_package_id
                    or preflight.manifest.sha256 != source.source_sha256
                    or preflight.manifest_fingerprint != source.manifest_fingerprint
                ):
                    raise DatasetIngestProjectionConflict("manifest lineage differs from source")
                source_size = preflight.total_file_size
                if source.lerobot is not None:
                    cursor.execute(
                        """SELECT total_bytes FROM ingest.raw_sources
                        WHERE organization_id=%s AND project_id=%s AND region_code=%s
                          AND raw_source_id=%s AND manifest_key=%s""",
                        (
                            source.organization_id,
                            source.project_id,
                            source.region_code,
                            source.lerobot.raw_upload_id,
                            source.object_key,
                        ),
                    )
                    raw_size = cursor.fetchone()
                    if raw_size is None:
                        raise DatasetIngestProjectionConflict("native Raw source is missing")
                    source_size = int(raw_size[0])
                facts = _ProjectionFacts(
                    collection_task_id=preflight.manifest.task_id,
                    robot_id=preflight.manifest.robot_id,
                    started_at=preflight.manifest.start_time,
                    camera_topics=frozenset(camera.topic for camera in preflight.manifest.cameras),
                    source_size_bytes=source_size,
                    upload_id=source.session_id,
                )
                self.projections.assert_task_dataset(
                    cursor,
                    source=source,
                    collection_task_id=facts.collection_task_id,
                    dataset_id=target.dataset_id,
                )
                self.projections.write(
                    cursor,
                    source=source,
                    alignment=alignment,
                    schema_snapshot_id=schema_snapshot_id,
                    frequency_hz=frequency_hz,
                    version=version,
                    ready=ready,
                    target=target,
                    facts=facts,
                    media_artifacts=media_artifacts,
                )
            connection.commit()
            return target
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def project_continuous_episode(
        self,
        *,
        source: ContinuousEpisodeProjectionSourceV1,
        alignment: AlignmentInputV1,
        schema_snapshot_id: str,
        frequency_hz: float,
        version: DatasetVersionRef | CollectionVersion,
        ready: DerivedReadyV1 | EpisodeDerivedReady,
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> DatasetIngestViewerTarget:
        """Publish a finalized soft Episode through the same immutable projection."""

        context = current_request_context()
        if (
            context.organization_id != source.organization_id
            or context.project_id != source.project_id
            or context.region_code != source.region_code
            or not context.service_identity
        ):
            raise DatasetIngestProjectionConflict(
                "continuous Episode projection requires the exact Worker scope"
            )
        if (
            alignment.rollout_id != source.rollout_id
            or alignment.source_sha256 != source.source_sha256
            or version.project_id != source.project_id
            or version.dataset_id != ready.dataset_id
            or version.version != ready.dataset_version
            or not self._commit_matches(source, version, ready)
            or version.schema_snapshot_id != schema_snapshot_id
            or version.frequency_hz != frequency_hz
            or ready.project_id != source.project_id
            or ready.rollout_id != source.rollout_id
            or ready.source_sha256 != source.source_sha256
            or ready.content_hash != version.content_hash
            or ready.step_count <= 0
            or source.rollout_id not in version.committed_rollouts
        ):
            raise DatasetIngestProjectionConflict(
                "continuous Episode lineage does not match the data commit"
            )
        target = _target(source, version, ready)
        facts = _ProjectionFacts(
            collection_task_id=source.collection_task_id,
            robot_id=source.robot_id,
            started_at=source.started_at,
            camera_topics=frozenset(source.camera_modality_keys),
            source_size_bytes=source.source_size_bytes,
            upload_id=source.recording_upload_id,
        )
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (
                        "/".join(
                            (
                                source.organization_id,
                                source.project_id,
                                source.region_code,
                                ready.dataset_id,
                            )
                        ),
                    ),
                )
                existing = self.projections.existing_target(
                    cursor, source=source, version=version, target=target
                )
                if existing is not None:
                    connection.commit()
                    return existing
                self.projections.assert_task_dataset(
                    cursor,
                    source=source,
                    collection_task_id=facts.collection_task_id,
                    dataset_id=target.dataset_id,
                )
                self.projections.write(
                    cursor,
                    source=source,
                    alignment=alignment,
                    schema_snapshot_id=schema_snapshot_id,
                    frequency_hz=frequency_hz,
                    version=version,
                    ready=ready,
                    target=target,
                    facts=facts,
                    media_artifacts=media_artifacts,
                )
            connection.commit()
            return target
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _preflight(cursor: Any, source: IngestProjectionSourceV1) -> ManifestPreflightResultV1:
        cursor.execute(
            """
            SELECT preflight_json
              FROM ingest.manifest_discoveries
             WHERE session_id = %s AND project_id = %s AND region_code = %s
               AND organization_id = %s
            """,
            (source.session_id, source.project_id, source.region_code, source.organization_id),
        )
        raw = cursor.fetchone()
        if raw is None:
            raise DatasetIngestProjectionConflict(
                "the committed Manifest projection is unavailable"
            )
        preflight = ManifestPreflightResultV1.model_validate(_row_document(raw))
        manifest = preflight.manifest
        if (
            manifest.project_id != source.project_id
            or manifest.rollout_id != source.rollout_id
            or manifest.data_package_id != source.data_package_id
            or manifest.sha256 != source.source_sha256
            or preflight.manifest_fingerprint != source.manifest_fingerprint
        ):
            raise DatasetIngestProjectionConflict(
                "the committed Manifest projection has changed identity"
            )
        return preflight
