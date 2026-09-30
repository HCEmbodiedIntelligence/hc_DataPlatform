"""Reassemble immutable independent Episodes without encoding their media again.

This is an export writer, not a primary-store commit or an authorization boundary.
Callers must freeze authorized package/revision selections and queue the work.
"""

import copy
import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH, VIDEO_PATH
from hc_data_platform.publishing.lerobot_format import make_task_table

from .annotated_delivery import write_annotated_delivery
from .models import FileEntry, canonical, digest
from .postgres import assert_scope
from .writer import Moments, write_json

PROCESSOR = "hc-lerobot-collection/1"
DATA_TEMPLATE = "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet"
ANNOTATION_FEATURES = {
    "subtask_index": {"dtype": "int64", "shape": [1], "names": None},
    "hc.annotation.excluded": {"dtype": "bool", "shape": [1], "names": None},
}


class CollectionIncompatible(ValueError):
    def __init__(self, field):
        self.field = field
        super().__init__(f"collection members have incompatible {field}; no implicit conversion")


@dataclass(frozen=True)
class CollectionSource:
    package: object
    annotation_file: object = None
    track: object = None
    review_document: object = None

    def __post_init__(self):
        if (self.annotation_file is None) != (self.track is None):
            raise ValueError("annotation collection selection needs a revision and explicit track")
        if self.annotation_file is None and self.review_document is not None:
            raise ValueError("a review cannot be selected without its annotation revision")


class LocalDerivative:
    def __init__(self, root, parent, manifest):
        self.root, self.receipt, self.annotation = root, parent.receipt, manifest
        path = root / "hc/delivery_manifest.json"
        self.manifest = SimpleNamespace(
            files=(
                *manifest.files,
                FileEntry(
                    path="hc/delivery_manifest.json",
                    size=path.stat().st_size,
                    sha256=file_hash(path),
                    media_type="application/json",
                ),
            )
        )
        self.entries = {entry.path: entry for entry in self.manifest.files}

    @contextmanager
    def file(self, relative):
        entry = self.entries[relative]
        path = self.root / relative
        if path.stat().st_size != entry.size or file_hash(path) != entry.sha256:
            raise ValueError("selected annotation artifact differs from its frozen inventory")
        yield path

    def document(self, relative):
        with self.file(relative) as path:
            return json.loads(path.read_bytes())


@contextmanager
def prepared(selection, parent_directory, checkpoint):
    if selection.annotation_file is None:
        yield selection.package
        return
    with TemporaryDirectory(prefix="collection-annotation-", dir=parent_directory) as temporary:
        root = Path(temporary) / "episode"
        manifest, _ = write_annotated_delivery(
            root,
            selection.package,
            selection.annotation_file,
            selection.track,
            checkpoint=checkpoint,
            review_document=selection.review_document,
        )
        yield LocalDerivative(root, selection.package, manifest)


def selected_info(package, modalities):
    info = copy.deepcopy(package.document("meta/info.json"))
    if info["codebase_version"] != "v3.0" or info["total_episodes"] != 1:
        raise CollectionIncompatible("independent LeRobot v3 package layout")
    depth = {
        key for key, value in info["features"].items() if value.get("info", {}).get("is_depth_map")
    }
    excluded = depth if modalities == "rgb" else set()
    # HC columns for excluded streams cannot masquerade as selected modalities.
    excluded_columns = {
        key
        for key in info["features"]
        if key in excluded or any(key.startswith("hc.") and key.endswith("." + d) for d in excluded)
    }
    for key in excluded_columns:
        del info["features"][key]
    for key in ("hc.feature_mapping", "hc.structured_features", "hc.source_shapes"):
        info[key] = {
            k: v
            for k, v in info.get(key, {}).items()
            if (v if key == "hc.feature_mapping" else k) not in excluded
        }
    return info, depth, excluded_columns


