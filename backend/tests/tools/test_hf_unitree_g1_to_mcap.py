from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from mcap.reader import make_reader
from PIL import Image

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.tools.hf_unitree_g1_to_mcap import (
    BASE_POSE_TOPIC,
    CAMERAS,
    DEX1_URDF_JOINTS,
    G1_JOINT_NAMES,
    JOINT_TOPIC,
    REQUIRED_TOPICS,
    ConversionRequest,
    SourceLayout,
    VideoSlice,
    _identity,
    _robot_configuration,
    acquire_source,
    load_episode_data,
    prune_source_cache,
    write_package,
)


def _source_row(frame: int) -> dict[str, object]:
    root = [0.1 * frame, 0.0, 0.78, 1.0, 0.0, 0.0, 0.0]
    joints = [frame + index / 100 for index in range(29)]
    desired = [value + 0.01 for value in [*root, *joints]]
    return {
        "episode_index": 0,
        "frame_index": frame,
        "timestamp": frame / 30,
        "task_index": 3,
        "observation.state.ee_state": [index / 10 for index in range(12)],
        "observation.state.hand_state": [5.5 - frame, 5.0 - frame],
        "observation.state.robot_q_current": [*root, *joints],
        "action.ee_action": [index / 20 for index in range(12)],
        "action.hand_cmd": [4.5 - frame, 4.0 - frame],
        "action.robot_q_desired": desired,
    }


def _jpeg(path: Path, color: tuple[int, int, int]) -> Path:
    Image.new("RGB", (32, 24), color).save(path, format="JPEG")
    return path


def test_loads_root_and_exact_29_joint_slice_from_lerobot_v3(tmp_path: Path) -> None:
    parquet_path = tmp_path / "source.parquet"
    pq.write_table(pa.Table.from_pylist([_source_row(0), _source_row(1)]), parquet_path)

    episode = load_episode_data(parquet_path, 0, 30.0)

    assert episode.frame_count == 2
    assert episode.relative_timestamps_ns == (0, 33_333_333)
    assert episode.root_positions[1] == (0.1, 0.0, 0.78)
    assert episode.root_orientations_wxyz[0] == (1.0, 0.0, 0.0, 0.0)
    assert len(episode.joints[0]) == len(G1_JOINT_NAMES) == 29
    assert episode.joints[1][0] == 1.0
    assert episode.joints[1][-1] == 1.28
    assert episode.hand_states_raw[0] == (5.5, 5.0)


def test_robot_configuration_marks_dex1_as_unmapped_without_calibration() -> None:
    configuration = _robot_configuration((*G1_JOINT_NAMES, *DEX1_URDF_JOINTS))
    mappings = configuration["joint_mapping"]
    assert isinstance(mappings, list)
    assert len(mappings) == 33
    assert mappings[:29] == [
        {
            "source_joint_name": name,
            "target_joint_name": name,
            "direction": "SAME",
        }
        for name in G1_JOINT_NAMES
    ]
    assert all(
        row["source_joint_name"].startswith("unmapped_no_calibration/") for row in mappings[29:]
    )
    provenance = configuration["mapping_provenance"]
    assert isinstance(provenance, dict)
    assert provenance["dex1"]["status"] == ("UNMAPPED_NO_PUBLISHED_CONTROLLER_TO_METRE_CALIBRATION")


def test_prune_source_cache_removes_only_episode_payloads(tmp_path: Path) -> None:
    cache_root = tmp_path / "cache"
    data_file = cache_root / "data/chunk-000/file-007.parquet"
    data_file.parent.mkdir(parents=True)
    data_file.write_bytes(b"parquet")
    metadata = cache_root / "meta/info.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text("{}", encoding="utf-8")
    videos: dict[str, VideoSlice] = {}
    for camera in CAMERAS:
        video_file = cache_root / f"videos/{camera.camera_id}/file-007.mp4"
        video_file.parent.mkdir(parents=True)
        video_file.write_bytes(b"video")
        videos[camera.feature_key] = VideoSlice(
            file=video_file,
            source_relative_path=str(video_file.relative_to(cache_root)),
            from_timestamp=0.0,
            to_timestamp=1.0,
        )
    layout = SourceLayout(
        info={},
        episode_metadata={"episode_index": 7},
        data_file=data_file,
        data_relative_path="data/chunk-000/file-007.parquet",
        videos=videos,
    )

    prune_source_cache(layout, cache_root)

    assert not data_file.exists()
    assert all(not video.file.exists() for video in videos.values())
    assert metadata.read_text(encoding="utf-8") == "{}"


