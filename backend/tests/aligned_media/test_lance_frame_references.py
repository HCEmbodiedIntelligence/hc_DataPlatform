from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from hc_data_platform.aligned_media.models import (
    AlignedMediaArtifactStatus,
    AlignedMediaArtifactV1,
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignmentCameraStagingArtifactV1,
    AlignmentStagingArtifactV1,
)
from hc_data_platform.aligned_media.staging import ArrowAlignedFrameReader
from hc_data_platform.alignment.arrow_writer import ArrowFragmentWriter
from hc_data_platform.alignment.models import (
    AlignedRowV1,
    AlignedValueV1,
    AlignmentStrategy,
)
from hc_data_platform.runtime import _ArrowStepSequence
from hc_data_platform.workflow.activities import (
    _write_alignment_camera_shard,
    _write_alignment_camera_shards,
)
from hc_data_platform.workflow.projection_store import LocalProjectionArtifactStore


def _value(
    value: object,
    *,
    valid: bool = True,
    repeated: bool = False,
    source_timestamp_ns: int = 0,
) -> AlignedValueV1:
    return AlignedValueV1(
        value=value if valid else None,
        source_timestamps_ns=(source_timestamp_ns,) if valid else (),
        time_error_ns=0 if valid else None,
        valid=valid,
        repeated=repeated,
        strategy=AlignmentStrategy.CAUSAL,
    )


def test_lance_steps_replace_camera_bytes_with_exact_mp4_frame_references(
    tmp_path: Path,
) -> None:
    writer = ArrowFragmentWriter(tmp_path)
    writer.begin(rollout_id="rollout-1", attempt_id="attempt-1")
    rows = (
        AlignedRowV1(
            rollout_id="rollout-1",
            step_index=0,
            timestamp_ns=0,
            modalities={
                "/camera/front/image": _value(b"jpeg-0"),
                "state": _value(1.5),
                "action": _value([0.1, 0.2]),
            },
            sample_valid=True,
        ),
        AlignedRowV1(
            rollout_id="rollout-1",
            step_index=1,
            timestamp_ns=33_333_333,
            modalities={
                "/camera/front/image": _value(b"jpeg-0", repeated=True, source_timestamp_ns=0),
                "state": _value(2.5, source_timestamp_ns=33_333_333),
                "action": _value([0.3, 0.4], source_timestamp_ns=33_333_333),
            },
            sample_valid=True,
        ),
        AlignedRowV1(
            rollout_id="rollout-1",
            step_index=2,
            timestamp_ns=66_666_666,
            modalities={
                "/camera/front/image": _value(None, valid=False),
                "state": _value(3.5, source_timestamp_ns=66_666_666),
                "action": _value([0.5, 0.6], source_timestamp_ns=66_666_666),
            },
            sample_valid=False,
        ),
    )
    for row in rows:
        writer.write_row(row)
    uri = writer.commit(
        row_count=len(rows),
        content_sha256="a" * 64,
        schema_sha256="b" * 64,
    )

    artifact = AlignedMediaArtifactV1(
        artifact_id="artifact-front",
        artifact_key="c" * 64,
        scope=AlignedMediaScopeV1(
            organization_id="organization-1",
            project_id="project-1",
            region_code="cn-test",
        ),
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        dataset_version=1,
        camera_id="/camera/front/image",
        profile_id="canonical-h264-crf20-v1",
        profile_version="1",
        alignment_version="be07-align/1:causal-30hz-v1",
        source_sha256="d" * 64,
        status=AlignedMediaArtifactStatus.READY,
        media_object_key="aligned-media/exact/media.mp4",
        frame_count=3,
        duration_seconds=0.1,
        width=64,
        height=48,
        created_at=datetime(2026, 8, 31, tzinfo=timezone.utc),
    )

    steps = tuple(_ArrowStepSequence(uri, len(rows), media_artifacts=(artifact,)))
    camera_refs = [step.modalities["/camera/front/image"] for step in steps]

    assert [item["frame_index"] for item in camera_refs] == [0, 1, 2]
    assert [item["pts"] for item in camera_refs] == [0, 1, 2]
    assert [item["timestamp_ns"] for item in camera_refs] == ["0", "33333333", "66666666"]
    assert camera_refs[1]["source_timestamps_ns"] == ["0"]
    assert all(item["pts_time_base_numerator"] == 1 for item in camera_refs)
    assert all(item["pts_time_base_denominator"] == 30 for item in camera_refs)
    assert [item["valid"] for item in camera_refs] == [True, True, False]
    assert [item["placeholder"] for item in camera_refs] == [False, False, True]
    assert [item["repeated"] for item in camera_refs] == [False, True, False]
    assert [item["dropped"] for item in camera_refs] == [False, False, True]
    assert all(item["object_key"] == "aligned-media/exact/media.mp4" for item in camera_refs)
    assert all(item["artifact_id"] == "artifact-front" for item in camera_refs)
    assert all(item["schema_version"] == "aligned-media-frame-ref/v1" for item in camera_refs)
    assert not any(isinstance(item, bytes) for item in camera_refs)

    assert [step.modalities["state"] for step in steps] == [1.5, 2.5, 3.5]
    assert [step.modalities["action"] for step in steps] == [
        [0.1, 0.2],
        [0.3, 0.4],
        [0.5, 0.6],
    ]
    assert steps[1].source_timestamps_ns["/camera/front/image"] == (0,)
    assert steps[1].valid["/camera/front/image"] is True
    assert steps[1].repeated["/camera/front/image"] is True
    assert steps[2].sample_valid is False