def contract(info):
    fields = (
        "fps",
        "features",
        "robot_type",
        "hc.feature_mapping",
        "hc.structured_features",
        "hc.source_shapes",
    )
    result = {key: copy.deepcopy(info.get(key)) for key in fields}
    # JSON may spell the same frequency as 30 or 30.0 across ingress writers.
    # Equality and the frozen contract digest must use the same representation.
    result["fps"] = float(result["fps"])
    for feature in result["features"].values():
        feature.get("info", {}).pop("hc.stats_valid_pixels", None)
        feature.get("info", {}).pop("hc.stats_status", None)
        if "video.fps" in feature.get("info", {}):
            feature["info"]["video.fps"] = float(feature["info"]["video.fps"])
    return result


def differing_field(left, right, path):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys()):
            current = f"{path}.{key}" if path else key
            if key not in left or key not in right:
                return current
            if left[key] != right[key]:
                return differing_field(left[key], right[key], current)
    return path


def preflight(selections, modalities):
    if modalities not in {"rgb", "rgbd"} or not 1 <= len(selections) <= 100:
        raise ValueError("collection requires 1..100 Episodes and explicit rgb/rgbd selection")
    sources = sorted(selections, key=lambda s: s.package.receipt.data.data_version_id)
    data = [s.package.receipt.data for s in sources]
    if len({d.identity.scope for d in data}) != 1:
        raise PermissionError("collection must stay in one tenant/project/region")
    if len({d.identity.key for d in data}) != len(data):
        raise ValueError("select at most one version of each Episode")
    baseline, profiles, tasks = None, None, set()
    for selection in sources:
        package = selection.package
        assert_scope(package.receipt.data.identity)
        info, depth, _ = selected_info(package, modalities)
        current = contract(info)
        if baseline is None:
            baseline = current
        for field, value in current.items():
            if value != baseline[field]:
                raise CollectionIncompatible(differing_field(baseline[field], value, field))
        evidence = package.document("hc/modalities.json").get("depth", {})
        if set(evidence) != depth:
            raise CollectionIncompatible("depth evidence inventory")
        selected_profiles = {
            feature: package.document(item["evidence_path"] + "/profile.json")
            for feature, item in evidence.items()
            if modalities == "rgbd"
        }
        if profiles is None:
            profiles = selected_profiles
        if selected_profiles != profiles:
            raise CollectionIncompatible(
                differing_field(profiles, selected_profiles, "depth_profile")
            )
        with package.file("meta/tasks.parquet") as path:
            rows = pq.read_table(path).to_pylist()
        if not rows or len({row["task_index"] for row in rows}) != len(rows):
            raise CollectionIncompatible("task table")
        tasks.update(row["task"] for row in rows)
    return sources, sorted(tasks)


def copy_member(root, view, entry, relative, checkpoint):
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    with view.file(entry.path) as origin, origin.open("rb") as source, target.open("xb") as output:
        for block in iter(lambda: source.read(1024**2), b""):
            checkpoint()
            output.write(block)
    if target.stat().st_size != entry.size or file_hash(target) != entry.sha256:
        raise ValueError("copied collection member differs from its frozen inventory")


def pooled_stats(items):
    """Pool moments by actual samples; depth excludes invalid pixels, count stays frames."""
    weight = sum(w for _, w in items)
    if weight <= 0:
        raise ValueError("cannot pool empty media statistics")
    mean = sum(np.asarray(s["mean"]) * w for s, w in items) / weight
    variance = (
        sum((np.square(s["std"]) + np.square(np.asarray(s["mean"]) - mean)) * w for s, w in items)
        / weight
    )
    return {
        "count": [sum(s["count"][0] for s, _ in items)],
        "min": np.minimum.reduce([s["min"] for s, _ in items]).tolist(),
        "max": np.maximum.reduce([s["max"] for s, _ in items]).tolist(),
        "mean": mean.tolist(),
        "std": np.sqrt(np.maximum(variance, 0)).tolist(),
    }


