from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, cast

import pytest
from mcap.writer import Writer as McapWriter
from pydantic import ValidationError

from hc_data_platform.alignment.models import AlignmentProfileV1, AlignmentStrategy
from hc_data_platform.continuous_recordings.asset_models import (
    CameraRecordingConfigV1,
    CreateRecordingUploadCommand,
    RecordingAssetManifestV1,
    RecordingAssetRole,
    RecordingConfigurationV1,
    SensorRecordingConfigV1,
    SensorTimestampMode,
)
from hc_data_platform.continuous_recordings.processing import (
    ContinuousEpisodeProcessingService,
    PostgresContinuousEpisodeWorkflowInputResolver,
)
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.lance_catalog.service import InMemoryLanceCatalog
from hc_data_platform.quality.engine import QualityEngine
from hc_data_platform.quality.models import ActionQualityProfileV1, QualityProfileV1
from hc_data_platform.verification.ports import RegisteredDecoderProbe
from hc_data_platform.workflow.ingest_dispatch import IngestWorkflowPlanBlocked
from hc_data_platform.workflow.models import (
    ContinuousEpisodeAssetV1,
    ContinuousEpisodeProjectionSourceV1,
    ContinuousEpisodeWorkflowInput,
)

START = datetime(2026, 8, 31, tzinfo=timezone.utc)


class CapturingProcessor(ContinuousEpisodeProcessingService):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(connection_factory=lambda: None, **kwargs)
        self.report: Mapping[str, object] | None = None

    def _put_qc_report(
        self,
        request: ContinuousEpisodeWorkflowInput,
        report_id: str,
        report_sha: str,
        status: str,
        document: Mapping[str, object],
    ) -> None:
        del request, report_id, report_sha, status
        self.report = document


class BusyReservationCursor:
    def __init__(self) -> None:
        self.statement = ""
        self.statements: list[str] = []

    def __enter__(self) -> BusyReservationCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, statement: str, _parameters: object) -> None:
        self.statement = " ".join(statement.split())
        self.statements.append(self.statement)

    def fetchone(self) -> tuple[str] | None:
        if "recording_id = %s AND episode_id = %s" in self.statement:
            return None
        if "dataset_id = %s AND reservation_status = 'RESERVED'" in self.statement:
            return ("workflow-sibling",)
        raise AssertionError(f"unexpected fetch for {self.statement}")


class BusyReservationConnection:
    def __init__(self) -> None:
        self.cursor_instance = BusyReservationCursor()
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> BusyReservationCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def _asset(
    *,
    asset_id: str,
    role: RecordingAssetRole,
    key: str,
    body: bytes,
    media_type: str,
    camera_id: str | None = None,
    modality_key: str | None = None,
) -> ContinuousEpisodeAssetV1:
    return ContinuousEpisodeAssetV1(
        asset_id=asset_id,
        role=role,
        camera_id=camera_id,
        modality_key=modality_key,
        media_type=media_type,
        object_key=key,
        size_bytes=len(body),
        content_sha256=hashlib.sha256(body).hexdigest(),
    )


def _sensor_mcap(path: Path) -> bytes:
    start_ns = int(START.timestamp()) * 1_000_000_000
    with path.open("wb") as stream:
        writer = McapWriter(stream)
        writer.start(profile="continuous-qc-test", library="pytest")
        schema_id = writer.register_schema(
            "JointState",
            "jsonschema",
            b'{"type":"object","properties":{"positions":{"type":"array"}}}',
        )
        channel_id = writer.register_channel("joint_state", "json", schema_id)
        for index in range(30):
            timestamp = start_ns + index * 1_000_000_000 // 30
            writer.add_message(
                channel_id,
                log_time=timestamp,
                publish_time=timestamp,
                sequence=index,
                data=json.dumps({"positions": [math.sin(index)]}).encode(),
            )
        writer.finish()
    return path.read_bytes()


@pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="FFmpeg runtime is unavailable",
)
def test_real_qc_rejects_video_time_base_mismatch(tmp_path: Path) -> None:
    video_path = tmp_path / "front.mp4"
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
            "testsrc2=size=320x240:rate=30:duration=1",
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "30",
            "-fps_mode",
            "cfr",
            "-video_track_timescale",
            "90000",
            str(video_path),
        ],
        check=True,
        timeout=60,
    )
    video = video_path.read_bytes()
    sensor = _sensor_mcap(tmp_path / "sensor.mcap")
    config = RecordingConfigurationV1(
        recorder_version="qc-test",
        primary_clock_domain="robot-clock",
        cameras=(
            CameraRecordingConfigV1(
                camera_id="front",
                topic="camera_front",
                fps=30,
                width=320,
                height=240,
                codec="h264",
                clock_domain="robot-clock",
                time_base_numerator=1,
                time_base_denominator=30,
            ),
        ),
        sensors=(
            SensorRecordingConfigV1(
                topic="joint_state",
                clock_domain="robot-clock",
                timestamp_mode=SensorTimestampMode.ABSOLUTE_NS,
            ),
        ),
    )
    config_body = config.model_dump_json().encode()
    storage = InMemoryObjectStorage()
    storage.objects.update(
        {"raw/front.mp4": video, "raw/sensor.mcap": sensor, "raw/config.json": config_body}
    )
    modalities = frozenset({"camera_front", "joint_state"})
    request = ContinuousEpisodeWorkflowInput(
        projection=ContinuousEpisodeProjectionSourceV1(
            organization_id="organization-qc",
            project_id="project-qc",
            region_code="cn-qc",
            recording_id="recording-qc",
            recording_upload_id="upload-qc",
            episode_id="episode_qc_01",
            rollout_id="recording-qc:episode_qc_01",
            data_package_id="package-qc",
            collection_task_id="task-qc",
            robot_id="robot-qc",
            source_sha256="a" * 64,
            manifest_fingerprint="b" * 64,
            source_size_bytes=len(video) + len(sensor) + len(config_body),
            started_at=START,
            ended_at=START + timedelta(seconds=1),
            camera_modality_keys=("camera_front",),
        ),
        dataset_id="dataset_qc",
        schema_snapshot_id="schema-qc",
        recording_config=config,
        assets=(
            _asset(
                asset_id="video-front",
                role=RecordingAssetRole.RAW_VIDEO,
                key="raw/front.mp4",
                body=video,
                media_type="video/mp4",
                camera_id="front",
                modality_key="camera_front",
            ),
            _asset(
                asset_id="sensor",
                role=RecordingAssetRole.SENSOR_DATA,
                key="raw/sensor.mcap",
                body=sensor,
                media_type="application/x-mcap",
            ),
            _asset(
                asset_id="config",
                role=RecordingAssetRole.RECORDING_CONFIG,
                key="raw/config.json",
                body=config_body,
                media_type="application/json",
            ),
        ),
        quality_profile=QualityProfileV1(
            profile_id="continuous-qc-test",
            required_topics=modalities,
            action=ActionQualityProfileV1(
                minimum_observation_count_risk=0,
                minimum_observation_count_reject=0,
            ),
        ),
        alignment_profile=AlignmentProfileV1(
            profile_id="continuous-qc-alignment",
            converter_version="continuous-mp4-sensor/1",
            frequency_hz=30,
            required_modalities=modalities,
            stream_strategies={
                "camera_front": AlignmentStrategy.NEAREST,
                "joint_state": AlignmentStrategy.LINEAR,
            },
        ),
        start_offset_ns=0,
        end_offset_ns=1_000_000_000,
        expected_dataset_version=1,
    )
    processor = CapturingProcessor(
        repository=cast(Any, None),
        storage=storage,
        decoder=RegisteredDecoderProbe(
            {("json", "jsonschema"): lambda _schema, message: json.loads(message)}
        ),
        quality=QualityEngine(),
        alignment=cast(Any, None),
        fragment_writers=cast(Any, None),
        staging=cast(Any, None),
        staging_ttl=timedelta(hours=1),
        catalog_fragments=cast(Any, None),
        catalog=cast(Any, None),
        media_repository=cast(Any, None),
        dataset_projection=cast(Any, None),
    )

    result = processor.qc(request)

    assert result.status == "REJECT"
    assert result.finding_codes == ("VIDEO_TIME_BASE_MISMATCH",)
    assert result.inspected_video_count == 1
    assert result.inspected_sensor_message_count == 30
    assert processor.report is not None
    assert processor.report["status"] == "REJECT"