def test_frame_reader_rejects_cross_rollout_staging(tmp_path: Path) -> None:
    writer = ArrowFragmentWriter(tmp_path / "writer")
    writer.begin(rollout_id="rollout-a", attempt_id="attempt-1")
    writer.write_row(
        AlignedRowV1(
            rollout_id="rollout-a",
            step_index=0,
            timestamp_ns=0,
            modalities={"front": _value(b"jpeg")},
            sample_valid=True,
        )
    )
    uri = writer.commit(
        row_count=1,
        content_sha256="e" * 64,
        schema_sha256="f" * 64,
    )
    source = Path(uri.removeprefix("file://"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    store = LocalProjectionArtifactStore(tmp_path / "staging")
    size = store.publish_file("staging/alignment.arrow", source, sha256=digest)
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    request = AlignedMediaGenerationRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-b",
        expected_dataset_version=1,
        camera_id="front",
        source_sha256="a" * 64,
        alignment=AlignmentStagingArtifactV1(
            object_key="staging/alignment.arrow",
            content_sha256=digest,
            size_bytes=size,
            row_count=1,
            alignment_version="align-v1",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )

    with pytest.raises(ValueError, match="rollout does not match"):
        tuple(ArrowAlignedFrameReader(store).read_camera_frames(request))


def test_frame_reader_downloads_camera_shard_instead_of_full_alignment(
    tmp_path: Path,
) -> None:
    writer = ArrowFragmentWriter(tmp_path / "writer-shard")
    writer.begin(rollout_id="rollout-a", attempt_id="attempt-shard")
    writer.write_row(
        AlignedRowV1(
            rollout_id="rollout-a",
            step_index=0,
            timestamp_ns=0,
            modalities={
                "front": _value(b"front-jpeg"),
                "rear": _value(b"rear-jpeg"),
            },
            sample_valid=True,
        )
    )
    uri = writer.commit(
        row_count=1,
        content_sha256="1" * 64,
        schema_sha256="2" * 64,
    )
    shard_path = tmp_path / "front.arrow"
    _write_alignment_camera_shard(
        source_path=Path(uri.removeprefix("file://")),
        destination_path=shard_path,
        rollout_id="rollout-a",
        camera_id="front",
        expected_rows=1,
    )
    digest = hashlib.sha256(shard_path.read_bytes()).hexdigest()
    store = LocalProjectionArtifactStore(tmp_path / "shard-store")
    size = store.publish_file("staging/front.arrow", shard_path, sha256=digest)
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    request = AlignedMediaGenerationRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-a",
        expected_dataset_version=1,
        camera_id="front",
        source_sha256="a" * 64,
        alignment=AlignmentStagingArtifactV1(
            object_key="staging/full-object-must-not-be-read.arrow",
            content_sha256="f" * 64,
            size_bytes=1,
            row_count=1,
            alignment_version="align-v1",
            camera_shards={
                "front": AlignmentCameraStagingArtifactV1(
                    object_key="staging/front.arrow",
                    content_sha256=digest,
                    size_bytes=size,
                    row_count=1,
                )
            },
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )

    frames = tuple(ArrowAlignedFrameReader(store).read_camera_frames(request))

    assert len(frames) == 1
    assert frames[0].image == b"front-jpeg"


def test_camera_shards_are_fanned_out_from_one_alignment_scan(tmp_path: Path) -> None:
    writer = ArrowFragmentWriter(tmp_path / "writer-fanout")
    writer.begin(rollout_id="rollout-a", attempt_id="attempt-fanout")
    for index in range(2):
        writer.write_row(
            AlignedRowV1(
                rollout_id="rollout-a",
                step_index=index,
                timestamp_ns=index * 33_333_333,
                modalities={
                    "front": _value(f"front-{index}".encode()),
                    "rear": _value(f"rear-{index}".encode()),
                },
                sample_valid=True,
            )
        )
    uri = writer.commit(
        row_count=2,
        content_sha256="3" * 64,
        schema_sha256="4" * 64,
    )
    destinations = {
        "front": tmp_path / "front-fanout.arrow",
        "rear": tmp_path / "rear-fanout.arrow",
    }

    _write_alignment_camera_shards(
        source_path=Path(uri.removeprefix("file://")),
        destinations=destinations,
        rollout_id="rollout-a",
        expected_rows=2,
    )

    store = LocalProjectionArtifactStore(tmp_path / "fanout-store")
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    shards: dict[str, AlignmentCameraStagingArtifactV1] = {}
    for camera_id, path in destinations.items():
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        key = f"staging/{camera_id}.arrow"
        shards[camera_id] = AlignmentCameraStagingArtifactV1(
            object_key=key,
            content_sha256=digest,
            size_bytes=store.publish_file(key, path, sha256=digest),
            row_count=2,
        )
    alignment = AlignmentStagingArtifactV1(
        object_key="staging/full-object-must-not-be-read.arrow",
        content_sha256="f" * 64,
        size_bytes=1,
        row_count=2,
        alignment_version="align-v1",
        camera_shards=shards,
        created_at=now,
        expires_at=now + timedelta(hours=1),
    )

    for camera_id in destinations:
        request = AlignedMediaGenerationRequestV1(
            project_id="project-1",
            dataset_id="dataset-1",
            rollout_id="rollout-a",
            expected_dataset_version=1,
            camera_id=camera_id,
            source_sha256="a" * 64,
            alignment=alignment,
        )
        frames = tuple(ArrowAlignedFrameReader(store).read_camera_frames(request))
        assert [frame.image for frame in frames] == [
            f"{camera_id}-0".encode(),
            f"{camera_id}-1".encode(),
        ]