def write_collection(root, selections, *, modalities, checkpoint=lambda: None):
    """Rebuild indexes, task/subtask tables, metadata, moments and media offsets.

    Per-Episode videos remain separate MP4s with their original zero-based PTS.
    No frame selection, resampling, requantization or transcoding occurs here.
    """
    sources, tasks = preflight(selections, modalities)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    info, _, _ = selected_info(sources[0].package, modalities)
    annotated = any(s.annotation_file is not None for s in sources)
    if annotated:
        info["features"].update(ANNOTATION_FEATURES)
    numeric = {
        k: Moments() for k, v in info["features"].items() if v["dtype"] not in {"video", "string"}
    }
    videos = {k: [] for k, v in info["features"].items() if v["dtype"] == "video"}
    episodes, members, subtask_rows = [], [], []
    offset = subtask_offset = 0
    mapping_schema = pa.schema(
        [
            ("index", pa.int64()),
            ("episode_index", pa.int64()),
            ("frame_index", pa.int64()),
            ("parent_data_version_id", pa.string()),
            ("parent_frame_index", pa.int64()),
            ("parent_timestamp_ns", pa.int64()),
        ]
    )
    (root / "hc").mkdir()
    with pq.ParquetWriter(root / "hc/collection_frames.parquet", mapping_schema) as mapping:
        for index, selection in enumerate(sources):
            checkpoint()
            with prepared(selection, root.parent, checkpoint) as view:
                member, episode, segments = write_member(
                    root,
                    index,
                    offset,
                    subtask_offset,
                    selection,
                    view,
                    modalities,
                    tasks,
                    info,
                    numeric,
                    videos,
                    mapping,
                    annotated,
                    checkpoint,
                )
            members.append(member)
            episodes.append(episode)
            subtask_rows.extend(segments)
            offset += member["data"]["frame_count"]
            subtask_offset += len(segments)
    statistics = {key: moments.document() for key, moments in numeric.items()}
    for key, items in videos.items():
        if items:
            statistics[key] = pooled_stats(items)
            statistics[key]["count"] = [offset]
        if info["features"][key].get("info", {}).get("is_depth_map"):
            info["features"][key]["info"]["hc.stats_valid_pixels"] = sum(w for _, w in items)
            if items:
                info["features"][key]["info"].pop("hc.stats_status", None)
            else:
                info["features"][key]["info"]["hc.stats_status"] = "NO_VALID_PIXELS"
    info.update(
        {
            "total_episodes": len(sources),
            "total_frames": offset,
            "total_tasks": len(tasks),
            "splits": {"train": f"0:{len(sources)}"},
            "data_path": DATA_TEMPLATE,
            "video_path": VIDEO_PATH if videos else None,
            "hc.package_version": PROCESSOR,
            "hc.collection": "hc/collection.json",
        }
    )
    (root / EPISODES_PATH).parent.mkdir(parents=True, exist_ok=True)
    # Arrow otherwise infers only the first row's keys, losing later measured
    # statistics when the first Episode has no valid depth. Null means absent.
    columns = dict.fromkeys(key for episode in episodes for key in episode)
    pq.write_table(
        pa.Table.from_pylist([{key: episode.get(key) for key in columns} for episode in episodes]),
        root / EPISODES_PATH,
    )
    pq.write_table(make_task_table(tasks), root / "meta/tasks.parquet")
    if annotated:
        (root / "hc/subtasks.jsonl").write_bytes(
            b"".join(canonical(r) + b"\n" for r in subtask_rows)
        )
    for path, document in {
        "meta/info.json": info,
        "meta/stats.json": statistics,
        "hc/collection.json": {
            "format": PROCESSOR,
            "members": members,
            "modalities": modalities,
            "is_reassembled_collection": True,
            "source_frames": "hc/collection_frames.parquet",
            "subtask_index_scope": "collection" if annotated else None,
            "media_policy": "verified_byte_copy",
            "transcoded": False,
            "requantized": False,
            "model_depth_training_support": "NOT_VALIDATED",
        },
    }.items():
        write_json(root, path, document)
    manifest = {
        "format": PROCESSOR,
        "reader_version": "lerobot/0.6.1",
        "modalities": modalities,
        "members": members,
        "total_frames": offset,
        "files": [
            FileEntry(
                path=p.relative_to(root).as_posix(),
                size=p.stat().st_size,
                sha256=file_hash(p),
                media_type="application/octet-stream",
            ).model_dump(mode="json")
            for p in sorted(root.rglob("*"))
            if p.is_file()
        ],
    }
    write_json(root, "hc/collection_manifest.json", manifest)
    return manifest, digest(manifest)