def test_acquire_source_reads_an_explicit_lerobot_revision_without_network(
    tmp_path: Path,
) -> None:
    source = tmp_path / "revision"
    info_path = source / "meta/info.json"
    info_path.parent.mkdir(parents=True)
    info_path.write_text(
        json.dumps(
            {
                "fps": 30,
                "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
                "video_path": (
                    "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
                ),
            }
        ),
        encoding="utf-8",
    )
    episode_row: dict[str, object] = {
        "episode_index": 0,
        "length": 2,
        "data/chunk_index": 0,
        "data/file_index": 0,
    }
    for camera in CAMERAS:
        prefix = f"videos/{camera.feature_key}"
        episode_row[f"{prefix}/chunk_index"] = 0
        episode_row[f"{prefix}/file_index"] = 0
        episode_row[f"{prefix}/from_timestamp"] = 0.0
        episode_row[f"{prefix}/to_timestamp"] = 2 / 30
        video = source / f"videos/{camera.feature_key}/chunk-000/file-000.mp4"
        video.parent.mkdir(parents=True)
        video.write_bytes(b"mp4")
    episodes = source / "meta/episodes/chunk-000/file-000.parquet"
    episodes.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([episode_row]), episodes)
    data = source / "data/chunk-000/file-000.parquet"
    data.parent.mkdir(parents=True)
    data.write_bytes(b"parquet")

    layout = acquire_source(
        repository="unitreerobotics/test",
        revision="a" * 40,
        episode_index=0,
        cache_root=tmp_path / "unused-cache",
        source_root=source,
    )

    assert layout.data_file == data
    assert layout.episode_metadata["length"] == 2
    assert {video.file for video in layout.videos.values()} == {
        source / f"videos/{camera.feature_key}/chunk-000/file-000.mp4" for camera in CAMERAS
    }


def test_writes_four_camera_package_and_projects_g1_joint_names(tmp_path: Path) -> None:
    parquet_path = tmp_path / "source.parquet"
    pq.write_table(pa.Table.from_pylist([_source_row(0), _source_row(1)]), parquet_path)
    episode = load_episode_data(parquet_path, 0, 30.0)
    camera_frames: dict[str, tuple[Path, ...]] = {}
    videos: dict[str, VideoSlice] = {}
    for camera_index, camera in enumerate(CAMERAS):
        directory = tmp_path / camera.camera_id
        directory.mkdir()
        camera_frames[camera.feature_key] = tuple(
            _jpeg(
                directory / f"{frame}.jpg",
                (camera_index * 50, frame * 80, 40),
            )
            for frame in range(2)
        )
        videos[camera.feature_key] = VideoSlice(
            file=tmp_path / f"{camera.camera_id}.mp4",
            source_relative_path=f"videos/{camera.camera_id}/file-000.mp4",
            from_timestamp=0.0,
            to_timestamp=2 / 30,
        )
    layout = SourceLayout(
        info={
            "codebase_version": "v3.0",
            "robot_type": "unitree_g1",
            "fps": 30,
            "features": {camera.feature_key: {"info": {"video.fps": 30}} for camera in CAMERAS},
        },
        episode_metadata={"episode_index": 0, "length": 2},
        data_file=parquet_path,
        data_relative_path="data/chunk-000/file-000.parquet",
        videos=videos,
    )
    request = ConversionRequest(
        project_id="project-a",
        collection_task_id="14d16ba1-d95a-5ee3-aaa7-7b7d78091b52",
        episode_index=0,
        repository="unitreerobotics/g1-test",
        requested_revision="main",
        resolved_revision="a" * 40,
        robot_id="unitree-g1-mode15-dex1",
        output_root=tmp_path / "output",
        capture_start=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )

    package = write_package(
        request=request,
        episode=episode,
        layout=layout,
        camera_frames=camera_frames,
    )
    manifest = parse_manifest_bytes((package / "rollout_manifest.json").read_bytes()).manifest
    recording_config = json.loads((package / "recording-config.json").read_text(encoding="utf-8"))

    assert manifest.source_recording is not None
    assert manifest.source_recording.repository == request.repository
    assert manifest.source_recording.resolved_revision == request.resolved_revision
    assert manifest.source_recording.episode_index == request.episode_index
    assert tuple(manifest.expected_topics) == REQUIRED_TOPICS
    assert [camera.camera_id for camera in manifest.cameras] == [
        "head_stereo_left",
        "head_stereo_right",
        "wrist_left",
        "wrist_right",
    ]
    assert recording_config["clock"]["nominal_frequency_hz"] == 30.0
    assert recording_config["streams"]["dex1_raw"]["urdf_drive_status"] == (
        "PRESERVED_UNSCALED_NOT_DRIVING_URDF"
    )

    with (package / "recording.mcap").open("rb") as stream:
        messages = tuple(make_reader(stream).iter_messages())
    by_topic: dict[str, list[dict[str, object]]] = {}
    for _schema, channel, message in messages:
        by_topic.setdefault(channel.topic, []).append(json.loads(message.data))

    assert {camera.topic for camera in CAMERAS}.issubset(by_topic)
    assert all(len(by_topic[camera.topic]) == 2 for camera in CAMERAS)
    assert by_topic[JOINT_TOPIC][0] == {
        "names": list(G1_JOINT_NAMES),
        "positions": [index / 100 for index in range(29)],
    }
    assert by_topic[BASE_POSE_TOPIC][1] == {
        "orientation_wxyz": [1.0, 0.0, 0.0, 0.0],
        "position_xyz": [0.1, 0.0, 0.78],
    }

    rebound = replace(request, robot_id="platform-robot-id")
    assert _identity(rebound)["data_package_id"] != manifest.data_package_id
