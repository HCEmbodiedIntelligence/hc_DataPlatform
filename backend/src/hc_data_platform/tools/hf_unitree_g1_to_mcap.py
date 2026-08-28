"""Convert pinned Unitree G1 LeRobot v3 episodes into HC upload packages.

The selected Hugging Face recording stores a floating-base pose followed by the
29 Unitree G1 joints, two raw Dex1 controller values, and four camera streams.
The companion robot-model folder is downloaded byte-for-byte from Unitree's
official ``unitree_ros`` repository.  Dex1 controller units are preserved but
are deliberately not converted to the URDF's prismatic metres because the
source does not publish that calibration.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, BinaryIO, cast
from xml.etree import ElementTree

from mcap.reader import make_reader
from mcap.writer import CompressionType, Writer
from PIL import Image

from hc_data_platform.ingest.manifest import parse_manifest_bytes
from hc_data_platform.ingest.ports import crc64_ecma
from hc_data_platform.verification.engine import McapVerifier
from hc_data_platform.verification.models import VerificationStatus
from hc_data_platform.verification.ports import RegisteredDecoderProbe

DEFAULT_REPOSITORY = (
    "unitreerobotics/G1_WBT_Dex1_Put_Clothes_into_Washing_Machine"
)
DEFAULT_REVISION = "6d698e2641cc4bb765cd738835fe3a4ecc0fe2c7"
DEFAULT_SOURCE_LICENSE = "Apache-2.0"
DEFAULT_CAPTURE_START = datetime(2026, 4, 16, tzinfo=timezone.utc)

ROBOT_REPOSITORY = "unitreerobotics/unitree_ros"
ROBOT_REVISION = "4ddbf6df0aa5bf8c8789d3edfa83e5e3ca45fe48"
ROBOT_SOURCE_ROOT = "robots/g1_description"
ROBOT_URDF_NAME = "g1_29dof_mode_15_with_dex1_1.urdf"
ROBOT_CONFIG_NAME = "robot.config.json"
ROBOT_OUTPUT_DIRECTORY = "unitree_g1_mode15_dex1"

RAW_FILE_NAME = "recording.mcap"
RECORDING_CONFIG_FILE_NAME = "recording-config.json"
MANIFEST_FILE_NAME = "rollout_manifest.json"
RECORDER_VERSION = "hf-unitree-g1-mcap/1.0.0"

STATE_TOPIC = "/humanoid/observation/state"
JOINT_TOPIC = "/robot/joint_states"
ACTION_TOPIC = "/humanoid/action"
BASE_POSE_TOPIC = "/robot/base_pose"
BASE_TARGET_TOPIC = "/robot/action/base_target"
GRIPPER_STATE_TOPIC = "/robot/dex1/raw_state"
GRIPPER_COMMAND_TOPIC = "/robot/dex1/raw_command"
END_EFFECTOR_STATE_TOPIC = "/robot/end_effector/state"
END_EFFECTOR_ACTION_TOPIC = "/robot/end_effector/action"
SOURCE_TOPIC = "/metadata/source"

G1_JOINT_NAMES = (
    "left_hip_pitch_joint",
    "left_hip_roll_joint",
    "left_hip_yaw_joint",
    "left_knee_joint",
    "left_ankle_pitch_joint",
    "left_ankle_roll_joint",
    "right_hip_pitch_joint",
    "right_hip_roll_joint",
    "right_hip_yaw_joint",
    "right_knee_joint",
    "right_ankle_pitch_joint",
    "right_ankle_roll_joint",
    "waist_yaw_joint",
    "waist_roll_joint",
    "waist_pitch_joint",
    "left_shoulder_pitch_joint",
    "left_shoulder_roll_joint",
    "left_shoulder_yaw_joint",
    "left_elbow_joint",
    "left_wrist_roll_joint",
    "left_wrist_pitch_joint",
    "left_wrist_yaw_joint",
    "right_shoulder_pitch_joint",
    "right_shoulder_roll_joint",
    "right_shoulder_yaw_joint",
    "right_elbow_joint",
    "right_wrist_roll_joint",
    "right_wrist_pitch_joint",
    "right_wrist_yaw_joint",
)
DEX1_URDF_JOINTS = (
    "left_dex1_finger_joint_1",
    "left_dex1_finger_joint_2",
    "right_dex1_finger_joint_1",
    "right_dex1_finger_joint_2",
)
DEX1_RAW_NAMES = ("left_dex1_open_close_raw", "right_dex1_open_close_raw")
END_EFFECTOR_NAMES = tuple(
    f"{side}_{component}"
    for side in ("left", "right")
    for component in ("x", "y", "z", "roll", "pitch", "yaw")
)


@dataclass(frozen=True)
class CameraSpec:
    camera_id: str
    feature_key: str
    topic: str
    frame_id: str


CAMERAS = (
    CameraSpec(
        "head_stereo_left",
        "observation.images.head_stereo_left",
        "/camera/head_stereo_left/image",
        "g1_head_stereo_left_optical",
    ),
    CameraSpec(
        "head_stereo_right",
        "observation.images.head_stereo_right",
        "/camera/head_stereo_right/image",
        "g1_head_stereo_right_optical",
    ),
    CameraSpec(
        "wrist_left",
        "observation.images.wrist_left",
        "/camera/wrist_left/image",
        "g1_wrist_left_optical",
    ),
    CameraSpec(
        "wrist_right",
        "observation.images.wrist_right",
        "/camera/wrist_right/image",
        "g1_wrist_right_optical",
    ),
)
REQUIRED_TOPICS = (
    *(camera.topic for camera in CAMERAS),
    JOINT_TOPIC,
    SOURCE_TOPIC,
    STATE_TOPIC,
)
ACTUAL_TOPICS = (
    *(camera.topic for camera in CAMERAS),
    ACTION_TOPIC,
    BASE_POSE_TOPIC,
    BASE_TARGET_TOPIC,
    END_EFFECTOR_ACTION_TOPIC,
    END_EFFECTOR_STATE_TOPIC,
    GRIPPER_COMMAND_TOPIC,
    GRIPPER_STATE_TOPIC,
    JOINT_TOPIC,
    SOURCE_TOPIC,
    STATE_TOPIC,
)


@dataclass(frozen=True)
class EpisodeData:
    fps: float
    relative_timestamps_ns: tuple[int, ...]
    frame_indexes: tuple[int, ...]
    task_index: int
    root_positions: tuple[tuple[float, ...], ...]
    root_orientations_wxyz: tuple[tuple[float, ...], ...]
    joints: tuple[tuple[float, ...], ...]
    hand_states_raw: tuple[tuple[float, ...], ...]
    end_effector_states: tuple[tuple[float, ...], ...]
    target_root_positions: tuple[tuple[float, ...], ...]
    target_root_orientations_wxyz: tuple[tuple[float, ...], ...]
    target_joints: tuple[tuple[float, ...], ...]
    hand_commands_raw: tuple[tuple[float, ...], ...]
    end_effector_actions: tuple[tuple[float, ...], ...]

    @property
    def frame_count(self) -> int:
        return len(self.relative_timestamps_ns)


@dataclass(frozen=True)
class VideoSlice:
    file: Path
    source_relative_path: str
    from_timestamp: float
    to_timestamp: float


@dataclass(frozen=True)
class SourceLayout:
    info: dict[str, Any]
    episode_metadata: dict[str, Any]
    data_file: Path
    data_relative_path: str
    videos: dict[str, VideoSlice]


@dataclass(frozen=True)
class ConversionRequest:
    project_id: str
    collection_task_id: str
    episode_index: int
    repository: str
    requested_revision: str
    resolved_revision: str
    robot_id: str
    output_root: Path
    capture_start: datetime


class _LocalFileStorage:
    def open_reader(self, object_key: str) -> BinaryIO:
        return Path(object_key).open("rb")


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _parse_datetime(value: str) -> datetime:
    normalized = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("capture start must be ISO-8601") from exc
    if parsed.tzinfo is None:
        raise argparse.ArgumentTypeError("capture start must include a timezone")
    return parsed.astimezone(timezone.utc)


def _request_json(url: str) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"User-Agent": "hc-data-platform/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            decoded = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"failed to read {url}") from exc
    if not isinstance(decoded, dict):
        raise RuntimeError(f"expected a JSON object from {url}")
    return cast(dict[str, Any], decoded)


def _resolved_revision(repository: str, revision: str) -> str:
    if len(revision) == 40 and all(char in "0123456789abcdefABCDEF" for char in revision):
        return revision.lower()
    encoded_repo = urllib.parse.quote(repository, safe="/")
    encoded_revision = urllib.parse.quote(revision, safe="")
    payload = _request_json(
        f"https://huggingface.co/api/datasets/{encoded_repo}/revision/{encoded_revision}"
    )
    value = payload.get("sha")
    if not isinstance(value, str) or len(value) != 40:
        raise RuntimeError("Hugging Face did not return an immutable revision")
    return value


def _hf_url(repository: str, revision: str, relative_path: str) -> str:
    path = urllib.parse.quote(relative_path, safe="/")
    rev = urllib.parse.quote(revision, safe="")
    return f"https://huggingface.co/datasets/{repository}/resolve/{rev}/{path}"


def _remote_size(url: str) -> int | None:
    request = urllib.request.Request(
        url,
        method="HEAD",
        headers={"User-Agent": "hc-data-platform/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            value = response.headers.get("Content-Length") or response.headers.get(
                "X-Linked-Size"
            )
    except (OSError, urllib.error.URLError) as exc:
        raise RuntimeError(f"failed to inspect {url}") from exc
    return int(value) if value else None


def _download_file(url: str, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    total_size: int | None = None
    try:
        total_size = _remote_size(url)
    except RuntimeError:
        if destination.is_file():
            return destination
    if destination.is_file() and (
        total_size is None or destination.stat().st_size == total_size
    ):
        return destination
    partial = destination.with_suffix(destination.suffix + ".part")
    if destination.exists():
        os.replace(destination, partial)
    for attempt in range(1, 9):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "hc-data-platform/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                append = offset > 0 and getattr(response, "status", None) == 206
                mode = "ab" if append else "wb"
                copied = offset if append else 0
                print(
                    f"download {destination.name} (attempt {attempt}/8)",
                    file=sys.stderr,
                    flush=True,
                )
                with partial.open(mode) as output:
                    while chunk := response.read(1024 * 1024):
                        output.write(chunk)
                        copied += len(chunk)
                        if total_size:
                            print(
                                f"\r  {copied / 1024**2:.1f} / "
                                f"{total_size / 1024**2:.1f} MiB "
                                f"({min(100, copied * 100 // total_size)}%)",
                                end="",
                                file=sys.stderr,
                                flush=True,
                            )
                if total_size:
                    print(file=sys.stderr)
        except (OSError, urllib.error.URLError) as exc:
            if attempt == 8:
                raise RuntimeError(f"failed to download {url}") from exc
            continue
        copied = partial.stat().st_size
        if total_size is None or copied == total_size:
            os.replace(partial, destination)
            return destination
    raise RuntimeError(f"download remained incomplete: {destination}")


def _format_path(template: str, *, chunk_index: int, file_index: int, video_key: str = "") -> str:
    try:
        return template.format(
            chunk_index=chunk_index,
            file_index=file_index,
            video_key=video_key,
            episode_chunk=chunk_index,
            episode_index=file_index,
        )
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"unsupported LeRobot v3 path template: {template}") from exc


def _parquet_rows(path: Path, columns: list[str] | None = None) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra for Parquet support") from exc
    return cast(list[dict[str, Any]], pq.read_table(path, columns=columns).to_pylist())


def _essential_episode_metadata(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in row.items()
        if not key.startswith("stats/") and not key.startswith("meta/")
    }


def acquire_source(
    *, repository: str, revision: str, episode_index: int, cache_root: Path
) -> SourceLayout:
    root = cache_root / repository.replace("/", "--") / revision
    info_path = _download_file(
        _hf_url(repository, revision, "meta/info.json"), root / "meta/info.json"
    )
    episode_meta_path = _download_file(
        _hf_url(repository, revision, "meta/episodes/chunk-000/file-000.parquet"),
        root / "meta/episodes/chunk-000/file-000.parquet",
    )
    info = cast(dict[str, Any], json.loads(info_path.read_text(encoding="utf-8")))
    episode_rows = _parquet_rows(episode_meta_path)
    matches = [row for row in episode_rows if int(row["episode_index"]) == episode_index]
    if len(matches) != 1:
        raise RuntimeError(f"episode {episode_index} is not uniquely declared in metadata")
    episode_metadata = _essential_episode_metadata(matches[0])
    data_template = info.get("data_path")
    video_template = info.get("video_path")
    if not isinstance(data_template, str) or not isinstance(video_template, str):
        raise RuntimeError("LeRobot v3 metadata is missing path templates")
    data_relative = _format_path(
        data_template,
        chunk_index=int(episode_metadata["data/chunk_index"]),
        file_index=int(episode_metadata["data/file_index"]),
    )
    data_file = _download_file(
        _hf_url(repository, revision, data_relative), root / data_relative
    )
    videos: dict[str, VideoSlice] = {}
    for camera in CAMERAS:
        prefix = f"videos/{camera.feature_key}"
        chunk = int(episode_metadata[f"{prefix}/chunk_index"])
        file_index = int(episode_metadata[f"{prefix}/file_index"])
        relative = _format_path(
            video_template,
            chunk_index=chunk,
            file_index=file_index,
            video_key=camera.feature_key,
        )
        videos[camera.feature_key] = VideoSlice(
            file=_download_file(
                _hf_url(repository, revision, relative), root / relative
            ),
            source_relative_path=relative,
            from_timestamp=float(episode_metadata[f"{prefix}/from_timestamp"]),
            to_timestamp=float(episode_metadata[f"{prefix}/to_timestamp"]),
        )
    return SourceLayout(
        info=info,
        episode_metadata=episode_metadata,
        data_file=data_file,
        data_relative_path=data_relative,
        videos=videos,
    )


def prune_source_cache(layout: SourceLayout, cache_root: Path) -> None:
    """Remove only source files used by one successfully converted episode.

    LeRobot video files are substantially larger than the resulting MCAP package.  This
    opt-in cleanup keeps bulk conversion bounded on a workstation while retaining shared
    metadata and the final validated upload package.  Every deletion target is resolved
    and required to remain below the explicitly selected cache root.
    """

    resolved_root = cache_root.resolve()
    files = {layout.data_file, *(video.file for video in layout.videos.values())}
    parents: set[Path] = set()
    for source in files:
        resolved = source.resolve()
        try:
            resolved.relative_to(resolved_root)
        except ValueError as exc:
            raise RuntimeError(f"refusing to prune outside cache root: {resolved}") from exc
        if resolved.is_file():
            resolved.unlink()
        parents.add(resolved.parent)

    for parent in sorted(parents, key=lambda value: len(value.parts), reverse=True):
        current = parent
        while current != resolved_root:
            try:
                current.relative_to(resolved_root)
            except ValueError as exc:
                raise RuntimeError(f"refusing to prune outside cache root: {current}") from exc
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent


def _finite_vector(value: object, *, field: str, width: int) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)):
        to_list = getattr(value, "tolist", None)
        if not callable(to_list):
            raise RuntimeError(f"{field} is not a numeric vector")
        value = to_list()
    if not isinstance(value, (list, tuple)) or len(value) != width:
        raise RuntimeError(f"{field} must have exactly {width} values")
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        raise RuntimeError(f"{field} contains non-finite values")
    return result


def load_episode_data(data_file: Path, episode_index: int, fps: float) -> EpisodeData:
    try:
        import pyarrow.compute as pc
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra for Parquet support") from exc
    columns = [
        "episode_index",
        "frame_index",
        "timestamp",
        "task_index",
        "observation.state.ee_state",
        "observation.state.hand_state",
        "observation.state.robot_q_current",
        "action.ee_action",
        "action.hand_cmd",
        "action.robot_q_desired",
    ]
    table = pq.read_table(data_file, columns=columns)
    table = table.filter(pc.equal(table["episode_index"], episode_index))
    rows = cast(list[dict[str, Any]], table.to_pylist())
    rows.sort(key=lambda row: int(row["frame_index"]))
    if not rows:
        raise RuntimeError(f"episode {episode_index} has no data rows")
    frame_indexes = tuple(int(row["frame_index"]) for row in rows)
    if frame_indexes != tuple(range(len(rows))):
        raise RuntimeError("episode frame indexes are not contiguous from zero")
    source_timestamps = tuple(float(row["timestamp"]) for row in rows)
    if any(not math.isfinite(item) for item in source_timestamps):
        raise RuntimeError("episode timestamps contain non-finite values")
    origin = source_timestamps[0]
    relative = tuple(round((item - origin) * 1_000_000_000) for item in source_timestamps)
    if relative[0] != 0 or any(
        left >= right for left, right in zip(relative, relative[1:], strict=False)
    ):
        raise RuntimeError("episode source timestamps are not strictly increasing")
    task_indexes = {int(row["task_index"]) for row in rows}
    if len(task_indexes) != 1:
        raise RuntimeError("episode contains multiple task indexes")
    current = tuple(
        _finite_vector(
            row["observation.state.robot_q_current"],
            field="observation.state.robot_q_current",
            width=36,
        )
        for row in rows
    )
    desired = tuple(
        _finite_vector(
            row["action.robot_q_desired"],
            field="action.robot_q_desired",
            width=36,
        )
        for row in rows
    )
    return EpisodeData(
        fps=fps,
        relative_timestamps_ns=relative,
        frame_indexes=frame_indexes,
        task_index=task_indexes.pop(),
        root_positions=tuple(item[:3] for item in current),
        root_orientations_wxyz=tuple(item[3:7] for item in current),
        joints=tuple(item[7:] for item in current),
        hand_states_raw=tuple(
            _finite_vector(
                row["observation.state.hand_state"],
                field="observation.state.hand_state",
                width=2,
            )
            for row in rows
        ),
        end_effector_states=tuple(
            _finite_vector(
                row["observation.state.ee_state"],
                field="observation.state.ee_state",
                width=12,
            )
            for row in rows
        ),
        target_root_positions=tuple(item[:3] for item in desired),
        target_root_orientations_wxyz=tuple(item[3:7] for item in desired),
        target_joints=tuple(item[7:] for item in desired),
        hand_commands_raw=tuple(
            _finite_vector(
                row["action.hand_cmd"], field="action.hand_cmd", width=2
            )
            for row in rows
        ),
        end_effector_actions=tuple(
            _finite_vector(
                row["action.ee_action"], field="action.ee_action", width=12
            )
            for row in rows
        ),
    )


def _extract_frames(
    video: VideoSlice, *, frame_count: int, destination: Path
) -> tuple[Path, ...]:
    destination.mkdir(parents=True, exist_ok=True)
    command = (
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-ss",
        f"{video.from_timestamp:.9f}",
        "-i",
        str(video.file),
        "-an",
        "-frames:v",
        str(frame_count),
        "-fps_mode",
        "passthrough",
        "-q:v",
        "3",
        str(destination / "frame_%06d.jpg"),
    )
    try:
        subprocess.run(command, check=True, timeout=900)
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to decode LeRobot video") from exc
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"ffmpeg could not decode {video.file}") from exc
    frames = tuple(sorted(destination.glob("frame_*.jpg")))
    if len(frames) != frame_count:
        raise RuntimeError(
            f"decoded {len(frames)} frames from {video.file}; expected {frame_count}"
        )
    return frames


def extract_camera_frames(
    layout: SourceLayout, episode: EpisodeData, temporary: Path
) -> dict[str, tuple[Path, ...]]:
    frames: dict[str, tuple[Path, ...]] = {}
    for camera in CAMERAS:
        frames[camera.feature_key] = _extract_frames(
            layout.videos[camera.feature_key],
            frame_count=episode.frame_count,
            destination=temporary / camera.camera_id,
        )
    return frames


CAMERA_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["data_base64", "encoding", "height", "jpeg_sha256", "width"],
    "properties": {
        "data_base64": {"type": "string", "contentEncoding": "base64"},
        "encoding": {"const": "jpeg"},
        "height": {"type": "integer", "minimum": 1},
        "jpeg_sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
        "width": {"type": "integer", "minimum": 1},
    },
}
JOINT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["names", "positions"],
    "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "positions": {"type": "array", "items": {"type": "number"}},
    },
}
ACTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["names", "values"],
    "properties": {
        "names": {"type": "array", "items": {"type": "string"}},
        "values": {"type": "array", "items": {"type": "number"}},
    },
}
POSE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["orientation_wxyz", "position_xyz"],
    "properties": {
        "position_xyz": {
            "type": "array",
            "minItems": 3,
            "maxItems": 3,
            "items": {"type": "number"},
        },
        "orientation_wxyz": {
            "type": "array",
            "minItems": 4,
            "maxItems": 4,
            "items": {"type": "number"},
        },
    },
}
SOURCE_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "episode_index",
        "license",
        "nominal_frequency_hz",
        "repository",
        "resolved_revision",
        "source_format",
        "task_index",
    ],
    "properties": {
        "episode_index": {"type": "integer", "minimum": 0},
        "license": {"type": "string"},
        "nominal_frequency_hz": {"type": "number", "exclusiveMinimum": 0},
        "repository": {"type": "string"},
        "resolved_revision": {"type": "string"},
        "source_format": {"type": "string"},
        "task_index": {"type": "integer", "minimum": 0},
    },
}


def _camera_message(frame: Path) -> bytes:
    encoded = frame.read_bytes()
    with Image.open(frame) as image:
        image.load()
        if image.format != "JPEG":
            raise RuntimeError(f"camera frame is not JPEG: {frame}")
        width, height = image.size
    return _json_bytes(
        {
            "data_base64": base64.b64encode(encoded).decode("ascii"),
            "encoding": "jpeg",
            "height": height,
            "jpeg_sha256": hashlib.sha256(encoded).hexdigest(),
            "width": width,
        }
    )


def _register_channels(writer: Writer) -> dict[str, int]:
    camera_schema = writer.register_schema(
        "hc.camera.JpegEnvelope", "jsonschema", _json_bytes(CAMERA_SCHEMA)
    )
    joint_schema = writer.register_schema(
        "hc.robot.JointState", "jsonschema", _json_bytes(JOINT_SCHEMA)
    )
    action_schema = writer.register_schema(
        "hc.robot.Action", "jsonschema", _json_bytes(ACTION_SCHEMA)
    )
    pose_schema = writer.register_schema(
        "hc.robot.Pose", "jsonschema", _json_bytes(POSE_SCHEMA)
    )
    source_schema = writer.register_schema(
        "hc.source.HuggingFace", "jsonschema", _json_bytes(SOURCE_SCHEMA)
    )
    channels = {
        camera.topic: writer.register_channel(camera.topic, "json", camera_schema)
        for camera in CAMERAS
    }
    for topic in (STATE_TOPIC, JOINT_TOPIC):
        channels[topic] = writer.register_channel(topic, "json", joint_schema)
    for topic in (
        ACTION_TOPIC,
        END_EFFECTOR_ACTION_TOPIC,
        END_EFFECTOR_STATE_TOPIC,
        GRIPPER_COMMAND_TOPIC,
        GRIPPER_STATE_TOPIC,
    ):
        channels[topic] = writer.register_channel(topic, "json", action_schema)
    for topic in (BASE_POSE_TOPIC, BASE_TARGET_TOPIC):
        channels[topic] = writer.register_channel(topic, "json", pose_schema)
    channels[SOURCE_TOPIC] = writer.register_channel(
        SOURCE_TOPIC, "json", source_schema
    )
    return channels


def write_mcap(
    destination: Path,
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    camera_frames: dict[str, tuple[Path, ...]],
) -> None:
    for camera in CAMERAS:
        if len(camera_frames.get(camera.feature_key, ())) != episode.frame_count:
            raise RuntimeError(f"camera {camera.feature_key} has the wrong frame count")
    start_ns = round(request.capture_start.timestamp() * 1_000_000_000)
    source = {
        "episode_index": request.episode_index,
        "license": DEFAULT_SOURCE_LICENSE,
        "nominal_frequency_hz": episode.fps,
        "repository": request.repository,
        "resolved_revision": request.resolved_revision,
        "source_format": "LeRobotDataset v3.0",
        "task_index": episode.task_index,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as output:
        writer = Writer(
            output,
            compression=CompressionType.ZSTD,
            chunk_size=8 * 1024 * 1024,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        writer.start(profile="hc-robotics-json/v1", library=RECORDER_VERSION)
        channels = _register_channels(writer)
        for index, relative_ns in enumerate(episode.relative_timestamps_ns):
            timestamp_ns = start_ns + relative_ns
            sequence = index + 1
            for camera in CAMERAS:
                writer.add_message(
                    channels[camera.topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=_camera_message(camera_frames[camera.feature_key][index]),
                )
            joint_message = _json_bytes(
                {"names": G1_JOINT_NAMES, "positions": episode.joints[index]}
            )
            for topic in (STATE_TOPIC, JOINT_TOPIC):
                writer.add_message(
                    channels[topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=joint_message,
                )
            values_by_topic = {
                ACTION_TOPIC: (G1_JOINT_NAMES, episode.target_joints[index]),
                END_EFFECTOR_STATE_TOPIC: (
                    END_EFFECTOR_NAMES,
                    episode.end_effector_states[index],
                ),
                END_EFFECTOR_ACTION_TOPIC: (
                    END_EFFECTOR_NAMES,
                    episode.end_effector_actions[index],
                ),
                GRIPPER_STATE_TOPIC: (DEX1_RAW_NAMES, episode.hand_states_raw[index]),
                GRIPPER_COMMAND_TOPIC: (
                    DEX1_RAW_NAMES,
                    episode.hand_commands_raw[index],
                ),
            }
            for topic, (names, values) in values_by_topic.items():
                writer.add_message(
                    channels[topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=_json_bytes({"names": names, "values": values}),
                )
            for topic, positions, orientations in (
                (
                    BASE_POSE_TOPIC,
                    episode.root_positions[index],
                    episode.root_orientations_wxyz[index],
                ),
                (
                    BASE_TARGET_TOPIC,
                    episode.target_root_positions[index],
                    episode.target_root_orientations_wxyz[index],
                ),
            ):
                writer.add_message(
                    channels[topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=_json_bytes(
                        {
                            "orientation_wxyz": orientations,
                            "position_xyz": positions,
                        }
                    ),
                )
            writer.add_message(
                channels[SOURCE_TOPIC],
                log_time=timestamp_ns,
                publish_time=timestamp_ns,
                sequence=sequence,
                data=_json_bytes(source),
            )
        writer.finish()


def _file_checksums(path: Path) -> tuple[int, str, int]:
    digest = hashlib.sha256()
    crc64 = 0
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    return size, digest.hexdigest(), crc64


def _identity(request: ConversionRequest) -> dict[str, str]:
    seed = (
        f"{request.project_id}|{request.collection_task_id}|{request.repository}|"
        f"{request.resolved_revision}|{request.episode_index}|{request.robot_id}|"
        f"{RECORDER_VERSION}"
    )
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return {
        "collection_job_id": f"hf-g1-job-{digest}",
        "rollout_id": f"hf-g1-ep-{request.episode_index:06d}-{digest}",
        "collection_session_id": f"hf-g1-session-{digest}",
        "recording_request_id": f"hf-g1-request-{digest}",
        "data_package_id": f"hf-g1-package-{request.episode_index:06d}-{digest}",
    }


def build_recording_config(
    *, request: ConversionRequest, episode: EpisodeData, layout: SourceLayout
) -> dict[str, object]:
    intervals = [
        right - left
        for left, right in zip(
            episode.relative_timestamps_ns,
            episode.relative_timestamps_ns[1:],
            strict=False,
        )
    ]
    median = sorted(intervals)[len(intervals) // 2] if intervals else round(
        1_000_000_000 / episode.fps
    )
    features = layout.info.get("features", {})
    cameras: list[dict[str, object]] = []
    for camera in CAMERAS:
        feature = features.get(camera.feature_key, {}) if isinstance(features, dict) else {}
        source_video = feature.get("info", {}) if isinstance(feature, dict) else {}
        video = layout.videos[camera.feature_key]
        cameras.append(
            {
                "camera_id": camera.camera_id,
                "source_feature": camera.feature_key,
                "source_path": video.source_relative_path,
                "source_time_window_seconds": {
                    "from": video.from_timestamp,
                    "to": video.to_timestamp,
                },
                "source_video": source_video,
                "output_topic": camera.topic,
            }
        )
    return {
        "schema_version": "hc-recording-config/v1",
        "source": {
            "repository": request.repository,
            "requested_revision": request.requested_revision,
            "resolved_revision": request.resolved_revision,
            "episode_index": request.episode_index,
            "data_path": layout.data_relative_path,
            "episode_metadata": layout.episode_metadata,
        },
        "clock": {
            "basis": "source_episode_timestamps",
            "nominal_frequency_hz": episode.fps,
            "frame_count": episode.frame_count,
            "start_offset_ns": episode.relative_timestamps_ns[0],
            "last_sample_offset_ns": episode.relative_timestamps_ns[-1],
            "observed_interval_ns": {
                "minimum": min(intervals) if intervals else median,
                "median": median,
                "maximum": max(intervals) if intervals else median,
            },
            "absolute_time": {
                "source_status": "NOT_PRESENT_IN_DATASET",
                "platform_anchor_utc": request.capture_start.isoformat(),
                "platform_anchor_purpose": "deterministic MCAP time window only",
            },
        },
        "streams": {
            "cameras": cameras,
            "root_pose": {
                "source_feature": "observation.state.robot_q_current[0:7]",
                "output_topic": BASE_POSE_TOPIC,
                "layout": ["x", "y", "z", "qw", "qx", "qy", "qz"],
            },
            "joint_state": {
                "source_feature": "observation.state.robot_q_current[7:36]",
                "output_topic": JOINT_TOPIC,
                "joint_names": list(G1_JOINT_NAMES),
                "order_source": "unitreerobotics/unitree_sdk2 JointIndex",
                "observed_range_radians": {
                    name: {
                        "minimum": min(row[index] for row in episode.joints),
                        "maximum": max(row[index] for row in episode.joints),
                    }
                    for index, name in enumerate(G1_JOINT_NAMES)
                },
                "value_policy": "SOURCE_TELEMETRY_UNCLAMPED",
            },
            "dex1_raw": {
                "source_feature": "observation.state.hand_state[0:2]",
                "output_topic": GRIPPER_STATE_TOPIC,
                "source_units": "controller units; 5.5 open to 0.0 closed",
                "observed_range": {
                    name: {
                        "minimum": min(row[index] for row in episode.hand_states_raw),
                        "maximum": max(row[index] for row in episode.hand_states_raw),
                    }
                    for index, name in enumerate(DEX1_RAW_NAMES)
                },
                "urdf_drive_status": "PRESERVED_UNSCALED_NOT_DRIVING_URDF",
            },
            "joint_target": {
                "source_feature": "action.robot_q_desired[7:36]",
                "output_topic": ACTION_TOPIC,
            },
        },
        "robot_model": {
            "manufacturer": "Unitree Robotics",
            "source_repository": ROBOT_REPOSITORY,
            "source_revision": ROBOT_REVISION,
            "entry_urdf": ROBOT_URDF_NAME,
            "dataset_robot_type": layout.info.get("robot_type"),
            "status": "OFFICIAL_MATCHING_G1_MODE15_DEX1_DESCRIPTION",
        },
        "source_metadata": layout.info,
    }


def build_manifest(
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    raw_file: Path,
    config_file: Path,
) -> dict[str, object]:
    size, sha256, crc64 = _file_checksums(raw_file)
    config_size, config_sha256, config_crc64 = _file_checksums(config_file)
    period_ns = round(1_000_000_000 / episode.fps)
    end = request.capture_start + timedelta(
        microseconds=(episode.relative_timestamps_ns[-1] + period_ns) / 1000
    )
    schemas = {
        **{camera.topic: "hc.camera.JpegEnvelope" for camera in CAMERAS},
        ACTION_TOPIC: "hc.robot.Action",
        BASE_POSE_TOPIC: "hc.robot.Pose",
        BASE_TARGET_TOPIC: "hc.robot.Pose",
        END_EFFECTOR_ACTION_TOPIC: "hc.robot.Action",
        END_EFFECTOR_STATE_TOPIC: "hc.robot.Action",
        GRIPPER_COMMAND_TOPIC: "hc.robot.Action",
        GRIPPER_STATE_TOPIC: "hc.robot.Action",
        JOINT_TOPIC: "hc.robot.JointState",
        SOURCE_TOPIC: "hc.source.HuggingFace",
        STATE_TOPIC: "hc.robot.JointState",
    }
    return {
        "schema_version": 1,
        "project_id": request.project_id,
        "task_id": request.collection_task_id,
        **_identity(request),
        "pico_instance_id": "hf-lerobot-importer",
        "sequence_no": request.episode_index + 1,
        "robot_id": request.robot_id,
        "start_time": request.capture_start.isoformat().replace("+00:00", "Z"),
        "end_time": end.isoformat().replace("+00:00", "Z"),
        "cameras": [
            {
                "camera_id": camera.camera_id,
                "topic": camera.topic,
                "frame_id": camera.frame_id,
                "encoding": "jpeg",
            }
            for camera in CAMERAS
        ],
        "topics": [
            {
                "name": topic,
                "required": topic in REQUIRED_TOPICS,
                "message_encoding": "json",
                "schema_name": schemas[topic],
            }
            for topic in ACTUAL_TOPICS
        ],
        "expected_topics": list(REQUIRED_TOPICS),
        "actual_topics": list(ACTUAL_TOPICS),
        "files": [
            {
                "path": RAW_FILE_NAME,
                "size": size,
                "sha256": sha256,
                "crc64": str(crc64),
                "media_type": "application/x-mcap",
                "role": "RAW_MCAP",
            },
            {
                "path": RECORDING_CONFIG_FILE_NAME,
                "size": config_size,
                "sha256": config_sha256,
                "crc64": str(config_crc64),
                "media_type": "application/json",
                "role": "AUXILIARY",
            },
        ],
        "file_size": size,
        "sha256": sha256,
        "crc64": str(crc64),
        "compression": "zstd",
        "recorder_version": RECORDER_VERSION,
        "source_recording": {
            "kind": "HUGGING_FACE_EPISODE",
            "repository": request.repository,
            "resolved_revision": request.resolved_revision,
            "episode_index": request.episode_index,
        },
    }


def validate_package(package: Path) -> None:
    manifest_path = package / MANIFEST_FILE_NAME
    raw_file = package / RAW_FILE_NAME
    config_file = package / RECORDING_CONFIG_FILE_NAME
    preflight = parse_manifest_bytes(manifest_path.read_bytes())
    manifest = preflight.manifest
    if _file_checksums(raw_file) != (
        manifest.file_size,
        manifest.sha256,
        manifest.crc64,
    ):
        raise RuntimeError("generated MCAP does not match Manifest checksums")
    auxiliary = next(
        (item for item in manifest.files if item.path == RECORDING_CONFIG_FILE_NAME),
        None,
    )
    if auxiliary is None or _file_checksums(config_file) != (
        auxiliary.size,
        auxiliary.sha256,
        auxiliary.crc64,
    ):
        raise RuntimeError("recording configuration does not match Manifest")

    def decode_json(_schema: bytes, message: bytes) -> object:
        return json.loads(message)

    report = McapVerifier(
        _LocalFileStorage(),
        decoder=RegisteredDecoderProbe({("json", "jsonschema"): decode_json}),
    ).verify(
        rollout_id=manifest.rollout_id,
        object_key=str(raw_file),
        source_sha256=manifest.sha256,
        required_topics=set(manifest.expected_topics),
        known_optional_topics=set(manifest.actual_topics) - set(manifest.expected_topics),
    )
    if report.status is not VerificationStatus.VERIFIED:
        codes = ", ".join(item.code.value for item in report.findings)
        raise RuntimeError(f"generated MCAP failed platform verification: {codes}")
    with raw_file.open("rb") as stream:
        observed = {
            channel.topic
            for _schema, channel, _message in make_reader(stream).iter_messages()
        }
    if observed != set(manifest.actual_topics):
        raise RuntimeError("generated MCAP topic inventory differs from Manifest")


def write_package(
    *,
    request: ConversionRequest,
    episode: EpisodeData,
    layout: SourceLayout,
    camera_frames: dict[str, tuple[Path, ...]],
) -> Path:
    package = request.output_root / _identity(request)["data_package_id"]
    if package.exists():
        validate_package(package)
        return package
    request.output_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{package.name}.", dir=request.output_root))
    try:
        raw_file = staging / RAW_FILE_NAME
        write_mcap(
            raw_file,
            request=request,
            episode=episode,
            camera_frames=camera_frames,
        )
        config_file = staging / RECORDING_CONFIG_FILE_NAME
        config_file.write_text(
            json.dumps(
                build_recording_config(request=request, episode=episode, layout=layout),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (staging / MANIFEST_FILE_NAME).write_text(
            json.dumps(
                build_manifest(
                    request=request,
                    episode=episode,
                    raw_file=raw_file,
                    config_file=config_file,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        validate_package(staging)
        os.replace(staging, package)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return package


def _raw_github_url(relative_path: str) -> str:
    encoded = "/".join(urllib.parse.quote(part, safe="") for part in relative_path.split("/"))
    return f"https://raw.githubusercontent.com/{ROBOT_REPOSITORY}/{ROBOT_REVISION}/{encoded}"


def _robot_configuration(actuated_joints: tuple[str, ...]) -> dict[str, object]:
    body = [
        {
            "source_joint_name": name,
            "target_joint_name": name,
            "direction": "SAME",
        }
        for name in G1_JOINT_NAMES
    ]
    unmapped = [
        {
            "source_joint_name": f"unmapped_no_calibration/{name}",
            "target_joint_name": name,
            "direction": "SAME",
        }
        for name in DEX1_URDF_JOINTS
    ]
    expected = set(G1_JOINT_NAMES) | set(DEX1_URDF_JOINTS)
    if set(actuated_joints) != expected:
        raise RuntimeError("official Unitree URDF actuated joints changed at the pinned revision")
    return {
        "format": "hc-robot-description/v1",
        "robot": {
            "id": "unitree-g1-mode15-dex1",
            "display_name": "Unitree G1 Mode 15 + Dex1-1",
            "serial_no": "HF-UNITREE-G1-WBT-OPEN-DATA",
        },
        "urdf": {"entry": ROBOT_URDF_NAME, "name": "g1_29dof_mode_15"},
        "joint_mapping": [*body, *unmapped],
        "mapping_provenance": {
            "body_joint_order": {
                "source": "unitreerobotics/unitree_sdk2 JointIndex",
                "dataset_slice": "observation.state.robot_q_current[7:36]",
                "status": "EXACT_29_JOINT_MAPPING",
            },
            "dex1": {
                "dataset_slice": "observation.state.hand_state[0:2]",
                "source_range": "5.5 open to 0.0 closed in controller units",
                "urdf_joints": list(DEX1_URDF_JOINTS),
                "status": "UNMAPPED_NO_PUBLISHED_CONTROLLER_TO_METRE_CALIBRATION",
                "note": (
                    "The four placeholder source names are intentionally absent from MCAP; "
                    "the official Dex1 geometry remains at its URDF rest position."
                ),
            },
        },
        "source": {
            "dataset_repository": DEFAULT_REPOSITORY,
            "dataset_revision": DEFAULT_REVISION,
            "robot_repository": ROBOT_REPOSITORY,
            "robot_revision": ROBOT_REVISION,
        },
    }


def acquire_robot_model_assets(*, cache_root: Path, output_root: Path) -> Path:
    destination = output_root / "_robot_model" / ROBOT_OUTPUT_DIRECTORY
    config_path = destination / ROBOT_CONFIG_NAME
    urdf_path = destination / ROBOT_URDF_NAME
    if destination.exists():
        if not config_path.is_file() or not urdf_path.is_file():
            raise RuntimeError(f"incomplete robot model directory: {destination}")
        return destination
    cache = cache_root / "robot-model" / ROBOT_REVISION / ROBOT_OUTPUT_DIRECTORY
    cached_urdf = _download_file(
        _raw_github_url(f"{ROBOT_SOURCE_ROOT}/{ROBOT_URDF_NAME}"),
        cache / ROBOT_URDF_NAME,
    )
    root = ElementTree.parse(cached_urdf).getroot()
    if root.tag != "robot":
        raise RuntimeError("official Unitree model is not a URDF robot document")
    mesh_references = sorted(
        {
            mesh.attrib["filename"]
            for mesh in root.findall(".//mesh")
            if "filename" in mesh.attrib
        }
    )
    for reference in mesh_references:
        relative = Path(reference)
        if relative.is_absolute() or ".." in relative.parts:
            raise RuntimeError(f"unsafe official URDF mesh reference: {reference}")
        _download_file(
            _raw_github_url(f"{ROBOT_SOURCE_ROOT}/{reference}"), cache / relative
        )
    license_file = _download_file(
        _raw_github_url("LICENSE"), cache / "UNITREE_LICENSE.txt"
    )
    actuated = tuple(
        joint.attrib["name"]
        for joint in root.findall("joint")
        if joint.attrib.get("type", "fixed").lower() != "fixed"
    )
    staging_parent = output_root / "_robot_model"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".unitree-g1-", dir=staging_parent))
    try:
        shutil.copy2(cached_urdf, staging / ROBOT_URDF_NAME)
        for reference in mesh_references:
            source = cache / reference
            target = staging / reference
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        shutil.copy2(license_file, staging / license_file.name)
        config_path_in_staging = staging / ROBOT_CONFIG_NAME
        config_path_in_staging.write_text(
            json.dumps(_robot_configuration(actuated), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        (staging / "MODEL_PROVENANCE.md").write_text(
            "# Unitree G1 model provenance\n\n"
            f"- Official repository: `{ROBOT_REPOSITORY}`\n"
            f"- Pinned revision: `{ROBOT_REVISION}`\n"
            f"- Entry URDF: `{ROBOT_SOURCE_ROOT}/{ROBOT_URDF_NAME}`\n"
            "- URDF and mesh bytes are unmodified.\n"
            "- The HC JSON file only records import identity and joint mappings.\n"
            "- Dex1 raw controller values are not converted to prismatic metres.\n",
            encoding="utf-8",
        )
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return destination


def _convert_episode(
    args: argparse.Namespace,
    *,
    repository: str,
    requested_revision: str,
    resolved_revision: str,
    episode_index: int,
    cache_root: Path,
    output_root: Path,
) -> Path:
    request = ConversionRequest(
        project_id=cast(str, args.project_id),
        collection_task_id=cast(str, args.collection_task_id),
        episode_index=episode_index,
        repository=repository,
        requested_revision=requested_revision,
        resolved_revision=resolved_revision,
        robot_id=cast(str, args.robot_id),
        output_root=output_root,
        capture_start=cast(datetime, args.capture_start) + timedelta(days=episode_index),
    )
    existing = output_root / _identity(request)["data_package_id"]
    if existing.exists():
        validate_package(existing)
        return existing
    layout = acquire_source(
        repository=repository,
        revision=resolved_revision,
        episode_index=episode_index,
        cache_root=cache_root,
    )
    fps_value = layout.info.get("fps")
    if not isinstance(fps_value, (int, float)) or not 0 < float(fps_value) <= 240:
        raise RuntimeError("LeRobot metadata has an invalid fps")
    episode = load_episode_data(layout.data_file, episode_index, float(fps_value))
    declared_length = layout.episode_metadata.get("length")
    if declared_length != episode.frame_count:
        raise RuntimeError(
            f"episode metadata length {declared_length} != data rows {episode.frame_count}"
        )
    with tempfile.TemporaryDirectory(prefix="hc-hf-unitree-g1-frames-") as temporary:
        frames = extract_camera_frames(layout, episode, Path(temporary))
        package = write_package(
            request=request,
            episode=episode,
            layout=layout,
            camera_frames=frames,
        )
    if cast(bool, args.prune_source_cache):
        prune_source_cache(layout, cache_root)
    return package


def convert(args: argparse.Namespace) -> tuple[Path, ...]:
    repository = cast(str, args.repository)
    requested_revision = cast(str, args.revision)
    resolved_revision = _resolved_revision(repository, requested_revision)
    cache_root = cast(Path, args.cache_dir).expanduser().resolve()
    output_root = cast(Path, args.output_dir).expanduser().resolve()
    model = acquire_robot_model_assets(cache_root=cache_root, output_root=output_root)
    print(f"official robot model {model}", file=sys.stderr)
    indexes = (
        range(cast(int, args.episode_count))
        if cast(bool, args.all_episodes)
        else (cast(int, args.episode),)
    )
    return tuple(
        _convert_episode(
            args,
            repository=repository,
            requested_revision=requested_revision,
            resolved_revision=resolved_revision,
            episode_index=index,
            cache_root=cache_root,
            output_root=output_root,
        )
        for index in indexes
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--episode", type=int, default=0)
    selection.add_argument("--all-episodes", action="store_true")
    parser.add_argument("--episode-count", type=int, default=100)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--robot-id", default="unitree-g1-mode15-dex1")
    parser.add_argument("--capture-start", type=_parse_datetime, default=DEFAULT_CAPTURE_START)
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path(".cache/huggingface/hc-unitree-g1-import"),
    )
    parser.add_argument(
        "--prune-source-cache",
        action="store_true",
        help=(
            "after each package validates, remove only that episode's downloaded "
            "Parquet/video source files while retaining shared metadata"
        ),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("artifacts/hf-unitree-g1-mcap")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.episode < 0:
        parser.error("--episode must be non-negative")
    if not 1 <= args.episode_count <= 100:
        parser.error("--episode-count must be between 1 and 100")
    try:
        packages = convert(args)
    except (RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    for package in packages:
        print(package)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