def write_member(
    root,
    index,
    offset,
    subtask_offset,
    selection,
    view,
    modalities,
    tasks,
    info,
    numeric,
    videos,
    mapping,
    annotated,
    checkpoint,
):
    parent = selection.package
    data = parent.receipt.data
    source_info, depth, excluded_columns = selected_info(parent, modalities)
    excluded = depth if modalities == "rgb" else set()
    attachment = f"hc/episodes/{index:06d}"
    local_info = view.document("meta/info.json")
    depth_paths = view.document("hc/modalities.json").get("depth", {})
    members = {e.path: e for e in view.manifest.files}
    for entry in view.manifest.files:
        if entry.path.startswith("videos/") or entry.path.startswith("data/"):
            continue
        if any(entry.path.startswith(depth_paths[d]["evidence_path"] + "/") for d in excluded):
            continue
        copy_member(root, view, entry, f"{attachment}/{entry.path}", checkpoint)
    for name, document in (("parent_manifest", parent.manifest), ("parent_commit", parent.receipt)):
        write_json(root, f"{attachment}/{name}.json", document.model_dump(mode="json"))
    with view.file("meta/tasks.parquet") as path:
        task_map = {
            r["task_index"]: tasks.index(r["task"]) for r in pq.read_table(path).to_pylist()
        }
    segments = []
    if selection.annotation_file is not None:
        with view.file("hc/subtasks.jsonl") as path:
            for line in path.read_bytes().splitlines():
                original = json.loads(line)
                segments.append(
                    {
                        **original,
                        "episode_index": index,
                        "source_subtask_index": original["subtask_index"],
                        "subtask_index": subtask_offset + original["subtask_index"],
                    }
                )
    episode_stats = {key: Moments() for key in numeric}
    relative_data = DATA_TEMPLATE.format(chunk_index=index // 1000, file_index=index % 1000)
    destination = root / relative_data
    destination.parent.mkdir(parents=True, exist_ok=True)
    seen, used_tasks = 0, set()
    with view.file(DATA_PATH) as path:
        parquet = pq.ParquetFile(path)
        schema = pa.schema([f for f in parquet.schema_arrow if f.name not in excluded_columns])
        if annotated and selection.annotation_file is None:
            schema = schema.append(pa.field("subtask_index", pa.int64())).append(
                pa.field("hc.annotation.excluded", pa.bool_())
            )
        with pq.ParquetWriter(destination, schema) as output:
            for batch in parquet.iter_batches(batch_size=1024):
                checkpoint()
                table = pa.Table.from_batches([batch]).select(
                    schema.names
                    if not (annotated and selection.annotation_file is None)
                    else [n for n in schema.names if n not in ANNOTATION_FEATURES]
                )
                frames = list(range(seen, seen + len(batch)))
                if table["frame_index"].to_pylist() != frames or table[
                    "episode_index"
                ].to_pylist() != [0] * len(batch):
                    raise ValueError("collection source is not an ordered independent Episode")
                replacements = {
                    "index": [offset + n for n in frames],
                    "episode_index": [index] * len(batch),
                    "task_index": [task_map[t] for t in table["task_index"].to_pylist()],
                }
                used_tasks.update(replacements["task_index"])
                if annotated:
                    original = (
                        table["subtask_index"].to_pylist()
                        if "subtask_index" in table.column_names
                        else [-1] * len(batch)
                    )
                    if any(s < -1 or s >= len(segments) for s in original):
                        raise ValueError("subtask index differs from selected track")
                    replacements["subtask_index"] = [
                        s + subtask_offset if s >= 0 else -1 for s in original
                    ]
                    if "hc.annotation.excluded" not in table.column_names:
                        replacements["hc.annotation.excluded"] = [False] * len(batch)
                for key, values in replacements.items():
                    values = pa.array(values, type=schema.field(key).type)
                    table = (
                        table.set_column(table.column_names.index(key), key, values)
                        if key in table.column_names
                        else table.append_column(key, values)
                    )
                output.write_table(table)
                for key in numeric:
                    values = table[key].to_pylist()
                    numeric[key].add(values)
                    episode_stats[key].add(values)
                mapping.write_table(
                    pa.Table.from_arrays(
                        [
                            table["index"],
                            table["episode_index"],
                            table["frame_index"],
                            pa.array([data.data_version_id] * len(batch)),
                            table["frame_index"],
                            table["hc.timestamp_ns"],
                        ],
                        schema=mapping.schema,
                    )
                )
                seen += len(batch)
    if seen != data.frame_count:
        raise ValueError("collection source frame count differs from its receipt")
    episode = {
        "episode_index": index,
        "tasks": [tasks[t] for t in sorted(used_tasks)],
        "length": seen,
        "data/chunk_index": index // 1000,
        "data/file_index": index % 1000,
        "meta/episodes/chunk_index": 0,
        "meta/episodes/file_index": 0,
        "dataset_from_index": offset,
        "dataset_to_index": offset + seen,
    }
    statistics = {key: moments.document() for key, moments in episode_stats.items()}
    source_stats, media = view.document("meta/stats.json"), {}
    for feature in videos:
        original = VIDEO_PATH.format(video_key=feature, chunk_index=0, file_index=0)
        relative = VIDEO_PATH.format(
            video_key=feature, chunk_index=index // 1000, file_index=index % 1000
        )
        entry = members[original]
        copy_member(root, view, entry, relative, checkpoint)
        media_info = local_info["features"][feature].get("info", {})
        weight = media_info.get("hc.stats_valid_pixels", seen)
        if feature in source_stats:
            if weight <= 0:
                raise ValueError("media statistics claim measurements without valid pixels")
            statistics[feature] = source_stats[feature]
            videos[feature].append((statistics[feature], weight))
        elif not (
            media_info.get("is_depth_map")
            and media_info.get("hc.stats_status") == "NO_VALID_PIXELS"
            and weight == 0
        ):
            raise ValueError("media statistics are missing without an explicit absence contract")
        media[feature] = {
            "path": relative,
            "source_path": original,
            "sha256": entry.sha256,
            "evidence_path": f"{attachment}/{depth_paths[feature]['evidence_path']}"
            if feature in depth
            else None,
        }
        for key, value in {
            "chunk_index": index // 1000,
            "file_index": index % 1000,
            "from_timestamp": 0.0,
            "to_timestamp": seen / info["fps"],
        }.items():
            episode[f"videos/{feature}/{key}"] = value
    for feature, values in statistics.items():
        for key, value in values.items():
            episode[f"stats/{feature}/{key}"] = value
    annotation = getattr(view, "annotation", None)
    member = {
        "episode_index": index,
        "data": data.model_dump(mode="json"),
        "source_manifest_sha256": parent.receipt.manifest_sha256,
        "dataset_from_index": offset,
        "dataset_to_index": offset + seen,
        "data_path": relative_data,
        "attachments_root": attachment,
        "media": media,
        "excluded_depth_features": sorted(excluded),
        "annotation": annotation.subtasks.model_dump(mode="json") if annotation else None,
        "annotation_review": annotation.review.model_dump(mode="json") if annotation else None,
        "camera_mapping": {
            name: feature
            for name, feature in source_info["hc.feature_mapping"].items()
            if feature in videos
        },
    }
    return member, episode, segments
