from __future__ import annotations

import hashlib
import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcap.writer import Writer as McapWriter
from pydantic import ValidationError

from hc_data_platform.continuous_recordings.asset_models import (
    CameraRecordingConfigV1,
    CompleteRecordingAssetCommand,
    CreateRecordingUploadCommand,
    EpisodeProcessingStatus,
    RecordingAssetManifestV1,
    RecordingAssetRole,
    RecordingConfigurationV1,
    RecordingUploadStatus,
    SensorRecordingConfigV1,
    SensorTimestampMode,
)
from hc_data_platform.continuous_recordings.asset_repository import (
    InMemoryRecordingAssetRepository,
)
from hc_data_platform.continuous_recordings.models import (
    ProposeModelSlicesCommand,
    RecordingStatus,
    SaveSliceDraftCommand,
    SliceRevisionStatus,
)
from hc_data_platform.continuous_recordings.repository import (
    InMemoryContinuousRecordingRepository,
)
from hc_data_platform.continuous_recordings.router import (
    get_continuous_recording_service,
    router,
)
from hc_data_platform.continuous_recordings.service import ContinuousRecordingService
from hc_data_platform.core.errors import ProblemException
from hc_data_platform.ingest.models import (
    CompletedPart,
    ContinuousCaptureSourceV1,
    IngestProcessingMode,
    ManifestCameraV1,
    ManifestFileV1,
    ManifestTopicV1,
    RolloutManifestV1,
)
from hc_data_platform.ingest.persistence import InMemoryIngestPersistence
from hc_data_platform.ingest.ports import InMemoryObjectStorage, crc64_ecma
from hc_data_platform.ingest.service import UploadSessionService
from hc_data_platform.security.auth import AuthContext
from hc_data_platform.security.http import require_auth_context
from hc_data_platform.verification.ports import RegisteredDecoderProbe


def _manifest(body: bytes) -> RolloutManifestV1:
    started_at = datetime(2026, 8, 31, 1, tzinfo=timezone.utc)
    sha256 = hashlib.sha256(body).hexdigest()
    crc64 = crc64_ecma(body)
    return RolloutManifestV1(
        project_id="project-a",
        task_id="task-a",
        collection_job_id="job-a",
        rollout_id="recording-rollout-a",
        collection_session_id="collection-session-a",
        recording_request_id="recording-request-a",
        data_package_id="capture-package-a",
        sequence_no=1,
        robot_id="robot-a",
        start_time=started_at,
        end_time=started_at + timedelta(hours=3),
        cameras=[ManifestCameraV1(camera_id="front", topic="/camera/front")],
        topics=[
            ManifestTopicV1(name="/camera/front", required=True),
            ManifestTopicV1(name="/joint_states", required=True),
        ],
        expected_topics=["/camera/front", "/joint_states"],
        actual_topics=["/camera/front", "/joint_states"],
        files=[
            ManifestFileV1(
                path="capture-20260831.tar.zst",
                size=len(body),
                sha256=sha256,
                crc64=crc64,
                media_type="application/zstd",
                role="CAPTURE_BUNDLE",
            )
        ],
        file_size=len(body),
        sha256=sha256,
        crc64=crc64,
        compression="zstd",
        recorder_version="2.0",
        processing_mode=IngestProcessingMode.CONTINUOUS_RECORDING,
        source_recording=ContinuousCaptureSourceV1(
            recording_id="recording-a",
            device_id="device-a",
            recorder_boot_id="boot-a",
        ),
    )


def _auth() -> AuthContext:
    return AuthContext(
        subject_id="operator-a",
        organization_ids=frozenset({"org-a"}),
        project_ids=frozenset({"project-a"}),
        region_codes=frozenset({"cn-hz"}),
        capabilities=frozenset({"upload.read", "upload.manage"}),
        scope_pairs=frozenset({("project-a", "cn-hz")}),
        organization_scope_triples=frozenset({("org-a", "project-a", "cn-hz")}),
    )


