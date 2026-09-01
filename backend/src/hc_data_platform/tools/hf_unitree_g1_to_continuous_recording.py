"""Build one long continuous-recording bundle from the pinned Unitree G1 dataset.

The source LeRobot v3 dataset stores 154 already ordered manipulation episodes in
shared Parquet and MP4 shards.  This tool turns the complete revision into one
uninterrupted platform recording: the four camera shard sequences are joined by
stream copy, while the source robot state/action rows are placed on one canonical
30 Hz recording clock in an indexed sensor MCAP.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, cast

from mcap.writer import CompressionType, Writer

from hc_data_platform.continuous_recordings.asset_models import (
    CameraRecordingConfigV1,
    CreateRecordingUploadCommand,
    RecordingAssetManifestV1,
    RecordingAssetRole,
    RecordingConfigurationV1,
    SensorRecordingConfigV1,
    SensorTimestampMode,
)
from hc_data_platform.ingest.cli import DEFAULT_PART_SIZE, effective_part_size
from hc_data_platform.ingest.ports import crc64_ecma

from .hf_unitree_g1_to_mcap import (
    ACTION_SCHEMA,
    ACTION_TOPIC,
    BASE_POSE_TOPIC,
    BASE_TARGET_TOPIC,
    CAMERAS,
    DEFAULT_REPOSITORY,
    DEFAULT_REVISION,
    DEFAULT_SOURCE_LICENSE,
    DEX1_RAW_NAMES,
    END_EFFECTOR_ACTION_TOPIC,
    END_EFFECTOR_NAMES,
    END_EFFECTOR_STATE_TOPIC,
    G1_JOINT_NAMES,
    GRIPPER_COMMAND_TOPIC,
    GRIPPER_STATE_TOPIC,
    JOINT_SCHEMA,
    JOINT_TOPIC,
    POSE_SCHEMA,
    REQUIRED_TOPICS,
    SOURCE_SCHEMA,
    SOURCE_TOPIC,
    STATE_TOPIC,
)
from .native_unitree_g1_recording import (
    CONFIG_PATH,
    SENSOR_PATH,
    UPLOAD_COMMAND_PATH,
    VideoProbe,
    probe_mp4,
)

RECORDER_VERSION = "hf-unitree-g1-continuous/1.0.0"
EPISODE_INDEX_PATH = "source-episodes.json"
PRIMARY_CLOCK = "recording-monotonic"
DEFAULT_CAPTURE_START = datetime(2026, 4, 16, tzinfo=timezone.utc)

SENSOR_TOPICS = (
    STATE_TOPIC,
    JOINT_TOPIC,
    ACTION_TOPIC,
    BASE_POSE_TOPIC,
    BASE_TARGET_TOPIC,
    END_EFFECTOR_STATE_TOPIC,
    END_EFFECTOR_ACTION_TOPIC,
    GRIPPER_STATE_TOPIC,
    GRIPPER_COMMAND_TOPIC,
    SOURCE_TOPIC,
)
REQUIRED_SENSOR_TOPICS = frozenset(REQUIRED_TOPICS).difference(
    camera.topic for camera in CAMERAS
)

_DATA_COLUMNS = (
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
)


@dataclass(frozen=True, slots=True)
class EpisodeBoundary:
    source_episode_index: int
    frame_count: int
    start_offset_ns: int
    end_offset_ns: int
    tasks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SourceInventory:
    source_root: Path
    info: Mapping[str, Any]
    fps: float
    total_frames: int
    source_parquet_rows: int
    episodes: tuple[EpisodeBoundary, ...]
    data_files: tuple[Path, ...]
    video_files_by_feature: Mapping[str, tuple[Path, ...]]

    @property
    def duration_ns(self) -> int:
        return round(self.total_frames * 1_000_000_000 / self.fps)

    @property
    def ignored_undeclared_rows(self) -> int:
        return self.source_parquet_rows - self.total_frames


@dataclass(frozen=True, slots=True)
class SynthesisRequest:
    source_root: Path
    output_dir: Path
    project_id: str
    collection_task_id: str
    robot_id: str
    device_id: str
    capture_started_at: datetime
    repository: str = DEFAULT_REPOSITORY
    resolved_revision: str = DEFAULT_REVISION
    collection_job_id: str | None = None
    recording_id: str | None = None
    part_size: int = DEFAULT_PART_SIZE


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
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


def _format_path(
    template: str,
    *,
    chunk_index: int,
    file_index: int,
    video_key: str = "",
) -> str:
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


def _safe_source_file(source_root: Path, relative_path: str) -> Path:
    path = PurePosixPath(relative_path)
    if (
        path.is_absolute()
        or "\\" in relative_path
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise RuntimeError(f"unsafe LeRobot source path: {relative_path}")
    candidate = source_root.joinpath(*path.parts)
    if not candidate.is_file() or candidate.stat().st_size < 1:
        raise RuntimeError(f"LeRobot source file is missing or empty: {candidate}")
    return candidate


def _parquet_rows(paths: Iterable[Path], columns: list[str]) -> list[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra for Parquet support") from exc
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(cast(list[dict[str, Any]], pq.read_table(path, columns=columns).to_pylist()))
    return rows


def _parquet_row_count(paths: Iterable[Path]) -> int:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra for Parquet support") from exc
    return sum(int(pq.ParquetFile(path).metadata.num_rows) for path in paths)


def _append_unique(values: list[Path], candidate: Path) -> None:
    if not values or values[-1] != candidate:
        if candidate in values:
            raise RuntimeError(f"source shard order is not contiguous: {candidate}")
        values.append(candidate)


def inspect_source(source_root: Path) -> SourceInventory:
    root = source_root.expanduser().resolve()
    info_path = _safe_source_file(root, "meta/info.json")
    info = cast(dict[str, Any], json.loads(info_path.read_text(encoding="utf-8")))
    try:
        fps = float(info["fps"])
        total_episodes = int(info["total_episodes"])
        declared_total_frames = int(info["total_frames"])
        data_template = str(info["data_path"])
        video_template = str(info["video_path"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError("LeRobot metadata is missing required dataset facts") from exc
    if not math.isfinite(fps) or fps <= 0:
        raise RuntimeError("LeRobot fps must be a finite positive number")
    if abs(fps - 30.0) > 1e-9:
        raise RuntimeError("continuous Episode processing requires this source to be 30 Hz")

    episode_paths = tuple(sorted((root / "meta/episodes").glob("chunk-*/*.parquet")))
    if not episode_paths:
        raise RuntimeError("LeRobot episode metadata is missing")
    episode_columns = ["episode_index", "length", "tasks", "data/chunk_index", "data/file_index"]
    for camera in CAMERAS:
        prefix = f"videos/{camera.feature_key}"
        episode_columns.extend(
            (
                f"{prefix}/chunk_index",
                f"{prefix}/file_index",
                f"{prefix}/from_timestamp",
                f"{prefix}/to_timestamp",
            )
        )
    rows = _parquet_rows(episode_paths, episode_columns)
    rows.sort(key=lambda row: int(row["episode_index"]))
    indexes = tuple(int(row["episode_index"]) for row in rows)
    if indexes != tuple(range(total_episodes)):
        raise RuntimeError("source episode indexes are not contiguous from zero")

    data_files: list[Path] = []
    videos: dict[str, list[Path]] = {camera.feature_key: [] for camera in CAMERAS}
    previous_video_end: dict[str, tuple[Path, float] | None] = {
        camera.feature_key: None for camera in CAMERAS
    }
    boundaries: list[EpisodeBoundary] = []
    cumulative_frames = 0
    for row in rows:
        episode_index = int(row["episode_index"])
        frame_count = int(row["length"])
        if frame_count < 1:
            raise RuntimeError(f"episode {episode_index} has no frames")
        data_relative = _format_path(
            data_template,
            chunk_index=int(row["data/chunk_index"]),
            file_index=int(row["data/file_index"]),
        )
        _append_unique(data_files, _safe_source_file(root, data_relative))

        expected_duration = frame_count / fps
        for camera in CAMERAS:
            prefix = f"videos/{camera.feature_key}"
            video_relative = _format_path(
                video_template,
                chunk_index=int(row[f"{prefix}/chunk_index"]),
                file_index=int(row[f"{prefix}/file_index"]),
                video_key=camera.feature_key,
            )
            video_path = _safe_source_file(root, video_relative)
            _append_unique(videos[camera.feature_key], video_path)
            start = float(row[f"{prefix}/from_timestamp"])
            end = float(row[f"{prefix}/to_timestamp"])
            if start < 0 or end <= start or abs((end - start) - expected_duration) > 1e-6:
                raise RuntimeError(
                    f"episode {episode_index} camera {camera.camera_id} has invalid timing"
                )
            previous = previous_video_end[camera.feature_key]
            if previous is None or previous[0] != video_path:
                if abs(start) > 1e-6:
                    raise RuntimeError(f"camera shard does not start at zero: {video_path}")
            elif abs(start - previous[1]) > 1e-6:
                raise RuntimeError(f"camera shard has a timing gap: {video_path}")
            previous_video_end[camera.feature_key] = (video_path, end)

        raw_tasks = row.get("tasks")
        tasks = (
            tuple(str(item) for item in raw_tasks)
            if isinstance(raw_tasks, (list, tuple))
            else ()
        )
        start_offset_ns = round(cumulative_frames * 1_000_000_000 / fps)
        cumulative_frames += frame_count
        boundaries.append(
            EpisodeBoundary(
                source_episode_index=episode_index,
                frame_count=frame_count,
                start_offset_ns=start_offset_ns,
                end_offset_ns=round(cumulative_frames * 1_000_000_000 / fps),
                tasks=tasks,
            )
        )
    if cumulative_frames != declared_total_frames:
        raise RuntimeError(
            f"episode rows contain {cumulative_frames} frames, expected {declared_total_frames}"
        )
    source_parquet_rows = _parquet_row_count(data_files)
    if source_parquet_rows < cumulative_frames:
        raise RuntimeError(
            f"source Parquet has {source_parquet_rows} rows, expected at least {cumulative_frames}"
        )
    return SourceInventory(
        source_root=root,
        info=info,
        fps=fps,
        total_frames=cumulative_frames,
        source_parquet_rows=source_parquet_rows,
        episodes=tuple(boundaries),
        data_files=tuple(data_files),
        video_files_by_feature={key: tuple(value) for key, value in videos.items()},
    )


def _ffconcat_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "'\\''")


def concatenate_video_shards(
    source_files: tuple[Path, ...],
    destination: Path,
    *,
    run: Any = subprocess.run,
) -> None:
    if not source_files:
        raise ValueError("at least one source video shard is required")
    destination.parent.mkdir(parents=True, exist_ok=True)
    list_path = destination.with_suffix(".ffconcat")
    list_path.write_text(
        "ffconcat version 1.0\n"
        + "".join(f"file '{_ffconcat_path(path)}'\n" for path in source_files),
        encoding="utf-8",
    )
    try:
        run(
            (
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                "-map",
                "0:v:0",
                "-an",
                "-c:v",
                "copy",
                "-movflags",
                "+faststart",
                str(destination),
            ),
            check=True,
            timeout=7200,
        )
    except FileNotFoundError as exc:
        raise RuntimeError("ffmpeg is required to join the source MP4 shards") from exc
    finally:
        list_path.unlink(missing_ok=True)


def _probe_frame_count(path: Path, *, run: Any = subprocess.run) -> int:
    completed = run(
        (
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_frames",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ),
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    try:
        count = int(completed.stdout.strip())
    except ValueError as exc:
        raise RuntimeError(f"ffprobe did not report an MP4 frame count for {path}") from exc
    if count < 1:
        raise RuntimeError(f"ffprobe reported an invalid MP4 frame count for {path}")
    return count


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


def _sensor_channels(writer: Writer) -> dict[str, int]:
    joint_schema = writer.register_schema(
        "hc.robot.JointState", "jsonschema", _json_bytes(JOINT_SCHEMA)
    )
    action_schema = writer.register_schema(
        "hc.robot.Action", "jsonschema", _json_bytes(ACTION_SCHEMA)
    )
    pose_schema = writer.register_schema("hc.robot.Pose", "jsonschema", _json_bytes(POSE_SCHEMA))
    source_schema = writer.register_schema(
        "hc.source.HuggingFace", "jsonschema", _json_bytes(SOURCE_SCHEMA)
    )
    channels = {
        STATE_TOPIC: writer.register_channel(STATE_TOPIC, "json", joint_schema),
        JOINT_TOPIC: writer.register_channel(JOINT_TOPIC, "json", joint_schema),
        ACTION_TOPIC: writer.register_channel(ACTION_TOPIC, "json", action_schema),
        END_EFFECTOR_STATE_TOPIC: writer.register_channel(
            END_EFFECTOR_STATE_TOPIC, "json", action_schema
        ),
        END_EFFECTOR_ACTION_TOPIC: writer.register_channel(
            END_EFFECTOR_ACTION_TOPIC, "json", action_schema
        ),
        GRIPPER_STATE_TOPIC: writer.register_channel(
            GRIPPER_STATE_TOPIC, "json", action_schema
        ),
        GRIPPER_COMMAND_TOPIC: writer.register_channel(
            GRIPPER_COMMAND_TOPIC, "json", action_schema
        ),
        BASE_POSE_TOPIC: writer.register_channel(BASE_POSE_TOPIC, "json", pose_schema),
        BASE_TARGET_TOPIC: writer.register_channel(BASE_TARGET_TOPIC, "json", pose_schema),
        SOURCE_TOPIC: writer.register_channel(SOURCE_TOPIC, "json", source_schema),
    }
    return channels


def _data_rows(paths: tuple[Path, ...]) -> Iterator[dict[str, Any]]:
    try:
        import pyarrow.parquet as pq
    except ImportError as exc:
        raise RuntimeError("install the backend data extra for Parquet support") from exc
    for path in paths:
        parquet = pq.ParquetFile(path)
        for batch in parquet.iter_batches(batch_size=2048, columns=list(_DATA_COLUMNS)):
            yield from cast(list[dict[str, Any]], batch.to_pylist())


def write_sensor_mcap(
    destination: Path,
    inventory: SourceInventory,
    *,
    repository: str = DEFAULT_REPOSITORY,
    resolved_revision: str = DEFAULT_REVISION,
) -> int:
    destination.parent.mkdir(parents=True, exist_ok=True)
    boundary_cursor = 0
    episode_frame_index = 0
    global_frame_index = 0
    message_count = 0
    ignored_undeclared_rows = 0
    with destination.open("wb") as output:
        writer = Writer(
            output,
            compression=CompressionType.ZSTD,
            chunk_size=16 * 1024 * 1024,
            enable_crcs=True,
            enable_data_crcs=True,
        )
        writer.start(profile="hc-native-sensors-json/v1", library=RECORDER_VERSION)
        channels = _sensor_channels(writer)
        for row in _data_rows(inventory.data_files):
            if boundary_cursor >= len(inventory.episodes):
                episode_index = int(row["episode_index"])
                if episode_index <= inventory.episodes[-1].source_episode_index:
                    raise RuntimeError(
                        "Parquet repeats a declared episode beyond the episode inventory"
                    )
                ignored_undeclared_rows += 1
                continue
            boundary = inventory.episodes[boundary_cursor]
            episode_index = int(row["episode_index"])
            frame_index = int(row["frame_index"])
            if episode_index != boundary.source_episode_index:
                raise RuntimeError("Parquet episode order differs from episode metadata")
            if frame_index != episode_frame_index:
                raise RuntimeError(f"episode {episode_index} frame indexes are not contiguous")

            source_timestamp = float(row["timestamp"])
            if not math.isfinite(source_timestamp):
                raise RuntimeError(f"episode {episode_index} has a non-finite timestamp")
            expected_source_timestamp = frame_index / inventory.fps
            if abs(source_timestamp - expected_source_timestamp) > 1e-5:
                raise RuntimeError(f"episode {episode_index} source timestamps are not 30 Hz")

            current = _finite_vector(
                row["observation.state.robot_q_current"],
                field="observation.state.robot_q_current",
                width=36,
            )
            desired = _finite_vector(
                row["action.robot_q_desired"],
                field="action.robot_q_desired",
                width=36,
            )
            end_effector_state = _finite_vector(
                row["observation.state.ee_state"],
                field="observation.state.ee_state",
                width=12,
            )
            end_effector_action = _finite_vector(
                row["action.ee_action"], field="action.ee_action", width=12
            )
            gripper_state = _finite_vector(
                row["observation.state.hand_state"],
                field="observation.state.hand_state",
                width=2,
            )
            gripper_command = _finite_vector(
                row["action.hand_cmd"], field="action.hand_cmd", width=2
            )
            timestamp_ns = round(global_frame_index * 1_000_000_000 / inventory.fps)
            sequence = global_frame_index + 1
            joint_message = _json_bytes(
                {"names": G1_JOINT_NAMES, "positions": current[7:]}
            )
            messages = {
                STATE_TOPIC: joint_message,
                JOINT_TOPIC: joint_message,
                ACTION_TOPIC: _json_bytes(
                    {"names": G1_JOINT_NAMES, "values": desired[7:]}
                ),
                END_EFFECTOR_STATE_TOPIC: _json_bytes(
                    {"names": END_EFFECTOR_NAMES, "values": end_effector_state}
                ),
                END_EFFECTOR_ACTION_TOPIC: _json_bytes(
                    {"names": END_EFFECTOR_NAMES, "values": end_effector_action}
                ),
                GRIPPER_STATE_TOPIC: _json_bytes(
                    {"names": DEX1_RAW_NAMES, "values": gripper_state}
                ),
                GRIPPER_COMMAND_TOPIC: _json_bytes(
                    {"names": DEX1_RAW_NAMES, "values": gripper_command}
                ),
                BASE_POSE_TOPIC: _json_bytes(
                    {"position_xyz": current[:3], "orientation_wxyz": current[3:7]}
                ),
                BASE_TARGET_TOPIC: _json_bytes(
                    {"position_xyz": desired[:3], "orientation_wxyz": desired[3:7]}
                ),
                SOURCE_TOPIC: _json_bytes(
                    {
                        "episode_index": episode_index,
                        "license": DEFAULT_SOURCE_LICENSE,
                        "nominal_frequency_hz": inventory.fps,
                        "repository": repository,
                        "resolved_revision": resolved_revision,
                        "source_format": "LeRobotDataset v3.0 synthesized-continuous",
                        "task_index": int(row["task_index"]),
                    }
                ),
            }
            for topic in SENSOR_TOPICS:
                writer.add_message(
                    channels[topic],
                    log_time=timestamp_ns,
                    publish_time=timestamp_ns,
                    sequence=sequence,
                    data=messages[topic],
                )
                message_count += 1
            global_frame_index += 1
            episode_frame_index += 1
            if episode_frame_index == boundary.frame_count:
                boundary_cursor += 1
                episode_frame_index = 0
            if global_frame_index % 10_000 == 0:
                print(
                    f"sensor MCAP {global_frame_index}/{inventory.total_frames} frames",
                    file=sys.stderr,
                    flush=True,
                )
        writer.finish()
    if global_frame_index != inventory.total_frames or boundary_cursor != len(inventory.episodes):
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"sensor MCAP consumed {global_frame_index} frames, expected {inventory.total_frames}"
        )
    if ignored_undeclared_rows != inventory.ignored_undeclared_rows:
        destination.unlink(missing_ok=True)
        raise RuntimeError(
            f"ignored {ignored_undeclared_rows} undeclared rows, "
            f"expected {inventory.ignored_undeclared_rows}"
        )
    if ignored_undeclared_rows:
        print(
            f"ignored {ignored_undeclared_rows} trailing Parquet rows without "
            "episode metadata/video",
            file=sys.stderr,
            flush=True,
        )
    return message_count


def _file_facts(path: Path, *, part_size: int) -> dict[str, int | str]:
    digest = hashlib.sha256()
    crc64 = 0
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
            crc64 = crc64_ecma(chunk, crc64)
    if size < 1:
        raise RuntimeError(f"recording asset is empty: {path}")
    selected_part_size = effective_part_size(file_size=size, requested_part_size=part_size)
    return {
        "size": size,
        "sha256": digest.hexdigest(),
        "crc64": crc64,
        "part_count": math.ceil(size / selected_part_size),
    }


def _identity(request: SynthesisRequest, inventory: SourceInventory) -> dict[str, str]:
    seed = "|".join(
        (
            request.project_id,
            request.collection_task_id,
            request.robot_id,
            request.repository,
            request.resolved_revision,
            str(len(inventory.episodes)),
            str(inventory.total_frames),
            RECORDER_VERSION,
        )
    )
    digest = hashlib.sha256(seed.encode()).hexdigest()[:16]
    return {
        "recording_id": request.recording_id or f"hf-g1-continuous-{digest}",
        "rollout_id": f"hf-g1-continuous-rollout-{digest}",
        "data_package_id": f"hf-g1-continuous-package-{digest}",
        "collection_job_id": request.collection_job_id or f"hf-g1-continuous-job-{digest}",
    }


def _episode_index_document(
    request: SynthesisRequest, inventory: SourceInventory
) -> dict[str, Any]:
    return {
        "schema_version": "hf-unitree-g1-continuous-source/v1",
        "repository": request.repository,
        "resolved_revision": request.resolved_revision,
        "license": DEFAULT_SOURCE_LICENSE,
        "synthesis": {
            "kind": "ordered_episode_concatenation",
            "recorder_version": RECORDER_VERSION,
            "gap_frames_between_episodes": 0,
            "timestamp_basis": "cumulative_source_frame_count_at_30_hz",
        },
        "episode_count": len(inventory.episodes),
        "frame_count": inventory.total_frames,
        "duration_ns": str(inventory.duration_ns),
        "source_parquet_rows": inventory.source_parquet_rows,
        "ignored_undeclared_rows": inventory.ignored_undeclared_rows,
        "undeclared_row_policy": "ignored_when_trailing_and_above_last_declared_episode",
        "episodes": [
            {
                "source_episode_index": item.source_episode_index,
                "frame_count": item.frame_count,
                "start_offset_ns": str(item.start_offset_ns),
                "end_offset_ns": str(item.end_offset_ns),
                "tasks": list(item.tasks),
            }
            for item in inventory.episodes
        ],
    }


def synthesize_recording(request: SynthesisRequest) -> Path:
    if request.capture_started_at.tzinfo is None:
        raise ValueError("capture_started_at must include a timezone")
    if request.part_size < 5 * 1024**2:
        raise ValueError("part_size must be at least 5 MiB")
    output_dir = request.output_dir.expanduser().resolve()
    if output_dir.exists():
        raise ValueError(f"output directory already exists: {output_dir}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    inventory = inspect_source(request.source_root)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=output_dir.parent))
    try:
        probes: dict[str, VideoProbe] = {}
        video_assets: list[RecordingAssetManifestV1] = []
        for camera in CAMERAS:
            print(f"join {camera.camera_id} video shards", file=sys.stderr, flush=True)
            destination = staging / f"videos/{camera.camera_id}.mp4"
            concatenate_video_shards(
                inventory.video_files_by_feature[camera.feature_key], destination
            )
            probes[camera.camera_id] = probe_mp4(destination)
            frame_count = _probe_frame_count(destination)
            if frame_count != inventory.total_frames:
                raise RuntimeError(
                    f"camera {camera.camera_id} has {frame_count} frames, "
                    f"expected {inventory.total_frames}"
                )
            facts = _file_facts(destination, part_size=request.part_size)
            video_assets.append(
                RecordingAssetManifestV1(
                    path=f"videos/{camera.camera_id}.mp4",
                    role=RecordingAssetRole.RAW_VIDEO,
                    camera_id=camera.camera_id,
                    media_type="video/mp4",
                    **facts,
                )
            )

        expected_duration_seconds = inventory.duration_ns / 1_000_000_000
        for camera_id, probe in probes.items():
            if abs(probe.duration_seconds - expected_duration_seconds) > 1 / inventory.fps:
                raise RuntimeError(
                    f"camera {camera_id} duration {probe.duration_seconds} does not match "
                    f"{expected_duration_seconds}"
                )

        sensor_destination = staging / SENSOR_PATH
        message_count = write_sensor_mcap(
            sensor_destination,
            inventory,
            repository=request.repository,
            resolved_revision=request.resolved_revision,
        )
        episode_index_path = staging / EPISODE_INDEX_PATH
        episode_index_path.write_text(
            json.dumps(
                _episode_index_document(request, inventory),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        recording_config = RecordingConfigurationV1(
            recorder_version=RECORDER_VERSION,
            primary_clock_domain=PRIMARY_CLOCK,
            cameras=tuple(
                CameraRecordingConfigV1(
                    camera_id=camera.camera_id,
                    topic=camera.topic,
                    fps=probes[camera.camera_id].fps,
                    width=probes[camera.camera_id].width,
                    height=probes[camera.camera_id].height,
                    codec=probes[camera.camera_id].codec,
                    clock_domain=PRIMARY_CLOCK,
                    time_base_numerator=probes[camera.camera_id].time_base_numerator,
                    time_base_denominator=probes[camera.camera_id].time_base_denominator,
                )
                for camera in CAMERAS
            ),
            sensors=tuple(
                SensorRecordingConfigV1(
                    topic=topic,
                    clock_domain=PRIMARY_CLOCK,
                    timestamp_mode=SensorTimestampMode.RECORDING_OFFSET_NS,
                    required=topic in REQUIRED_SENSOR_TOPICS,
                )
                for topic in SENSOR_TOPICS
            ),
        )
        config_destination = staging / CONFIG_PATH
        config_destination.parent.mkdir(parents=True, exist_ok=True)
        config_destination.write_text(
            json.dumps(recording_config.model_dump(mode="json"), ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        capture_started_at = request.capture_started_at.astimezone(timezone.utc)
        command = CreateRecordingUploadCommand(
            **_identity(request, inventory),
            collection_task_id=request.collection_task_id,
            robot_id=request.robot_id,
            device_id=request.device_id,
            capture_started_at=capture_started_at,
            capture_ended_at=capture_started_at
            + timedelta(microseconds=inventory.duration_ns // 1000),
            recording_config=recording_config,
            assets=(
                *video_assets,
                RecordingAssetManifestV1(
                    path=SENSOR_PATH,
                    role=RecordingAssetRole.SENSOR_DATA,
                    media_type="application/x-mcap",
                    **_file_facts(sensor_destination, part_size=request.part_size),
                ),
                RecordingAssetManifestV1(
                    path=CONFIG_PATH,
                    role=RecordingAssetRole.RECORDING_CONFIG,
                    media_type="application/json",
                    **_file_facts(config_destination, part_size=request.part_size),
                ),
                RecordingAssetManifestV1(
                    path=EPISODE_INDEX_PATH,
                    role=RecordingAssetRole.AUXILIARY,
                    media_type="application/json",
                    **_file_facts(episode_index_path, part_size=request.part_size),
                ),
            ),
        )
        (staging / UPLOAD_COMMAND_PATH).write_text(
            json.dumps(command.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        summary = {
            "recording_id": command.recording_id,
            "episode_count": len(inventory.episodes),
            "frame_count": inventory.total_frames,
            "sensor_message_count": message_count,
            "ignored_undeclared_rows": inventory.ignored_undeclared_rows,
            "duration_ns": str(inventory.duration_ns),
            "camera_count": len(CAMERAS),
            "asset_count": len(command.assets),
        }
        print(json.dumps(summary, ensure_ascii=False), file=sys.stderr, flush=True)
        os.replace(staging, output_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return output_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--collection-task-id", required=True)
    parser.add_argument("--collection-job-id")
    parser.add_argument("--robot-id", required=True)
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--recording-id")
    parser.add_argument("--capture-start", type=_parse_datetime, default=DEFAULT_CAPTURE_START)
    parser.add_argument("--repository", default=DEFAULT_REPOSITORY)
    parser.add_argument("--revision", default=DEFAULT_REVISION)
    parser.add_argument("--part-size-mib", type=int, default=DEFAULT_PART_SIZE // 1024**2)
    return parser


def run(args: argparse.Namespace) -> Path:
    return synthesize_recording(
        SynthesisRequest(
            source_root=cast(Path, args.source_root),
            output_dir=cast(Path, args.output_dir),
            project_id=cast(str, args.project_id),
            collection_task_id=cast(str, args.collection_task_id),
            collection_job_id=cast(str | None, args.collection_job_id),
            robot_id=cast(str, args.robot_id),
            device_id=cast(str, args.device_id),
            recording_id=cast(str | None, args.recording_id),
            capture_started_at=cast(datetime, args.capture_start),
            repository=cast(str, args.repository),
            resolved_revision=cast(str, args.revision),
            part_size=int(args.part_size_mib) * 1024**2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        output = run(args)
    except (OSError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