def test_upload_contract_rejects_a_missing_configured_camera() -> None:
    video = b"front"
    sensor = b"sensor"
    config_body = b"config"
    config = RecordingConfigurationV1(
        recorder_version="inventory-test",
        primary_clock_domain="robot-clock",
        cameras=(
            CameraRecordingConfigV1(
                camera_id=camera,
                topic=f"camera_{camera}",
                fps=30,
                codec="h264",
                clock_domain="robot-clock",
                time_base_denominator=30,
            )
            for camera in ("front", "rear")
        ),
    )

    def manifest(
        path: str,
        role: RecordingAssetRole,
        body: bytes,
        media_type: str,
        camera_id: str | None = None,
    ) -> RecordingAssetManifestV1:
        return RecordingAssetManifestV1(
            path=path,
            role=role,
            camera_id=camera_id,
            media_type=media_type,
            size=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
            crc64=crc64_ecma(body),
            part_count=1,
        )

    with pytest.raises(ValidationError, match="exactly match recording config cameras"):
        CreateRecordingUploadCommand(
            recording_id="recording-missing-camera",
            rollout_id="rollout-missing-camera",
            data_package_id="package-missing-camera",
            collection_task_id="task-missing-camera",
            collection_job_id="job-missing-camera",
            robot_id="robot-missing-camera",
            device_id="device-missing-camera",
            capture_started_at=START,
            capture_ended_at=START + timedelta(seconds=1),
            recording_config=config,
            assets=(
                manifest(
                    "videos/front.mp4",
                    RecordingAssetRole.RAW_VIDEO,
                    video,
                    "video/mp4",
                    "front",
                ),
                manifest(
                    "sensors/robot.mcap",
                    RecordingAssetRole.SENSOR_DATA,
                    sensor,
                    "application/x-mcap",
                ),
                manifest(
                    "recording/config.json",
                    RecordingAssetRole.RECORDING_CONFIG,
                    config_body,
                    "application/json",
                ),
            ),
        )


def test_schema_selection_fails_closed_on_incompatible_lance_schema() -> None:
    catalog = InMemoryLanceCatalog()
    catalog.register_schema(
        DatasetSchemaSnapshot.create(
            project_id="project-schema",
            dataset_id="dataset_schema",
            schema_snapshot_id="schema-existing",
            frequency_hz=30,
            fields={"legacy": "json"},
        )
    )
    resolver = PostgresContinuousEpisodeWorkflowInputResolver(lambda: None, catalog)

    with pytest.raises(IngestWorkflowPlanBlocked, match="compatible TAGGING Schema"):
        resolver._select_schema(  # noqa: SLF001
            project_id="project-schema",
            dataset_id="dataset_schema",
            schema_ids=("schema-continuous",),
            modality_keys=frozenset({"camera_front", "joint_state"}),
        )


def test_dataset_version_reservation_blocks_a_sibling_episode() -> None:
    connection = BusyReservationConnection()
    resolver = PostgresContinuousEpisodeWorkflowInputResolver(
        lambda: connection,
        InMemoryLanceCatalog(),
    )

    with pytest.raises(
        IngestWorkflowPlanBlocked,
        match="another continuous Episode owns the next Dataset version",
    ):
        resolver._reserve_dataset_version(  # noqa: SLF001
            organization_id="organization-reservation",
            project_id="project-reservation",
            region_code="cn-reservation",
            recording_id="recording-second",
            episode_id="episode_second",
            workflow_id="workflow-second",
            dataset_id="dataset_shared",
        )

    assert connection.rolled_back
    assert not connection.committed
    assert connection.closed
    assert not any(
        statement.startswith("INSERT") for statement in connection.cursor_instance.statements
    )