def _committed_services() -> tuple[
    ContinuousRecordingService,
    UploadSessionService,
    InMemoryIngestPersistence,
    str,
]:
    body = b"whole-three-hour-video-and-robot-data-bundle"
    manifest = _manifest(body)
    storage = InMemoryObjectStorage()
    persistence = InMemoryIngestPersistence()
    ingest = UploadSessionService(storage, persistence)
    session = ingest.create_session(
        manifest=manifest,
        region_code="cn-hz",
        idempotency_key="continuous-recording-upload",
    )
    assert session.multipart_upload_id is not None
    part = storage.upload_part(
        session.multipart_upload_id,
        1,
        body,
        key=session.object_key,
    )
    ingest.complete_upload(
        session.session_id,
        [CompletedPart(part_number=part.part_number, etag=part.etag)],
    )
    committed = ingest.commit_manifest(
        organization_id="org-a",
        session_id=session.session_id,
        manifest=manifest,
        actor_id="operator-a",
        request_id="request-a",
    )
    assert committed.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING
    assert committed.workflow is None
    assert persistence.get_workflow_trigger(session.session_id) is None
    raw_source = ingest.authorize_raw_media(
        session_id=session.session_id,
        actor_id="operator-a",
        request_id="request-raw-bundle",
    )
    assert raw_source.format == "CAPTURE_BUNDLE"
    assert raw_source.media_type == "application/zstd"
    service = ContinuousRecordingService(InMemoryContinuousRecordingRepository(), ingest)
    return service, ingest, persistence, session.session_id


def _register(service: ContinuousRecordingService, session_id: str):  # type: ignore[no-untyped-def]
    return service.register(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        upload_session_id=session_id,
        actor_id="operator-a",
        request_id="request-register",
    )


def test_continuous_capture_manifest_requires_one_whole_bundle() -> None:
    manifest = _manifest(b"bundle")
    assert manifest.processing_mode is IngestProcessingMode.CONTINUOUS_RECORDING
    assert manifest.source_fingerprint is not None
    assert manifest.end_time - manifest.start_time == timedelta(hours=3)
    assert manifest.files[0].role == "CAPTURE_BUNDLE"

    with pytest.raises(ValidationError):
        RolloutManifestV1.model_validate(
            {
                **manifest.model_dump(mode="json"),
                "files": [
                    {
                        **manifest.files[0].model_dump(mode="json"),
                        "role": "RAW_MCAP",
                    }
                ],
            }
        )


def test_manual_slice_draft_finalizes_virtual_episodes_without_copying_source() -> None:
    service, _ingest, _persistence, session_id = _committed_services()
    registered = _register(service, session_id)
    recording = registered.data
    assert recording.status is RecordingStatus.READY_FOR_SLICING
    assert recording.duration_ns == 3 * 60 * 60 * 1_000_000_000
    assert recording.current_revision == 0

    draft = service.save_draft(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-a",
        command=SaveSliceDraftCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": "episode_pick_002",
                        "start_offset_ns": "7200000000000",
                        "end_offset_ns": "7260000000000",
                        "title": "第二次成功抓取",
                    },
                    {
                        "episode_id": "episode_pick_001",
                        "start_offset_ns": "120000000000",
                        "end_offset_ns": "180000000000",
                        "title": "第一次成功抓取",
                    },
                ]
            }
        ),
        if_match='"v1"',
        actor_id="operator-a",
        request_id="request-draft",
    )
    assert draft.revision.status is SliceRevisionStatus.DRAFT
    assert [item.episode_id for item in draft.revision.slices] == [
        "episode_pick_001",
        "episode_pick_002",
    ]
    assert all(item.source_sha256 == recording.source_sha256 for item in draft.revision.slices)
    assert all(
        item.source_upload_session_id == recording.upload_session_id
        for item in draft.revision.slices
    )

    finalized = service.finalize(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-a",
        expected_draft_revision=1,
        if_match='"v2"',
        actor_id="operator-a",
        request_id="request-finalize",
    )
    assert finalized.recording.status is RecordingStatus.SLICED
    assert finalized.recording.finalized_revision == 2
    assert finalized.revision.status is SliceRevisionStatus.FINALIZED
    assert len(finalized.revision.slices) == 2
    assert all(item.revision == 2 for item in finalized.revision.slices)


