"""Bounded-memory single-Episode v3 writer, sharing existing exporter contracts."""

from __future__ import annotations

import io
import itertools
import shutil
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from hc_data_platform.publishing.export_video import inspect_depth_video, inspect_video
from hc_data_platform.publishing.exporters import _feature_type, _lerobot_value
from hc_data_platform.publishing.lerobot_export import (
    DATA_PATH,
    EPISODES_PATH,
    VIDEO_PATH,
    _feature_names,
)

from .models import PackageInput, canonical
from .postgres import assert_scope
from .prepared_assets import PreparedAssets
from .validation import parquet_rows, validate_package


class Moments:
    def __init__(self):
        self.count = 0
        self.mean = self.m2 = self.minimum = self.maximum = None

    def add(self, values):
        array = np.asarray(values, dtype=np.float64)
        if array.ndim == 1:
            array = array[:, None]
        if not np.isfinite(array).all():
            raise ValueError("numeric feature contains nonfinite values")
        count, mean = len(array), array.mean(axis=0)
        m2 = np.square(array - mean).sum(axis=0)
        minimum, maximum = array.min(axis=0), array.max(axis=0)
        if not self.count:
            self.count, self.mean, self.m2 = count, mean, m2
            self.minimum, self.maximum = minimum, maximum
            return
        delta, total = mean - self.mean, self.count + count
        self.m2 += m2 + np.square(delta) * self.count * count / total
        self.mean += delta * count / total
        self.minimum = np.minimum(self.minimum, minimum)
        self.maximum = np.maximum(self.maximum, maximum)
        self.count = total

    def document(self):
        return {
            "count": [self.count],
            "min": self.minimum.tolist(),
            "max": self.maximum.tolist(),
            "mean": self.mean.tolist(),
            "std": np.sqrt(self.m2 / self.count).tolist(),
        }


def write_json(root, path, value):
    (root / path).parent.mkdir(parents=True, exist_ok=True)
    (root / path).write_bytes(canonical(value))


