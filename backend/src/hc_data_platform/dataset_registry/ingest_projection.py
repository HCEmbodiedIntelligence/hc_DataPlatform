"""Atomic P05/P06 projections created from a successful ingest commit."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
)
from hc_data_platform.alignment.models import AlignmentInputV1, ModalityKind
from hc_data_platform.core.context import current_request_context
from hc_data_platform.ingest.models import ManifestPreflightResultV1
from hc_data_platform.lance_catalog.models import DatasetVersionRef, DerivedReadyV1
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.security.versioning import ResourceVersion
from hc_data_platform.workflow.models import (
    ContinuousEpisodeProjectionSourceV1,
    IngestProjectionSourceV1,
)

from .models import (
    DatasetIngestViewerTarget,
    DatasetPageActor,
    DatasetPageContentReference,
    DatasetPageContentSnapshot,
    DatasetPageDetailFacts,
    DatasetPageDetailSummary,
    DatasetPageEpisodeAlignedMediaBinding,
    DatasetPageEpisodeDataBinding,
    DatasetPageEpisodeRecord,
    DatasetPageEpisodeRevision,
    DatasetPageEpisodeStream,
    DatasetPageManifestEntry,
    DatasetPageManifestSummary,
    DatasetPageMetadata,
    DatasetPageReadyVersion,
    DatasetPageRecord,
    DatasetPageRevisionSnapshotReference,
    DatasetPageScope,
    DatasetPageSourceProvenance,
    DatasetPageVersionCapacityFacts,
    DatasetPageVersionContentProjection,
    DatasetPageVersionManifestEntryRecord,
    DatasetPageVersionSchemaChannel,
    DatasetPageVersionSchemaDetail,
    DatasetPageVersionSchemaSummary,
)
from .repository import DatasetPageAuditEvent, PostgresDatasetPageRepository


class DatasetIngestProjectionConflict(RuntimeError):
    """The durable projection disagrees with immutable Lance lineage."""


DataValueKind = Literal["SCALAR", "VECTOR", "POINTCLOUD_XYZ", "EVENT"]


def _document(value: object) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:32]
    return f"{prefix}_{digest}"


def _version_id(version: int) -> str:
    return f"version_lance_{version}"


def _numeric_sequence(value: object) -> tuple[float, ...] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if not value or not all(
        isinstance(item, (int, float)) and not isinstance(item, bool) for item in value
    ):
        return None
    return tuple(float(item) for item in value)


def _numeric_vector_payload(value: object) -> tuple[float, ...] | None:
    direct = _numeric_sequence(value)
    if direct is not None:
        return direct
    if not isinstance(value, Mapping):
        return None
    for key in ("positions", "values"):
        candidate = _numeric_sequence(value.get(key))
        if candidate is not None:
            return candidate
    position = _numeric_sequence(value.get("position_xyz"))
    orientation = _numeric_sequence(value.get("orientation_wxyz"))
    if position is not None and orientation is not None:
        return (*position, *orientation)
    return None


def _value_kind(values: Sequence[object]) -> DataValueKind:
    candidate = next((value for value in values if value is not None), None)
    if isinstance(candidate, bool):
        return "EVENT"
    if isinstance(candidate, (int, float)):
        return "SCALAR"
    if _numeric_vector_payload(candidate) is not None:
        return "VECTOR"
    return "EVENT"


def _stream_kind(topic: str, modality: ModalityKind, value_kind: DataValueKind) -> str:
    lowered = topic.casefold()
    if modality is ModalityKind.POINT_CLOUD:
        return "POINTCLOUD"
    if value_kind == "EVENT":
        return "EVENT"
    if "end_effector" in lowered and "state" in lowered:
        return "POSE"
    if "joint" in lowered:
        return "JOINT_STATE"
    if "observation/state" in lowered or "raw_state" in lowered:
        return "JOINT_STATE"
    if modality is ModalityKind.ACTION or "action" in lowered or "command" in lowered:
        return "ACTION"
    if modality is ModalityKind.FORCE or "force" in lowered or "wrench" in lowered:
        return "FORCE"
    if modality is ModalityKind.IMU or "imu" in lowered:
        return "IMU"
    if "pose" in lowered:
        return "POSE"
    if "tactile" in lowered or "touch" in lowered:
        return "TACTILE"
    return "EVENT"


def _row_document(raw: object) -> object:
    return raw[0] if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)) else raw


@dataclass(frozen=True, slots=True)
class _ProjectionFacts:
    collection_task_id: str
    robot_id: str
    started_at: datetime
    camera_topics: frozenset[str]
    source_size_bytes: int
    upload_id: str


class PostgresDatasetIngestProjector:
    """Publish one all-or-nothing, idempotent Dataset/Version/Episode view."""

    def __init__(
        self, connection_factory: Callable[[], Any], *, native_manifest_parser: Any = None
    ) -> None:
        self._connection_factory = connection_factory
        self._native_manifest_parser = native_manifest_parser

    def project(
        self,
        *,
        source: IngestProjectionSourceV1,
        alignment: AlignmentInputV1,
        schema_snapshot_id: str,
        frequency_hz: float,
        version: DatasetVersionRef,
        ready: DerivedReadyV1,
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
            or version.lance_version != ready.lance_version
            or version.schema_snapshot_id != schema_snapshot_id
            or version.frequency_hz != frequency_hz
            or ready.project_id != source.project_id
            or ready.rollout_id != source.rollout_id
            or ready.source_sha256 != source.source_sha256
            or ready.content_hash != version.content_hash
            or ready.step_count <= 0
            or source.rollout_id not in version.committed_rollouts
            or len(version.committed_rollouts) != version.version
        ):
            raise DatasetIngestProjectionConflict(
                "dataset ingest projection lineage does not match the Lance commit"
            )

        target = DatasetIngestViewerTarget(
            dataset_id=ready.dataset_id,
            version_id=_version_id(ready.dataset_version),
            episode_id=_stable_id("episode", source.rollout_id),
            revision_id=_stable_id("revision", source.rollout_id, source.source_sha256),
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
                existing = self._existing_target(
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
                self._assert_task_dataset(
                    cursor,
                    source=source,
                    collection_task_id=facts.collection_task_id,
                    dataset_id=target.dataset_id,
                )
                self._insert_projection(
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
        version: DatasetVersionRef,
        ready: DerivedReadyV1,
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
            or version.lance_version != ready.lance_version
            or version.schema_snapshot_id != schema_snapshot_id
            or version.frequency_hz != frequency_hz
            or ready.project_id != source.project_id
            or ready.rollout_id != source.rollout_id
            or ready.source_sha256 != source.source_sha256
            or ready.content_hash != version.content_hash
            or ready.step_count <= 0
            or source.rollout_id not in version.committed_rollouts
            or len(version.committed_rollouts) != version.version
        ):
            raise DatasetIngestProjectionConflict(
                "continuous Episode lineage does not match the Lance commit"
            )
        target = DatasetIngestViewerTarget(
            dataset_id=ready.dataset_id,
            version_id=_version_id(ready.dataset_version),
            episode_id=_stable_id("episode", source.rollout_id),
            revision_id=_stable_id("revision", source.rollout_id, source.source_sha256),
        )
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
                existing = self._existing_target(
                    cursor, source=source, version=version, target=target
                )
                if existing is not None:
                    connection.commit()
                    return existing
                self._assert_task_dataset(
                    cursor,
                    source=source,
                    collection_task_id=facts.collection_task_id,
                    dataset_id=target.dataset_id,
                )
                self._insert_projection(
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
    def _existing_target(
        cursor: Any,
        *,
        source: IngestProjectionSourceV1 | ContinuousEpisodeProjectionSourceV1,
        version: DatasetVersionRef,
        target: DatasetIngestViewerTarget,
    ) -> DatasetIngestViewerTarget | None:
        cursor.execute(
            """
            SELECT version_document
              FROM dataset_registry.dataset_versions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (
                source.organization_id,
                source.project_id,
                source.region_code,
                target.dataset_id,
                target.version_id,
            ),
        )
        raw = cursor.fetchone()
        if raw is None:
            return None
        persisted = DatasetPageReadyVersion.model_validate(_row_document(raw))
        revision = next(
            (
                item
                for item in persisted.content_snapshot.revision_refs
                if item.episode_id == target.episode_id
            ),
            None,
        )
        expected_token = f"lance-version:{version.storage_commit_id}"
        if revision is None and isinstance(source, ContinuousEpisodeProjectionSourceV1):
            # Preserve already published projections from the older local-ID
            # scheme when recovering a lost receipt. New recordings use the
            # recording-qualified rollout identity, so episode_0001 cannot clash.
            revision = next(
                (
                    item
                    for item in persisted.content_snapshot.revision_refs
                    if item.episode_id == source.episode_id
                    and item.content_sha256 == source.source_sha256
                    and item.revision_id
                    == _stable_id("revision", source.episode_id, source.source_sha256)
                ),
                None,
            )
            if revision is not None:
                target = target.model_copy(
                    update={"episode_id": revision.episode_id, "revision_id": revision.revision_id}
                )
        if (
            persisted.version_id != target.version_id
            or revision is None
            or revision.revision_id != target.revision_id
            or revision.content_sha256 != source.source_sha256
            or persisted.version_token != expected_token
        ):
            raise DatasetIngestProjectionConflict(
                "an existing Dataset version disagrees with the Lance commit"
            )
        key = (
            source.organization_id,
            source.project_id,
            source.region_code,
            target.dataset_id,
            target.version_id,
        )
        cursor.execute(
            """
            SELECT
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_content_projections
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_episodes
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s AND episode_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_episode_revisions
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s AND revision_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_schema_summaries
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_schema_details
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_source_provenance
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s AND provenance_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_capacity_facts
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_version_manifest_entries
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND version_id = %s AND revision_id = %s
                ),
                EXISTS (
                    SELECT 1 FROM dataset_registry.dataset_detail_facts
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s
                )
            """,
            (
                *key,
                *key,
                target.episode_id,
                *key,
                target.revision_id,
                *key,
                *key,
                *key,
                _stable_id("provenance", source.data_package_id),
                *key,
                *key,
                target.revision_id,
                *key[:-1],
            ),
        )
        completeness = cursor.fetchone()
        if completeness is None or not all(bool(value) for value in completeness):
            raise DatasetIngestProjectionConflict(
                "an existing Dataset version has an incomplete product projection"
            )
        return target

    @staticmethod
    def _preflight(cursor: Any, source: IngestProjectionSourceV1) -> ManifestPreflightResultV1:
        cursor.execute(
            """
            SELECT preflight_json
              FROM ingest.manifest_discoveries
             WHERE session_id = %s AND project_id = %s AND region_code = %s
            """,
            (source.session_id, source.project_id, source.region_code),
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

    @staticmethod
    def _assert_task_dataset(
        cursor: Any,
        *,
        source: IngestProjectionSourceV1 | ContinuousEpisodeProjectionSourceV1,
        collection_task_id: str,
        dataset_id: str,
    ) -> None:
        cursor.execute(
            """
            SELECT dataset_id
              FROM collection_tasks.collection_tasks
             WHERE organization_id = %s AND project_id = %s
               AND collection_task_id = %s
            """,
            (source.organization_id, source.project_id, collection_task_id),
        )
        raw = cursor.fetchone()
        if raw is None or str(raw[0]) != dataset_id:
            raise DatasetIngestProjectionConflict(
                "the Dataset does not belong to the Manifest collection task"
            )

    def _insert_projection(
        self,
        cursor: Any,
        *,
        source: IngestProjectionSourceV1 | ContinuousEpisodeProjectionSourceV1,
        alignment: AlignmentInputV1,
        schema_snapshot_id: str,
        frequency_hz: float,
        version: DatasetVersionRef,
        ready: DerivedReadyV1,
        target: DatasetIngestViewerTarget,
        facts: _ProjectionFacts,
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> None:
        scope = DatasetPageScope(
            organization_id=source.organization_id,
            project_id=source.project_id,
            region_code=source.region_code,
        )
        previous_episodes, previous_revisions, previous_projection = self._previous_content(
            cursor,
            scope=scope,
            dataset_id=target.dataset_id,
            dataset_version=ready.dataset_version,
            committed_rollout_count=len(version.committed_rollouts),
        )
        streams = self._streams(
            alignment=alignment,
            camera_topics=facts.camera_topics,
            ready=ready,
            frequency_hz=frequency_hz,
            media_artifacts=media_artifacts,
        )
        current_ref = DatasetPageRevisionSnapshotReference(
            episode_id=target.episode_id,
            revision_id=target.revision_id,
            ordinal=len(previous_episodes),
            content_sha256=source.source_sha256,
        )
        current_episode = DatasetPageEpisodeRecord(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            episode_id=target.episode_id,
            storage_region_code=scope.region_code,
            selected_revision=current_ref,
            included=True,
            success_state="SUCCEEDED",
            task=facts.collection_task_id,
            robot_id=facts.robot_id,
            review_status=None,
            review_finding_count="0",
            started_at=facts.started_at,
            started_at_ns=str(alignment.start_ns),
            has_finding=False,
            change_type="INGESTED",
        )
        current_revision = DatasetPageEpisodeRevision(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            episode_id=target.episode_id,
            revision_id=target.revision_id,
            ordinal=current_ref.ordinal,
            content_sha256=source.source_sha256,
            started_at_ns=str(alignment.start_ns),
            duration_ns=str(alignment.end_ns - alignment.start_ns),
            streams=streams,
        )
        episodes = (*previous_episodes, current_episode)
        revisions = (*previous_revisions, current_revision)
        version_streams = tuple(
            {
                stream.channel_path: stream for revision in revisions for stream in revision.streams
            }.values()
        )
        revision_refs = tuple(item.selected_revision for item in episodes)
        schema_ref = DatasetPageContentReference(
            reference_type="DATASET_SCHEMA",
            reference_id=schema_snapshot_id,
            reference_version=str(ready.dataset_version),
            sha256=version.schema_fingerprint,
        )
        source_ref = DatasetPageContentReference(
            reference_type="SOURCE_MANIFEST",
            reference_id=source.data_package_id,
            reference_version="1",
            sha256=source.manifest_fingerprint,
        )
        source_refs = self._source_refs(previous_projection, source_ref)
        snapshot_payload = {
            "revision_refs": [item.model_dump(mode="json") for item in revision_refs],
            "schema_ref": schema_ref.model_dump(mode="json"),
            "source_manifest_refs": [item.model_dump(mode="json") for item in source_refs],
        }
        content_snapshot = DatasetPageContentSnapshot(
            content_snapshot_id=f"lance-snapshot-{ready.dataset_version}",
            content_snapshot_hash=canonical_hash(snapshot_payload),
            revision_refs=revision_refs,
            schema_ref=schema_ref,
            source_manifest_refs=source_refs,
        )
        entries = self._manifest_entries(
            cursor,
            scope=scope,
            dataset_id=target.dataset_id,
            dataset_version=ready.dataset_version,
            current=DatasetPageManifestEntry(
                entry_id=f"entry-{current_revision.revision_id}",
                episode_id=current_revision.episode_id,
                revision_id=current_revision.revision_id,
                role="REVISION",
                size_bytes=str(facts.source_size_bytes),
                sha256=current_revision.content_sha256,
                safe_locator=f"episode:{current_revision.episode_id}",
            ),
        )
        manifest_summary = DatasetPageManifestSummary(
            manifest_id=f"manifest-lance-{ready.dataset_version}",
            format_version="dataset-manifest/v1",
            canonicalization="rfc8785-json",
            sha256=canonical_hash([item.entry.model_dump(mode="json") for item in entries]),
            entry_count=str(len(entries)),
        )
        page_version = DatasetPageReadyVersion(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            display_version=f"v{ready.dataset_version}",
            kind="RAW",
            created_at=version.created_at,
            etag=ResourceVersion().etag,
            version_token=f"lance-version:{version.storage_commit_id}",
            status="READY",
            published_at=version.created_at,
            content_snapshot=content_snapshot,
            manifest=manifest_summary,
        )
        content_projection = DatasetPageVersionContentProjection(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            content_snapshot=content_snapshot,
            manifest=manifest_summary,
            operational_revision=f"lance:{version.storage_commit_id}",
        )
        record = self._dataset_record(
            cursor,
            scope=scope,
            dataset_id=target.dataset_id,
            collection_task_id=facts.collection_task_id,
            robot_id=facts.robot_id,
            version=page_version,
            channels=tuple(stream.channel_path for stream in version_streams),
            episode_count=len(episodes),
        )
        self._put_dataset(cursor, record)
        self._put_version(cursor, page_version)
        self._put_content(cursor, content_projection)
        for episode in episodes:
            self._put_episode(cursor, episode)
        for revision in revisions:
            self._put_revision(cursor, revision)
        for entry in entries:
            self._put_manifest_entry(cursor, entry)

        schema_summary = DatasetPageVersionSchemaSummary(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            schema_snapshot=schema_ref,
            channel_count=str(len(version_streams)),
        )
        schema_detail = DatasetPageVersionSchemaDetail(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            schema_snapshot=schema_ref,
            channel_count=str(len(version_streams)),
            channels=tuple(
                DatasetPageVersionSchemaChannel(
                    channel_id=stream.episode_stream_id,
                    name=stream.channel_path,
                    data_type=(
                        "binary/jpeg"
                        if stream.aligned_media_binding is not None
                        else stream.data_binding.value_kind
                        if stream.data_binding is not None
                        else "unknown"
                    ),
                )
                for stream in version_streams
            ),
        )
        self._put_schema(cursor, schema_summary, schema_detail)
        provenance = DatasetPageSourceProvenance(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            storage_region_code=scope.region_code,
            provenance_id=_stable_id("provenance", source.data_package_id),
            upload_id=facts.upload_id,
            source_id=facts.robot_id,
            source_display_name=facts.robot_id,
            source_manifest_id=source.data_package_id,
            source_manifest_sha256=source.manifest_fingerprint,
            verified_object_set_hash=source.source_sha256,
            registered_at=version.created_at,
        )
        provenance_items = self._source_provenance(
            cursor,
            scope=scope,
            dataset_id=target.dataset_id,
            dataset_version=ready.dataset_version,
            current=provenance,
            expected_sources=()
            if previous_projection is None
            else previous_projection.content_snapshot.source_manifest_refs,
        )
        for item in provenance_items:
            self._put_provenance(cursor, item)
        source_increment = facts.source_size_bytes
        if (
            isinstance(source, ContinuousEpisodeProjectionSourceV1)
            and previous_projection is not None
            and any(
                ref.reference_id == source.data_package_id
                for ref in previous_projection.content_snapshot.source_manifest_refs
            )
        ):
            source_increment = 0
        if (
            isinstance(source, IngestProjectionSourceV1)
            and source.lerobot is not None
            and sum(item.upload_id == facts.upload_id for item in provenance_items) > 1
        ):
            source_increment = 0  # All Episodes reference the same immutable Raw folder.
        source_bytes = (
            self._source_bytes(
                cursor,
                scope,
                target.dataset_id,
                ready.dataset_version,
            )
            + source_increment
        )
        capacity = DatasetPageVersionCapacityFacts(
            scope=scope,
            dataset_id=target.dataset_id,
            version_id=target.version_id,
            state="PARTIAL",
            source_bytes=str(source_bytes),
            required_physical_bytes=None,
            actual_oss_bytes=None,
            calculated_at=version.created_at,
            basis_revision=f"lance:{version.storage_commit_id}",
        )
        self._put_capacity(cursor, capacity)
        detail_facts = DatasetPageDetailFacts(
            scope=scope,
            dataset_id=target.dataset_id,
            summary=DatasetPageDetailSummary(
                episode_count=str(len(episodes)),
                effective_duration_ns=str(sum(int(item.duration_ns) for item in revisions)),
                source_bytes=str(source_bytes),
                required_physical_bytes="0",
                actual_oss_bytes=None,
                pending_review_version_count="0",
                returned_version_count="0",
                actionable_draft_count="0",
                calculated_at=version.created_at,
                calculation_state="PARTIAL",
            ),
        )
        self._put_facts(cursor, detail_facts)
        PostgresDatasetPageRepository._insert_audit(
            cursor,
            DatasetPageAuditEvent(
                project_id=scope.project_id,
                region_code=scope.region_code,
                actor_id="hc-data-worker",
                action="dataset.ingest_version.projected",
                resource_id=target.version_id,
                request_id=current_request_context().request_id,
                outcome="SUCCEEDED",
                occurred_at=datetime.now(timezone.utc),
                after_hash=canonical_hash(page_version.model_dump(mode="json")),
                details={
                    "dataset_id": target.dataset_id,
                    "episode_id": target.episode_id,
                    "revision_id": target.revision_id,
                    "stream_count": len(streams),
                },
            ),
        )

    @staticmethod
    def _previous_content(
        cursor: Any,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        dataset_version: int,
        committed_rollout_count: int,
    ) -> tuple[
        tuple[DatasetPageEpisodeRecord, ...],
        tuple[DatasetPageEpisodeRevision, ...],
        DatasetPageVersionContentProjection | None,
    ]:
        if dataset_version == 1:
            return (), (), None
        previous_id = _version_id(dataset_version - 1)
        key = (
            scope.organization_id,
            scope.project_id,
            scope.region_code,
            dataset_id,
            previous_id,
        )
        cursor.execute(
            """
            SELECT content_document
              FROM dataset_registry.dataset_version_content_projections
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            key,
        )
        raw_projection = cursor.fetchone()
        if raw_projection is None:
            if committed_rollout_count > 1:
                raise DatasetIngestProjectionConflict(
                    "the preceding product Dataset version is unavailable for "
                    "snapshot carry-forward"
                )
            return (), (), None
        projection = DatasetPageVersionContentProjection.model_validate(
            _row_document(raw_projection)
        )
        cursor.execute(
            """
            SELECT episode_document
              FROM dataset_registry.dataset_version_episodes
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
             ORDER BY ordinal, episode_id
            """,
            key,
        )
        episodes = tuple(
            DatasetPageEpisodeRecord.model_validate(_row_document(row)).model_copy(
                update={"version_id": _version_id(dataset_version)}
            )
            for row in cursor.fetchall()
        )
        cursor.execute(
            """
            SELECT revision_document
              FROM dataset_registry.dataset_version_episode_revisions
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
             ORDER BY ordinal, episode_id
            """,
            key,
        )
        revisions = tuple(
            DatasetPageEpisodeRevision.model_validate(_row_document(row)).model_copy(
                update={"version_id": _version_id(dataset_version)}
            )
            for row in cursor.fetchall()
        )
        episode_refs = tuple(item.selected_revision for item in episodes)
        revision_keys = {
            (item.episode_id, item.revision_id, item.ordinal, item.content_sha256)
            for item in revisions
        }
        if (
            projection.version_id != previous_id
            or len(episodes) != committed_rollout_count - 1
            or len(episodes) != len(revisions)
            or tuple(projection.content_snapshot.revision_refs) != episode_refs
            or {
                (item.episode_id, item.revision_id, item.ordinal, item.content_sha256)
                for item in episode_refs
            }
            != revision_keys
        ):
            raise DatasetIngestProjectionConflict(
                "the preceding Dataset snapshot has incomplete Episode revisions"
            )
        return episodes, revisions, projection

    @staticmethod
    def _source_refs(
        previous: DatasetPageVersionContentProjection | None,
        current: DatasetPageContentReference,
    ) -> tuple[DatasetPageContentReference, ...]:
        candidates = (
            *(() if previous is None else previous.content_snapshot.source_manifest_refs),
            current,
        )
        return tuple(
            {
                f"{item.reference_type}:{item.reference_id}:{item.reference_version}": item
                for item in candidates
            }.values()
        )

    @staticmethod
    def _source_provenance(
        cursor: Any,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        dataset_version: int,
        current: DatasetPageSourceProvenance,
        expected_sources: Sequence[DatasetPageContentReference],
    ) -> tuple[DatasetPageSourceProvenance, ...]:
        if dataset_version == 1:
            return (current,)
        cursor.execute(
            """
            SELECT provenance_document
              FROM dataset_registry.dataset_version_source_provenance
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
             ORDER BY registered_at, provenance_id
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                dataset_id,
                _version_id(dataset_version - 1),
            ),
        )
        previous = tuple(
            DatasetPageSourceProvenance.model_validate(_row_document(row)).model_copy(
                update={"version_id": _version_id(dataset_version)}
            )
            for row in cursor.fetchall()
        )
        expected = {(item.reference_id, item.sha256) for item in expected_sources}
        actual = {(item.source_manifest_id, item.source_manifest_sha256) for item in previous}
        if actual != expected or len(previous) != len(expected):
            raise DatasetIngestProjectionConflict(
                "the preceding Dataset snapshot has incomplete source provenance"
            )
        return tuple({item.provenance_id: item for item in (*previous, current)}.values())

    @staticmethod
    def _manifest_entries(
        cursor: Any,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        dataset_version: int,
        current: DatasetPageManifestEntry,
    ) -> tuple[DatasetPageVersionManifestEntryRecord, ...]:
        previous: tuple[DatasetPageManifestEntry, ...] = ()
        if dataset_version > 1:
            cursor.execute(
                """
                SELECT entry_document
                  FROM dataset_registry.dataset_version_manifest_entries
                 WHERE organization_id = %s AND project_id = %s AND region_code = %s
                   AND dataset_id = %s AND version_id = %s AND entry_role = 'REVISION'
                 ORDER BY entry_id
                """,
                (
                    scope.organization_id,
                    scope.project_id,
                    scope.region_code,
                    dataset_id,
                    _version_id(dataset_version - 1),
                ),
            )
            previous = tuple(
                DatasetPageManifestEntry.model_validate(_row_document(row))
                for row in cursor.fetchall()
            )
            if len(previous) != dataset_version - 1:
                raise DatasetIngestProjectionConflict(
                    "the preceding Dataset snapshot has incomplete Manifest entries"
                )
        return tuple(
            DatasetPageVersionManifestEntryRecord(
                scope=scope,
                dataset_id=dataset_id,
                version_id=_version_id(dataset_version),
                entry=item,
            )
            for item in (*previous, current)
        )

    @staticmethod
    def _streams(
        *,
        alignment: AlignmentInputV1,
        camera_topics: frozenset[str],
        ready: DerivedReadyV1,
        frequency_hz: float,
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> tuple[DatasetPageEpisodeStream, ...]:
        media_by_camera = {artifact.camera_id: artifact for artifact in media_artifacts}
        if set(media_by_camera) != camera_topics or any(
            artifact.status is not AlignedMediaArtifactStatus.READY
            or artifact.dataset_id != ready.dataset_id
            or artifact.rollout_id != ready.rollout_id
            or artifact.dataset_version != ready.dataset_version
            or artifact.frame_count != ready.step_count
            for artifact in media_by_camera.values()
        ):
            raise DatasetIngestProjectionConflict(
                "camera projection requires one READY aligned MP4 per Manifest camera"
            )
        streams: list[DatasetPageEpisodeStream] = []
        for topic, source_stream in sorted(alignment.streams.items()):
            stream_id = _stable_id("stream", alignment.rollout_id, topic)
            if topic in camera_topics:
                streams.append(
                    DatasetPageEpisodeStream(
                        episode_stream_id=stream_id,
                        channel_path=topic,
                        kind="RGB_VIDEO",
                        t_start_ns=str(alignment.start_ns),
                        t_end_ns=str(alignment.end_ns),
                        aligned_media_binding=DatasetPageEpisodeAlignedMediaBinding(
                            rollout_id=alignment.rollout_id,
                            dataset_version=ready.dataset_version,
                            artifact_id=media_by_camera[topic].artifact_id,
                            camera_id=topic,
                            start_step=0,
                            end_step=ready.step_count,
                        ),
                    )
                )
                continue
            values = tuple(sample.value for sample in source_stream.samples)
            value_kind = (
                "POINTCLOUD_XYZ"
                if source_stream.kind is ModalityKind.POINT_CLOUD
                else _value_kind(values)
            )
            kind = _stream_kind(topic, source_stream.kind, value_kind)
            if kind == "EVENT":
                value_kind = "EVENT"
            streams.append(
                DatasetPageEpisodeStream(
                    episode_stream_id=stream_id,
                    channel_path=topic,
                    kind=kind,
                    t_start_ns=str(alignment.start_ns),
                    t_end_ns=str(alignment.end_ns),
                    data_binding=DatasetPageEpisodeDataBinding(
                        rollout_id=alignment.rollout_id,
                        lance_version=ready.lance_version,
                        modality_key=topic,
                        value_kind=value_kind,
                        start_step=0,
                        end_step=ready.step_count,
                    ),
                )
            )
        return tuple(streams)

    @staticmethod
    def _dataset_record(
        cursor: Any,
        *,
        scope: DatasetPageScope,
        dataset_id: str,
        collection_task_id: str,
        robot_id: str,
        version: DatasetPageReadyVersion,
        channels: tuple[str, ...],
        episode_count: int,
    ) -> DatasetPageRecord:
        cursor.execute(
            """
            SELECT dataset_document
              FROM dataset_registry.datasets
            WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s
             FOR UPDATE
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                dataset_id,
            ),
        )
        raw = cursor.fetchone()
        if raw is None:
            return DatasetPageRecord(
                scope=scope,
                dataset_id=dataset_id,
                name=f"Dataset {dataset_id}",
                description="由采集上传与质检流水线自动登记。",
                availability="ACTIVE",
                owner=DatasetPageActor(id="hc-data-worker", display_name="数据处理服务"),
                created_at=version.created_at,
                updated_at=version.created_at,
                activity_at=version.created_at,
                etag=ResourceVersion().etag,
                metadata=DatasetPageMetadata(
                    robot_id=robot_id,
                    collection_task_id=collection_task_id,
                    task=collection_task_id,
                    asset_state="READY",
                    storage_class="STANDARD",
                    channels=channels,
                ),
                episode_count=str(episode_count),
                # Ingest creates an INTERNAL working snapshot.  It must not be
                # advertised as the dataset's manually published version.
                current_ready_version=None,
            )
        existing = DatasetPageRecord.model_validate(_row_document(raw))
        resource_version = ResourceVersion.from_etag(existing.etag)
        metadata = existing.metadata.model_copy(
            update={
                "robot_id": existing.metadata.robot_id or robot_id,
                "collection_task_id": existing.metadata.collection_task_id or collection_task_id,
                "task": existing.metadata.task or collection_task_id,
                "asset_state": "READY",
                "channels": tuple(dict.fromkeys((*existing.metadata.channels, *channels))),
            }
        )
        return existing.model_copy(
            update={
                "updated_at": version.created_at,
                "activity_at": version.created_at,
                "etag": ResourceVersion(resource_version.value + 1).etag,
                "metadata": metadata,
                "episode_count": str(episode_count),
                # Preserve the last business release while the internal Lance
                # working snapshot advances.
                "current_ready_version": existing.current_ready_version,
            }
        )

    @staticmethod
    def _put_dataset(cursor: Any, record: DatasetPageRecord) -> None:
        metadata = record.metadata
        cursor.execute(
            """
            INSERT INTO dataset_registry.datasets (
                organization_id, project_id, region_code, dataset_id, folder_path,
                name, description,
                labels, availability, owner_id, owner_display_name, robot_model_id, robot_id,
                collection_task_id, task, scene, asset_state, storage_class, channels,
                episode_count,
                pending_review_version_count, returned_version_count, actionable_draft_count,
                version, dataset_document, created_at, updated_at, activity_at
            ) VALUES (
                %s, %s, %s, %s, %s::text[], %s, %s, %s::jsonb,
                %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s
            ) ON CONFLICT (organization_id, project_id, region_code, dataset_id) DO UPDATE SET
                folder_path = EXCLUDED.folder_path, name = EXCLUDED.name,
                description = EXCLUDED.description, labels = EXCLUDED.labels,
                availability = EXCLUDED.availability, owner_id = EXCLUDED.owner_id,
                owner_display_name = EXCLUDED.owner_display_name,
                robot_model_id = EXCLUDED.robot_model_id, robot_id = EXCLUDED.robot_id,
                collection_task_id = EXCLUDED.collection_task_id,
                task = EXCLUDED.task, scene = EXCLUDED.scene, asset_state = EXCLUDED.asset_state,
                storage_class = EXCLUDED.storage_class, channels = EXCLUDED.channels,
                episode_count = EXCLUDED.episode_count,
                pending_review_version_count = EXCLUDED.pending_review_version_count,
                returned_version_count = EXCLUDED.returned_version_count,
                actionable_draft_count = EXCLUDED.actionable_draft_count,
                version = EXCLUDED.version, dataset_document = EXCLUDED.dataset_document,
                updated_at = EXCLUDED.updated_at, activity_at = EXCLUDED.activity_at
            """,
            (
                record.scope.organization_id,
                record.scope.project_id,
                record.scope.region_code,
                record.dataset_id,
                list(record.folder_path),
                record.name,
                record.description,
                _document(list(record.labels)),
                record.availability,
                record.owner.id,
                record.owner.display_name,
                metadata.robot_model_id,
                metadata.robot_id,
                metadata.collection_task_id,
                metadata.task,
                metadata.scene,
                metadata.asset_state,
                metadata.storage_class,
                _document(list(metadata.channels)),
                int(record.episode_count),
                int(record.pending_review_version_count),
                int(record.returned_version_count),
                int(record.actionable_draft_count),
                ResourceVersion.from_etag(record.etag).value,
                _document(record),
                record.created_at,
                record.updated_at,
                record.activity_at,
            ),
        )

    @staticmethod
    def _put_version(cursor: Any, version: DatasetPageReadyVersion) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_versions (
                organization_id, project_id, region_code, dataset_id, version_id,
                display_version, version_kind, version_status, created_at, published_at,
                version_document, version_scope
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, 'INTERNAL')
            """,
            (
                version.scope.organization_id,
                version.scope.project_id,
                version.scope.region_code,
                version.dataset_id,
                version.version_id,
                version.display_version,
                version.kind,
                version.status,
                version.created_at,
                version.published_at,
                _document(version),
            ),
        )

    @staticmethod
    def _put_content(cursor: Any, value: DatasetPageVersionContentProjection) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_content_projections (
                organization_id, project_id, region_code, dataset_id, version_id,
                content_snapshot_id, content_snapshot_hash, manifest_id, manifest_sha256,
                operational_revision, content_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                value.content_snapshot.content_snapshot_id,
                value.content_snapshot.content_snapshot_hash,
                value.manifest.manifest_id,
                value.manifest.sha256,
                value.operational_revision,
                _document(value),
            ),
        )

    @staticmethod
    def _put_episode(cursor: Any, value: DatasetPageEpisodeRecord) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_episodes (
                organization_id, project_id, region_code, dataset_id, version_id, episode_id,
                storage_region_code, revision_id, ordinal, started_at, started_at_ns,
                included, success_state,
                task, robot_id, review_status, review_finding_count, has_finding, change_type,
                episode_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                      %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                value.episode_id,
                value.storage_region_code,
                value.selected_revision.revision_id,
                value.selected_revision.ordinal,
                value.started_at,
                int(value.started_at_ns),
                value.included,
                value.success_state,
                value.task,
                value.robot_id,
                value.review_status,
                int(value.review_finding_count or "0"),
                value.has_finding,
                value.change_type,
                _document(value),
            ),
        )

    @staticmethod
    def _put_revision(cursor: Any, value: DatasetPageEpisodeRevision) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_episode_revisions (
                organization_id, project_id, region_code, dataset_id, version_id,
                episode_id, revision_id, ordinal, revision_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                value.episode_id,
                value.revision_id,
                value.ordinal,
                _document(value),
            ),
        )

    @staticmethod
    def _put_manifest_entry(cursor: Any, value: DatasetPageVersionManifestEntryRecord) -> None:
        entry = value.entry
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_manifest_entries (
                organization_id, project_id, region_code, dataset_id, version_id,
                entry_id, episode_id, revision_id, entry_role, size_bytes, entry_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                entry.entry_id,
                entry.episode_id,
                entry.revision_id,
                entry.role,
                int(entry.size_bytes),
                _document(entry),
            ),
        )

    @staticmethod
    def _put_schema(
        cursor: Any,
        summary: DatasetPageVersionSchemaSummary,
        detail: DatasetPageVersionSchemaDetail,
    ) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_schema_summaries (
                organization_id, project_id, region_code, dataset_id, version_id,
                schema_snapshot_id, channel_count, schema_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                summary.scope.organization_id,
                summary.scope.project_id,
                summary.scope.region_code,
                summary.dataset_id,
                summary.version_id,
                summary.schema_snapshot.reference_id,
                int(summary.channel_count or "0"),
                _document(summary),
            ),
        )
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_schema_details (
                organization_id, project_id, region_code, dataset_id, version_id,
                schema_snapshot_id, channel_count, schema_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                detail.scope.organization_id,
                detail.scope.project_id,
                detail.scope.region_code,
                detail.dataset_id,
                detail.version_id,
                detail.schema_snapshot.reference_id,
                int(detail.channel_count),
                _document(detail),
            ),
        )

    @staticmethod
    def _put_provenance(cursor: Any, value: DatasetPageSourceProvenance) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_source_provenance (
                organization_id, project_id, region_code, dataset_id, version_id,
                provenance_id, storage_region_code, upload_id, source_id, source_display_name,
                source_manifest_id, registered_at, provenance_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                value.provenance_id,
                value.storage_region_code,
                value.upload_id,
                value.source_id,
                value.source_display_name,
                value.source_manifest_id,
                value.registered_at,
                _document(value),
            ),
        )

    @staticmethod
    def _source_bytes(
        cursor: Any,
        scope: DatasetPageScope,
        dataset_id: str,
        dataset_version: int,
    ) -> int:
        if dataset_version == 1:
            return 0
        cursor.execute(
            """
            SELECT (capacity_document ->> 'source_bytes')::numeric
              FROM dataset_registry.dataset_version_capacity_facts
             WHERE organization_id = %s AND project_id = %s AND region_code = %s
               AND dataset_id = %s AND version_id = %s
            """,
            (
                scope.organization_id,
                scope.project_id,
                scope.region_code,
                dataset_id,
                _version_id(dataset_version - 1),
            ),
        )
        raw = cursor.fetchone()
        if raw is None or raw[0] is None:
            raise DatasetIngestProjectionConflict(
                "the preceding Dataset snapshot has incomplete capacity facts"
            )
        return int(raw[0])

    @staticmethod
    def _put_capacity(cursor: Any, value: DatasetPageVersionCapacityFacts) -> None:
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_version_capacity_facts (
                organization_id, project_id, region_code, dataset_id, version_id,
                capacity_state, calculated_at, capacity_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                value.version_id,
                value.state,
                value.calculated_at,
                _document(value),
            ),
        )

    @staticmethod
    def _put_facts(cursor: Any, value: DatasetPageDetailFacts) -> None:
        summary = value.summary
        cursor.execute(
            """
            INSERT INTO dataset_registry.dataset_detail_facts (
                organization_id, project_id, region_code, dataset_id, episode_count,
                effective_duration_ns, source_bytes, required_physical_bytes,
                actual_oss_bytes, pending_review_version_count, returned_version_count,
                actionable_draft_count, calculation_state, calculated_at, fact_document
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (organization_id, project_id, region_code, dataset_id) DO UPDATE SET
                episode_count = EXCLUDED.episode_count,
                effective_duration_ns = EXCLUDED.effective_duration_ns,
                source_bytes = EXCLUDED.source_bytes,
                required_physical_bytes = EXCLUDED.required_physical_bytes,
                actual_oss_bytes = EXCLUDED.actual_oss_bytes,
                pending_review_version_count = EXCLUDED.pending_review_version_count,
                returned_version_count = EXCLUDED.returned_version_count,
                actionable_draft_count = EXCLUDED.actionable_draft_count,
                calculation_state = EXCLUDED.calculation_state,
                calculated_at = EXCLUDED.calculated_at,
                fact_document = EXCLUDED.fact_document
            """,
            (
                value.scope.organization_id,
                value.scope.project_id,
                value.scope.region_code,
                value.dataset_id,
                int(summary.episode_count),
                int(summary.effective_duration_ns),
                int(summary.source_bytes),
                int(summary.required_physical_bytes),
                None if summary.actual_oss_bytes is None else int(summary.actual_oss_bytes),
                int(summary.pending_review_version_count),
                int(summary.returned_version_count),
                int(summary.actionable_draft_count),
                summary.calculation_state,
                summary.calculated_at,
                _document(value),
            ),
        )