def test_slice_windows_reject_overlap_and_out_of_recording_range() -> None:
    with pytest.raises(ValidationError):
        SaveSliceDraftCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": "episode_a1",
                        "start_offset_ns": "0",
                        "end_offset_ns": "200",
                    },
                    {
                        "episode_id": "episode_a2",
                        "start_offset_ns": "100",
                        "end_offset_ns": "300",
                    },
                ]
            }
        )

    service, _ingest, _persistence, session_id = _committed_services()
    recording = _register(service, session_id).data
    with pytest.raises(ProblemException) as rejected:
        service.save_draft(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            recording_id=recording.recording_id,
            command=SaveSliceDraftCommand.model_validate(
                {
                    "slices": [
                        {
                            "episode_id": "episode_outside",
                            "start_offset_ns": str(recording.duration_ns - 1),
                            "end_offset_ns": str(recording.duration_ns + 1),
                        }
                    ]
                }
            ),
            if_match='"v1"',
            actor_id="operator-a",
            request_id="request-outside",
        )
    assert rejected.value.problem.code == "EPISODE_SLICE_OUT_OF_RANGE"


def test_continuous_recording_http_contract_registers_saves_and_finalizes() -> None:
    service, _ingest, _persistence, session_id = _committed_services()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth_context] = _auth
    app.dependency_overrides[get_continuous_recording_service] = lambda: service
    client = TestClient(app)
    root = "/api/v1/projects/project-a/regions/cn-hz/continuous-recordings"
    headers = {"X-Organization-Id": "org-a"}

    registered = client.post(root, headers=headers, json={"upload_session_id": session_id})
    assert registered.status_code == 201
    assert registered.headers["ETag"] == '"v1"'
    assert registered.json()["data"]["status"] == "READY_FOR_SLICING"

    draft = client.put(
        f"{root}/recording-a/slice-draft",
        headers={**headers, "If-Match": '"v1"'},
        json={
            "slices": [
                {
                    "episode_id": "episode_http_01",
                    "start_offset_ns": "0",
                    "end_offset_ns": "30000000000",
                }
            ]
        },
    )
    assert draft.status_code == 200
    assert draft.headers["ETag"] == '"v2"'
    assert draft.json()["revision"]["status"] == "DRAFT"

    finalized = client.post(
        f"{root}/recording-a/slice-draft:finalize",
        headers={**headers, "If-Match": '"v2"'},
        json={"expected_draft_revision": 1},
    )
    assert finalized.status_code == 200
    assert finalized.headers["ETag"] == '"v3"'
    assert finalized.json()["recording"]["status"] == "SLICED"
    assert finalized.json()["revision"]["slices"][0]["episode_id"] == "episode_http_01"


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
        sha256=hashlib.sha256(body).hexdigest(),
        crc64=crc64_ecma(body),
        part_count=1,
    )


def _joint_sensor_mcap(capture_started_at: datetime) -> bytes:
    stream = io.BytesIO()
    writer = McapWriter(stream)
    writer.start(profile="continuous-recording-test", library="pytest")
    schema_id = writer.register_schema(
        "sensor_msgs/msg/JointState",
        "jsonschema",
        b'{"type":"object"}',
    )
    channel_id = writer.register_channel("/robot/joint_states", "json", schema_id)
    start_ns = int(capture_started_at.timestamp()) * 1_000_000_000
    for index in range(3):
        timestamp_ns = start_ns + index * 1_000_000_000
        writer.add_message(
            channel_id,
            log_time=timestamp_ns,
            publish_time=timestamp_ns,
            sequence=index,
            data=json.dumps(
                {
                    "name": ["shoulder", "elbow"],
                    "position": [index * 0.1, index * -0.2],
                }
            ).encode(),
        )
    writer.finish()
    return stream.getvalue()


