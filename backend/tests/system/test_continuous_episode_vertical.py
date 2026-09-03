from __future__ import annotations

import asyncio
import hashlib
import json
import math
import shutil
import statistics
import subprocess
import threading
import time
from collections import Counter
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcap.writer import Writer as McapWriter
from temporalio.contrib.pydantic import pydantic_data_converter
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Replayer, Worker

from hc_data_platform.aligned_media.artifact_store import LocalAlignedMediaArtifactStore
from hc_data_platform.aligned_media.audit import InMemoryAlignedMediaAuditRecorder
from hc_data_platform.aligned_media.capacity import InMemoryMediaCapacityGate
from hc_data_platform.aligned_media.encoder import FFmpegMp4Encoder
from hc_data_platform.aligned_media.memory import InMemoryAlignedMediaRepository
from hc_data_platform.aligned_media.models import (
    AlignedMediaFrameReferenceV1,
    AlignedMediaScopeV1,
    AlignedMediaSelectorV1,
)
from hc_data_platform.aligned_media.router import configure_aligned_media
from hc_data_platform.aligned_media.router import router as aligned_media_router
from hc_data_platform.aligned_media.service import (
    AlignedMediaAuthorizationService,
    AlignedMediaGenerationService,
)
from hc_data_platform.aligned_media.staging import ArrowAlignedFrameReader
from hc_data_platform.alignment.engine import AlignmentEngine
from hc_data_platform.alignment.models import (
    AlignmentProfileV1,
    AlignmentStrategy,
)
from hc_data_platform.annotation.automation import (
    AutomaticAnnotationTaskService,
    InMemoryAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.models import (
    TagNodeDefinition,
    TagSchemaDocument,
    TagSchemaStatus,
    TagSchemaTarget,
    TagSchemaVersion,
)
from hc_data_platform.continuous_recordings.asset_models import (
    CameraRecordingConfigV1,
    CompleteRecordingAssetCommand,
    CreateRecordingUploadCommand,
    EpisodeProcessing,
    EpisodeProcessingStatus,
    RecordingAssetManifestV1,
    RecordingAssetRole,
    RecordingConfigurationV1,
    SensorRecordingConfigV1,
    SensorTimestampMode,
)
from hc_data_platform.continuous_recordings.asset_repository import (
    InMemoryRecordingAssetRepository,
)
from hc_data_platform.continuous_recordings.models import (
    RecordingScope,
    SaveSliceDraftCommand,
)
from hc_data_platform.continuous_recordings.processing import (
    ContinuousEpisodeProcessingService,
)
from hc_data_platform.continuous_recordings.repository import (
    InMemoryContinuousRecordingRepository,
)
from hc_data_platform.continuous_recordings.service import ContinuousRecordingService
from hc_data_platform.core.events import DomainEventEnvelope
from hc_data_platform.dataset_registry.models import DatasetIngestViewerTarget
from hc_data_platform.ingest.models import CompletedPart
from hc_data_platform.ingest.persistence import InMemoryIngestPersistence
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.lance_catalog.models import (
    AlignedFragmentManifestV1,
    DatasetSchemaSnapshot,
    DatasetVersionRef,
    DerivedReadyV1,
    StepRecord,
)
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import ActionQualityProfileV1, QualityProfileV1
from hc_data_platform.runtime import ArrowCatalogFragmentAdapter, ArrowFragmentWriterFactory
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.outbox import (
    InMemoryOutboxDeliveryRepository,
    OutboxDispatcher,
)
from hc_data_platform.verification.ports import RegisteredDecoderProbe
from hc_data_platform.workflow.activities import (
    ALL_ACTIVITIES,
    ActivityDependencies,
    configure_activity_dependencies,
)
from hc_data_platform.workflow.continuous_episode_dispatch import (
    ContinuousEpisodeOutboxHandler,
)
from hc_data_platform.workflow.models import (
    ContinuousEpisodeAssetV1,
    ContinuousEpisodeProjectionSourceV1,
    ContinuousEpisodeQcActivityOutput,
    ContinuousEpisodeStateActivityInput,
    ContinuousEpisodeStateActivityOutput,
    ContinuousEpisodeWorkflowInput,
    JobRecord,
    JobStatus,
    WorkflowJobPersistenceActivityInput,
)
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore
from hc_data_platform.workflow.service import TemporalWorkflowLauncher
from hc_data_platform.workflow.temporal_workflows import (
    ALL_WORKFLOWS,
    ContinuousRecordingEpisodeWorkflow,
)

pytestmark = pytest.mark.integration

ORGANIZATION_ID = "organization-vertical"
PROJECT_ID = "project-vertical"
REGION_CODE = "cn-vertical"
DATASET_ID = "dataset_vertical"
RECORDING_ID = "recording-vertical"
EPISODE_ID = "episode_vertical_01"
ROLLOUT_ID = f"{RECORDING_ID}:{EPISODE_ID}"
SCHEMA_ID = "schema-continuous-vertical"
CAPTURE_START = datetime(2026, 8, 31, tzinfo=timezone.utc)
FRAME_COUNT = 1_800
CAMERAS = ("front", "rear", "left", "right")
CAMERA_TOPICS = tuple(f"camera_{camera}" for camera in CAMERAS)
SENSOR_TOPIC = "joint_state"


class CountingObjectStorage(InMemoryObjectStorage):
    def __init__(self) -> None:
        super().__init__()
        self.read_attempts: Counter[str] = Counter()
        self.read_bytes: Counter[str] = Counter()

    def read_chunks(self, key: str, chunk_size: int = 8 * 1024 * 1024) -> Iterator[bytes]:
        self.read_attempts[key] += 1
        for chunk in super().read_chunks(key, chunk_size):
            self.read_bytes[key] += len(chunk)
            yield chunk


class MeasuredEncoder(FFmpegMp4Encoder):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.durations: list[float] = []
        self._active = 0
        self.max_concurrency = 0
        self._lock = threading.Lock()

    def encode(self, **kwargs: Any):  # type: ignore[no-untyped-def]
        with self._lock:
            self._active += 1
            self.max_concurrency = max(self.max_concurrency, self._active)
        started = time.monotonic()
        try:
            return super().encode(**kwargs)
        finally:
            elapsed = time.monotonic() - started
            with self._lock:
                self.durations.append(elapsed)
                self._active -= 1


class InMemoryContinuousProcessor(ContinuousEpisodeProcessingService):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(connection_factory=lambda: None, **kwargs)
        self.qc_reports: dict[str, dict[str, object]] = {}
        self.committed_reservations: list[int] = []

    def _put_qc_report(
        self,
        request: ContinuousEpisodeWorkflowInput,
        report_id: str,
        report_sha: str,
        status: str,
        document: Any,
    ) -> None:
        del request
        candidate: dict[str, object] = {
            "sha256": report_sha,
            "status": status,
            "document": dict(document),
        }
        existing = self.qc_reports.setdefault(report_id, candidate)
        if existing != candidate:
            raise RuntimeError("QC report identity collision")

    def _settle_version_reservation(self, episode: Any) -> None:
        del episode

    def _mark_version_reservation_committed(
        self,
        request: ContinuousEpisodeWorkflowInput,
        *,
        dataset_version: int,
    ) -> None:
        del request
        if dataset_version not in self.committed_reservations:
            self.committed_reservations.append(dataset_version)


class ViewerProjection:
    def __init__(self, *, fail_first: bool = False) -> None:
        self.calls = 0
        self.fail_first = fail_first

    def project_continuous_episode(self, **kwargs: Any) -> DatasetIngestViewerTarget:
        self.calls += 1
        if self.fail_first and self.calls == 1:
            raise RuntimeError("injected crash after Lance commit")
        source = kwargs["source"]
        version = kwargs["version"]
        return DatasetIngestViewerTarget(
            dataset_id=DATASET_ID,
            version_id=f"version_lance_{version.version}",
            episode_id=source.episode_id,
            revision_id=f"revision_{source.episode_id.removeprefix('episode_')}",
        )


class FailBeforeCommitCatalog(InMemoryLanceCatalog):
    def __init__(self) -> None:
        super().__init__()
        self.commit_attempts = 0

    def commit_fragment(
        self,
        manifest: AlignedFragmentManifestV1,
        steps: Sequence[StepRecord],
        *,
        simulate_catalog_failure: bool = False,
    ) -> tuple[DatasetVersionRef, DerivedReadyV1]:
        self.commit_attempts += 1
        if self.commit_attempts == 1:
            raise RuntimeError("injected crash before Lance commit")
        return super().commit_fragment(
            manifest,
            steps,
            simulate_catalog_failure=simulate_catalog_failure,
        )


class JobRecorder:
    def __init__(self) -> None:
        self.jobs: list[JobRecord] = []

    def put_job(self, request: WorkflowJobPersistenceActivityInput) -> None:
        self.jobs.append(request.job)


class StaticResolver:
    def __init__(self, request: ContinuousEpisodeWorkflowInput) -> None:
        self.request = request
        self.calls = 0

    def resolve(self, **kwargs: object) -> ContinuousEpisodeWorkflowInput:
        self.calls += 1
        assert kwargs == {
            "organization_id": ORGANIZATION_ID,
            "project_id": PROJECT_ID,
            "region_code": REGION_CODE,
            "recording_id": RECORDING_ID,
            "episode_id": EPISODE_ID,
            "finalized_revision": 2,
        }
        return self.request


class RejectingProcessor:
    def __init__(self, request: ContinuousEpisodeWorkflowInput) -> None:
        source = request.projection
        self.episode = EpisodeProcessing(
            scope=RecordingScope(
                organization_id=source.organization_id,
                project_id=source.project_id,
                region_code=source.region_code,
            ),
            recording_id=source.recording_id,
            episode_id=source.episode_id,
            finalized_revision=1,
            start_offset_ns=request.start_offset_ns,
            end_offset_ns=request.end_offset_ns,
            workflow_id="continuous-qc-reject-test",
        )
        self.qc_calls = 0
        self.align_calls = 0

    def update_state(
        self, request: ContinuousEpisodeStateActivityInput
    ) -> ContinuousEpisodeStateActivityOutput:
        assert self.episode.status is request.expected_status
        self.episode = self.episode.model_copy(
            update={
                "status": request.new_status,
                "qc_report_id": request.qc_report_id or self.episode.qc_report_id,
                "alignment_attempt_id": (
                    request.alignment_attempt_id or self.episode.alignment_attempt_id
                ),
                "failure_code": request.failure_code,
                "failure_stage": request.failure_stage,
                "updated_at": datetime.now(timezone.utc),
            }
        )
        return ContinuousEpisodeStateActivityOutput(episode=self.episode)

    def qc(self, request: ContinuousEpisodeWorkflowInput) -> ContinuousEpisodeQcActivityOutput:
        del request
        self.qc_calls += 1
        return ContinuousEpisodeQcActivityOutput(
            report_id="continuous-qc-reject-report",
            status="REJECT",
            finding_codes=("VIDEO_TIME_BASE_MISMATCH",),
            inspected_video_count=1,
            inspected_sensor_message_count=30,
        )

    def align(self, request: ContinuousEpisodeWorkflowInput) -> Any:
        del request
        self.align_calls += 1
        raise AssertionError("QC rejected Episodes must not align")

    def commit_bundle(self, **kwargs: Any) -> Any:
        del kwargs
        raise AssertionError("QC rejected Episodes must not commit")


def _auth() -> AuthContext:
    return AuthContext(
        subject_id="operator-vertical",
        organization_ids=frozenset({ORGANIZATION_ID}),
        project_ids=frozenset({PROJECT_ID}),
        region_codes=frozenset({REGION_CODE}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
        scope_pairs=frozenset({(PROJECT_ID, REGION_CODE)}),
        organization_scope_triples=frozenset({(ORGANIZATION_ID, PROJECT_ID, REGION_CODE)}),
    )


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _asset_manifest(
    path: str,
    role: RecordingAssetRole,
    body: bytes,
    *,
    media_type: str,
    camera_id: str | None = None,
) -> RecordingAssetManifestV1:
    return RecordingAssetManifestV1(
        path=path,
        role=role,
        camera_id=camera_id,
        media_type=media_type,
        size=len(body),
        sha256=_sha(body),
        crc64=crc64_ecma(body),
        part_count=1,
    )


def _make_video(path: Path, hue: int) -> bytes:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30:duration=60",
            "-vf",
            f"hue=h={hue}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "35",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "60",
            "-keyint_min",
            "60",
            "-sc_threshold",
            "0",
            "-bf",
            "0",
            "-threads",
            "2",
            "-r",
            "30",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            "30",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
        timeout=240,
    )
    return path.read_bytes()


def _make_sensor_mcap(path: Path) -> bytes:
    capture_start_ns = int(CAPTURE_START.timestamp()) * 1_000_000_000
    with path.open("wb") as stream:
        writer = McapWriter(stream)
        writer.start(profile="hc-continuous-v2", library="vertical-test")
        schema_id = writer.register_schema(
            "hc.robot.JointState",
            "jsonschema",
            json.dumps(
                {
                    "type": "object",
                    "properties": {"positions": {"type": "array", "items": {"type": "number"}}},
                },
                sort_keys=True,
            ).encode(),
        )
        channel_id = writer.register_channel(
            SENSOR_TOPIC,
            "json",
            schema_id,
        )
        for index in range(FRAME_COUNT):
            timestamp = capture_start_ns + index * 1_000_000_000 // 30
            writer.add_message(
                channel_id,
                log_time=timestamp,
                publish_time=timestamp,
                sequence=index,
                data=json.dumps(
                    {"positions": [math.sin(index / 90), math.cos(index / 120)]},
                    separators=(",", ":"),
                ).encode(),
            )
        writer.finish()
    body = path.read_bytes()
    assert b"\xff\xd8\xff" not in body
    return body


def _annotation_service() -> tuple[
    AutomaticAnnotationTaskService,
    InMemoryAutomaticAnnotationRepository,
]:
    repository = InMemoryAutomaticAnnotationRepository()
    draft = TagSchemaVersion(
        schema_id="tag-schema-continuous",
        project_id=PROJECT_ID,
        name="Continuous Episode tags",
        version=1,
        document=TagSchemaDocument(
            nodes=(
                TagNodeDefinition(
                    tag_id="event",
                    code="event",
                    display_name="Event",
                ),
            )
        ),
        compatible_targets=(
            TagSchemaTarget(
                region_code=REGION_CODE,
                dataset_id=DATASET_ID,
                dataset_schema_snapshot_id=SCHEMA_ID,
            ),
        ),
        content_hash="e" * 64,
        created_by="vertical-test",
    )
    repository.create_tag_schema_version(draft)
    repository.publish_tag_schema_version(
        draft,
        draft.model_copy(
            update={
                "status": TagSchemaStatus.PUBLISHED,
                "published_by": "vertical-test",
                "published_at": CAPTURE_START,
            }
        ),
    )
    return AutomaticAnnotationTaskService(repository), repository


def _build_input(
    *,
    repository: InMemoryContinuousRecordingRepository,
    asset_repository: InMemoryRecordingAssetRepository,
    scope: RecordingScope,
    upload_id: Any,
    source_sha256: str,
) -> ContinuousEpisodeWorkflowInput:
    processing = repository.get_episode_processing(scope, RECORDING_ID, EPISODE_ID)
    assert processing is not None
    upload = asset_repository.get_upload(scope, upload_id)
    assert upload is not None
    assets = asset_repository.list_assets(scope, upload.upload_id)
    configuration = upload.command.recording_config
    config_by_camera = {camera.camera_id: camera for camera in configuration.cameras}
    projected_assets = tuple(
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
        for asset in assets
    )
    modalities = frozenset((*CAMERA_TOPICS, SENSOR_TOPIC))
    return ContinuousEpisodeWorkflowInput(
        projection=ContinuousEpisodeProjectionSourceV1(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            recording_id=RECORDING_ID,
            recording_upload_id=str(upload.upload_id),
            episode_id=EPISODE_ID,
            rollout_id=ROLLOUT_ID,
            data_package_id="package-vertical",
            collection_task_id="task-vertical",
            robot_id="robot-vertical",
            source_sha256=source_sha256,
            manifest_fingerprint="b" * 64,
            source_size_bytes=sum(item.manifest.size for item in assets),
            started_at=CAPTURE_START,
            ended_at=CAPTURE_START + timedelta(seconds=60),
            camera_modality_keys=CAMERA_TOPICS,
        ),
        dataset_id=DATASET_ID,
        schema_snapshot_id=SCHEMA_ID,
        recording_config=configuration,
        assets=projected_assets,
        quality_profile=QualityProfileV1(
            profile_id="continuous-vertical-qc",
            required_topics=modalities,
            action=ActionQualityProfileV1(
                minimum_observation_count_risk=0,
                minimum_observation_count_reject=0,
            ),
        ),
        alignment_profile=AlignmentProfileV1(
            profile_id="continuous-vertical-alignment",
            converter_version="continuous-mp4-sensor/1",
            frequency_hz=30,
            required_modalities=modalities,
            stream_strategies={
                **{topic: AlignmentStrategy.NEAREST for topic in CAMERA_TOPICS},
                SENSOR_TOPIC: AlignmentStrategy.LINEAR,
            },
            default_tolerance_ns=20_000_000,
        ),
        start_offset_ns=0,
        end_offset_ns=60_000_000_000,
        expected_dataset_version=1,
        media_task_queue="continuous-vertical-media",
    )


def _reject_workflow_input() -> ContinuousEpisodeWorkflowInput:
    configuration = RecordingConfigurationV1(
        recorder_version="reject-test",
        primary_clock_domain="robot-clock",
        cameras=(
            CameraRecordingConfigV1(
                camera_id="front",
                topic="camera_front",
                fps=30,
                codec="h264",
                clock_domain="robot-clock",
                time_base_denominator=30,
            ),
        ),
        sensors=(
            SensorRecordingConfigV1(
                topic=SENSOR_TOPIC,
                clock_domain="robot-clock",
                timestamp_mode=SensorTimestampMode.ABSOLUTE_NS,
            ),
        ),
    )
    modalities = frozenset({"camera_front", SENSOR_TOPIC})
    return ContinuousEpisodeWorkflowInput(
        projection=ContinuousEpisodeProjectionSourceV1(
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            recording_id="recording-qc-reject",
            recording_upload_id="upload-qc-reject",
            episode_id="episode_qc_reject",
            rollout_id="recording-qc-reject:episode_qc_reject",
            data_package_id="package-qc-reject",
            collection_task_id="task-qc-reject",
            robot_id="robot-qc-reject",
            source_sha256="1" * 64,
            manifest_fingerprint="2" * 64,
            source_size_bytes=3,
            started_at=CAPTURE_START,
            ended_at=CAPTURE_START + timedelta(seconds=1),
            camera_modality_keys=("camera_front",),
        ),
        dataset_id="dataset_qc_reject",
        schema_snapshot_id="schema-qc-reject",
        recording_config=configuration,
        assets=(
            ContinuousEpisodeAssetV1(
                asset_id="video-front",
                role=RecordingAssetRole.RAW_VIDEO,
                camera_id="front",
                modality_key="camera_front",
                media_type="video/mp4",
                object_key="raw/front.mp4",
                size_bytes=1,
                content_sha256="3" * 64,
            ),
            ContinuousEpisodeAssetV1(
                asset_id="sensor",
                role=RecordingAssetRole.SENSOR_DATA,
                media_type="application/x-mcap",
                object_key="raw/sensor.mcap",
                size_bytes=1,
                content_sha256="4" * 64,
            ),
            ContinuousEpisodeAssetV1(
                asset_id="config",
                role=RecordingAssetRole.RECORDING_CONFIG,
                media_type="application/json",
                object_key="raw/config.json",
                size_bytes=1,
                content_sha256="5" * 64,
            ),
        ),
        quality_profile=QualityProfileV1(
            profile_id="continuous-qc-reject",
            required_topics=modalities,
            action=ActionQualityProfileV1(
                minimum_observation_count_risk=0,
                minimum_observation_count_reject=0,
            ),
        ),
        alignment_profile=AlignmentProfileV1(
            profile_id="continuous-qc-reject",
            converter_version="continuous-mp4-sensor/1",
            frequency_hz=30,
            required_modalities=modalities,
            stream_strategies={
                "camera_front": AlignmentStrategy.NEAREST,
                SENSOR_TOPIC: AlignmentStrategy.LINEAR,
            },
        ),
        start_offset_ns=0,
        end_offset_ns=1_000_000_000,
        expected_dataset_version=1,
    )


@pytest.mark.asyncio
async def test_continuous_v2_four_camera_real_media_temporal_vertical(
    tmp_path: Path,
) -> None:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        pytest.skip("FFmpeg and FFprobe are required for the real-media vertical test")

    generation_started = time.monotonic()
    video_paths = [tmp_path / f"{camera}.mp4" for camera in CAMERAS]
    video_bodies = await asyncio.gather(
        *(
            asyncio.to_thread(_make_video, path, index * 75)
            for index, path in enumerate(video_paths)
        )
    )
    sensor_body = _make_sensor_mcap(tmp_path / "sensor.mcap")
    configuration = RecordingConfigurationV1(
        recorder_version="vertical-1",
        primary_clock_domain="robot-clock",
        cameras=tuple(
            CameraRecordingConfigV1(
                camera_id=camera,
                topic=topic,
                fps=30,
                width=1280,
                height=720,
                codec="h264",
                clock_domain="robot-clock",
                time_base_numerator=1,
                time_base_denominator=30,
            )
            for camera, topic in zip(CAMERAS, CAMERA_TOPICS, strict=True)
        ),
        sensors=(
            SensorRecordingConfigV1(
                topic=SENSOR_TOPIC,
                clock_domain="robot-clock",
                timestamp_mode=SensorTimestampMode.ABSOLUTE_NS,
            ),
        ),
    )
    config_body = configuration.model_dump_json().encode()
    bodies = {
        **{
            f"videos/{camera}.mp4": body for camera, body in zip(CAMERAS, video_bodies, strict=True)
        },
        "sensors/robot.mcap": sensor_body,
        "recording/config.json": config_body,
    }
    assert len({_sha(body) for body in video_bodies}) == 4

    command = CreateRecordingUploadCommand(
        recording_id=RECORDING_ID,
        rollout_id="recording-rollout-vertical",
        data_package_id="package-vertical",
        collection_task_id="task-vertical",
        collection_job_id="job-vertical",
        robot_id="robot-vertical",
        device_id="device-vertical",
        capture_started_at=CAPTURE_START,
        capture_ended_at=CAPTURE_START + timedelta(seconds=60),
        recording_config=configuration,
        assets=tuple(
            [
                _asset_manifest(
                    f"videos/{camera}.mp4",
                    RecordingAssetRole.RAW_VIDEO,
                    body,
                    media_type="video/mp4",
                    camera_id=camera,
                )
                for camera, body in zip(CAMERAS, video_bodies, strict=True)
            ]
            + [
                _asset_manifest(
                    "sensors/robot.mcap",
                    RecordingAssetRole.SENSOR_DATA,
                    sensor_body,
                    media_type="application/x-mcap",
                ),
                _asset_manifest(
                    "recording/config.json",
                    RecordingAssetRole.RECORDING_CONFIG,
                    config_body,
                    media_type="application/json",
                ),
            ]
        ),
    )
    storage = CountingObjectStorage()
    recording_repository = InMemoryContinuousRecordingRepository()
    asset_repository = InMemoryRecordingAssetRepository()
    service = ContinuousRecordingService(
        recording_repository,
        UploadSessionService(storage, InMemoryIngestPersistence()),
        asset_repository,
        storage,
    )
    grant = service.begin_upload(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        command=command,
        actor_id="operator-vertical",
    )
    for asset in asset_repository.list_assets(grant.upload.scope, grant.upload.upload_id):
        part = storage.upload_part(
            asset.multipart_upload_id,
            1,
            bodies[asset.manifest.path],
            key=asset.object_key,
        )
        service.complete_asset(
            auth=_auth(),
            organization_id=ORGANIZATION_ID,
            project_id=PROJECT_ID,
            region_code=REGION_CODE,
            upload_id=grant.upload.upload_id,
            asset_id=asset.asset_id,
            command=CompleteRecordingAssetCommand(
                parts=(CompletedPart(part_number=1, etag=part.etag),)
            ),
        )
    committed = service.commit_upload(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        upload_id=grant.upload.upload_id,
        actor_id="operator-vertical",
        request_id="commit-vertical",
    )
    draft = service.save_draft(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        recording_id=RECORDING_ID,
        command=SaveSliceDraftCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": EPISODE_ID,
                        "start_offset_ns": "0",
                        "end_offset_ns": "60000000000",
                    }
                ]
            }
        ),
        if_match=committed.data.etag,
        actor_id="operator-vertical",
        request_id="draft-vertical",
    )
    service.finalize(
        auth=_auth(),
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
        recording_id=RECORDING_ID,
        expected_draft_revision=draft.revision.revision,
        if_match=draft.recording.etag,
        actor_id="operator-vertical",
        request_id="finalize-vertical",
    )
    ledger = recording_repository.get_episode_processing(
        grant.upload.scope, RECORDING_ID, EPISODE_ID
    )
    assert ledger is not None and ledger.status is EpisodeProcessingStatus.PENDING_QC
    assert ledger.workflow_id is not None
    assert len(recording_repository.outbox_events) == 1

    catalog = FailBeforeCommitCatalog()
    snapshot = DatasetSchemaSnapshot.create(
        project_id=PROJECT_ID,
        dataset_id=DATASET_ID,
        schema_snapshot_id=SCHEMA_ID,
        frequency_hz=30,
        fields={topic: "json" for topic in (*CAMERA_TOPICS, SENSOR_TOPIC)},
    )
    catalog.register_schema(snapshot)
    staging = LocalProjectionArtifactStore(tmp_path / "projection-objects")
    media_repository = InMemoryAlignedMediaRepository()
    media_store = LocalAlignedMediaArtifactStore(tmp_path / "aligned-media-objects")
    encoder = MeasuredEncoder(
        tmp_path / "media-encode",
        raw_storage=storage,
        ffmpeg_threads=2,
    )
    media = AlignedMediaGenerationService(
        frame_reader=ArrowAlignedFrameReader(staging),
        encoder=encoder,
        repository=media_repository,
        store=media_store,
        capacity_gate=InMemoryMediaCapacityGate(2),
    )
    viewer_projection = ViewerProjection(fail_first=True)
    processor = InMemoryContinuousProcessor(
        repository=recording_repository,
        storage=storage,
        decoder=RegisteredDecoderProbe(
            {("json", "jsonschema"): lambda _schema, message: json.loads(message)}
        ),
        quality=QualityEngine(),
        alignment=AlignmentEngine(),
        fragment_writers=ArrowFragmentWriterFactory(tmp_path / "alignment-local"),
        staging=staging,
        staging_ttl=timedelta(hours=2),
        catalog_fragments=ArrowCatalogFragmentAdapter(catalog),  # type: ignore[arg-type]
        catalog=catalog,
        media_repository=media_repository,
        dataset_projection=viewer_projection,
    )
    workflow_input = _build_input(
        repository=recording_repository,
        asset_repository=asset_repository,
        scope=grant.upload.scope,
        upload_id=grant.upload.upload_id,
        source_sha256="c" * 64,
    )
    annotation_tasks, annotation_repository = _annotation_service()
    jobs = JobRecorder()
    configure_activity_dependencies(
        ActivityDependencies(
            aligned_media=media,
            aligned_media_repository=media_repository,
            aligned_media_store=media_store,
            alignment_staging=staging,
            annotation_tasks=annotation_tasks,
            workflow_jobs=jobs,
            continuous_episode_processing=processor,
        )
    )

    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        resolver = StaticResolver(workflow_input)
        launcher = TemporalWorkflowLauncher(
            "unused",
            task_queue="continuous-vertical-main",
            client=environment.client,
        )
        handler = ContinuousEpisodeOutboxHandler(launcher, resolver)
        event = DomainEventEnvelope.model_validate(
            next(iter(recording_repository.outbox_events.values()))
        )
        delivery = InMemoryOutboxDeliveryRepository((event,))
        dispatcher = OutboxDispatcher(
            delivery,
            {handler.EVENT_TYPE: handler},
            worker_id="vertical-outbox",
        )
        async with (
            Worker(
                environment.client,
                task_queue="continuous-vertical-main",
                workflows=list(ALL_WORKFLOWS),
                activities=list(ALL_ACTIVITIES),
            ),
            Worker(
                environment.client,
                task_queue="continuous-vertical-media",
                activities=list(ALL_ACTIVITIES),
            ),
        ):
            assert await dispatcher.dispatch_one(
                organization_id=ORGANIZATION_ID,
                project_id=PROJECT_ID,
                region_code=REGION_CODE,
            )
            result = await environment.client.get_workflow_handle(
                ledger.workflow_id,
                result_type=JobRecord,
            ).result()
            assert result.status is JobStatus.SUCCEEDED, json.dumps(
                processor.qc_reports, sort_keys=True, default=str
            )
            assert delivery.state(event.event_id)["published_at"] is not None
            assert resolver.calls == 1

            replayed = cast(JobRecord, await handler(event))
            assert replayed.job_id == ledger.workflow_id
            assert resolver.calls == 2

            history = await environment.client.get_workflow_handle(
                ledger.workflow_id
            ).fetch_history()
            await Replayer(
                workflows=[ContinuousRecordingEpisodeWorkflow],
                data_converter=pydantic_data_converter,
            ).replay_workflow(history)
    finally:
        await environment.shutdown()

    ready = recording_repository.get_episode_processing(
        grant.upload.scope, RECORDING_ID, EPISODE_ID
    )
    assert ready is not None
    assert ready.status is EpisodeProcessingStatus.READY
    assert ready.dataset_id == DATASET_ID
    assert ready.dataset_version == ready.lance_version == 1
    assert ready.annotation_task_id is not None
    assert ready.aligned_media_camera_count == 4
    assert processor.committed_reservations == [1]
    assert len(processor.qc_reports) == 1
    assert next(iter(processor.qc_reports.values()))["status"] == "PASS"
    assert viewer_projection.calls == 2
    assert catalog.commit_attempts == 3
    assert len(annotation_repository.automatic_triggers) == 1

    media_scope = AlignedMediaScopeV1(
        organization_id=ORGANIZATION_ID,
        project_id=PROJECT_ID,
        region_code=REGION_CODE,
    )
    artifacts = tuple(
        artifact
        for camera_id in CAMERA_TOPICS
        if (
            artifact := media_repository.find_by_selector(
                media_scope,
                AlignedMediaSelectorV1(
                    project_id=PROJECT_ID,
                    dataset_id=DATASET_ID,
                    rollout_id=ROLLOUT_ID,
                    dataset_version=1,
                    camera_id=camera_id,
                ),
                profile_id="canonical-h264-crf20-v1",
            )
        )
        is not None
    )
    assert len(artifacts) == 4
    for artifact in artifacts:
        assert artifact.dataset_committed_at is not None
        assert artifact.frame_count == FRAME_COUNT
        assert artifact.fps == 30
        assert artifact.duration_seconds == 60
        assert artifact.width == 1280 and artifact.height == 720
        assert artifact.media_object_key is not None
        media_path = media_store.resolve_local_object(artifact.media_object_key)
        assert media_path is not None
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-count_frames",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name,pix_fmt,avg_frame_rate,time_base,nb_read_frames:format=duration",
                "-of",
                "json",
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        facts = json.loads(probe.stdout)
        stream = facts["streams"][0]
        assert stream["codec_name"] == "h264"
        assert stream["pix_fmt"] == "yuv420p"
        assert stream["avg_frame_rate"] == "30/1"
        assert stream["time_base"] == "1/30"
        assert int(stream["nb_read_frames"]) == FRAME_COUNT
        assert float(facts["format"]["duration"]) == 60

    window = catalog.read_steps(
        DATASET_ID,
        ROLLOUT_ID,
        0,
        4,
        project_id=PROJECT_ID,
    )
    assert len(window.steps) == 4
    assert window.steps[0].modalities[SENSOR_TOPIC]["positions"] == [0.0, 1.0]
    for step in window.steps:
        for camera_id in CAMERA_TOPICS:
            value = step.modalities[camera_id]
            assert isinstance(value, dict)
            assert not isinstance(value, bytes)
            assert "image" not in value and "jpeg" not in json.dumps(value).lower()
            reference = AlignedMediaFrameReferenceV1.model_validate(value)
            assert reference.camera_id == camera_id
            assert reference.frame_index == step.step_index
            assert reference.pts == step.step_index
            assert reference.valid and not reference.placeholder

    authorization = AlignedMediaAuthorizationService(
        repository=media_repository,
        store=media_store,
    )
    configure_aligned_media(
        authorization,
        media_store,
        InMemoryAlignedMediaAuditRecorder(),
    )
    app = FastAPI()
    app.include_router(aligned_media_router)
    client = TestClient(app)
    for camera_id in CAMERA_TOPICS:
        descriptor = authorization.authorize(
            media_scope,
            AlignedMediaSelectorV1(
                project_id=PROJECT_ID,
                dataset_id=DATASET_ID,
                rollout_id=ROLLOUT_ID,
                dataset_version=1,
                camera_id=camera_id,
            ),
        )
        response = client.get(descriptor.media_url, headers={"Range": "bytes=0-1023"})
        assert response.status_code == 206
        assert response.headers["content-range"].startswith("bytes 0-1023/")
        assert len(response.content) == 1_024

    assert not any((tmp_path / "projection-objects").rglob("*.arrow"))
    assert not any((tmp_path / "alignment-local").rglob("*.arrow"))
    video_keys = {
        asset.object_key
        for asset in asset_repository.list_assets(grant.upload.scope, grant.upload.upload_id)
        if asset.manifest.role is RecordingAssetRole.RAW_VIDEO
    }
    sensor_keys = {
        asset.object_key
        for asset in asset_repository.list_assets(grant.upload.scope, grant.upload.upload_id)
        if asset.manifest.role is RecordingAssetRole.SENSOR_DATA
    }
    config_keys = {
        asset.object_key
        for asset in asset_repository.list_assets(grant.upload.scope, grant.upload.upload_id)
        if asset.manifest.role is RecordingAssetRole.RECORDING_CONFIG
    }
    video_read_attempts = {key: storage.read_attempts[key] for key in sorted(video_keys)}
    assert all(count == 3 for count in video_read_attempts.values()), video_read_attempts
    assert all(storage.read_attempts[key] == 3 for key in sensor_keys)
    assert all(storage.read_attempts[key] == 2 for key in config_keys)
    assert len(encoder.durations) == 4
    ordered = sorted(encoder.durations)
    capacity = {
        "schema_version": "continuous-vertical-capacity/v1",
        "generation_seconds": round(time.monotonic() - generation_started, 3),
        "raw_object_bytes": sum(len(body) for body in bodies.values()),
        "object_store_read_bytes": sum(storage.read_bytes.values()),
        "canonical_object_bytes": sum(item.total_bytes for item in artifacts),
        "object_store_write_bytes": sum(len(body) for body in bodies.values())
        + sum(item.total_bytes for item in artifacts),
        "temp_disk_bytes_observed": sum(
            path.stat().st_size for path in tmp_path.rglob("*") if path.is_file()
        ),
        "arrow_staging_files_after_cleanup": len(tuple(tmp_path.rglob("*.arrow"))),
        "ffmpeg_concurrency": encoder.max_concurrency,
        "ffmpeg_seconds_p50": round(statistics.median(ordered), 3),
        "ffmpeg_seconds_p95": round(ordered[-1], 3),
    }
    print(json.dumps(capacity, sort_keys=True))


