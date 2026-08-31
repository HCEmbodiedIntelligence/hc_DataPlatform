from __future__ import annotations

import hashlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageStat

from hc_data_platform.alignment.models import (
    AlignedFragmentManifestV1,
    AlignmentInputV1,
    AlignmentProfileV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.alignment.ports import AlignmentPort, FragmentWriterPort
from hc_data_platform.quality.models import (
    QcReportV1,
    QualityInputV1,
    QualityProfileV1,
    QualityStatus,
    QualityStreamObservationV1,
)
from hc_data_platform.quality.ports import QualityEvaluationPort
from hc_data_platform.tools import hf_unitree_g1_to_mcap as source_reader

from .orchestration import LeRobotEpisodeSourceRefV1


@dataclass(frozen=True, slots=True)
class EpisodeStream:
    """Unified internal Episode stream consumed directly by QC and alignment."""

    source: LeRobotEpisodeSourceRefV1
    source_sha256: str
    rollout_id: str
    episode: source_reader.EpisodeData
    camera_frames: dict[str, tuple[Path, ...]]

    @property
    def start_ns(self) -> int:
        return 0

    @property
    def end_ns(self) -> int:
        period_ns = round(1_000_000_000 / self.episode.fps)
        return self.episode.relative_timestamps_ns[-1] + period_ns

    @property
    def quality_input(self) -> QualityInputV1:
        return QualityInputV1(
            rollout_id=self.rollout_id,
            source_sha256=self.source_sha256,
            start_ns=self.start_ns,
            end_ns=self.end_ns,
            topic_timestamps_ns={},
        )

    @property
    def alignment_input(self) -> AlignmentInputV1:
        camera_topics = {camera.topic for camera in source_reader.CAMERAS}
        return AlignmentInputV1(
            rollout_id=self.rollout_id,
            source_sha256=self.source_sha256,
            attempt_id=f"lerobot-v3-{self.source_sha256[:24]}",
            start_ns=self.start_ns,
            end_ns=self.end_ns,
            streams={
                topic: ModalityStreamV1(
                    kind=(
                        ModalityKind.IMAGE
                        if topic in camera_topics
                        else ModalityKind.ACTION
                        if "action" in topic or "command" in topic or "target" in topic
                        else ModalityKind.DISCRETE
                        if topic == source_reader.SOURCE_TOPIC
                        else ModalityKind.CONTINUOUS
                    ),
                    samples=(),
                )
                for topic in source_reader.ACTUAL_TOPICS
            },
        )

    def quality_observations(self) -> Iterator[QualityStreamObservationV1]:
        for topic, timestamp_ns, value in self._samples():
            if topic in {camera.topic for camera in source_reader.CAMERAS}:
                if not isinstance(value, (bytes, bytearray, memoryview)):
                    yield QualityStreamObservationV1(
                        topic=topic,
                        timestamp_ns=timestamp_ns,
                        is_camera=True,
                        corrupt=True,
                    )
                    continue
                encoded = bytes(value)
                try:
                    with _InMemoryImage(encoded) as image:
                        luma = float(ImageStat.Stat(image.convert("L")).mean[0])
                    yield QualityStreamObservationV1(
                        topic=topic,
                        timestamp_ns=timestamp_ns,
                        is_camera=True,
                        luma_mean=luma,
                        fingerprint=hashlib.sha256(encoded).hexdigest(),
                    )
                except Exception:
                    yield QualityStreamObservationV1(
                        topic=topic,
                        timestamp_ns=timestamp_ns,
                        is_camera=True,
                        corrupt=True,
                    )
            else:
                yield QualityStreamObservationV1(topic=topic, timestamp_ns=timestamp_ns)

    def alignment_samples(self) -> Iterator[tuple[str, TimedSampleV1]]:
        for topic, timestamp_ns, value in self._samples():
            yield topic, TimedSampleV1(timestamp_ns=timestamp_ns, value=value)

    def _samples(self) -> Iterator[tuple[str, int, object]]:
        source = {
            "episode_index": self.source.episode_index,
            "nominal_frequency_hz": self.episode.fps,
            "raw_upload_id": self.source.raw_upload_id,
            "raw_manifest_key": self.source.raw_manifest_key,
            "source_format": self.source.source_format,
            "task_index": self.episode.task_index,
        }
        for index, timestamp_ns in enumerate(self.episode.relative_timestamps_ns):
            for camera in source_reader.CAMERAS:
                frame = self.camera_frames[camera.feature_key][index]
                yield camera.topic, timestamp_ns, frame.read_bytes()
            joint_value = {
                "names": source_reader.G1_JOINT_NAMES,
                "positions": self.episode.joints[index],
            }
            yield source_reader.STATE_TOPIC, timestamp_ns, joint_value
            yield source_reader.JOINT_TOPIC, timestamp_ns, joint_value
            values_by_topic = {
                source_reader.ACTION_TOPIC: (
                    source_reader.G1_JOINT_NAMES,
                    self.episode.target_joints[index],
                ),
                source_reader.END_EFFECTOR_STATE_TOPIC: (
                    source_reader.END_EFFECTOR_NAMES,
                    self.episode.end_effector_states[index],
                ),
                source_reader.END_EFFECTOR_ACTION_TOPIC: (
                    source_reader.END_EFFECTOR_NAMES,
                    self.episode.end_effector_actions[index],
                ),
                source_reader.GRIPPER_STATE_TOPIC: (
                    source_reader.DEX1_RAW_NAMES,
                    self.episode.hand_states_raw[index],
                ),
                source_reader.GRIPPER_COMMAND_TOPIC: (
                    source_reader.DEX1_RAW_NAMES,
                    self.episode.hand_commands_raw[index],
                ),
            }
            for topic, (names, values) in values_by_topic.items():
                yield topic, timestamp_ns, {"names": names, "values": values}
            yield (
                source_reader.BASE_POSE_TOPIC,
                timestamp_ns,
                {
                    "orientation_wxyz": self.episode.root_orientations_wxyz[index],
                    "position_xyz": self.episode.root_positions[index],
                },
            )
            yield (
                source_reader.BASE_TARGET_TOPIC,
                timestamp_ns,
                {
                    "orientation_wxyz": self.episode.target_root_orientations_wxyz[index],
                    "position_xyz": self.episode.target_root_positions[index],
                },
            )
            yield source_reader.SOURCE_TOPIC, timestamp_ns, source


class _InMemoryImage:
    """Pillow context wrapper for in-memory JPEG bytes."""

    def __init__(self, encoded: bytes) -> None:
        from io import BytesIO

        self._stream = BytesIO(encoded)
        self._image = Image.open(self._stream)

    def __enter__(self) -> Image.Image:
        self._image.load()
        return self._image

    def __exit__(self, *_args: object) -> None:
        self._image.close()
        self._stream.close()


class LeRobotAdapter:
    """Read native LeRobot Raw and expose an EpisodeStream without writing MCAP."""

    @contextmanager
    def open_episode(
        self,
        source_root: Path,
        source: LeRobotEpisodeSourceRefV1,
        *,
        raw_manifest_sha256: str,
    ) -> Iterator[EpisodeStream]:
        if len(raw_manifest_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in raw_manifest_sha256
        ):
            raise ValueError("Raw manifest SHA-256 must be lowercase hexadecimal")
        layout = source_reader.acquire_source(
            repository="platform-raw/lerobot",
            revision=source.raw_upload_id,
            episode_index=source.episode_index,
            cache_root=source_root,
            source_root=source_root,
        )
        fps_value = layout.info.get("fps")
        if not isinstance(fps_value, (int, float)) or not 0 < float(fps_value) <= 240:
            raise RuntimeError("LeRobot metadata has an invalid fps")
        episode = source_reader.load_episode_data(
            layout.data_file,
            source.episode_index,
            float(fps_value),
        )
        if layout.episode_metadata.get("length") != episode.frame_count:
            raise RuntimeError("LeRobot episode metadata length differs from Parquet rows")
        with tempfile.TemporaryDirectory(prefix="hc-lerobot-adapter-") as temporary:
            frames = source_reader.extract_camera_frames(layout, episode, Path(temporary))
            yield EpisodeStream(
                source=source,
                source_sha256=raw_manifest_sha256,
                rollout_id=(f"lerobot-{source.raw_upload_id[:16]}-ep-{source.episode_index:06d}"),
                episode=episode,
                camera_frames=frames,
            )


@dataclass(frozen=True, slots=True)
class LeRobotEpisodeProcessingResult:
    quality: QcReportV1
    alignment: AlignedFragmentManifestV1 | None


class LeRobotEpisodeProcessor:
    """Connect a LeRobot EpisodeStream to the common QC/alignment engines."""

    def __init__(self, quality: QualityEvaluationPort, alignment: AlignmentPort) -> None:
        self._quality = quality
        self._alignment = alignment

    def process(
        self,
        stream: EpisodeStream,
        *,
        quality_profile: QualityProfileV1,
        alignment_profile: AlignmentProfileV1,
        writer: FragmentWriterPort,
    ) -> LeRobotEpisodeProcessingResult:
        quality = self._quality.evaluate_stream(
            stream.quality_input,
            stream.quality_observations(),
            quality_profile,
        )
        if quality.status is not QualityStatus.PASS:
            return LeRobotEpisodeProcessingResult(quality=quality, alignment=None)
        metadata = stream.alignment_input
        aligned = self._alignment.align_stream_to_writer(
            rollout_id=metadata.rollout_id,
            source_sha256=metadata.source_sha256,
            attempt_id=metadata.attempt_id,
            start_ns=metadata.start_ns,
            end_ns=metadata.end_ns,
            stream_kinds={name: item.kind for name, item in metadata.streams.items()},
            samples=stream.alignment_samples(),
            profile=alignment_profile,
            writer=writer,
        )
        return LeRobotEpisodeProcessingResult(quality=quality, alignment=aligned)
