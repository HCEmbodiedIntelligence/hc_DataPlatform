"""Local, verified-cache reader used by both browser and robot ingest processing."""

from __future__ import annotations

import json
import math
import struct
import subprocess
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any

from hc_data_platform.data_formats import lerobot_g1 as g1

from .column_validation import validate_columns
from .local_files import format_path as format_path
from .local_files import local_file as local_file
from .local_files import safe_relative as safe_relative
from .profiles import NativeProfile, profile_for_info
from .source_profile import validate_processing_info


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
    for name, feature in info["features"].items():
        if feature.get("dtype") != "video":
            continue
        prefix = f"videos/{name}"
        required.add(
            format_path(
                info["video_path"],
                video_key=name,
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
    extra_values: dict[str, tuple[Any, ...]] = field(default_factory=dict)

    @property
    def frame_count(self) -> int:
        return len(self.frame_indexes)


def read_episode(
    root: Path, index: int, *, preserve_features=False, capture_snapshot=None
) -> tuple[NativeProfile, Any, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from hc_data_platform.capture_schemes.schema import project_info

    source_info = json.loads(local_file(root, "meta/info.json").read_text())
    info = validate_processing_info(
        project_info(source_info, capture_snapshot),
        preserve_features=preserve_features,
    )
    profile = profile_for_info(info, preserve_features=preserve_features)
    if profile.legacy_g1:
        layout = g1.read_source(
            index,
            lambda relative: local_file(root, relative),
            metadata_files=[
                local_file(root, path.relative_to(root).as_posix())
                for path in sorted((root / "meta/episodes").glob("chunk-*/*.parquet"))
            ],
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
    sources = (capture_snapshot or {}).get("effective_adapter", {}).get("feature_sources", {})
    source_features = dict(source_info["features"])
    for name, feature in (capture_snapshot or {}).get("output", {}).get("features", {}).items():
        original_name = sources.get(name, name)
        if original_name in source_features:
            source_features[original_name] = {
                **source_features[original_name],
                "required": feature["required"],
                "role": feature["role"],
            }
    validate_columns(table, source_features)
    if capture_snapshot is not None:
        projected_names = [
            name for name, feature in info["features"].items() if feature["dtype"] != "video"
        ]
        table = table.select([sources.get(name, name) for name in projected_names]).rename_columns(
            projected_names
        )
        from hc_data_platform.capture_schemes.axes import source_axis_order

        for name, declaration in capture_snapshot["output"]["features"].items():
            original = source_info["features"].get(sources.get(name, name))
            order = source_axis_order(name, declaration, original or {})
            if order is not None:
                column = table[name]
                reordered = pa.array(
                    [[value[i] for i in order] for value in column.to_pylist()], type=column.type
                )
                table = table.set_column(table.schema.get_field_index(name), name, reordered)
        validate_columns(table, info["features"])
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
    if "source.timestamp_ns" in table.column_names and (
        table.schema.field("source.timestamp_ns").type != pa.int64()
        or table["source.timestamp_ns"].null_count
    ):
        raise ValueError("LEROBOT_SOURCE_TIMESTAMP: source timestamps must be non-null int64")
    for key in ("observation.state", "action"):
        kind = table.schema.field(key).type
        if not (
            (pa.types.is_list(kind) or pa.types.is_fixed_size_list(kind))
            and kind.value_type == pa.from_numpy_dtype(info["features"][key]["dtype"])
        ):
            raise ValueError(f"LEROBOT_VECTOR_TYPE: {key} must contain float32 values")
    rows = table.to_pylist()
    if not set(profile.extra_features).issubset(table.column_names):
        raise ValueError("LEROBOT_COLUMN_MISSING: declared source features absent from Parquet")
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
        from .context import read_context

        context = read_context(root) or {}
        if context.get("contract_version") == "openarm-capture/v1":
            from .capture import read_capture_episode

            context, source_episode, mappings = read_capture_episode(root, info, metadata, rows)
    elif profile.profile_id == "openarmx-v1":
        raise ValueError("OPENARM_CONTEXT_MISSING: capture-context.json is required")
    videos = {}
    for camera in profile.cameras:
        source_feature = sources.get(camera.feature_key, camera.feature_key)
        prefix = f"videos/{source_feature}"
        relative_video = format_path(
            info["video_path"],
            video_key=source_feature,
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
        tuple(r["source.timestamp_ns"] for r in rows)
        if "source.timestamp_ns" in table.column_names
        else (),
        {key: tuple(row[key] for row in rows) for key in profile.extra_features},
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
            "-show_streams",
            "-show_entries",
            "frame=best_effort_timestamp,width,height:stream=time_base,codec_name,pix_fmt,avg_frame_rate,width,height",
            "-of",
            "json",
            str(video.file),
        ],
        capture_output=True,
        check=True,
        timeout=120,
    )
    probe = json.loads(result.stdout)
    if result.stderr.strip():
        raise ValueError("LEROBOT_VIDEO_DECODE: decoder reported invalid media")
    stream = probe["streams"][0]
    actual_info = {
        "video.codec": stream["codec_name"],
        "video.pix_fmt": stream["pix_fmt"],
        "video.fps": float(Fraction(stream["avg_frame_rate"])),
        "video.width": stream["width"],
        "video.height": stream["height"],
    }
    if any(
        key in feature.get("info", {}) and feature["info"][key] != value
        for key, value in actual_info.items()
    ):
        raise ValueError("LEROBOT_VIDEO_METADATA: decoded stream differs from declared media")
    time_base = Fraction(probe["streams"][0]["time_base"])
    frames = [
        f
        for f in probe["frames"]
        if video.from_timestamp - 1e-7
        <= float(int(f["best_effort_timestamp"]) * time_base)
        < video.to_timestamp - 1e-7
    ]
    height, width, _ = feature["shape"]
    if len(frames) != count or any(
        f["width"] != width
        or f["height"] != height
        or abs(float(int(f["best_effort_timestamp"]) * time_base) - video.from_timestamp - i / fps)
        > 2e-6
        for i, f in enumerate(frames)
    ):
        raise ValueError("LEROBOT_VIDEO_FRAMES: decoded shape, count or PTS differ from grid")