def test_video_assets_upload_to_oss_then_human_finalizes_model_cut_for_direct_preview() -> None:
    capture_started_at = datetime(2026, 8, 31, 1, tzinfo=timezone.utc)
    video = b"original-three-hour-h264-video"
    sensor = _joint_sensor_mcap(capture_started_at)
    config = b'{"camera":"front","fps":30}'
    bodies = {
        "videos/front.mp4": video,
        "sensors/robot.mcap": sensor,
        "recording/config.json": config,
    }
    command = CreateRecordingUploadCommand(
        recording_id="recording-video-a",
        rollout_id="recording-rollout-video-a",
        data_package_id="capture-package-video-a",
        collection_task_id="task-a",
        collection_job_id="job-a",
        robot_id="robot-a",
        device_id="device-a",
        capture_started_at=capture_started_at,
        capture_ended_at=datetime(2026, 8, 31, 4, tzinfo=timezone.utc),
        recording_config=RecordingConfigurationV1(
            recorder_version="2.3",
            primary_clock_domain="robot-monotonic",
            cameras=(
                CameraRecordingConfigV1(
                    camera_id="front",
                    topic="/camera/front",
                    fps=30,
                    width=1920,
                    height=1080,
                    codec="h264",
                    clock_domain="robot-monotonic",
                    time_base_denominator=90_000,
                ),
            ),
            sensors=(
                SensorRecordingConfigV1(
                    topic="/robot/joint_states",
                    clock_domain="robot-monotonic",
                    timestamp_mode=SensorTimestampMode.ABSOLUTE_NS,
                ),
            ),
        ),
        assets=(
            _asset_manifest(
                "videos/front.mp4",
                RecordingAssetRole.RAW_VIDEO,
                video,
                media_type="video/mp4",
                camera_id="front",
            ),
            _asset_manifest(
                "sensors/robot.mcap",
                RecordingAssetRole.SENSOR_DATA,
                sensor,
                media_type="application/x-mcap",
            ),
            _asset_manifest(
                "recording/config.json",
                RecordingAssetRole.RECORDING_CONFIG,
                config,
                media_type="application/json",
            ),
        ),
    )
    storage = InMemoryObjectStorage()
    ingest = UploadSessionService(storage, InMemoryIngestPersistence())
    recording_repository = InMemoryContinuousRecordingRepository()
    asset_repository = InMemoryRecordingAssetRepository()
    service = ContinuousRecordingService(
        recording_repository,
        ingest,
        asset_repository,
        storage,
        RegisteredDecoderProbe(
            {("json", "jsonschema"): lambda _schema, message: json.loads(message)}
        ),
    )

    grant = service.begin_upload(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        command=command,
        actor_id="operator-a",
    )
    assert grant.upload.status is RecordingUploadStatus.UPLOADING
    assert len(grant.assets) == 3
    assert all(asset.parts[0].url.startswith("memory://multipart/") for asset in grant.assets)

    for asset in asset_repository.list_assets(grant.upload.scope, grant.upload.upload_id):
        uploaded = storage.upload_part(
            asset.multipart_upload_id,
            1,
            bodies[asset.manifest.path],
            key=asset.object_key,
        )
        completed = service.complete_asset(
            auth=_auth(),
            organization_id="org-a",
            project_id="project-a",
            region_code="cn-hz",
            upload_id=grant.upload.upload_id,
            asset_id=asset.asset_id,
            command=CompleteRecordingAssetCommand(
                parts=(CompletedPart(part_number=1, etag=uploaded.etag),)
            ),
        )
    assert completed.upload.status is RecordingUploadStatus.READY_TO_COMMIT
    assert len(storage.objects) == 3

    registered = service.commit_upload(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        upload_id=grant.upload.upload_id,
        actor_id="operator-a",
        request_id="request-commit-video",
    )
    assert registered.data.schema_version == "continuous-recording/v2"
    assert registered.data.recording_upload_id == grant.upload.upload_id
    assert registered.data.upload_session_id is None
    assert registered.data.video_asset_count == 1

    # The manual cutter must be able to preview the raw OSS video before any
    # model proposal, draft revision, or finalized Episode exists.
    recording_preview = service.authorize_recording_videos(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
    )
    assert len(recording_preview.sources) == 1
    assert recording_preview.sources[0].duration_ns == 10_800_000_000_000
    assert recording_preview.sources[0].materialization == "ORIGINAL_RECORDING"

    # Raw joint playback is available on the recording clock before any Episode,
    # QC, alignment, or Lance Dataset exists.
    sensor_window = service.read_recording_sensor_window(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
        topic=None,
        start_offset_ns=0,
        end_offset_ns=2_500_000_000,
        maximum_samples=100,
    )
    assert sensor_window.topic == "/robot/joint_states"
    assert [sample.offset_ns for sample in sensor_window.samples] == [
        0,
        1_000_000_000,
        2_000_000_000,
    ]
    assert sensor_window.samples[1].value == {
        "name": ["shoulder", "elbow"],
        "position": [0.1, -0.2],
    }
    assert sensor_window.truncated is False

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth_context] = _auth
    app.dependency_overrides[get_continuous_recording_service] = lambda: service
    response = TestClient(app).get(
        "/api/v1/projects/project-a/regions/cn-hz/continuous-recordings/"
        "recording-video-a/sensor-window",
        headers={"X-Organization-Id": "org-a"},
        params={
            "start_offset_ns": "0",
            "end_offset_ns": "2500000000",
            "maximum_samples": "100",
        },
    )
    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "no-store"
    assert response.json()["samples"][2]["offset_ns"] == "2000000000"

    proposed = service.propose_model_slices(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
        command=ProposeModelSlicesCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": "episode_model_01",
                        "start_offset_ns": "1000000000",
                        "end_offset_ns": "9000000000",
                    }
                ],
                "model": {
                    "model_id": "episode-segmenter",
                    "model_version": "2026-08-31",
                    "confidence": 0.92,
                },
            }
        ),
        if_match='"v1"',
        actor_id="model-service",
        request_id="request-model-proposal",
    )
    assert proposed.revision.authoring_mode.value == "MODEL"

    reviewed = service.save_draft(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
        command=SaveSliceDraftCommand.model_validate(
            {
                "slices": [
                    {
                        "episode_id": "episode_reviewed_01",
                        "start_offset_ns": "2000000000",
                        "end_offset_ns": "8000000000",
                        "notes": "人工复核并收紧模型边界",
                    }
                ]
            }
        ),
        if_match='"v2"',
        actor_id="operator-a",
        request_id="request-human-review",
    )
    assert reviewed.revision.authoring_mode.value == "HUMAN"

    finalized = service.finalize(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
        expected_draft_revision=2,
        if_match='"v3"',
        actor_id="operator-a",
        request_id="request-finalize-reviewed",
    )
    assert finalized.recording.finalized_revision == 3
    processing = service.list_episode_processing(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
    )
    assert processing.items[0].status is EpisodeProcessingStatus.PENDING_QC
    assert processing.items[0].workflow_id is not None
    assert processing.items[0].event_id is not None
    assert len(recording_repository.outbox_events) == 1
    event = next(iter(recording_repository.outbox_events.values()))
    assert event.event_type == "continuous-recording.episode.workflow.requested.v1"
    assert event.payload["workflow_id"] == processing.items[0].workflow_id

    preview = service.authorize_episode_videos(
        auth=_auth(),
        organization_id="org-a",
        project_id="project-a",
        region_code="cn-hz",
        recording_id="recording-video-a",
        episode_id="episode_reviewed_01",
    )
    assert len(preview.sources) == 1
    assert preview.sources[0].source_url.startswith("memory://object/raw/v2/")
    assert preview.sources[0].start_offset_ns == 2_000_000_000
    assert preview.sources[0].end_offset_ns == 8_000_000_000
    assert preview.sources[0].materialization == "ORIGINAL_VIDEO_TIME_WINDOW"
    assert len(storage.objects) == 3  # preview did not generate another video
