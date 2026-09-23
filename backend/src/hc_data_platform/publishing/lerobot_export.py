"""Portable LeRobot v3 schema, frozen annotation overlays and archive validation."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from hc_data_platform.aligned_media.models import AlignedMediaFrameReferenceV1

from .export_assets import ExportAssetsPort, export_error
from .export_video import inspect_video, materialize_video
from .models import ExportStepV1, PublishedDatasetManifestV1
from .service import canonical_json_bytes

EXPORT_REVISION = "lerobot-materialized-v2"
VIDEO_PATH = "videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"
DATA_PATH = "data/chunk-000/file-000.parquet"
EPISODES_PATH = "meta/episodes/chunk-000/file-000.parquet"


def _camera(name: str) -> bool:
    return name.startswith(("/camera/", "camera.", "observation.images."))


def _feature_names(names: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        if name in {"/humanoid/action", "/action"}:
            target = "action"
        elif name == "/humanoid/observation/state" or (
            name in {"/robot/joint_states", "/joint_states"}
            and "/humanoid/observation/state" not in names
        ):
            target = "observation.state"
        elif _camera(name):
            camera = (
                name.removeprefix("/camera/")
                .removesuffix("/image")
                .removeprefix("camera.")
                .removeprefix("observation.images.")
            )
            target = "observation.images." + re.sub(r"[^A-Za-z0-9_.-]", "_", camera)
        elif name.startswith(("observation.", "action")) and "/" not in name:
            target = name
        else:
            target = "hc.source." + re.sub(
                r"[^A-Za-z0-9_.-]", "_", name.strip("/").replace("/", ".")
            )
        if target in result.values():
            raise export_error(
                "LEROBOT_MODALITY_SCHEMA_MISMATCH", "Two source modalities map to the same feature."
            )
        result[name] = target
    return result


def _numeric_stats(values: Sequence[Any]) -> dict[str, Any]:
    array = np.asarray(values, dtype=np.float64)
    if array.ndim == 1:
        array = array[:, None]
    if not np.isfinite(array).all():
        raise export_error(
            "LEROBOT_NONFINITE_VALUE", "Numeric export features contain NaN or infinity."
        )
    stats = {
        name: value.tolist()
        for name, value in {
            "min": array.min(axis=0),
            "max": array.max(axis=0),
            "mean": array.mean(axis=0),
            "std": array.std(axis=0),
        }.items()
    }
    stats.update(
        {
            f"q{int(q * 100):02d}": np.quantile(array, q, axis=0).tolist()
            for q in (0.01, 0.1, 0.5, 0.9, 0.99)
        }
    )
    stats["count"] = [len(array)]
    return stats


def _video_stats(items: Sequence[dict[str, Any]]) -> dict[str, Any]:
    count = sum(item["count"][0] for item in items)
    mean = sum(np.asarray(item["mean"]) * item["count"][0] for item in items) / count
    variance = (
        sum(
            (np.square(item["std"]) + np.square(np.asarray(item["mean"]) - mean)) * item["count"][0]
            for item in items
        )
        / count
    )
    return {
        "min": np.minimum.reduce([item["min"] for item in items]).tolist(),
        "max": np.maximum.reduce([item["max"] for item in items]).tolist(),
        "mean": mean.tolist(),
        "std": np.sqrt(np.maximum(0, variance)).tolist(),
        "count": [count],
    }


def _tags(metadata: dict[str, Any], steps: Sequence[ExportStepV1]) -> list[dict[str, Any]]:
    mapped = []
    for tag in metadata.get("tags", []):
        ranges: list[dict[str, int]] = []
        for frame, step in enumerate(steps):
            if not tag["start_step"] <= step.step_index < tag["end_step"]:
                continue
            if ranges and ranges[-1]["source_end_step"] == step.step_index:
                ranges[-1]["end_frame"] = frame + 1
                ranges[-1]["source_end_step"] = step.step_index + 1
            else:
                ranges.append(
                    {
                        "start_frame": frame,
                        "end_frame": frame + 1,
                        "source_start_step": step.step_index,
                        "source_end_step": step.step_index + 1,
                    }
                )
        if ranges:
            mapped.append({**tag, "ranges": ranges})
    return mapped


class LeRobotArchive:
    def __init__(
        self,
        manifest: PublishedDatasetManifestV1,
        steps: Sequence[ExportStepV1],
        assets: ExportAssetsPort | None,
    ) -> None:
        self.manifest, self.steps, self.assets = manifest, steps, assets
        frequencies = {rollout.alignment_frequency_hz for rollout in manifest.rollouts}
        if len(frequencies) != 1:
            raise export_error(
                "LEROBOT_FREQUENCY_MISMATCH", "All Episodes must share one frame frequency."
            )
        self.fps = frequencies.pop()
        self.mapping = _feature_names(sorted(steps[0].modalities))
        self.episodes = [
            [step for step in steps if step.rollout_id == rollout.rollout_id]
            for rollout in manifest.rollouts
        ]
        self.metadata: list[dict[str, Any]] = []
        for rollout, episode in zip(manifest.rollouts, self.episodes, strict=True):
            source = episode[0].modalities.get("/metadata/source", {})
            if isinstance(source, str):
                source = json.loads(source)
            if assets is None:
                if rollout.annotation_revision is not None or any(
                    _camera(name) for name in self.mapping
                ):
                    raise export_error(
                        "EXPORT_ASSET_SOURCE_UNAVAILABLE",
                        "Media and frozen annotation readers must be configured.",
                    )
                metadata = {"tags": [], "task": "Robot demonstration", "robot_type": "unknown"}
            else:
                metadata = assets.episode_metadata(manifest, rollout, source)
            if rollout.annotation_revision is None:
                metadata = {**metadata, "tags": []}
            self.metadata.append(metadata)
        self.annotations: dict[str, Any] = {
            "schema_version": "hc-lerobot-annotations/v1",
            "publication_content_hash": manifest.content_hash,
            "interval_convention": "half-open",
            "episodes": [
                {
                    "episode_index": index,
                    "rollout_id": rollout.rollout_id,
                    "annotation_task_id": rollout.annotation_task_id,
                    "annotation_revision": rollout.annotation_revision,
                    "annotation_submission_id": rollout.annotation_submission_id,
                    **{
                        key: value
                        for key, value in metadata.items()
                        if key not in {"tags", "task", "robot_type"}
                    },
                    "tags": _tags(metadata, episode),
                }
                for index, (rollout, episode, metadata) in enumerate(
                    zip(manifest.rollouts, self.episodes, self.metadata, strict=True)
                )
            ],
        }

    def build(self) -> bytes:
        from .exporters import (
            _feature_type,
            _lerobot_value,
            _parquet_bytes,
            _require_arrow,
            _zip_files,
        )

        pa, pq = _require_arrow()
        features: dict[str, Any] = {}
        columns: dict[str, list[Any]] = {}
        types: dict[str, Any] = {}
        encodings: dict[str, Any] = {}
        shape: tuple[int, ...]
        values: list[Any]
        for name, target in self.mapping.items():
            if _camera(name):
                target = f"hc.source_ref.{target}"
                values = [
                    canonical_json_bytes(step.modalities[name]).decode() for step in self.steps
                ]
                arrow_type, dtype, shape = pa.string(), "string", (1,)
                encoding = None
            else:
                converted = [_lerobot_value(step.modalities[name]) for step in self.steps]
                values, encoding = [item[0] for item in converted], converted[0][1]
                if any(item[1] != encoding for item in converted):
                    raise export_error(
                        "LEROBOT_MODALITY_SCHEMA_MISMATCH",
                        "Feature encoding changed within the export.",
                    )
                arrow_type, dtype, shape = _feature_type(values[0], pa)
                if dtype == "float64" or target in {"action", "observation.state"}:
                    dtype = "float32"
                    arrow_type = pa.float32()
                    for size in reversed(shape):
                        arrow_type = pa.list_(arrow_type, size)
            types[target] = arrow_type
            # Arrow enforces the declared shape; float32 values below are also
            # the exact values used for exported normalization statistics.
            columns[target] = pa.array(values, type=arrow_type).to_pylist()
            features[target] = {
                "dtype": dtype,
                "shape": list(shape or (1,)),
                "names": (encoding or {}).get("names"),
                **({"units": encoding["units"]} if encoding and "units" in encoding else {}),
            }
            declarations = [m.get("source_features", {}).get(target) for m in self.metadata]
            if any(declarations):
                if any(d != declarations[0] for d in declarations):
                    raise export_error(
                        "LEROBOT_MODALITY_SCHEMA_MISMATCH",
                        "Source feature definitions differ between episodes.",
                    )
                declaration = declarations[0]
                if (
                    declaration["shape"] != features[target]["shape"]
                    or declaration["names"] != features[target]["names"]
                ):
                    raise export_error(
                        "LEROBOT_MODALITY_SCHEMA_MISMATCH",
                        "Numeric axis identities changed during processing.",
                    )
                features[target].update(declaration)
            if encoding:
                encodings[target] = encoding

        scalar = {
            "timestamp": pa.float32(),
            "frame_index": pa.int64(),
            "episode_index": pa.int64(),
            "index": pa.int64(),
            "task_index": pa.int64(),
            "hc.rollout_id": pa.string(),
            "hc.source_step_index": pa.int64(),
            "hc.source_timestamp_ns": pa.int64(),
            "hc.sample_valid": pa.bool_(),
            "hc.tag_ids": pa.string(),
            "hc.tag_labels": pa.string(),
        }
        for target in self.mapping.values():
            scalar.update(
                {
                    f"hc.source_timestamps_ns.{target}": pa.string(),
                    f"hc.time_error_ns.{target}": pa.int64(),
                    f"hc.valid.{target}": pa.bool_(),
                    f"hc.repeated.{target}": pa.bool_(),
                }
            )
        for key, dtype in scalar.items():
            types[key], columns[key] = dtype, []
            features[key] = {
                "dtype": "float32"
                if key == "timestamp"
                else "bool"
                if pa.types.is_boolean(dtype)
                else "int64"
                if pa.types.is_integer(dtype)
                else "string",
                "shape": [1],
                "names": None,
            }

        tasks = list(dict.fromkeys(item["task"] for item in self.metadata))
        episode_rows: list[dict[str, Any]] = []
        files: dict[str, bytes] = {}
        video_statistics: dict[str, list[dict[str, Any]]] = {}
        media_receipts: list[dict[str, Any]] = []
        offset = 0
        with tempfile.TemporaryDirectory(prefix="hc-lerobot-export-") as directory:
            root = Path(directory)
            for index, (rollout, selected, metadata) in enumerate(
                zip(self.manifest.rollouts, self.episodes, self.metadata, strict=True)
            ):
                episode: dict[str, Any] = {
                    "episode_index": index,
                    "tasks": [metadata["task"]],
                    "length": len(selected),
                    "data/chunk_index": 0,
                    "data/file_index": 0,
                    "meta/episodes/chunk_index": 0,
                    "meta/episodes/file_index": 0,
                    "dataset_from_index": offset,
                    "dataset_to_index": offset + len(selected),
                }
                tags = self.annotations["episodes"][index]["tags"]
                for frame_index, step in enumerate(selected):
                    active = [
                        tag
                        for tag in tags
                        if tag["start_step"] <= step.step_index < tag["end_step"]
                    ]
                    frame_values = {
                        "timestamp": frame_index / self.fps,
                        "frame_index": frame_index,
                        "episode_index": index,
                        "index": offset + frame_index,
                        "task_index": tasks.index(metadata["task"]),
                        "hc.rollout_id": step.rollout_id,
                        "hc.source_step_index": step.step_index,
                        "hc.source_timestamp_ns": step.timestamp_ns,
                        "hc.sample_valid": step.sample_valid,
                        "hc.tag_ids": canonical_json_bytes(
                            [tag["annotation_id"] for tag in active]
                        ).decode(),
                        "hc.tag_labels": canonical_json_bytes(
                            [tag.get("label") or tag["tag_id"] for tag in active]
                        ).decode(),
                    }
                    for name, target in self.mapping.items():
                        frame_values.update(
                            {
                                f"hc.source_timestamps_ns.{target}": canonical_json_bytes(
                                    step.source_timestamps_ns.get(name, ())
                                ).decode(),
                                f"hc.time_error_ns.{target}": step.time_error_ns.get(name)
                                if step.time_error_ns.get(name) is not None
                                else -1,
                                f"hc.valid.{target}": step.valid.get(name, True),
                                f"hc.repeated.{target}": step.repeated.get(name, False),
                            }
                        )
                    for key, value in frame_values.items():
                        columns[key].append(value)
                for name, target in self.mapping.items():
                    if not _camera(name):
                        continue
                    assert self.assets is not None
                    references = [
                        AlignedMediaFrameReferenceV1.model_validate(step.modalities[name])
                        for step in selected
                    ]
                    relative = VIDEO_PATH.format(
                        video_key=target, chunk_index=index // 1000, file_index=index % 1000
                    )
                    output = root / "camera.mp4"
                    feature, stats, receipts = materialize_video(
                        self.assets, self.manifest, rollout, references, output, fps=self.fps
                    )
                    if target in features and features[target] != feature:
                        raise export_error(
                            "LEROBOT_MODALITY_SCHEMA_MISMATCH",
                            "Camera format changed between Episodes.",
                        )
                    features[target] = feature
                    video_statistics.setdefault(target, []).append(stats)
                    files[relative] = output.read_bytes()
                    media_receipts.append(
                        {
                            "episode_index": index,
                            "camera": target,
                            "path": relative,
                            "sources": receipts,
                        }
                    )
                    for suffix, value in {
                        "chunk_index": index // 1000,
                        "file_index": index % 1000,
                        "from_timestamp": 0.0,
                        "to_timestamp": len(selected) / self.fps,
                    }.items():
                        episode[f"videos/{target}/{suffix}"] = value
                    for key, value in stats.items():
                        episode[f"stats/{target}/{key}"] = value
                for key in columns:
                    if features[key]["dtype"] not in {"string", "binary"}:
                        values = pa.array(
                            columns[key][offset : offset + len(selected)], type=types[key]
                        ).to_pylist()
                        for stat, value in _numeric_stats(values).items():
                            episode[f"stats/{key}/{stat}"] = value
                episode_rows.append(episode)
                offset += len(selected)

        table = pa.Table.from_arrays(
            [pa.array(columns[key], type=types[key]) for key in columns], names=list(columns)
        )
        stats = {
            key: _numeric_stats(table[key].to_pylist())
            for key in columns
            if features[key]["dtype"] not in {"string", "binary"}
        }
        stats.update({key: _video_stats(items) for key, items in video_statistics.items()})
        tasks_table = pa.table(
            {"task_index": pa.array(range(len(tasks)), type=pa.int64()), "task": tasks}
        )
        pandas_metadata = {
            "index_columns": ["task"],
            "column_indexes": [],
            "columns": [
                {
                    "name": "task_index",
                    "field_name": "task_index",
                    "pandas_type": "int64",
                    "numpy_type": "int64",
                    "metadata": None,
                },
                {
                    "name": "task",
                    "field_name": "task",
                    "pandas_type": "unicode",
                    "numpy_type": "object",
                    "metadata": None,
                },
            ],
            "creator": {"library": "hc-data-platform", "version": "2"},
            "pandas_version": "2.0.0",
        }
        tasks_table = tasks_table.replace_schema_metadata(
            {b"pandas": canonical_json_bytes(pandas_metadata)}
        )
        robot_types = set(item["robot_type"] for item in self.metadata)
        info = {
            "codebase_version": "v3.0",
            "fps": self.fps,
            "features": features,
            "total_episodes": len(self.episodes),
            "total_frames": len(self.steps),
            "total_tasks": len(tasks),
            "chunks_size": 1000,
            "data_files_size_in_mb": 100,
            "video_files_size_in_mb": 500,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": VIDEO_PATH if video_statistics else None,
            "robot_type": next(iter(robot_types)) if len(robot_types) == 1 else "mixed",
            "hc.export_revision": EXPORT_REVISION,
            "hc.feature_mapping": self.mapping,
            "hc.structured_features": encodings,
            "hc.annotations_path": "meta/annotations.json",
            "splits": {"train": f"0:{len(self.episodes)}"},
        }
        files.update(
            {
                DATA_PATH: _parquet_bytes(table, pq),
                EPISODES_PATH: _parquet_bytes(pa.Table.from_pylist(episode_rows), pq),
                "meta/tasks.parquet": _parquet_bytes(tasks_table, pq),
                "meta/info.json": canonical_json_bytes(info),
                "meta/stats.json": canonical_json_bytes(stats),
                "meta/annotations.json": canonical_json_bytes(self.annotations),
            }
        )
        files["hc-publication-manifest.json"] = canonical_json_bytes(
            {
                "schema_version": "hc-lerobot-export/v2",
                "export_revision": EXPORT_REVISION,
                "publication_content_hash": self.manifest.content_hash,
                "media": media_receipts,
                "rollouts": [item.model_dump(mode="json") for item in self.manifest.rollouts],
                "files": {
                    name: {"sha256": hashlib.sha256(body).hexdigest(), "size": len(body)}
                    for name, body in files.items()
                },
            }
        )
        return _zip_files(files)

    def validate(self, content: bytes) -> None:
        import io

        from .exporters import _lerobot_value, _require_arrow

        pa, pq = _require_arrow()
        try:
            with (
                zipfile.ZipFile(io.BytesIO(content)) as archive,
                tempfile.TemporaryDirectory(prefix="hc-lerobot-check-") as directory,
            ):
                info = json.loads(archive.read("meta/info.json"))
                receipt = json.loads(archive.read("hc-publication-manifest.json"))
                annotations = json.loads(archive.read("meta/annotations.json"))
                stats = json.loads(archive.read("meta/stats.json"))
                if (
                    info["hc.export_revision"] != EXPORT_REVISION
                    or info["codebase_version"] != "v3.0"
                    or info["fps"] != self.fps
                    or info["total_frames"] != len(self.steps)
                    or info["total_episodes"] != len(self.episodes)
                    or receipt["publication_content_hash"] != self.manifest.content_hash
                    or annotations != self.annotations
                    or info["hc.feature_mapping"] != self.mapping
                    or any("/" in name for name in info["features"])
                ):
                    raise ValueError("Export metadata differs from the frozen input")
                if set(archive.namelist()) != set(receipt["files"]) | {
                    "hc-publication-manifest.json"
                }:
                    raise ValueError("Export file inventory is incomplete")
                for name, descriptor in receipt["files"].items():
                    body = archive.read(name)
                    if (
                        len(body) != descriptor["size"]
                        or hashlib.sha256(body).hexdigest() != descriptor["sha256"]
                    ):
                        raise ValueError("Export file checksum differs")
                table = pq.read_table(pa.BufferReader(archive.read(DATA_PATH)))
                episodes = pq.read_table(pa.BufferReader(archive.read(EPISODES_PATH))).to_pylist()
                tasks = pq.read_table(
                    pa.BufferReader(archive.read("meta/tasks.parquet"))
                ).to_pylist()
                rows = table.to_pylist()
                if (
                    len(rows) != len(self.steps)
                    or len(episodes) != len(self.episodes)
                    or len(tasks) != info["total_tasks"]
                ):
                    raise ValueError("Export row counts differ")
                offset = 0
                video_statistics: dict[str, list[dict[str, Any]]] = {}
                for index, (selected, episode) in enumerate(
                    zip(self.episodes, episodes, strict=True)
                ):
                    if (
                        episode["episode_index"] != index
                        or episode["length"] != len(selected)
                        or episode["dataset_from_index"] != offset
                        or episode["dataset_to_index"] != offset + len(selected)
                    ):
                        raise ValueError("Episode offsets differ")
                    for frame, source in enumerate(selected):
                        row = rows[offset + frame]
                        active = [
                            tag
                            for tag in self.annotations["episodes"][index]["tags"]
                            if tag["start_step"] <= source.step_index < tag["end_step"]
                        ]
                        if (
                            row["index"] != offset + frame
                            or row["frame_index"] != frame
                            or row["episode_index"] != index
                            or abs(row["timestamp"] - frame / self.fps) > 1e-4
                            or row["hc.source_step_index"] != source.step_index
                            or row["hc.source_timestamp_ns"] != source.timestamp_ns
                            or tasks[row["task_index"]]["task"] != self.metadata[index]["task"]
                            or json.loads(row["hc.tag_ids"])
                            != [tag["annotation_id"] for tag in active]
                            or json.loads(row["hc.tag_labels"])
                            != [tag.get("label") or tag["tag_id"] for tag in active]
                        ):
                            raise ValueError("Export frame mapping differs")
                        for name, target in self.mapping.items():
                            if _camera(name):
                                if (
                                    json.loads(row[f"hc.source_ref.{target}"])
                                    != source.modalities[name]
                                ):
                                    raise ValueError("Camera source mapping differs")
                            else:
                                expected = pa.scalar(
                                    _lerobot_value(source.modalities[name])[0],
                                    type=table[target].type,
                                ).as_py()
                                if row[target] != expected:
                                    raise ValueError("Export numeric values differ")
                    for name, target in self.mapping.items():
                        if not _camera(name):
                            continue
                        relative = info["video_path"].format(
                            video_key=target,
                            chunk_index=episode[f"videos/{target}/chunk_index"],
                            file_index=episode[f"videos/{target}/file_index"],
                        )
                        path = Path(directory) / "camera.mp4"
                        path.write_bytes(archive.read(relative))
                        feature, video_stats = inspect_video(
                            path, frame_count=len(selected), fps=self.fps
                        )
                        if (
                            feature != info["features"][target]
                            or episode[f"videos/{target}/from_timestamp"] != 0
                            or abs(
                                episode[f"videos/{target}/to_timestamp"] - len(selected) / self.fps
                            )
                            > 1e-6
                        ):
                            raise ValueError("Export video schema/timeline differs")
                        video_statistics.setdefault(target, []).append(video_stats)
                        for stat, value in video_stats.items():
                            if episode[f"stats/{target}/{stat}"] != value:
                                raise ValueError("Episode video statistics differ")
                    for key, feature in info["features"].items():
                        if feature["dtype"] in {"string", "binary", "video"}:
                            continue
                        values = table[key].slice(offset, len(selected)).to_pylist()
                        for stat, value in _numeric_stats(values).items():
                            if episode[f"stats/{key}/{stat}"] != value:
                                raise ValueError("Episode numeric statistics differ")
                    offset += len(selected)
                expected_stats = {
                    key: _numeric_stats(table[key].to_pylist())
                    for key, feature in info["features"].items()
                    if feature["dtype"] not in {"string", "binary", "video"}
                }
                expected_stats.update(
                    {key: _video_stats(items) for key, items in video_statistics.items()}
                )
                if stats != expected_stats:
                    raise ValueError("Export normalization statistics differ")
        except (KeyError, ValueError, OSError, zipfile.BadZipFile) as exc:
            raise export_error(
                "LEROBOT_VALIDATION_FAILED", f"LeRobot archive validation failed: {exc}"
            ) from exc
