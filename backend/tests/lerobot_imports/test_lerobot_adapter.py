from __future__ import annotations

from pathlib import Path

import hashlib
import subprocess

from hc_data_platform.aligned_media.models import OriginalVideoReferenceV1

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
    video = tmp_path / "original.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pixel_format",
            "rgb24",
            "-video_size",
            "16x16",
            "-framerate",
            "30",
            "-i",
            "pipe:0",
            "-frames:v",
            "2",
            "-threads",
            "1",
            "-c:v",
            "libx264",
            str(video),
        ],
        input=bytes([40]) * (16 * 16 * 3) + bytes([140]) * (16 * 16 * 3),
        check=True,
    )
    originals = {
        camera.topic: OriginalVideoReferenceV1(
            object_key="raw/original.mp4",
            size_bytes=video.stat().st_size,
            content_sha256=hashlib.sha256(video.read_bytes()).hexdigest(),
            start_seconds=0,
            end_seconds=2 / 30,
            width=16,
            height=16,
            codec="h264",
            fps=30,
        )
        for camera in source_reader.CAMERAS
    }
    layout = source_reader.SourceLayout(
        info={},
        episode_metadata={},
        data_file=tmp_path / "data.parquet",
        data_relative_path="data.parquet",
        videos={
            c.feature_key: source_reader.VideoSlice(video, "original.mp4", 0, 2 / 30)
            for c in source_reader.CAMERAS
        },
    )
    stream = EpisodeStream(
        source=LeRobotEpisodeSourceRefV1(
            raw_upload_id="a" * 32,
            raw_manifest_key=f"raw/org/dataset/{'a' * 32}/manifest.json",
            episode_index=0,
        ),
        source_sha256="b" * 64,
        rollout_id="lerobot-rollout-0",
        episode=_episode(),
        layout=layout,
        original_videos=originals,
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
    assert not tuple(tmp_path.rglob("*.jpg"))
    assert all(isinstance(sample.value, dict) for topic, sample in aligned if topic in originals)
    # Native actions exist in Parquet and must reach QC's decoded action rules.
    # An empty QualityInput used to mark every valid import QC_ACTION_MISSING.
    from hc_data_platform.quality.engine import QualityEngine
    from hc_data_platform.quality.models import QualityProfileV1, QualityStatus

    assert len(stream.quality_input.actions) == 2
    assert len(stream.quality_input.joints) == 2
    report = QualityEngine().evaluate_stream(
        stream.quality_input,
        stream.quality_observations(),
        QualityProfileV1(
            profile_id="native-test",
            required_topics=frozenset(source_reader.ACTUAL_TOPICS),
            action={"topic": source_reader.ACTION_TOPIC},
        ),
    )
    assert report.status is QualityStatus.PASS, report.findings
