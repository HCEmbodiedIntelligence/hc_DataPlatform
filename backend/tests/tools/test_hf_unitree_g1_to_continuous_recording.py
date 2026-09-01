from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from mcap.reader import make_reader

from hc_data_platform.continuous_recordings.asset_models import (
    CreateRecordingUploadCommand,
    RecordingAssetRole,
    SensorTimestampMode,
)
from hc_data_platform.tools import hf_unitree_g1_to_continuous_recording as subject
from hc_data_platform.tools.hf_unitree_g1_to_mcap import (
    CAMERAS,
    JOINT_TOPIC,
    SOURCE_TOPIC,
)
from hc_data_platform.tools.native_unitree_g1_recording import (
    SENSOR_PATH,
    UPLOAD_COMMAND_PATH,
    VideoProbe,
)


def _data_row(episode: int, frame: int) -> dict[str, object]:
    root = [episode / 10, 0.0, 0.78, 1.0, 0.0, 0.0, 0.0]
    joints = [episode + frame + index / 100 for index in range(29)]
    current = [*root, *joints]
    return {
        "episode_index": episode,
        "frame_index": frame,
        "timestamp": frame / 30,
        "task_index": 7,
        "observation.state.ee_state": [index / 10 for index in range(12)],
        "observation.state.hand_state": [5.5 - frame, 5.0 - frame],
        "observation.state.robot_q_current": current,
        "action.ee_action": [index / 20 for index in range(12)],
        "action.hand_cmd": [4.5 - frame, 4.0 - frame],
        "action.robot_q_desired": [value + 0.01 for value in current],
    }


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    info = {
        "codebase_version": "v3.0",
        "robot_type": "unitree_g1",
        "total_episodes": 2,
        "total_frames": 3,
        "fps": 30,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4",
    }
    info_path = root / "meta/info.json"
    info_path.parent.mkdir(parents=True)
    info_path.write_text(json.dumps(info), encoding="utf-8")
    episode_rows: list[dict[str, object]] = []
    start = 0.0
    for episode, frame_count in ((0, 2), (1, 1)):
        end = start + frame_count / 30
        row: dict[str, object] = {
            "episode_index": episode,
            "length": frame_count,
            "tasks": [f"task-{episode}"],
            "data/chunk_index": 0,
            "data/file_index": 0,
        }
        for camera in CAMERAS:
            prefix = f"videos/{camera.feature_key}"
            row[f"{prefix}/chunk_index"] = 0
            row[f"{prefix}/file_index"] = 0
            row[f"{prefix}/from_timestamp"] = start
            row[f"{prefix}/to_timestamp"] = end
        episode_rows.append(row)
        start = end
    episodes_path = root / "meta/episodes/chunk-000/file-000.parquet"
    episodes_path.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist(episode_rows), episodes_path)
    data_path = root / "data/chunk-000/file-000.parquet"
    data_path.parent.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist([_data_row(0, 0), _data_row(0, 1), _data_row(1, 0)]),
        data_path,
    )
    for camera in CAMERAS:
        video = root / f"videos/{camera.feature_key}/chunk-000/file-000.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(f"video-{camera.camera_id}".encode())
    return root


def test_inspects_all_episode_boundaries_and_shared_video_shards(tmp_path: Path) -> None:
    inventory = subject.inspect_source(_source(tmp_path))

    assert inventory.total_frames == 3
    assert inventory.duration_ns == 100_000_000
    assert [(item.start_offset_ns, item.end_offset_ns) for item in inventory.episodes] == [
        (0, 66_666_667),
        (66_666_667, 100_000_000),
    ]
    assert all(len(paths) == 1 for paths in inventory.video_files_by_feature.values())


def test_writes_all_robot_topics_on_one_continuous_recording_clock(tmp_path: Path) -> None:
    inventory = subject.inspect_source(_source(tmp_path))
    destination = tmp_path / "robot.mcap"

    count = subject.write_sensor_mcap(destination, inventory)

    assert count == 3 * len(subject.SENSOR_TOPICS)
    by_topic: dict[str, list[tuple[int, dict[str, object]]]] = {}
    with destination.open("rb") as stream:
        for _schema, channel, message in make_reader(stream).iter_messages():
            by_topic.setdefault(channel.topic, []).append(
                (message.log_time, json.loads(message.data))
            )
    assert [timestamp for timestamp, _value in by_topic[JOINT_TOPIC]] == [
        0,
        33_333_333,
        66_666_667,
    ]
    assert [value["episode_index"] for _timestamp, value in by_topic[SOURCE_TOPIC]] == [
        0,
        0,
        1,
    ]


def test_ignores_only_trailing_rows_without_episode_metadata_or_video(tmp_path: Path) -> None:
    source = _source(tmp_path)
    data_path = source / "data/chunk-000/file-000.parquet"
    rows = pq.read_table(data_path).to_pylist()
    rows.append(_data_row(2, 0))
    pq.write_table(pa.Table.from_pylist(rows), data_path)
    inventory = subject.inspect_source(source)

    count = subject.write_sensor_mcap(tmp_path / "robot.mcap", inventory)

    assert inventory.source_parquet_rows == 4
    assert inventory.ignored_undeclared_rows == 1
    assert count == 3 * len(subject.SENSOR_TOPICS)


def test_synthesizes_an_upload_ready_v2_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)

    def fake_concat(_sources: tuple[Path, ...], destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"joined-video")

    monkeypatch.setattr(subject, "concatenate_video_shards", fake_concat)
    monkeypatch.setattr(subject, "_probe_frame_count", lambda _path: 3)
    monkeypatch.setattr(
        subject,
        "probe_mp4",
        lambda _path: VideoProbe(
            codec="av1",
            fps=30.0,
            width=640,
            height=480,
            duration_seconds=0.1,
            time_base_numerator=1,
            time_base_denominator=15_360,
        ),
    )
    output = subject.synthesize_recording(
        subject.SynthesisRequest(
            source_root=source,
            output_dir=tmp_path / "output",
            project_id="project-a",
            collection_task_id="task-a",
            robot_id="robot-a",
            device_id="g1-source-test",
            capture_started_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
        )
    )

    command = CreateRecordingUploadCommand.model_validate_json(
        (output / UPLOAD_COMMAND_PATH).read_text(encoding="utf-8")
    )
    assert command.schema_version == "continuous-recording-upload/v2"
    assert (
        command.capture_ended_at - command.capture_started_at
    ).total_seconds() == pytest.approx(0.1)
    assert len(command.recording_config.cameras) == 4
    assert {sensor.topic for sensor in command.recording_config.sensors} == set(
        subject.SENSOR_TOPICS
    )
    assert all(
        sensor.timestamp_mode is SensorTimestampMode.RECORDING_OFFSET_NS
        for sensor in command.recording_config.sensors
    )
    assert {asset.role for asset in command.assets} == {
        RecordingAssetRole.RAW_VIDEO,
        RecordingAssetRole.SENSOR_DATA,
        RecordingAssetRole.RECORDING_CONFIG,
        RecordingAssetRole.AUXILIARY,
    }
    assert (output / SENSOR_PATH).is_file()
    source_index = json.loads((output / subject.EPISODE_INDEX_PATH).read_text(encoding="utf-8"))
    assert source_index["episode_count"] == 2
    assert source_index["duration_ns"] == "100000000"
