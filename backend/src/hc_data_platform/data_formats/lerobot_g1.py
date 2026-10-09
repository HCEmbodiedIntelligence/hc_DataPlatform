"""Pinned G1 LeRobot layout and numeric decoding shared by CLI and ingest.

The caller supplies already authorized local files. No network, upload, cache
policy, command-line arguments or runtime assembly belongs to this reader.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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


def read_source(
    episode_index: int,
    resolve_file: Callable[[str], Path],
    *,
    metadata_files: Sequence[Path] | None = None,
) -> SourceLayout:
    info_path = resolve_file("meta/info.json")
    episode_meta_path = resolve_file("meta/episodes/chunk-000/file-000.parquet")
    info = cast(dict[str, Any], json.loads(info_path.read_text(encoding="utf-8")))
    episode_rows = (
        [row for path in metadata_files for row in _parquet_rows(path)]
        if metadata_files is not None
        else _parquet_rows(episode_meta_path)
    )
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
    data_file = resolve_file(data_relative)
    videos: dict[str, VideoSlice] = {}
    for camera in CAMERAS:
        prefix = f"videos/{camera.feature_key}"
        chunk = int(episode_metadata[f"{prefix}/chunk_index"])
        file_index = int(episode_metadata[f"{prefix}/file_index"])
        relative = _format_path(
            video_template, chunk_index=chunk, file_index=file_index, video_key=camera.feature_key
        )
        videos[camera.feature_key] = VideoSlice(
            file=resolve_file(relative),
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
    table = pq.read_table(
        data_file,
        columns=columns,
        filters=[("episode_index", "=", episode_index)],
        use_threads=False,
    )
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
            _finite_vector(row["action.hand_cmd"], field="action.hand_cmd", width=2) for row in rows
        ),
        end_effector_actions=tuple(
            _finite_vector(row["action.ee_action"], field="action.ee_action", width=12)
            for row in rows
        ),
    )