@pytest.mark.asyncio
async def test_continuous_episode_temporal_qc_reject_sets_terminal_evidence() -> None:
    request = _reject_workflow_input()
    processor = RejectingProcessor(request)
    jobs = JobRecorder()
    configure_activity_dependencies(
        ActivityDependencies(
            workflow_jobs=jobs,
            continuous_episode_processing=cast(Any, processor),
        )
    )

    environment = await WorkflowEnvironment.start_time_skipping(
        data_converter=pydantic_data_converter
    )
    try:
        async with Worker(
            environment.client,
            task_queue="continuous-qc-reject",
            workflows=[ContinuousRecordingEpisodeWorkflow],
            activities=list(ALL_ACTIVITIES),
        ):
            result = await environment.client.execute_workflow(
                ContinuousRecordingEpisodeWorkflow.run,
                request,
                id="continuous-qc-reject-test",
                task_queue="continuous-qc-reject",
            )
    finally:
        await environment.shutdown()

    assert result.status is JobStatus.QUALITY_REJECTED
    assert result.error_code == "QC_REJECTED"
    assert result.result == {
        "qc_report_id": "continuous-qc-reject-report",
        "finding_codes": ["VIDEO_TIME_BASE_MISMATCH"],
        "training_eligible": False,
        "raw_preserved": True,
    }
    assert processor.episode.status is EpisodeProcessingStatus.QC_FAILED
    assert processor.episode.qc_report_id == "continuous-qc-reject-report"
    assert processor.episode.failure_stage == "qc"
    assert processor.episode.failure_code == "QC_REJECTED"
    assert processor.qc_calls == 1
    assert processor.align_calls == 0
    assert jobs.jobs[-1].status is JobStatus.QUALITY_REJECTED
