"""Worker-only QC, sensor alignment, and commit adapters for soft Episodes."""

from __future__ import annotations

import hashlib
import heapq
import json
import math
import subprocess
import tempfile
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager, suppress
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, unquote, urlparse

from pydantic import TypeAdapter, ValidationError

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaScopeV1,
    AlignmentStagingArtifactV1,
)
from hc_data_platform.aligned_media.ports import AlignedMediaRepositoryPort
from hc_data_platform.alignment.models import (
    AlignedRowV1,
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.alignment.ports import AlignmentPort, FragmentWriterPort
from hc_data_platform.dataset_registry.models import DatasetId, DatasetIngestViewerTarget
from hc_data_platform.ingest.models import (
    ManifestCameraV1,
    ManifestDiscoveryV1,
    ManifestTopicV1,
)
from hc_data_platform.ingest.ports import ObjectStoragePort
from hc_data_platform.lance_catalog.models import (
    DatasetSchemaSnapshot,
    DatasetVersionRef,
    DerivedReadyV1,
)
from hc_data_platform.lance_catalog.ports import LanceCatalogPort
from hc_data_platform.quality.models import (
    QualityInputV1,
    QualityStatus,
    QualityStreamObservationV1,
)
from hc_data_platform.quality.ports import QualityEvaluationPort
from hc_data_platform.security.audit import canonical_hash
from hc_data_platform.verification.ports import DecoderProbe
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.models import (
    AlignedBundleCommitActivityOutput,
    AlignmentActivityInput,
    ContinuousEpisodeAlignmentActivityOutput,
    ContinuousEpisodeAssetV1,
    ContinuousEpisodeProjectionSourceV1,
    ContinuousEpisodeQcActivityOutput,
    ContinuousEpisodeStateActivityInput,
    ContinuousEpisodeStateActivityOutput,
    ContinuousEpisodeWorkflowInput,
)
from hc_data_platform.workflow.projection_store import ProjectionArtifactStorePort

from .asset_models import (
    EpisodeProcessingStatus,
    RecordingAsset,
    RecordingAssetRole,
    RecordingConfigurationV1,
    RecordingUpload,
    SensorTimestampMode,
)
from .models import ContinuousRecording, RecordingScope
from .repository import ContinuousRecordingRepository

_DATASET_ID = TypeAdapter(DatasetId)
_COPY_CHUNK_BYTES = 8 * 1024 * 1024
_MAX_SENSOR_MESSAGES = 2_000_000


class ContinuousEpisodeProcessingError(RuntimeError):
    code = "CONTINUOUS_EPISODE_PROCESSING_FAILED"


class ContinuousEpisodeVersionConflict(ContinuousEpisodeProcessingError):
    code = "ALIGNED_MEDIA_VERSION_CONFLICT"


class FragmentWriterFactoryPort(Protocol):
    def create(self, request: AlignmentActivityInput) -> Any: ...


class CatalogFragmentAdapterPort(Protocol):
    def prepare_streaming(
        self,
        request: AlignmentActivityInput,
        manifest: Any,
        media_artifacts: Sequence[AlignedMediaArtifactV1] = (),
    ) -> tuple[Any, Sequence[Any]]: ...


class ContinuousDatasetProjectionPort(Protocol):
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
    ) -> DatasetIngestViewerTarget: ...


