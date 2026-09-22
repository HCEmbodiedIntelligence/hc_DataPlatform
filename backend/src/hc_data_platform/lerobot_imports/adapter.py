from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageStat

from hc_data_platform.aligned_media.models import OriginalVideoReferenceV1
from hc_data_platform.alignment.models import (
    AlignmentInputV1,
    ModalityKind,
    ModalityStreamV1,
    TimedSampleV1,
)
from hc_data_platform.quality.models import (
    ActionObservation,
    JointObservation,
    QualityInputV1,
    QualityStreamObservationV1,
)
from hc_data_platform.tools import hf_unitree_g1_to_mcap as source_reader

from .orchestration import LeRobotEpisodeSourceRefV1
from .profiles import profile_for_info
from .reader import read_episode


@dataclass(frozen=True, slots=True)
class EpisodeStream:
    """Unified internal Episode stream consumed directly by QC and alignment."""

    source: LeRobotEpisodeSourceRefV1
    source_sha256: str
    rollout_id: str
    episode: source_reader.EpisodeData
    layout: source_reader.SourceLayout
    original_videos: dict[str, OriginalVideoReferenceV1]
    camera_luma: dict[tuple[str, int], float] = field(default_factory=dict)

    @property
    def profile(self):
        return profile_for_info(self.layout.info)

    @property
    def start_ns(self) -> int:
        return 0

    @property
    def end_ns(self) -> int:
        period_ns = round(1_000_000_000 / self.episode.fps)
        # Parquet float timestamps have sub-microsecond rounding noise. Preserve
        # genuine gaps, but don't manufacture an extra aligned frame for that noise.
        observed = self.episode.relative_timestamps_ns[-1] + period_ns
        nominal = int(self.episode.frame_count * 1_000_000_000 / self.episode.fps)
        return nominal if abs(observed - nominal) <= 1_000 else observed

    @property
    def quality_input(self) -> QualityInputV1:
        return QualityInputV1(
            rollout_id=self.rollout_id,
            source_sha256=self.source_sha256,
            start_ns=self.start_ns,
            end_ns=self.end_ns,
            topic_timestamps_ns={},
            actions=tuple(
                ActionObservation(timestamp_ns=timestamp, values=values)
                for timestamp, values in zip(
                    self.episode.relative_timestamps_ns, self.episode.target_joints, strict=True
                )
            ),
            joints=tuple(
                JointObservation(
                    timestamp_ns=timestamp,
                    positions=dict(zip(self.profile.state_names, values, strict=True)),
                )
                for timestamp, values in zip(
                    self.episode.relative_timestamps_ns, self.episode.joints, strict=True
                )
            ),
        )

    @property
    def alignment_input(self) -> AlignmentInputV1:
        camera_topics = {camera.topic for camera in self.profile.cameras}
        attempt = self.source.processing_attempt_id or self.source.import_attempt_id
        return AlignmentInputV1(
            rollout_id=self.rollout_id,
            source_sha256=self.source_sha256,
            attempt_id=(
                f"lerobot-retry-{attempt}" if attempt else f"lerobot-v3-{self.source_sha256[:24]}"
            ),
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
                for topic in self.profile.topics
            },
        )

    def alignment_samples(self) -> Iterator[tuple[str, TimedSampleV1]]:
        for topic, timestamp_ns, value in self._samples():
            yield topic, TimedSampleV1(timestamp_ns=timestamp_ns, value=value)

    def quality_observations(self) -> Iterator[QualityStreamObservationV1]:
        """Decode one bounded gray frame at a time; never encode or save images."""
        for camera in self.profile.cameras:
            video = self.layout.videos[camera.feature_key]
            original = self.original_videos[camera.topic]
            command = [
                "ffmpeg",
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-ss",
                f"{video.from_timestamp:.9f}",
                "-i",
                str(video.file),
                "-map",
                "0:v:0",
                "-an",
                "-sn",
                "-threads",
                "1",
                "-frames:v",
                str(self.episode.frame_count),
                "-fps_mode",
                "passthrough",
                "-pix_fmt",
                "gray",
                "-f",
                "rawvideo",
                "pipe:1",
            ]
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors)
                try:
                    assert process.stdout is not None
                    size = original.width * original.height
                    if size > 32 * 1024**2:
                        raise ValueError("video frame exceeds bounded QC decode size")
                    for timestamp in self.episode.relative_timestamps_ns:
                        frame = process.stdout.read(size)
                        if len(frame) != size:
                            yield QualityStreamObservationV1(
                                topic=camera.topic,
                                timestamp_ns=timestamp,
                                is_camera=True,
                                corrupt=True,
                            )
                            continue
                        with Image.frombytes(
                            "L", (original.width, original.height), frame
                        ) as image:
                            luma = float(ImageStat.Stat(image).mean[0])
                        self.camera_luma[(camera.topic, timestamp)] = luma
                        yield QualityStreamObservationV1(
                            topic=camera.topic,
                            timestamp_ns=timestamp,
                            is_camera=True,
                            luma_mean=luma,
                            fingerprint=hashlib.sha256(frame).hexdigest(),
                        )
                    if process.wait(timeout=30) != 0:
                        raise RuntimeError("original video decoding failed during quality checks")
                finally:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
                    if process.stdout is not None:
                        process.stdout.close()
        camera_topics = set(self.original_videos)
        for topic, timestamp, _value in self._samples():
            if topic not in camera_topics:
                yield QualityStreamObservationV1(topic=topic, timestamp_ns=timestamp)

    def _samples(self) -> Iterator[tuple[str, int, object]]:
        if not self.profile.legacy_g1:
            yield from self._generic_samples()
            return
        source = {
            "episode_index": self.source.episode_index,
            "nominal_frequency_hz": self.episode.fps,
            "raw_upload_id": self.source.raw_upload_id,
            "raw_manifest_key": self.source.raw_manifest_key,
            "source_format": self.source.source_format,
            "task_index": self.episode.task_index,
        }
        for index, timestamp_ns in enumerate(self.episode.relative_timestamps_ns):
            for camera in self.profile.cameras:
                original = self.original_videos[camera.topic]
                source_pts_ns = round(original.start_seconds * 1_000_000_000) + timestamp_ns
                yield (
                    camera.topic,
                    timestamp_ns,
                    {
                        "source_frame_index": round(source_pts_ns * original.fps / 1_000_000_000),
                        "source_pts_ns": source_pts_ns,
                    },
                )
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

    def _generic_samples(self) -> Iterator[tuple[str, int, object]]:
        episode = self.episode
        units = [axis["unit"] for axis in episode.context.get("profile", {}).get("axes", [])]
        features = self.layout.info["features"]
        state_units = features["observation.state"].get("units", units)
        action_units = features["action"].get("units", units)
        for index, timestamp in enumerate(episode.relative_timestamps_ns):
            yield (
                self.profile.joint_topic,
                timestamp,
                {
                    "names": list(self.profile.state_names),
                    "positions": episode.joints[index],
                    **({"units": state_units} if state_units else {}),
                },
            )
            yield (
                self.profile.action_topic,
                timestamp,
                {
                    "names": list(self.profile.action_names),
                    "values": episode.target_joints[index],
                    **({"units": action_units} if action_units else {}),
                },
            )
            for camera in self.profile.cameras:
                original = self.original_videos[camera.topic]
                pts = round(original.start_seconds * 1e9) + timestamp
                yield (
                    camera.topic,
                    timestamp,
                    {
                        "source_frame_index": round(pts * episode.fps / 1e9),
                        "source_pts_ns": pts,
                    },
                )
            yield (
                source_reader.SOURCE_TOPIC,
                timestamp,
                {
                    "episode_index": self.source.episode_index,
                    "nominal_frequency_hz": episode.fps,
                    "raw_upload_id": self.source.raw_upload_id,
                    "raw_manifest_key": self.source.raw_manifest_key,
                    "source_format": self.source.source_format,
                    "task_index": episode.task_index,
                    "source_timestamp": episode.source_timestamps[index],
                    "capture_context_path": "capture-context.json" if episode.context else None,
                    "source_episode": episode.source_episode,
                    "source_mapping": episode.mappings[index] if episode.mappings else None,
                    "profile": episode.context.get("profile"),
                },
            )


