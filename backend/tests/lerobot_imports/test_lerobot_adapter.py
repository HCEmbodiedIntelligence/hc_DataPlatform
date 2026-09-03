from __future__ import annotations

from pathlib import Path

from PIL import Image

from hc_data_platform.lerobot_imports.adapter import EpisodeStream
from hc_data_platform.lerobot_imports.orchestration import LeRobotEpisodeSourceRefV1
from hc_data_platform.tools import hf_unitree_g1_to_mcap as source_reader


def _vectors(width: int, count: int = 2) -> tuple[tuple[float, ...], ...]:
    return tuple(tuple(float(index) for index in range(width)) for _ in range(count))


def _episode() -> source_reader.EpisodeData:
    return source_reader.EpisodeData(
        fps=30.0,
        relative_timestamps_ns=(0, 33_333_333),
        frame_indexes=(0, 1),
        task_index=0,
        root_positions=_vectors(3),
        root_orientations_wxyz=_vectors(4),
        joints=_vectors(29),
        hand_states_raw=_vectors(2),
        end_effector_states=_vectors(12),
        target_root_positions=_vectors(3),
        target_root_orientations_wxyz=_vectors(4),
        target_joints=_vectors(29),
        hand_commands_raw=_vectors(2),
        end_effector_actions=_vectors(12),
    )


def test_episode_stream_feeds_quality_and_alignment_without_mcap(tmp_path: Path) -> None:
    frames: dict[str, tuple[Path, ...]] = {}
    for camera in source_reader.CAMERAS:
        camera_frames: list[Path] = []
        for index in range(2):
            path = tmp_path / f"{camera.camera_id}-{index}.jpg"
            Image.new("RGB", (4, 3), color=(20 + index, 30, 40)).save(path, "JPEG")
            camera_frames.append(path)
        frames[camera.feature_key] = tuple(camera_frames)
    stream = EpisodeStream(
        source=LeRobotEpisodeSourceRefV1(
            raw_upload_id="a" * 32,
            raw_manifest_key=f"raw/org/dataset/{'a' * 32}/manifest.json",
            episode_index=0,
        ),
        source_sha256="b" * 64,
        rollout_id="lerobot-rollout-0",
        episode=_episode(),
        camera_frames=frames,
    )

    quality = tuple(stream.quality_observations())
    aligned = tuple(stream.alignment_samples())

    assert stream.quality_input.rollout_id == "lerobot-rollout-0"
    assert set(stream.alignment_input.streams) == set(source_reader.ACTUAL_TOPICS)
    assert len(quality) == len(source_reader.ACTUAL_TOPICS) * 2
    assert len(aligned) == len(source_reader.ACTUAL_TOPICS) * 2
    camera_quality = [item for item in quality if item.is_camera]
    assert camera_quality and all(not item.corrupt and item.fingerprint for item in camera_quality)
    assert not tuple(tmp_path.glob("*.mcap"))