class PostgresContinuousEpisodeManifestDiscovery:
    """Project recording-config cameras into the existing P08 Manifest contract."""

    def __init__(self, connection_factory: Callable[[], Any]) -> None:
        self._connection_factory = connection_factory

    def find(
        self, *, project_id: str, region_code: str, rollout_id: str
    ) -> ManifestDiscoveryV1 | None:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT upload.upload_document, recording.robot_id
                      FROM ingest.recording_episode_processing processing
                      JOIN ingest.continuous_recordings recording
                        ON recording.organization_id = processing.organization_id
                       AND recording.project_id = processing.project_id
                       AND recording.region_code = processing.region_code
                       AND recording.recording_id = processing.recording_id
                      JOIN ingest.recording_uploads upload
                        ON upload.organization_id = recording.organization_id
                       AND upload.project_id = recording.project_id
                       AND upload.region_code = recording.region_code
                       AND upload.upload_id = recording.recording_upload_id
                     WHERE processing.project_id = %s AND processing.region_code = %s
                       AND processing.recording_id || ':' || processing.episode_id = %s
                       AND upload.status = 'COMMITTED'
                    """,
                    (project_id, region_code, rollout_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                upload = RecordingUpload.model_validate(row[0])
                config = upload.command.recording_config
                return ManifestDiscoveryV1(
                    robot_id=str(row[1]),
                    cameras=tuple(
                        ManifestCameraV1(
                            camera_id=camera.modality_key,
                            topic=camera.modality_key,
                            encoding=camera.codec,
                        )
                        for camera in config.cameras
                    ),
                    topics=tuple(
                        [
                            ManifestTopicV1(name=camera.modality_key, required=True)
                            for camera in config.cameras
                        ]
                        + [
                            ManifestTopicV1(name=sensor.topic, required=sensor.required)
                            for sensor in config.sensors
                        ]
                    ),
                )
        finally:
            connection.close()


class PostgresContinuousEpisodeWorkflowInputResolver:
    """Resolve only persisted immutable facts after an Outbox event is claimed."""

    def __init__(
        self,
        connection_factory: Callable[[], Any],
        catalog: LanceCatalogPort,
        *,
        media_task_queue: str = "hc-media-pipeline",
    ) -> None:
        self._connection_factory = connection_factory
        self._catalog = catalog
        self._media_task_queue = media_task_queue

    def resolve(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        episode_id: str,
        finalized_revision: int,
    ) -> ContinuousEpisodeWorkflowInput:
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT processing.episode_document, recording.recording_document,
                           upload.upload_document
                      FROM ingest.recording_episode_processing processing
                      JOIN ingest.continuous_recordings recording
                        ON recording.organization_id = processing.organization_id
                       AND recording.project_id = processing.project_id
                       AND recording.region_code = processing.region_code
                       AND recording.recording_id = processing.recording_id
                      JOIN ingest.recording_uploads upload
                        ON upload.organization_id = recording.organization_id
                       AND upload.project_id = recording.project_id
                       AND upload.region_code = recording.region_code
                       AND upload.upload_id = recording.recording_upload_id
                     WHERE processing.organization_id = %s AND processing.project_id = %s
                       AND processing.region_code = %s AND processing.recording_id = %s
                       AND processing.episode_id = %s AND upload.status = 'COMMITTED'
                    """,
                    (organization_id, project_id, region_code, recording_id, episode_id),
                )
                row = cursor.fetchone()
                if row is None:
                    raise _blocked("the finalized continuous Episode input is missing")
                from .asset_models import EpisodeProcessing

                processing = EpisodeProcessing.model_validate(row[0])
                recording = ContinuousRecording.model_validate(row[1])
                upload = RecordingUpload.model_validate(row[2])
                if processing.finalized_revision != finalized_revision:
                    raise _blocked("the continuous Episode finalized revision has changed")
                if processing.status in {
                    EpisodeProcessingStatus.QC_FAILED,
                    EpisodeProcessingStatus.READY,
                }:
                    raise _blocked("the continuous Episode is already terminal")
                cursor.execute(
                    """
                    SELECT asset.asset_document
                      FROM ingest.recording_episode_asset_windows asset_window
                      JOIN ingest.recording_upload_assets asset
                        ON asset.organization_id = asset_window.organization_id
                       AND asset.project_id = asset_window.project_id
                       AND asset.region_code = asset_window.region_code
                       AND asset.upload_id = asset_window.upload_id
                       AND asset.asset_id = asset_window.asset_id
                     WHERE asset_window.organization_id = %s AND asset_window.project_id = %s
                       AND asset_window.region_code = %s AND asset_window.recording_id = %s
                       AND asset_window.episode_id = %s AND asset.status = 'COMMITTED'
                     ORDER BY asset.asset_path
                    """,
                    (organization_id, project_id, region_code, recording_id, episode_id),
                )
                raw_assets = tuple(
                    RecordingAsset.model_validate(item[0]) for item in cursor.fetchall()
                )
                dataset_id = self._task_dataset(
                    cursor,
                    organization_id=organization_id,
                    project_id=project_id,
                    collection_task_id=recording.collection_task_id,
                )
                configuration = upload.command.recording_config
                modality_keys = frozenset(
                    [camera.modality_key for camera in configuration.cameras]
                    + [sensor.topic for sensor in configuration.sensors]
                )
                if not configuration.sensors:
                    raise _blocked(
                        "recording config must enumerate SENSOR_DATA topics for worker QC"
                    )
                quality_profile = self._quality_profile(cursor, project_id, modality_keys)
                schema_ids = self._schema_targets(
                    cursor,
                    project_id=project_id,
                    region_code=region_code,
                    dataset_id=dataset_id,
                )
        finally:
            connection.close()

        snapshot = self._select_schema(
            project_id=project_id,
            dataset_id=dataset_id,
            schema_ids=schema_ids,
            modality_keys=modality_keys,
        )
        source_sha256 = canonical_hash(
            {
                "pipeline": "continuous-recording-episode/v1",
                "recording_id": recording_id,
                "episode_id": episode_id,
                "start_offset_ns": processing.start_offset_ns,
                "end_offset_ns": processing.end_offset_ns,
                "config": configuration.model_dump(mode="json"),
                "assets": [
                    {
                        "asset_id": str(asset.asset_id),
                        "role": asset.manifest.role.value,
                        "sha256": asset.manifest.sha256,
                    }
                    for asset in raw_assets
                ],
            }
        )
        camera_keys = tuple(camera.modality_key for camera in configuration.cameras)
        config_by_camera = {camera.camera_id: camera for camera in configuration.cameras}
        assets = tuple(
            ContinuousEpisodeAssetV1(
                asset_id=str(asset.asset_id),
                role=asset.manifest.role,
                camera_id=asset.manifest.camera_id,
                modality_key=(
                    None
                    if asset.manifest.camera_id is None
                    else config_by_camera[asset.manifest.camera_id].modality_key
                ),
                media_type=asset.manifest.media_type,
                object_key=asset.object_key,
                size_bytes=asset.manifest.size,
                content_sha256=asset.manifest.sha256,
            )
            for asset in raw_assets
        )
        expected_version = self._reserve_dataset_version(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            recording_id=recording_id,
            episode_id=episode_id,
            workflow_id=processing.workflow_id or "",
            dataset_id=dataset_id,
        )
        projection = ContinuousEpisodeProjectionSourceV1(
            organization_id=organization_id,
            project_id=project_id,
            region_code=region_code,
            recording_id=recording_id,
            recording_upload_id=str(recording.recording_upload_id),
            episode_id=episode_id,
            rollout_id=f"{recording_id}:{episode_id}",
            data_package_id=recording.data_package_id,
            collection_task_id=recording.collection_task_id,
            robot_id=recording.robot_id,
            source_sha256=source_sha256,
            manifest_fingerprint=recording.manifest_fingerprint,
            source_size_bytes=sum(asset.manifest.size for asset in raw_assets),
            started_at=_absolute_time(recording.capture_started_at, processing.start_offset_ns),
            ended_at=_absolute_time(recording.capture_started_at, processing.end_offset_ns),
            camera_modality_keys=camera_keys,
        )
        return ContinuousEpisodeWorkflowInput(
            projection=projection,
            dataset_id=dataset_id,
            schema_snapshot_id=snapshot.schema_snapshot_id,
            recording_config=configuration,
            assets=assets,
            quality_profile=quality_profile,
            alignment_profile=AlignmentProfileV1(
                profile_id=f"continuous:{snapshot.schema_snapshot_id}",
                converter_version="continuous-mp4-sensor/1",
                frequency_hz=30,
                required_modalities=modality_keys,
                stream_strategies={key: _strategy_for(key, configuration) for key in modality_keys},
                default_tolerance_ns=20_000_000,
            ),
            start_offset_ns=processing.start_offset_ns,
            end_offset_ns=processing.end_offset_ns,
            expected_dataset_version=expected_version,
            media_task_queue=self._media_task_queue,
        )

    def _reserve_dataset_version(
        self,
        *,
        organization_id: str,
        project_id: str,
        region_code: str,
        recording_id: str,
        episode_id: str,
        workflow_id: str,
        dataset_id: str,
    ) -> int:
        if not workflow_id:
            raise _blocked("the continuous Episode workflow identity is missing")
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
                    (":".join((organization_id, project_id, region_code, dataset_id)),),
                )
                cursor.execute(
                    """
                    SELECT dataset_id, dataset_version, reservation_status, workflow_id
                      FROM ingest.recording_episode_dataset_version_reservations
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND recording_id = %s AND episode_id = %s
                     FOR UPDATE
                    """,
                    (
                        organization_id,
                        project_id,
                        region_code,
                        recording_id,
                        episode_id,
                    ),
                )
                existing = cursor.fetchone()
                if existing is not None and str(existing[2]) in {"RESERVED", "COMMITTED"}:
                    if str(existing[0]) != dataset_id or str(existing[3]) != workflow_id:
                        raise _blocked("the continuous Episode version reservation changed lineage")
                    connection.commit()
                    return int(existing[1])
                cursor.execute(
                    """
                    SELECT workflow_id
                      FROM ingest.recording_episode_dataset_version_reservations
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND dataset_id = %s AND reservation_status = 'RESERVED'
                     FOR UPDATE
                    """,
                    (organization_id, project_id, region_code, dataset_id),
                )
                busy = cursor.fetchone()
                if busy is not None:
                    raise _blocked("another continuous Episode owns the next Dataset version")
                current = self._catalog.current_version(dataset_id, project_id=project_id)
                dataset_version = 1 if current is None else current.version + 1
                now = datetime.now(timezone.utc)
                if existing is None:
                    cursor.execute(
                        """
                        INSERT INTO ingest.recording_episode_dataset_version_reservations (
                            organization_id, project_id, region_code, recording_id, episode_id,
                            workflow_id, dataset_id, dataset_version, reservation_status,
                            created_at, updated_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'RESERVED', %s, %s)
                        """,
                        (
                            organization_id,
                            project_id,
                            region_code,
                            recording_id,
                            episode_id,
                            workflow_id,
                            dataset_id,
                            dataset_version,
                            now,
                            now,
                        ),
                    )
                else:
                    cursor.execute(
                        """
                        UPDATE ingest.recording_episode_dataset_version_reservations
                           SET workflow_id = %s, dataset_id = %s, dataset_version = %s,
                               reservation_status = 'RESERVED', updated_at = %s
                         WHERE organization_id = %s AND project_id = %s AND region_code = %s
                           AND recording_id = %s AND episode_id = %s
                           AND reservation_status = 'RELEASED'
                        """,
                        (
                            workflow_id,
                            dataset_id,
                            dataset_version,
                            now,
                            organization_id,
                            project_id,
                            region_code,
                            recording_id,
                            episode_id,
                        ),
                    )
                    if cursor.rowcount != 1:
                        raise _blocked("the continuous Episode version reservation changed")
            connection.commit()
            return dataset_version
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _task_dataset(
        cursor: Any,
        *,
        organization_id: str,
        project_id: str,
        collection_task_id: str,
    ) -> str:
        cursor.execute(
            """
            SELECT dataset_id FROM collection_tasks.collection_tasks
             WHERE organization_id = %s AND project_id = %s AND collection_task_id = %s
            """,
            (organization_id, project_id, collection_task_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise _blocked("the recording collection task has no Dataset")
        dataset_id = str(row[0])
        try:
            _DATASET_ID.validate_python(dataset_id)
        except ValidationError as exc:
            raise _blocked("the recording collection task has an invalid Dataset") from exc
        return dataset_id

    @staticmethod
    def _quality_profile(cursor: Any, project_id: str, topics: frozenset[str]) -> Any:
        from hc_data_platform.quality.models import QualityProfileV1

        cursor.execute(
            "SELECT profile_json FROM quality_profiles WHERE project_id = %s "
            "ORDER BY profile_id, profile_version",
            (project_id,),
        )
        candidates = [
            profile
            for row in cursor.fetchall()
            if (profile := QualityProfileV1.model_validate(row[0])).required_topics == topics
            and profile.default_timing.target_frequency_hz == 30
        ]
        if len(candidates) != 1:
            raise _blocked(
                "the project must have exactly one 30 Hz QC profile for recording config topics"
            )
        return candidates[0]

    @staticmethod
    def _schema_targets(
        cursor: Any, *, project_id: str, region_code: str, dataset_id: str
    ) -> tuple[str, ...]:
        cursor.execute(
            """
            SELECT dataset_schema_snapshot_id FROM annotation.tag_schema_bindings
             WHERE project_id = %s AND region_code = %s AND task_kind = 'TAGGING'
               AND dataset_id = %s ORDER BY dataset_schema_snapshot_id
            """,
            (project_id, region_code, dataset_id),
        )
        return tuple(str(row[0]) for row in cursor.fetchall())

    def _select_schema(
        self,
        *,
        project_id: str,
        dataset_id: str,
        schema_ids: Sequence[str],
        modality_keys: frozenset[str],
    ) -> DatasetSchemaSnapshot:
        candidates = [
            DatasetSchemaSnapshot.create(
                project_id=project_id,
                dataset_id=dataset_id,
                schema_snapshot_id=schema_id,
                frequency_hz=30.0,
                fields={topic: "json" for topic in modality_keys},
            )
            for schema_id in schema_ids
        ]
        existing = self._catalog.schema_for(project_id, dataset_id)
        compatible = [item for item in candidates if existing is None or existing == item]
        if len(compatible) != 1:
            raise _blocked("the task Dataset must have one compatible TAGGING Schema binding")
        try:
            self._catalog.register_schema(compatible[0])
        except Exception as exc:
            raise _blocked("the continuous Episode Dataset Schema is incompatible") from exc
        return compatible[0]


class ContinuousEpisodeProcessingService:
    def __init__(
        self,
        *,
        connection_factory: Callable[[], Any],
        repository: ContinuousRecordingRepository,
        storage: ObjectStoragePort,
        decoder: DecoderProbe,
        quality: QualityEvaluationPort,
        alignment: AlignmentPort,
        fragment_writers: FragmentWriterFactoryPort,
        staging: ProjectionArtifactStorePort,
        staging_ttl: timedelta,
        catalog_fragments: CatalogFragmentAdapterPort,
        catalog: LanceCatalogPort,
        media_repository: AlignedMediaRepositoryPort,
        dataset_projection: ContinuousDatasetProjectionPort,
        ffmpeg_binary: str = "ffmpeg",
        ffprobe_binary: str = "ffprobe",
        process_timeout_seconds: float = 3_600,
    ) -> None:
        self._connection_factory = connection_factory
        self._repository = repository
        self._storage = storage
        self._decoder = decoder
        self._quality = quality
        self._alignment = alignment
        self._fragment_writers = fragment_writers
        self._staging = staging
        self._staging_ttl = staging_ttl
        self._catalog_fragments = catalog_fragments
        self._catalog = catalog
        self._media_repository = media_repository
        self._dataset_projection = dataset_projection
        self._ffmpeg = ffmpeg_binary
        self._ffprobe = ffprobe_binary
        self._timeout = process_timeout_seconds

    def update_state(
        self, request: ContinuousEpisodeStateActivityInput
    ) -> ContinuousEpisodeStateActivityOutput:
        scope = RecordingScope(
            organization_id=request.organization_id,
            project_id=request.project_id,
            region_code=request.region_code,
        )
        current = self._repository.get_episode_processing(
            scope, request.recording_id, request.episode_id
        )
        if current is None:
            raise ContinuousEpisodeProcessingError("continuous Episode ledger is missing")
        updates = {
            "status": request.new_status,
            "qc_report_id": request.qc_report_id or current.qc_report_id,
            "alignment_attempt_id": request.alignment_attempt_id or current.alignment_attempt_id,
            "dataset_id": request.dataset_id,
            "dataset_episode_id": request.dataset_episode_id,
            "dataset_version": request.dataset_version,
            "lance_version": request.lance_version,
            "annotation_task_id": request.annotation_task_id,
            "aligned_media_camera_count": request.aligned_media_camera_count,
            "failure_code": request.failure_code,
            "failure_stage": request.failure_stage,
            "updated_at": datetime.now(timezone.utc),
        }
        desired = current.model_copy(update=updates)
        if current.status is request.new_status:
            comparable = desired.model_copy(update={"updated_at": current.updated_at})
            if comparable != current:
                raise ContinuousEpisodeProcessingError(
                    "idempotent Episode state replay disagrees with persisted evidence"
                )
            self._settle_version_reservation(current)
            return ContinuousEpisodeStateActivityOutput(episode=current)
        if current.status is not request.expected_status:
            raise ContinuousEpisodeProcessingError(
                f"Episode state is {current.status.value}, expected {request.expected_status.value}"
            )
        persisted = self._repository.save_episode_processing(
            desired, expected_status=request.expected_status
        )
        self._settle_version_reservation(persisted)
        return ContinuousEpisodeStateActivityOutput(episode=persisted)

    def qc(self, request: ContinuousEpisodeWorkflowInput) -> ContinuousEpisodeQcActivityOutput:
        findings: set[str] = set()
        sensor_count = 0
        with self._localized_assets(
            request,
            roles={
                RecordingAssetRole.RAW_VIDEO,
                RecordingAssetRole.SENSOR_DATA,
                RecordingAssetRole.RECORDING_CONFIG,
            },
        ) as localized:
            config_asset = _only_role(request.assets, RecordingAssetRole.RECORDING_CONFIG)
            try:
                raw_config = json.loads(localized[config_asset.asset_id].read_bytes())
                persisted_config = RecordingConfigurationV1.model_validate(raw_config)
            except Exception:
                persisted_config = None
                findings.add("RECORDING_CONFIG_INVALID")
            if persisted_config != request.recording_config:
                findings.add("RECORDING_CONFIG_MISMATCH")
            if any(
                camera.clock_domain != request.recording_config.primary_clock_domain
                for camera in request.recording_config.cameras
            ) or any(
                sensor.clock_domain != request.recording_config.primary_clock_domain
                for sensor in request.recording_config.sensors
            ):
                findings.add("CLOCK_DOMAIN_MISMATCH")

            config_by_camera = {
                camera.camera_id: camera for camera in request.recording_config.cameras
            }
            sensor_counter = [0]

            def quality_observations() -> Iterator[QualityStreamObservationV1]:
                for asset in _assets_for_role(request.assets, RecordingAssetRole.SENSOR_DATA):
                    yield from self._sensor_quality_observations(
                        localized[asset.asset_id],
                        request,
                        findings=findings,
                        counter=sensor_counter,
                    )
                for asset in _assets_for_role(request.assets, RecordingAssetRole.RAW_VIDEO):
                    camera = config_by_camera.get(asset.camera_id or "")
                    if camera is None:
                        findings.add("VIDEO_CAMERA_MISSING_FROM_CONFIG")
                        continue
                    yield from self._video_quality_observations(
                        localized[asset.asset_id],
                        request=request,
                        camera=camera,
                        findings=findings,
                    )

            joints, actions = self._openarm_numeric_quality(request, localized, findings)
            quality_data = QualityInputV1(
                rollout_id=request.projection.rollout_id,
                source_sha256=request.projection.source_sha256,
                start_ns=_datetime_ns(request.projection.started_at),
                end_ns=_datetime_ns(request.projection.ended_at),
                topic_timestamps_ns={},
                joints=joints,
                actions=actions,
            )
            report = self._quality.evaluate_stream(
                quality_data, quality_observations(), request.quality_profile
            )
            sensor_count = sensor_counter[0]
            if report.status is not QualityStatus.PASS:
                findings.add(f"QUALITY_{report.status.value}")

        status = "PASS" if not findings else "REJECT"
        report_document = {
            "schema_version": "continuous-episode-qc/v1",
            "recording_id": request.projection.recording_id,
            "episode_id": request.projection.episode_id,
            "rollout_id": request.projection.rollout_id,
            "source_sha256": request.projection.source_sha256,
            "status": status,
            "finding_codes": sorted(findings),
            "video_count": len(_assets_for_role(request.assets, RecordingAssetRole.RAW_VIDEO)),
            "sensor_message_count": sensor_count,
            "quality_report": report.model_dump(mode="json"),
        }
        report_sha = canonical_hash(report_document)
        report_id = f"crqc_{report_sha[:32]}"
        self._put_qc_report(request, report_id, report_sha, status, report_document)
        return ContinuousEpisodeQcActivityOutput(
            report_id=report_id,
            status=status,
            finding_codes=tuple(sorted(findings)),
            inspected_video_count=len(
                _assets_for_role(request.assets, RecordingAssetRole.RAW_VIDEO)
            ),
            inspected_sensor_message_count=sensor_count,
        )

    def align(
        self, request: ContinuousEpisodeWorkflowInput
    ) -> ContinuousEpisodeAlignmentActivityOutput:
        start_ns = _datetime_ns(request.projection.started_at)
        end_ns = _datetime_ns(request.projection.ended_at)
        if request.recording_config.recorder_version == "openarm-session/v2":
            from .openarm_grid import frame_at, frame_time

            end_ns = start_ns + frame_time(
                frame_at(request.end_offset_ns) - frame_at(request.start_offset_ns)
            )
        stream_kinds = {
            camera.modality_key: ModalityKind.IMAGE for camera in request.recording_config.cameras
        }
        stream_kinds.update(
            {
                sensor.topic: _sensor_kind(sensor.topic)
                for sensor in request.recording_config.sensors
            }
        )
        attempt_id = f"continuous-{request.projection.source_sha256[:24]}"
        data = AlignmentInputV1(
            rollout_id=request.projection.rollout_id,
            source_sha256=request.projection.source_sha256,
            attempt_id=attempt_id,
            start_ns=start_ns,
            end_ns=end_ns,
            streams={
                name: ModalityStreamV1(kind=kind, samples=())
                for name, kind in sorted(stream_kinds.items())
            },
        )
        alignment_request = AlignmentActivityInput(
            organization_id=request.projection.organization_id,
            project_id=request.projection.project_id,
            region_code=request.projection.region_code,
            dataset_id=request.dataset_id,
            schema_snapshot_id=request.schema_snapshot_id,
            data=data,
            profile=request.alignment_profile,
        )
        sensor_assets = _assets_for_role(request.assets, RecordingAssetRole.SENSOR_DATA)
        with self._localized_assets(request, roles={RecordingAssetRole.SENSOR_DATA}) as localized:
            iterators: list[Iterator[tuple[int, str, TimedSampleV1]]] = [
                self._sensor_alignment_samples(localized[item.asset_id], request)
                for item in sensor_assets
            ]
            iterators.extend(
                self._camera_alignment_samples(request, camera)
                for camera in request.recording_config.cameras
            )
            merged = heapq.merge(*iterators, key=lambda item: (item[0], item[1]))
            writer = _CompleteFrameWriter(self._fragment_writers.create(alignment_request))
            manifest = self._alignment.align_stream_to_writer(
                rollout_id=data.rollout_id,
                source_sha256=data.source_sha256,
                attempt_id=data.attempt_id,
                start_ns=data.start_ns,
                end_ns=data.end_ns,
                stream_kinds=stream_kinds,
                samples=((topic, sample) for _timestamp, topic, sample in merged),
                profile=request.alignment_profile,
                writer=writer,
            )
        staging = self._publish_staging(request, manifest)
        current = self._catalog.current_version(
            request.dataset_id, project_id=request.projection.project_id
        )
        expected = 1 if current is None else current.version + 1
        if expected != request.expected_dataset_version:
            raise ContinuousEpisodeVersionConflict(
                "reserved Dataset version changed before continuous Episode alignment"
            )
        return ContinuousEpisodeAlignmentActivityOutput(
            alignment=alignment_request,
            staged_manifest=manifest,
            alignment_staging=staging,
            expected_dataset_version=expected,
        )

    def commit_bundle(
        self,
        *,
        workflow_input: ContinuousEpisodeWorkflowInput,
        alignment: AlignmentActivityInput,
        staged_manifest: Any,
        alignment_staging: AlignmentStagingArtifactV1,
        expected_dataset_version: int,
        expected_camera_ids: Sequence[str],
        media_artifacts: Sequence[AlignedMediaArtifactV1],
    ) -> AlignedBundleCommitActivityOutput:
        source = workflow_input.projection
        if (
            expected_dataset_version != workflow_input.expected_dataset_version
            or staged_manifest.row_count != alignment_staging.row_count
            or set(expected_camera_ids) != {item.camera_id for item in media_artifacts}
            or len(expected_camera_ids) != len(media_artifacts)
            or any(
                item.status is not AlignedMediaArtifactStatus.READY
                or item.dataset_id != workflow_input.dataset_id
                or item.rollout_id != source.rollout_id
                or item.dataset_version != expected_dataset_version
                or item.frame_count != staged_manifest.row_count
                or item.alignment_version != alignment_staging.alignment_version
                for item in media_artifacts
            )
        ):
            raise ContinuousEpisodeProcessingError(
                "continuous Episode aligned-media bundle is incomplete"
            )
        current = self._catalog.current_version(
            workflow_input.dataset_id, project_id=source.project_id
        )
        next_version = 1 if current is None else current.version + 1
        recovering = (
            current is not None
            and current.version >= expected_dataset_version
            and source.rollout_id in current.committed_rollouts
        )
        if next_version != expected_dataset_version and not recovering:
            raise ContinuousEpisodeVersionConflict(
                "reserved Dataset version changed before continuous Episode commit"
            )
        scope = AlignedMediaScopeV1(
            organization_id=source.organization_id,
            project_id=source.project_id,
            region_code=source.region_code,
        )
        artifact_ids = tuple(item.artifact_id for item in media_artifacts)
        now = datetime.now(timezone.utc)
        self._media_repository.begin_dataset_commit(
            scope=scope,
            artifact_ids=artifact_ids,
            lease_expires_at=now + timedelta(hours=6),
            now=now,
        )
        if recovering:
            # The immutable Lance receipt survives a lost downstream receipt,
            # later appends, and staging cleanup. Verify it before replaying only
            # the missing projection/annotation work.
            version = self._catalog.version_snapshot(
                workflow_input.dataset_id,
                project_id=source.project_id,
                version=expected_dataset_version,
            )
            lineage = self._catalog.lineage(
                workflow_input.dataset_id,
                source.rollout_id,
                project_id=source.project_id,
                version=expected_dataset_version,
            )
            if (
                lineage.source_sha256 != source.source_sha256
                or lineage.converter_version != staged_manifest.converter_version
                or lineage.schema_snapshot_id != workflow_input.schema_snapshot_id
            ):
                raise ContinuousEpisodeProcessingError("committed Episode lineage changed")
            row_count = 0
            for begin in range(0, staged_manifest.row_count + 1, 4096):
                rows = self._catalog.read_steps(
                    workflow_input.dataset_id,
                    source.rollout_id,
                    begin,
                    min(begin + 4096, staged_manifest.row_count + 1),
                    project_id=source.project_id,
                    version=expected_dataset_version,
                ).steps
                if not all(row.sample_valid for row in rows):
                    raise ContinuousEpisodeProcessingError("committed Episode has invalid rows")
                row_count += len(rows)
            if row_count != staged_manifest.row_count:
                raise ContinuousEpisodeProcessingError("committed Episode rows are incomplete")
            ready = DerivedReadyV1(
                project_id=source.project_id,
                dataset_id=workflow_input.dataset_id,
                rollout_id=source.rollout_id,
                source_sha256=source.source_sha256,
                converter_version=lineage.converter_version,
                dataset_version=version.version,
                lance_version=version.lance_version,
                step_count=row_count,
                content_hash=version.content_hash,
            )
        else:
            with self._staging.local_file(
                alignment_staging.object_key,
                expected_sha256=alignment_staging.content_sha256,
                expected_size=alignment_staging.size_bytes,
            ) as path:
                local_manifest = staged_manifest.model_copy(
                    update={"staging_uri": path.resolve().as_uri()}
                )
                catalog_manifest, steps = self._catalog_fragments.prepare_streaming(
                    alignment, local_manifest, media_artifacts
                )
                version, ready = self._catalog.commit_fragment(
                    catalog_manifest, steps, expected_version=expected_dataset_version
                )
        if version.version != expected_dataset_version:
            raise ContinuousEpisodeProcessingError(
                "Lance committed a version different from canonical media"
            )
        self._media_repository.mark_dataset_committed(
            scope=scope,
            artifact_ids=artifact_ids,
            now=datetime.now(timezone.utc),
        )
        data = alignment.data
        if data is None:
            raise ContinuousEpisodeProcessingError("continuous alignment metadata is missing")
        viewer = self._dataset_projection.project_continuous_episode(
            source=source,
            alignment=data,
            schema_snapshot_id=workflow_input.schema_snapshot_id,
            frequency_hz=30,
            version=version,
            ready=ready,
            media_artifacts=media_artifacts,
        )
        self._mark_version_reservation_committed(workflow_input, dataset_version=version.version)
        return AlignedBundleCommitActivityOutput(
            version=version, derived_ready=ready, viewer_target=viewer
        )

    def _settle_version_reservation(self, episode: Any) -> None:
        if episode.status not in {
            EpisodeProcessingStatus.QC_FAILED,
            EpisodeProcessingStatus.FAILED,
            EpisodeProcessingStatus.READY,
        }:
            return
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                if episode.status is EpisodeProcessingStatus.READY:
                    cursor.execute(
                        """
                        SELECT reservation_status, dataset_id, dataset_version
                          FROM ingest.recording_episode_dataset_version_reservations
                         WHERE organization_id = %s AND project_id = %s AND region_code = %s
                           AND recording_id = %s AND episode_id = %s
                        """,
                        (
                            episode.scope.organization_id,
                            episode.scope.project_id,
                            episode.scope.region_code,
                            episode.recording_id,
                            episode.episode_id,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None or (str(row[0]), str(row[1]), int(row[2])) != (
                        "COMMITTED",
                        episode.dataset_id,
                        episode.dataset_version,
                    ):
                        raise ContinuousEpisodeProcessingError(
                            "READY Episode has no committed Dataset version reservation"
                        )
                else:
                    cursor.execute(
                        """
                        UPDATE ingest.recording_episode_dataset_version_reservations
                           SET reservation_status = 'RELEASED', updated_at = %s
                         WHERE organization_id = %s AND project_id = %s AND region_code = %s
                           AND recording_id = %s AND episode_id = %s
                           AND reservation_status = 'RESERVED'
                        """,
                        (
                            datetime.now(timezone.utc),
                            episode.scope.organization_id,
                            episode.scope.project_id,
                            episode.scope.region_code,
                            episode.recording_id,
                            episode.episode_id,
                        ),
                    )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _mark_version_reservation_committed(
        self,
        request: ContinuousEpisodeWorkflowInput,
        *,
        dataset_version: int,
    ) -> None:
        source = request.projection
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE ingest.recording_episode_dataset_version_reservations
                       SET reservation_status = 'COMMITTED', updated_at = %s
                     WHERE organization_id = %s AND project_id = %s AND region_code = %s
                       AND recording_id = %s AND episode_id = %s
                       AND dataset_id = %s AND dataset_version = %s
                       AND reservation_status IN ('RESERVED', 'RELEASED')
                    """,
                    (
                        datetime.now(timezone.utc),
                        source.organization_id,
                        source.project_id,
                        source.region_code,
                        source.recording_id,
                        source.episode_id,
                        request.dataset_id,
                        dataset_version,
                    ),
                )
                if cursor.rowcount != 1:
                    cursor.execute(
                        """
                        SELECT reservation_status
                          FROM ingest.recording_episode_dataset_version_reservations
                         WHERE organization_id = %s AND project_id = %s AND region_code = %s
                           AND recording_id = %s AND episode_id = %s
                           AND dataset_id = %s AND dataset_version = %s
                        """,
                        (
                            source.organization_id,
                            source.project_id,
                            source.region_code,
                            source.recording_id,
                            source.episode_id,
                            request.dataset_id,
                            dataset_version,
                        ),
                    )
                    row = cursor.fetchone()
                    if row is None or str(row[0]) != "COMMITTED":
                        raise ContinuousEpisodeProcessingError(
                            "continuous Episode Dataset version reservation was lost"
                        )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @contextmanager
    def _localized_assets(
        self,
        request: ContinuousEpisodeWorkflowInput,
        *,
        roles: set[RecordingAssetRole],
    ) -> Iterator[dict[str, Path]]:
        selected = tuple(item for item in request.assets if item.role in roles)
        with tempfile.TemporaryDirectory(prefix="continuous-episode-") as raw_directory:
            root = Path(raw_directory)
            localized: dict[str, Path] = {}
            for asset in selected:
                suffix = Path(urlparse(asset.object_key).path).suffix or ".raw"
                path = root / f"{asset.asset_id}{suffix}"
                digest = hashlib.sha256()
                written = 0
                with path.open("wb") as target:
                    for chunk in self._storage.read_chunks(asset.object_key):
                        if not isinstance(chunk, bytes):
                            raise TypeError("object storage returned non-bytes")
                        written += len(chunk)
                        if written > asset.size_bytes:
                            raise ContinuousEpisodeProcessingError(
                                "localized raw asset exceeded its receipt"
                            )
                        digest.update(chunk)
                        target.write(chunk)
                if written != asset.size_bytes or digest.hexdigest() != asset.content_sha256:
                    raise ContinuousEpisodeProcessingError(
                        "localized raw asset differs from its immutable receipt"
                    )
                localized[asset.asset_id] = path
            yield localized

    def _openarm_numeric_quality(self, request, localized, findings):
        """Evaluate the actual recorded vectors as well as MCAP timing and video."""
        from hc_data_platform.quality.models import ActionObservation, JointObservation

        if request.recording_config.recorder_version != "openarm-session/v2":
            return (), ()
        configured = {s.topic: s for s in request.recording_config.sensors}
        joints, actions = [], []
        layouts = {}
        lower, upper = (
            _datetime_ns(request.projection.started_at),
            _datetime_ns(request.projection.ended_at),
        )
        for asset in _assets_for_role(request.assets, RecordingAssetRole.SENSOR_DATA):
            for count, (_, channel, message) in enumerate(
                _mcap_messages(localized[asset.asset_id]), start=1
            ):
                if count > _MAX_SENSOR_MESSAGES:
                    raise ContinuousEpisodeProcessingError(
                        "sensor MCAP exceeds the bounded message limit"
                    )
                topic = str(channel.topic)
                if topic not in {"/action", "/observation/state"} or topic not in configured:
                    continue
                timestamp = _episode_sensor_timestamp(
                    int(message.log_time), configured[topic].timestamp_mode, request
                )
                if timestamp is None:
                    continue
                if not lower <= timestamp < upper:
                    continue
                try:
                    body = json.loads(message.data)
                    names, units, values = body["names"], body["units"], body["values"]
                    if (
                        not isinstance(values, list)
                        or not 1 <= len(values) <= 256
                        or len(names) != len(values)
                        or len(units) != len(values)
                        or len(set(names)) != len(names)
                        or any(not isinstance(n, str) or not n for n in names + units)
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in values)
                    ):
                        raise ValueError("invalid vector layout")
                    layout = (tuple(names), tuple(units))
                    if layouts.setdefault(topic, layout) != layout:
                        raise ValueError("vector layout changed")
                    if topic == "/action":
                        actions.append(
                            ActionObservation(timestamp_ns=timestamp, values=tuple(values))
                        )
                    else:
                        joints.append(
                            JointObservation(
                                timestamp_ns=timestamp,
                                positions=dict(zip(names, values, strict=True)),
                            )
                        )
                except (ValueError, TypeError, KeyError, AttributeError):
                    findings.add("OPENARM_CAPTURE_VECTOR_INVALID")
        if not joints or not actions or layouts.get("/action") != layouts.get("/observation/state"):
            findings.add("OPENARM_CAPTURE_VECTOR_LAYOUT_MISMATCH")
        return tuple(joints), tuple(actions)

    def _sensor_quality_observations(
        self,
        path: Path,
        request: ContinuousEpisodeWorkflowInput,
        *,
        findings: set[str],
        counter: list[int],
    ) -> Iterator[QualityStreamObservationV1]:
        configured = {sensor.topic: sensor for sensor in request.recording_config.sensors}
        seen: set[str] = set()
        try:
            for count, (schema, channel, message) in enumerate(_mcap_messages(path), start=1):
                if count > _MAX_SENSOR_MESSAGES:
                    raise ContinuousEpisodeProcessingError(
                        "sensor MCAP exceeds the bounded message limit"
                    )
                topic = str(channel.topic)
                if topic not in configured:
                    findings.add("SENSOR_TOPIC_INVENTORY_MISMATCH")
                    continue
                seen.add(topic)
                if _looks_like_jpeg_message(channel, message.data):
                    findings.add("SENSOR_MCAP_CONTAINS_JPEG")
                if schema is None or not self._decoder.supports(
                    channel.message_encoding, schema.encoding
                ):
                    findings.add("SENSOR_DECODER_UNAVAILABLE")
                else:
                    try:
                        self._decoder.probe(
                            message_encoding=channel.message_encoding,
                            schema_encoding=schema.encoding,
                            schema_name=schema.name,
                            schema_data=schema.data,
                            message_data=message.data,
                        )
                    except Exception:
                        findings.add("SENSOR_DECODE_FAILED")
                timestamp = _episode_sensor_timestamp(
                    int(message.log_time), configured[topic].timestamp_mode, request
                )
                if timestamp is None:
                    continue
                if (
                    _datetime_ns(request.projection.started_at)
                    <= timestamp
                    < _datetime_ns(request.projection.ended_at)
                ):
                    if (
                        request.recording_config.recorder_version == "openarm-session/v2"
                        and topic == "/openarm/capture_validity"
                    ):
                        try:
                            validity = json.loads(message.data)
                            if validity.get("valid") is not True:
                                findings.add("OPENARM_CAPTURE_FRAME_INVALID")
                        except (ValueError, TypeError, AttributeError):
                            findings.add("OPENARM_CAPTURE_VALIDITY_INVALID")
                    counter[0] += 1
                    yield QualityStreamObservationV1(
                        topic=topic,
                        timestamp_ns=timestamp,
                        is_camera=False,
                    )
        except Exception as exc:
            if isinstance(exc, ContinuousEpisodeProcessingError):
                raise
            findings.add("SENSOR_MCAP_INVALID")
        required = {sensor.topic for sensor in request.recording_config.sensors if sensor.required}
        if not required.issubset(seen):
            findings.add("SENSOR_REQUIRED_TOPIC_MISSING")

    def _video_quality_observations(
        self,
        path: Path,
        *,
        request: ContinuousEpisodeWorkflowInput,
        camera: Any,
        findings: set[str],
    ) -> Iterator[QualityStreamObservationV1]:
        frame_inventory = path.with_name(f"{path.name}.frames.csv")
        try:
            probe = subprocess.run(
                [
                    self._ffprobe,
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=codec_name,width,height,avg_frame_rate,time_base,start_time,duration",
                    "-of",
                    "json",
                    str(path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=min(self._timeout, 600),
            )
            payload = json.loads(probe.stdout)
            streams = payload.get("streams")
            if not isinstance(streams, list) or len(streams) != 1:
                raise ValueError("ffprobe inventory is incomplete")
            stream = streams[0]
            if str(stream.get("codec_name", "")).lower() != camera.codec.lower():
                findings.add("VIDEO_CODEC_MISMATCH")
            if not _rate_matches(str(stream.get("avg_frame_rate", "")), camera.fps):
                findings.add("VIDEO_FPS_MISMATCH")
            if str(stream.get("time_base", "")) != (
                f"{camera.time_base_numerator}/{camera.time_base_denominator}"
            ):
                findings.add("VIDEO_TIME_BASE_MISMATCH")
            if camera.width is not None and int(stream.get("width", 0)) != camera.width:
                findings.add("VIDEO_WIDTH_MISMATCH")
            if camera.height is not None and int(stream.get("height", 0)) != camera.height:
                findings.add("VIDEO_HEIGHT_MISMATCH")
            clip_start_ns = request.start_offset_ns - camera.capture_start_offset_ns
            clip_end_ns = request.end_offset_ns - camera.capture_start_offset_ns
            duration_ns = round(float(stream.get("duration") or 0) * 1_000_000_000)
            if clip_start_ns < 0 or clip_end_ns > duration_ns:
                findings.add("VIDEO_EPISODE_WINDOW_MISSING")
            self._decode_video_window(path, clip_start_ns, clip_end_ns)
            with frame_inventory.open("w", encoding="utf-8") as inventory:
                subprocess.run(
                    [
                        self._ffprobe,
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-read_intervals",
                        (
                            f"{max(0, clip_start_ns) / 1_000_000_000:.9f}%"
                            f"{max(0, clip_end_ns) / 1_000_000_000:.9f}"
                        ),
                        "-show_entries",
                        "frame=best_effort_timestamp_time",
                        "-of",
                        "csv=p=0",
                        str(path),
                    ],
                    check=True,
                    stdout=inventory,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=min(self._timeout, 600),
                )
            capture_start_ns = _datetime_ns(request.projection.started_at) - request.start_offset_ns
            previous = -1
            observed = 0
            with frame_inventory.open("r", encoding="utf-8") as inventory:
                for line in inventory:
                    # FFprobe appends an empty CSV side-data column to some keyframes
                    # (for example ``0.000000,``). The requested timestamp is always
                    # the first column, so retain frame zero instead of treating it as
                    # a missing PTS.
                    # FFprobe CSV may put a side-data separator on its own blank
                    # line. It is not a frame with missing timestamps.
                    if not line.strip():
                        continue
                    raw_timestamp = line.strip().split(",", maxsplit=1)[0]
                    if not raw_timestamp or raw_timestamp == "N/A":
                        findings.add("VIDEO_PTS_MISSING")
                        continue
                    pts_ns = round(float(raw_timestamp) * 1_000_000_000)
                    if pts_ns <= previous:
                        findings.add("VIDEO_PTS_NOT_STRICT")
                    previous = pts_ns
                    in_window = clip_start_ns <= pts_ns < clip_end_ns
                    timestamp_ns = capture_start_ns + camera.capture_start_offset_ns + pts_ns
                    if request.recording_config.recorder_version == "openarm-session/v2":
                        from .openarm_grid import frame_at, frame_time

                        frame = frame_at(pts_ns + camera.capture_start_offset_ns)
                        first, last = (
                            frame_at(request.start_offset_ns),
                            frame_at(request.end_offset_ns),
                        )
                        in_window = first <= frame < last
                        timestamp_ns = _datetime_ns(request.projection.started_at) + frame_time(
                            frame - first
                        )
                    if in_window:
                        observed += 1
                        yield QualityStreamObservationV1(
                            topic=camera.modality_key,
                            timestamp_ns=timestamp_ns,
                            is_camera=True,
                            corrupt=False,
                        )
            if not observed:
                findings.add("VIDEO_EPISODE_HAS_NO_FRAMES")
        except FileNotFoundError as exc:
            raise ContinuousEpisodeProcessingError(
                "FFmpeg/FFprobe is unavailable for continuous Episode QC"
            ) from exc
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError, TypeError):
            findings.add("VIDEO_DECODE_FAILED")
        finally:
            with suppress(OSError):
                frame_inventory.unlink()

    def _decode_video_window(self, path: Path, start_ns: int, end_ns: int) -> None:
        if start_ns < 0 or end_ns <= start_ns:
            raise ValueError("video Episode window is invalid")
        subprocess.run(
            [
                self._ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-ss",
                f"{start_ns / 1_000_000_000:.9f}",
                "-i",
                str(path),
                "-t",
                f"{(end_ns - start_ns) / 1_000_000_000:.9f}",
                "-map",
                "0:v:0",
                "-an",
                "-f",
                "null",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=self._timeout,
        )

    def _sensor_alignment_samples(
        self, path: Path, request: ContinuousEpisodeWorkflowInput
    ) -> Iterator[tuple[int, str, TimedSampleV1]]:
        configured = {sensor.topic: sensor for sensor in request.recording_config.sensors}
        previous = -1
        for count, (schema, channel, message) in enumerate(_mcap_messages(path), start=1):
            if count > _MAX_SENSOR_MESSAGES:
                raise ContinuousEpisodeProcessingError(
                    "sensor MCAP exceeds the bounded message limit"
                )
            topic = str(channel.topic)
            sensor = configured.get(topic)
            if sensor is None:
                raise ContinuousEpisodeProcessingError(
                    "sensor MCAP topic inventory differs from recording config"
                )
            if schema is None or not self._decoder.supports(
                channel.message_encoding, schema.encoding
            ):
                raise ContinuousEpisodeProcessingError(f"sensor topic {topic!r} has no decoder")
            timestamp = _episode_sensor_timestamp(
                int(message.log_time), sensor.timestamp_mode, request
            )
            if timestamp is None:
                continue
            if timestamp < previous:
                raise ContinuousEpisodeProcessingError(
                    "sensor MCAP timestamps are not globally ordered"
                )
            previous = timestamp
            if (
                not _datetime_ns(request.projection.started_at)
                <= timestamp
                < _datetime_ns(request.projection.ended_at)
            ):
                continue
            value = _bounded_value(
                self._decoder.probe(
                    message_encoding=channel.message_encoding,
                    schema_encoding=schema.encoding,
                    schema_name=schema.name,
                    schema_data=schema.data,
                    message_data=message.data,
                )
            )
            sample = TimedSampleV1(timestamp_ns=timestamp, value=value)
            yield timestamp, topic, sample

    @staticmethod
    def _camera_alignment_samples(
        request: ContinuousEpisodeWorkflowInput, camera: Any
    ) -> Iterator[tuple[int, str, TimedSampleV1]]:
        start_ns = _datetime_ns(request.projection.started_at)
        end_ns = _datetime_ns(request.projection.ended_at)
        source_start_ns = request.start_offset_ns - camera.capture_start_offset_ns
        frame_count = math.ceil((end_ns - start_ns) * 30 / 1_000_000_000)
        if request.recording_config.recorder_version == "openarm-session/v2":
            from .openarm_grid import frame_at, frame_time

            frame_count = frame_at(request.end_offset_ns) - frame_at(request.start_offset_ns)
            end_ns = start_ns + frame_time(frame_count)
        for index in range(frame_count):
            timestamp = start_ns + index * 1_000_000_000 // 30
            if timestamp >= end_ns:
                break
            source_offset_ns = source_start_ns + index * 1_000_000_000 // 30
            sample = TimedSampleV1(
                timestamp_ns=timestamp,
                value={
                    "camera_id": camera.camera_id,
                    "source_frame_index": max(
                        0, round(source_offset_ns * camera.fps / 1_000_000_000)
                    ),
                    "source_pts_ns": source_offset_ns,
                },
            )
            yield timestamp, camera.modality_key, sample

    def _publish_staging(self, request: ContinuousEpisodeWorkflowInput, manifest: Any) -> Any:
        parsed = urlparse(manifest.staging_uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ContinuousEpisodeProcessingError(
                "continuous alignment writer did not return a local Arrow file"
            )
        path = Path(unquote(parsed.path)).resolve()
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            while chunk := stream.read(_COPY_CHUNK_BYTES):
                digest.update(chunk)
        content_sha = digest.hexdigest()
        object_key = "/".join(
            (
                "staging/alignment",
                quote(request.projection.organization_id, safe="-._~"),
                quote(request.projection.project_id, safe="-._~"),
                quote(request.dataset_id, safe="-._~"),
                quote(manifest.rollout_id, safe="-._~"),
                quote(manifest.attempt_id, safe="-._~"),
                f"{content_sha}.arrow",
            )
        )
        try:
            size = self._staging.publish_file(object_key, path, sha256=content_sha)
        finally:
            with suppress(OSError):
                path.unlink()
            with suppress(OSError):
                path.parent.rmdir()
        now = datetime.now(timezone.utc)
        return AlignmentStagingArtifactV1(
            object_key=object_key,
            content_sha256=content_sha,
            size_bytes=size,
            row_count=manifest.row_count,
            alignment_version=f"{manifest.converter_version}:{manifest.profile_id}",
            camera_shards={},
            created_at=now,
            expires_at=now + self._staging_ttl,
        )

    def _put_qc_report(
        self,
        request: ContinuousEpisodeWorkflowInput,
        report_id: str,
        report_sha: str,
        status: str,
        document: Mapping[str, object],
    ) -> None:
        source = request.projection
        connection = self._connection_factory()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO ingest.recording_episode_qc_reports (
                        organization_id, project_id, region_code, recording_id,
                        episode_id, report_id, report_sha256, status,
                        report_document, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (report_id) DO NOTHING
                    """,
                    (
                        source.organization_id,
                        source.project_id,
                        source.region_code,
                        source.recording_id,
                        source.episode_id,
                        report_id,
                        report_sha,
                        status,
                        json.dumps(document, sort_keys=True, separators=(",", ":")),
                        datetime.now(timezone.utc),
                    ),
                )
                cursor.execute(
                    """
                    SELECT report_sha256, status FROM ingest.recording_episode_qc_reports
                     WHERE report_id = %s
                    """,
                    (report_id,),
                )
                row = cursor.fetchone()
                if row is None or (str(row[0]), str(row[1])) != (report_sha, status):
                    raise ContinuousEpisodeProcessingError("QC report identity collision")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def _assets_for_role(
    assets: Sequence[ContinuousEpisodeAssetV1], role: RecordingAssetRole
) -> tuple[ContinuousEpisodeAssetV1, ...]:
    return tuple(item for item in assets if item.role is role)


def _only_role(
    assets: Sequence[ContinuousEpisodeAssetV1], role: RecordingAssetRole
) -> ContinuousEpisodeAssetV1:
    selected = _assets_for_role(assets, role)
    if len(selected) != 1:
        raise ContinuousEpisodeProcessingError(f"Episode requires exactly one {role.value}")
    return selected[0]


def _mcap_messages(path: Path) -> Iterator[tuple[Any, Any, Any]]:
    try:
        from mcap.reader import make_reader
    except ImportError as exc:
        raise ContinuousEpisodeProcessingError("MCAP data dependencies are unavailable") from exc
    with path.open("rb") as stream:
        yield from make_reader(stream).iter_messages()


def _looks_like_jpeg_message(channel: Any, payload: bytes) -> bool:
    if payload.startswith(b"\xff\xd8\xff"):
        return True
    if "jpeg" in str(channel.message_encoding).lower():
        return True
    if channel.message_encoding != "json":
        return False
    try:
        value = json.loads(payload)
    except Exception:
        return False
    return isinstance(value, dict) and str(value.get("encoding", "")).lower() in {
        "jpeg",
        "jpg",
    }


def _bounded_value(value: object) -> object:
    remaining = [10_000]

    def normalize(candidate: object, depth: int = 0) -> object:
        remaining[0] -= 1
        if remaining[0] < 0 or depth > 12:
            raise ContinuousEpisodeProcessingError("decoded sensor value is too complex")
        if candidate is None or isinstance(candidate, (bool, int, str)):
            return candidate
        if isinstance(candidate, float):
            if not math.isfinite(candidate):
                raise ContinuousEpisodeProcessingError("decoded sensor value is non-finite")
            return candidate
        if isinstance(candidate, Mapping):
            return {str(key): normalize(item, depth + 1) for key, item in candidate.items()}
        if isinstance(candidate, Sequence) and not isinstance(
            candidate, (str, bytes, bytearray, memoryview)
        ):
            return [normalize(item, depth + 1) for item in candidate]
        dump = getattr(candidate, "model_dump", None)
        if callable(dump):
            return normalize(dump(mode="json"), depth + 1)
        attributes = getattr(candidate, "__dict__", None)
        if isinstance(attributes, Mapping):
            return normalize(
                {key: item for key, item in attributes.items() if not str(key).startswith("_")},
                depth + 1,
            )
        slots = getattr(type(candidate), "__slots__", ())
        if isinstance(slots, str):
            slots = (slots,)
        if isinstance(slots, Sequence):
            values = {
                name: getattr(candidate, name)
                for name in slots
                if isinstance(name, str) and not name.startswith("_") and hasattr(candidate, name)
            }
            if len(values) == 1:
                return normalize(next(iter(values.values())), depth + 1)
            if values:
                return normalize(values, depth + 1)
        raise ContinuousEpisodeProcessingError("decoded sensor value is not JSON-compatible")

    result = normalize(value)
    if len(json.dumps(result, separators=(",", ":")).encode()) > 1024 * 1024:
        raise ContinuousEpisodeProcessingError("decoded sensor value exceeds one MiB")
    return result


class _CompleteFrameWriter:
    """Never publish an aligned fragment with absent required samples."""

    def __init__(self, writer: FragmentWriterPort) -> None:
        self.writer = writer

    def begin(self, *, rollout_id: str, attempt_id: str) -> None:
        self.writer.begin(rollout_id=rollout_id, attempt_id=attempt_id)

    def write_row(self, row: AlignedRowV1) -> None:
        if not row.sample_valid:
            missing = sorted(name for name, value in row.modalities.items() if not value.valid)
            raise ContinuousEpisodeProcessingError(
                f"aligned frame {row.step_index} has missing required samples: {missing}"
            )
        self.writer.write_row(row)

    def commit(self, *, row_count: int, content_sha256: str, schema_sha256: str) -> str:
        if row_count == 0:
            raise ContinuousEpisodeProcessingError("aligned Episode has no frames")
        return self.writer.commit(
            row_count=row_count, content_sha256=content_sha256, schema_sha256=schema_sha256
        )

    def abort(self) -> None:
        self.writer.abort()


def _episode_sensor_timestamp(
    raw_timestamp_ns: int, mode: SensorTimestampMode, request: ContinuousEpisodeWorkflowInput
) -> int | None:
    if request.recording_config.recorder_version == "openarm-session/v2":
        from .openarm_grid import slice_frame_time

        if mode is not SensorTimestampMode.RECORDING_OFFSET_NS:
            raise ContinuousEpisodeProcessingError("OpenArm grid requires recording offsets")
        try:
            relative = slice_frame_time(
                raw_timestamp_ns, request.start_offset_ns, request.end_offset_ns
            )
        except ValueError as error:
            raise ContinuousEpisodeProcessingError(str(error)) from error
        return None if relative is None else _datetime_ns(request.projection.started_at) + relative
    return _sensor_timestamp(
        raw_timestamp_ns, mode, request.projection.started_at, request.start_offset_ns
    )


def _sensor_timestamp(
    raw_timestamp_ns: int,
    mode: SensorTimestampMode,
    episode_started_at: datetime,
    episode_start_offset_ns: int,
) -> int:
    if mode is SensorTimestampMode.ABSOLUTE_NS:
        return raw_timestamp_ns
    capture_start_ns = _datetime_ns(episode_started_at) - episode_start_offset_ns
    return capture_start_ns + raw_timestamp_ns


def _sensor_kind(topic: str) -> ModalityKind:
    lowered = topic.lower()
    if "action" in lowered or "command" in lowered:
        return ModalityKind.ACTION
    if "imu" in lowered:
        return ModalityKind.IMU
    if "force" in lowered or "wrench" in lowered:
        return ModalityKind.FORCE
    if "point" in lowered or "lidar" in lowered:
        return ModalityKind.POINT_CLOUD
    if "joint" in lowered or "state" in lowered:
        return ModalityKind.CONTINUOUS
    return ModalityKind.DISCRETE


def _strategy_for(topic: str, config: RecordingConfigurationV1) -> Any:
    from hc_data_platform.alignment.models import AlignmentStrategy

    if topic in {camera.modality_key for camera in config.cameras}:
        return AlignmentStrategy.NEAREST
    kind = _sensor_kind(topic)
    return {
        ModalityKind.ACTION: AlignmentStrategy.CAUSAL,
        ModalityKind.IMU: AlignmentStrategy.WINDOW_MEAN,
        ModalityKind.FORCE: AlignmentStrategy.WINDOW_MEAN,
        ModalityKind.POINT_CLOUD: AlignmentStrategy.NEAREST,
        ModalityKind.CONTINUOUS: AlignmentStrategy.LINEAR,
        ModalityKind.DISCRETE: AlignmentStrategy.RECENT,
    }[kind]


def _rate_matches(raw: str, expected: float) -> bool:
    try:
        observed = float(Fraction(raw))
    except (ValueError, ZeroDivisionError):
        return False
    return abs(observed - expected) <= 1e-6


def _absolute_time(started_at: datetime, offset_ns: int) -> datetime:
    return started_at.astimezone(timezone.utc) + timedelta(microseconds=offset_ns // 1000)


def _datetime_ns(value: datetime) -> int:
    epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
    delta = value.astimezone(timezone.utc) - epoch
    return (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000


def _blocked(detail: str) -> IngestWorkflowPlanBlocked:
    return IngestWorkflowPlanBlocked(detail)
