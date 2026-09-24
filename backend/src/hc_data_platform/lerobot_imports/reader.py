"""Local, verified-cache reader used by both browser and robot ingest processing."""

from __future__ import annotations

import json
import math
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from hc_data_platform.tools import hf_unitree_g1_to_mcap as g1

from .profiles import NativeProfile, profile_for_info
from .source_profile import validate_processing_info


def safe_relative(value: str) -> str:
    if (
        not value
        or PurePosixPath(value).is_absolute()
        or "\\" in value
        or ":" in value
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise ValueError(f"LEROBOT_UNSAFE_PATH: {value}")
    return value


def local_file(root: Path, relative: str) -> Path:
    path = root / safe_relative(relative)
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError(f"LEROBOT_SYMLINK: {relative}")
    if not path.is_file():
        raise ValueError(f"LEROBOT_ASSET_MISSING: {relative}")
    return path


def format_path(template: str, **values: Any) -> str:
    try:
        return safe_relative(template.format(**values))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("LEROBOT_PATH_TEMPLATE: unsupported v3 path template") from exc


def episode_metadata(root: Path, info: dict[str, Any]) -> list[dict[str, Any]]:
    import pyarrow.parquet as pq

    rows = [
        row
        for path in sorted((root / "meta/episodes").glob("chunk-*/*.parquet"))
        for row in pq.read_table(local_file(root, path.relative_to(root).as_posix())).to_pylist()
    ]
    rows.sort(key=lambda row: row["episode_index"])
    if (
        not rows
        or len(rows) != info.get("total_episodes")
        or [r["episode_index"] for r in rows] != list(range(len(rows)))
    ):
        raise ValueError("LEROBOT_EPISODE_INVENTORY: expected unique contiguous episode indexes")
    offset = 0
    for row in rows:
        length = row.get("length")
        if type(length) is not int or length < 1:
            raise ValueError("LEROBOT_FRAME_COUNT: episode length must be positive")
        if (
            row.get("dataset_from_index") != offset
            or row.get("dataset_to_index") != offset + length
        ):
            raise ValueError("LEROBOT_EPISODE_OFFSETS: inconsistent dataset frame ranges")
        offset += length
    if offset != info.get("total_frames"):
        raise ValueError("LEROBOT_FRAME_COUNT: total_frames differs from episode metadata")
    return rows


def required_episode_files(info: dict[str, Any], metadata: dict[str, Any]) -> set[str]:
    required = {
        format_path(
            info["data_path"],
            chunk_index=metadata["data/chunk_index"],
            file_index=metadata["data/file_index"],
        )
    }
    for camera in profile_for_info(info).cameras:
        prefix = f"videos/{camera.feature_key}"
        required.add(
            format_path(
                info["video_path"],
                video_key=camera.feature_key,
                chunk_index=metadata[f"{prefix}/chunk_index"],
                file_index=metadata[f"{prefix}/file_index"],
            )
        )
    return required


@dataclass(frozen=True)
class GenericEpisode:
    fps: float
    relative_timestamps_ns: tuple[int, ...]
    frame_indexes: tuple[int, ...]
    task_index: int
    joints: tuple[tuple[float, ...], ...]
    target_joints: tuple[tuple[float, ...], ...]
    source_timestamps: tuple[float, ...]
    context: dict[str, Any]
    source_episode: dict[str, Any]
    mappings: tuple[dict[str, Any], ...]
    source_timestamp_ns: tuple[int, ...] = ()

    @property
    def frame_count(self) -> int:
        return len(self.frame_indexes)


def read_episode(root: Path, index: int) -> tuple[NativeProfile, Any, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    info = validate_processing_info(json.loads(local_file(root, "meta/info.json").read_text()))
    profile = profile_for_info(info)
    if profile.legacy_g1:
        layout = g1.acquire_source(
            repository="platform-raw/lerobot",
            revision="committed",
            episode_index=index,
            cache_root=root,
            source_root=root,
        )
        episode = g1.load_episode_data(layout.data_file, index, float(info["fps"]))
        if layout.episode_metadata["length"] != episode.frame_count:
            raise ValueError("LEROBOT_FRAME_COUNT: metadata differs from rows")
        return profile, layout, episode
    metadata = episode_metadata(root, info)[index]
    relative = format_path(
        info["data_path"],
        chunk_index=metadata["data/chunk_index"],
        file_index=metadata["data/file_index"],
    )
    data_file = local_file(root, relative)
    table = pq.read_table(data_file, filters=[("episode_index", "=", index)])
    required = {
        "observation.state",
        "action",
        "timestamp",
        "episode_index",
        "frame_index",
        "index",
        "task_index",
    }
    if not required <= set(table.column_names):
        raise ValueError(f"LEROBOT_COLUMN_MISSING: {sorted(required - set(table.column_names))}")
    for key in ("observation.state", "action"):
        kind = table.schema.field(key).type
        if not (
            (pa.types.is_list(kind) or pa.types.is_fixed_size_list(kind))
            and kind.value_type == pa.float32()
        ):
            raise ValueError(f"LEROBOT_VECTOR_TYPE: {key} must contain float32 values")
    rows = table.to_pylist()
    count, fps = metadata["length"], float(info["fps"])
    if len(rows) != count:
        raise ValueError("LEROBOT_FRAME_COUNT: metadata differs from rows")
    vectors: dict[str, list[tuple[float, ...]]] = {"observation.state": [], "action": []}
    for frame, row in enumerate(rows):
        if (
            row["frame_index"] != frame
            or row["index"] != metadata["dataset_from_index"] + frame
            or not isinstance(row["timestamp"], (int, float))
            or not math.isfinite(row["timestamp"])
            or abs(row["timestamp"] - struct.unpack("f", struct.pack("f", frame / fps))[0]) > 1e-7
        ):
            raise ValueError("LEROBOT_TIME_GRID: rows must preserve the declared fixed grid")
        for key, names in (
            ("observation.state", profile.state_names),
            ("action", profile.action_names),
        ):
            value = row[key]
            if (
                not isinstance(value, list)
                or len(value) != len(names)
                or any(v is None or not math.isfinite(v) for v in value)
            ):
                raise ValueError(f"LEROBOT_VECTOR_VALUE: invalid {key} at frame {frame}")
            vectors[key].append(tuple(value))
    tasks = pq.read_table(local_file(root, "meta/tasks.parquet")).to_pylist()
    task_map = {r["task_index"]: r.get("task", r.get("__index_level_0__")) for r in tasks}
    indexes = {r["task_index"] for r in rows}
    if (
        len(tasks) != info.get("total_tasks")
        or len(task_map) != len(tasks)
        or len(indexes) != 1
        or not indexes <= task_map.keys()
        or metadata.get("tasks") != [task_map[rows[0]["task_index"]]]
    ):
        raise ValueError("LEROBOT_TASK_MAPPING: inconsistent task metadata")
    context, source_episode, mappings = {}, {}, ()
    if (root / "capture-context.json").exists():
        from .capture import read_capture_episode

        context, source_episode, mappings = read_capture_episode(root, info, metadata, rows)
    elif profile.profile_id == "openarmx-v1":
        raise ValueError("OPENARM_CONTEXT_MISSING: capture-context.json is required")
    videos = {}
    for camera in profile.cameras:
        prefix = f"videos/{camera.feature_key}"
        relative_video = format_path(
            info["video_path"],
            video_key=camera.feature_key,
            chunk_index=metadata[f"{prefix}/chunk_index"],
            file_index=metadata[f"{prefix}/file_index"],
        )
        start, end = metadata[f"{prefix}/from_timestamp"], metadata[f"{prefix}/to_timestamp"]
        if (
            not all(isinstance(v, (float, int)) and math.isfinite(v) for v in (start, end))
            or start < 0
            or abs(end - start - count / fps) > 1e-6
        ):
            raise ValueError("LEROBOT_VIDEO_RANGE: video duration differs from episode")
        video = g1.VideoSlice(local_file(root, relative_video), relative_video, start, end)
        validate_video(video, info["features"][camera.feature_key], count, fps)
        videos[camera.feature_key] = video
    layout = g1.SourceLayout(info, metadata, data_file, relative, videos)
    episode = GenericEpisode(
        fps,
        tuple(round(i * 1e9 / fps) for i in range(count)),
        tuple(range(count)),
        rows[0]["task_index"],
        tuple(vectors["observation.state"]),
        tuple(vectors["action"]),
        tuple(r["timestamp"] for r in rows),
        context,
        source_episode,
        mappings,
        tuple(r['source.timestamp_ns'] for r in rows) if 'source.timestamp_ns' in table.column_names else (),
    )
    return profile, layout, episode


def validate_video(video: Any, feature: dict[str, Any], count: int, fps: float) -> None:
    # Decode the declared slice, including PTS, rather than trusting MP4 header counts.
    try:
        _validate_video(video, feature, count, fps)
    except (subprocess.SubprocessError, KeyError, TypeError) as exc:
        raise ValueError("LEROBOT_VIDEO_DECODE: cannot decode the declared video slice") from exc


def _validate_video(video: Any, feature: dict[str, Any], count: int, fps: float) -> None:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-read_intervals",
            f"{video.from_timestamp}%{video.to_timestamp + 1.0}",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time,width,height",
            "-of",
            "json",
            str(video.file),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    frames = [
        f
        for f in json.loads(result.stdout)["frames"]
        if video.from_timestamp - 1e-7
        <= float(f["best_effort_timestamp_time"])
        < video.to_timestamp - 1e-7
    ]
    height, width, _ = feature["shape"]
    if len(frames) != count or any(
        f["width"] != width
        or f["height"] != height
        or abs(float(f["best_effort_timestamp_time"]) - video.from_timestamp - i / fps) > 2e-6
        for i, f in enumerate(frames)
    ):
        raise ValueError("LEROBOT_VIDEO_FRAMES: decoded shape, count or PTS differ from grid")