class LeRobotAdapter:
    """Read native LeRobot Raw and expose an EpisodeStream without writing MCAP."""

    @contextmanager
    def open_episode(
        self,
        source_root: Path,
        source: LeRobotEpisodeSourceRefV1,
        *,
        raw_manifest_sha256: str,
        original_files: dict[str, tuple[str, int, str]],
    ) -> Iterator[EpisodeStream]:
        if len(raw_manifest_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in raw_manifest_sha256
        ):
            raise ValueError("Raw manifest SHA-256 must be lowercase hexadecimal")
        profile, layout, episode = read_episode(source_root, source.episode_index)
        originals: dict[str, OriginalVideoReferenceV1] = {}
        for camera in profile.cameras:
            video = layout.videos[camera.feature_key]
            probe = subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_entries",
                    "stream=width,height,codec_name,avg_frame_rate",
                    "-of",
                    "json",
                    str(video.file),
                ],
                capture_output=True,
                check=True,
                timeout=30,
            )
            info = json.loads(probe.stdout)["streams"][0]
            key, size, sha = original_files[video.source_relative_path]
            originals[camera.topic] = OriginalVideoReferenceV1(
                object_key=key,
                size_bytes=size,
                content_sha256=sha,
                start_seconds=video.from_timestamp,
                end_seconds=video.to_timestamp,
                width=info["width"],
                height=info["height"],
                codec=info["codec_name"],
                fps=episode.fps,
            )
        yield EpisodeStream(
            source=source,
            source_sha256=raw_manifest_sha256,
            rollout_id=f"lerobot-{source.raw_upload_id[:16]}-ep-{source.episode_index:06d}",
            episode=episode,
            layout=layout,
            original_videos=originals,
        )