def write_episode(
    root: Path, source: PackageInput, steps, assets: PreparedAssets, *, fps: float, metadata: dict
):
    assert_scope(source.identity)
    if not np.isfinite(fps) or fps <= 0:
        raise ValueError("Episode frequency must be finite and positive")
    iterator = iter(steps)
    first = next(iterator, None)
    if first is None:
        raise ValueError("cannot write an empty Episode")
    mapping = _feature_names(sorted(first.modalities), camera_topics=metadata.get("camera_topics"))
    cameras = set(assets.videos)
    if not cameras.issubset(mapping.values()):
        raise ValueError("prepared camera lacks an aligned modality")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    (root / DATA_PATH).parent.mkdir(parents=True)
    (root / EPISODES_PATH).parent.mkdir(parents=True)
    (root / "hc").mkdir()
    features, types, encodings, source_types, source_shapes = {}, {}, {}, {}, {}
    for name, target in mapping.items():
        value, encoding = _lerobot_value(first.modalities[name])
        if target in cameras:
            column, value, encoding = (
                "hc.source_ref." + target,
                canonical(first.modalities[name]).decode(),
                None,
            )
        else:
            column = target
        arrow_type, dtype, shape = _feature_type(value, pa)
        source_types[target], source_shapes[target] = arrow_type, list(shape)
        # Official LeRobot treats shape=(1,) as a scalar Parquet column. Keep
        # the source shape separately so platform reads reconstruct [value].
        if shape == (1,):
            arrow_type = arrow_type.value_type
        types[column] = arrow_type
        # Never inherit the legacy exporter's blanket float32 downcast.
        features[column] = {
            "dtype": dtype,
            "shape": list(shape or (1,)),
            "names": (encoding or {}).get("names"),
        }
        if encoding:
            encodings[target] = encoding
            if "units" in encoding:
                features[column]["units"] = encoding["units"]
        for suffix, kind, type_ in (
            ("source_timestamps_ns", "string", pa.string()),
            ("time_error_ns", "string", pa.string()),
            ("valid", "bool", pa.bool_()),
            ("repeated", "bool", pa.bool_()),
        ):
            key = f"hc.{suffix}.{target}"
            types[key] = type_
            features[key] = {"dtype": kind, "shape": [1], "names": None}
    for key, dtype, type_ in (
        ("index", "int64", pa.int64()),
        ("frame_index", "int64", pa.int64()),
        ("episode_index", "int64", pa.int64()),
        ("task_index", "int64", pa.int64()),
        ("timestamp", "float32", pa.float32()),
        ("hc.timestamp_ns", "int64", pa.int64()),
        ("hc.sample_valid", "bool", pa.bool_()),
    ):
        if key in types:
            raise ValueError(f"source modality conflicts with standard index: {key}")
        types[key] = type_
        features[key] = {"dtype": dtype, "shape": [1], "names": None}
    schema = pa.schema(list(types.items()))
    frame_schema = pa.schema(
        [
            ("frame_index", pa.int64()),
            ("timestamp_ns", pa.int64()),
            ("source_timestamps_ns", pa.string()),
            ("time_error_ns", pa.string()),
            ("valid", pa.string()),
            ("repeated", pa.string()),
        ]
    )
    moments = {
        key: Moments() for key, f in features.items() if f["dtype"] not in {"string", "binary"}
    }
    count = 0
    pending, source_rows = [], []

    def flush(data_writer, frame_writer):
        table = pa.Table.from_pylist(pending, schema=schema)
        data_writer.write_table(table)
        for key, statistics in moments.items():
            statistics.add(table[key].to_pylist())
        frame_writer.write_table(pa.Table.from_pylist(source_rows, schema=frame_schema))
        pending.clear()
        source_rows.clear()

    with (
        pq.ParquetWriter(root / DATA_PATH, schema) as output,
        pq.ParquetWriter(root / "hc/source_frames.parquet", frame_schema) as source_output,
    ):
        for step in itertools.chain((first,), iterator):
            if (
                step.step_index != count
                or step.rollout_id != source.identity.rollout_id
                or set(step.modalities) != set(mapping)
            ):
                raise ValueError("aligned step identity/schema/order differs from frozen input")
            row = {
                "index": count,
                "frame_index": count,
                "episode_index": 0,
                "task_index": 0,
                "timestamp": count / fps,
                "hc.timestamp_ns": step.timestamp_ns,
                "hc.sample_valid": step.sample_valid,
            }
            for name, target in mapping.items():
                if target in cameras:
                    row["hc.source_ref." + target] = canonical(step.modalities[name]).decode()
                else:
                    value, encoding = _lerobot_value(step.modalities[name])
                    if encoding != encodings.get(target):
                        raise ValueError("numeric semantics changed within Episode")
                    if _feature_type(value, pa)[0] != source_types[target]:
                        raise ValueError("numeric dtype/shape changed within Episode")
                    row[target] = value[0] if source_shapes[target] == [1] else value
                # Unknown stays unknown in the trace; never infer validity from absent evidence.
                row[f"hc.source_timestamps_ns.{target}"] = canonical(
                    step.source_timestamps_ns.get(name, ())
                ).decode()
                row[f"hc.time_error_ns.{target}"] = canonical(step.time_error_ns.get(name)).decode()
                row[f"hc.valid.{target}"] = step.valid.get(name, False)
                row[f"hc.repeated.{target}"] = step.repeated.get(name, False)
            pending.append(row)
            source_rows.append(
                {
                    "frame_index": count,
                    "timestamp_ns": step.timestamp_ns,
                    **{
                        key: canonical(getattr(step, key)).decode()
                        for key in ("source_timestamps_ns", "time_error_ns", "valid", "repeated")
                    },
                }
            )
            count += 1
            if len(pending) == 1024:
                flush(output, source_output)
        if pending:
            flush(output, source_output)
    stats = {key: item.document() for key, item in moments.items()}
    task = metadata.get("task") or "unknown"
    episode = {
        "episode_index": 0,
        "tasks": [task],
        "length": count,
        "data/chunk_index": 0,
        "data/file_index": 0,
        "meta/episodes/chunk_index": 0,
        "meta/episodes/file_index": 0,
        "dataset_from_index": 0,
        "dataset_to_index": count,
    }
    depth_paths, media_receipts = {}, []
    for key in sorted(cameras):
        media = assets.video(source.identity, key)
        relative = VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(media.path, path)
        depth_info = media.feature.get("info", {})
        if depth_info.get("is_depth_map"):
            if media.evidence_root is None or not media.evidence_files:
                raise ValueError("encoded depth requires frozen profile/masks/source evidence")
            evidence_path = "hc/depth/" + key
            for file in media.evidence_files:
                target = root / evidence_path / file.path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(media.evidence_root / file.path, target)
            depth_paths[key] = {"evidence_path": evidence_path}

            def valid_masks(mask_path=root / evidence_path / "validity/flags.parquet"):
                for record in parquet_rows(mask_path):
                    with Image.open(io.BytesIO(record["flags_png"])) as image:
                        yield np.asarray(image) & 1 != 0

            feature, statistics = inspect_depth_video(
                path, frame_count=count, fps=fps, info=depth_info, masks=valid_masks()
            )
        else:
            feature, statistics = inspect_video(path, frame_count=count, fps=fps)
        if feature["shape"] != media.feature["shape"]:
            raise ValueError("prepared media dimensions differ from frozen source")
        features[key] = feature
        if statistics:
            stats[key] = statistics
        media_receipts.append(
            {
                "feature": key,
                "source_sha256": media.sha256,
                "operation": "byte_copy",
                "requantized": False,
                "transcoded": False,
            }
        )
        for suffix, value in {
            "chunk_index": 0,
            "file_index": 0,
            "from_timestamp": 0.0,
            "to_timestamp": count / fps,
        }.items():
            episode[f"videos/{key}/{suffix}"] = value
    for key, statistics in stats.items():
        for field, value in statistics.items():
            episode[f"stats/{key}/{field}"] = value
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    from hc_data_platform.publishing.lerobot_format import make_task_table

    pq.write_table(make_task_table([task]), root / "meta/tasks.parquet")
    info = {
        "codebase_version": "v3.0",
        "fps": fps,
        "features": features,
        "total_episodes": 1,
        "total_frames": count,
        "total_tasks": 1,
        "chunks_size": 1000,
        "data_files_size_in_mb": 100,
        "video_files_size_in_mb": 500,
        "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
        "video_path": VIDEO_PATH if cameras else None,
        "splits": {"train": "0:1"},
        "robot_type": metadata.get("robot_type", "unknown"),
        "hc.feature_mapping": mapping,
        "hc.structured_features": encodings,
        "hc.source_shapes": source_shapes,
        "hc.package_version": "hc-episode-package/1",
    }
    for relative, value in {
        "meta/info.json": info,
        "meta/stats.json": stats,
        "hc/source.json": {
            **metadata.get("source", {}),
            "identity": source.identity.model_dump(mode="json"),
            "source_sha256": source.source_sha256,
        },
        "hc/modalities.json": {**metadata.get("modalities", {}), "depth": depth_paths},
        "hc/calibration.json": metadata.get("calibration", {"status": "unknown"}),
        "hc/quality.json": metadata.get("quality", {"status": "UNKNOWN", "rules": []}),
        "hc/conversion_report.json": {
            "processor_version": source.processor_version,
            "media": media_receipts,
            "numeric_policy": "preserve inferred source precision",
            "timestamp_policy": "official float32 seconds; exact hc.timestamp_ns",
            "numeric_stats": "streaming min/max/mean/std; no quantiles claimed",
        },
    }.items():
        write_json(root, relative, value)
    if metadata.get("quality_rules") is not None:
        from hc_data_platform.quality.evidence import QcRuleEvidence
        from hc_data_platform.quality.models import QcReportV1

        evidence = QcRuleEvidence.model_validate(metadata["quality_rules"])
        quality = metadata["quality"]
        report = QcReportV1.model_validate(quality.get("quality_report", quality))
        evidence.validate_report(report)
        if (
            evidence.source_sha256 != source.source_sha256
            or evidence.rollout_id != source.identity.rollout_id
        ):
            raise ValueError("QC rule evidence belongs to another Episode source")
        write_json(root, "hc/quality_rules.json", evidence.model_dump(mode="json"))
    for entry in assets.auxiliary_files:
        # Selected recording provenance is an HC extension. It cannot replace
        # numeric/video/official metadata or the writer's own evidence files.
        from .validation import package_file

        if assets.auxiliary_root is None or not entry.path.startswith("hc/recording/"):
            raise ValueError("prepared auxiliary files require their recording evidence root")
        origin = package_file(assets.auxiliary_root, entry.path)
        from hc_data_platform.publishing.depth_evidence import file_hash

        if origin.stat().st_size != entry.size or file_hash(origin) != entry.sha256:
            raise ValueError("prepared recording evidence differs from its frozen receipt")
        target = root / entry.path
        if target.exists():
            raise ValueError("recording evidence cannot overwrite an Episode file")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(origin, target)
    return validate_package(root, source)
