"""Materialize a revision-bound Tag as a complete, separately committed Episode.

Only authorized package/revision services may supply the inputs. This generator
does not create product rows or edit annotation history or recording slice tables.
"""

import hashlib
import io
import json
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from PIL import Image

from hc_data_platform.annotation.subtasks import SubtaskProjection
from hc_data_platform.continuous_recordings.package import (
    encoding_configuration,
    prepare_rgb_window,
)
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.export_video import inspect_depth_video, inspect_video
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH, VIDEO_PATH
from hc_data_platform.publishing.lerobot_format import make_task_table

from .depth_window import PARENT_TIME_VERSION, prepare_depth_window
from .derivation_models import TagEpisodePlan
from .derivation_quality import AUTO_QUALITY, evaluate_tag_quality, freeze_quality
from .frame_window import DepthWindow
from .models import canonical
from .postgres import PackageConflict, assert_scope
from .prepared_assets import PreparedVideo
from .reader import window_rows
from .schemas import EpisodeSchemas
from .validation import parquet_rows, validate_package
from .writer import Moments, write_json


def derivation_encoding(quality=None):
    encoding = {**encoding_configuration(), "depth_window": PARENT_TIME_VERSION}
    if quality is not None:
        encoding["quality"] = quality
    return encoding


def verify_annotation(package, annotation, snapshot):
    if hashlib.sha256(snapshot.content).hexdigest() != snapshot.file.content_sha256:
        raise PackageConflict("parent annotation bytes differ from their frozen hash")
    projection = SubtaskProjection.freeze(
        json.loads(snapshot.content),
        package.receipt.data,
        snapshot.file.content_sha256,
        annotation.binding.track,
    )
    if (
        projection.binding != annotation.binding
        or snapshot.file.status != "READY"
        or snapshot.file.task_id != projection.binding.task_id
        or snapshot.file.revision != projection.binding.revision
        or len(snapshot.content) != annotation.annotation_size
        or package.receipt.manifest_sha256 != annotation.parent_manifest_sha256
        or len(projection.segments) != 1
    ):
        raise PackageConflict("Tag derivation requires one exact immutable annotation interval")
    return projection.segments[0]


def freeze_tag_episode(
    package, annotation, snapshot, schema, *, display_name=None, quality_contract=AUTO_QUALITY
):
    tag = verify_annotation(package, annotation, snapshot)
    EpisodeSchemas.validate(schema)
    info = package.document("meta/info.json")
    if info["fps"] != schema.frequency_hz:
        raise PackageConflict("parent package frequency differs from its logical schema")
    with package.file(DATA_PATH) as path:

        def stamp(index):
            return next(window_rows(path, index, index + 1, ["hc.timestamp_ns"]))["hc.timestamp_ns"]

        origin, start = stamp(0), stamp(tag.start_step)
        last = stamp(tag.end_step - 1)
        end = stamp(tag.end_step) if tag.end_step < package.receipt.data.frame_count else None
    quality = freeze_quality(
        package, info, start, last, end, tag.end_step - tag.start_step, quality_contract
    )
    return TagEpisodePlan(
        parent=package.receipt.data,
        parent_input=package.manifest.input,
        annotation=annotation,
        schema_snapshot=schema,
        annotation_id=tag.annotation_id,
        start_step=tag.start_step,
        end_step=tag.end_step,
        parent_origin_timestamp_ns=origin,
        start_timestamp_ns=start,
        next_parent_timestamp_ns=end,
        display_name=display_name or tag.label or tag.tag_id,
        encoding=derivation_encoding(quality),
    )


def copy_member(package, path, target, checkpoint):
    target.parent.mkdir(parents=True, exist_ok=True)
    with package.file(path) as source, source.open("rb") as stream, target.open("xb") as output:
        for block in iter(lambda: stream.read(1024**2), b""):
            checkpoint()
            output.write(block)
    entry = package.entries[path]
    if target.stat().st_size != entry.size or file_hash(target) != entry.sha256:
        raise PackageConflict("Tag derivation parent file changed during copying")


def write_tag_episode(root, package, snapshot, plan, *, checkpoint=lambda: None):
    """Write every collected modality, exact Arrow types, parent times and trace."""
    TagEpisodePlan.model_validate(plan.model_dump(mode="json"))
    assert_scope(plan.parent.identity)
    tag = verify_annotation(package, plan.annotation, snapshot)
    if (
        package.receipt.data != plan.parent
        or package.manifest.input != plan.parent_input
        or (tag.start_step, tag.end_step, tag.annotation_id)
        != (plan.start_step, plan.end_step, plan.annotation_id)
        or derivation_encoding(plan.encoding.get("quality")) != plan.encoding
    ):
        raise PackageConflict("Tag derivation input or encoder differs from the frozen plan")
    if (
        freeze_tag_episode(
            package,
            plan.annotation,
            snapshot,
            plan.schema_snapshot,
            display_name=plan.display_name,
            quality_contract=plan.encoding.get("quality"),
        )
        != plan
    ):
        raise PackageConflict("Tag derivation timestamps/schema differ from the verified parent")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    info = package.document("meta/info.json")
    fps, count = info["fps"], plan.frame_count
    features = info["features"]
    numeric = {
        k: Moments() for k, f in features.items() if f["dtype"] not in {"video", "string", "binary"}
    }
    tasks, task_map = [], {}
    with package.file("meta/tasks.parquet") as path:
        source_tasks = {t["task_index"]: t["task"] for t in parquet_rows(path)}
    with package.file(DATA_PATH) as path:
        selected_tasks = {
            r["task_index"]
            for r in window_rows(path, plan.start_step, plan.end_step, ["task_index"])
        }
        for index in sorted(selected_tasks):
            task_map[index] = len(tasks)
            tasks.append(source_tasks[index])
        schema = pq.read_schema(path)
        (root / DATA_PATH).parent.mkdir(parents=True)
        mapping_schema = pa.schema(
            [
                ("frame_index", pa.int64()),
                ("parent_frame_index", pa.int64()),
                ("parent_timestamp_ns", pa.int64()),
                ("parent_data_version_id", pa.string()),
            ]
        )
        mapping_path = root / "hc/derivation/frames.parquet"
        mapping_path.parent.mkdir(parents=True)
        pending, trace = [], []
        with (
            pq.ParquetWriter(root / DATA_PATH, schema) as output,
            pq.ParquetWriter(mapping_path, mapping_schema) as mapping,
        ):

            def flush():
                table = pa.Table.from_pylist(pending, schema=schema)
                output.write_table(table)
                mapping.write_table(pa.Table.from_pylist(trace, schema=mapping_schema))
                for key, moments in numeric.items():
                    moments.add(table[key].to_pylist())
                pending.clear()
                trace.clear()

            seen = 0
            for row in window_rows(path, plan.start_step, plan.end_step):
                checkpoint()
                parent_frame = row["frame_index"]
                if parent_frame != plan.start_step + seen or row["episode_index"] != 0:
                    raise PackageConflict(
                        "parent Episode frame order differs from the frozen interval"
                    )
                if seen == 0 and row["hc.timestamp_ns"] != plan.start_timestamp_ns:
                    raise PackageConflict("parent interval timestamp changed")
                trace.append(
                    {
                        "frame_index": seen,
                        "parent_frame_index": parent_frame,
                        "parent_timestamp_ns": row["hc.timestamp_ns"],
                        "parent_data_version_id": plan.parent.data_version_id,
                    }
                )
                row.update(
                    index=seen,
                    frame_index=seen,
                    timestamp=seen / fps,
                    task_index=task_map[row["task_index"]],
                )
                if seen == 0:
                    for key in row:
                        if key.startswith("hc.repeated."):
                            row[key] = False
                pending.append(row)
                seen += 1
                if len(pending) == 1024:
                    flush()
            if pending:
                flush()
        if seen != count:
            raise PackageConflict("parent interval is incomplete")
    source_frames(package, root, plan, checkpoint)
    stats = {key: value.document() for key, value in numeric.items()}
    episode = {
        "episode_index": 0,
        "tasks": tasks,
        "length": count,
        "data/chunk_index": 0,
        "data/file_index": 0,
        "meta/episodes/chunk_index": 0,
        "meta/episodes/file_index": 0,
        "dataset_from_index": 0,
        "dataset_to_index": count,
    }
    modalities = package.document("hc/modalities.json")
    receipts = []
    window = DepthWindow(plan.start_step, plan.end_step, count, fps, plan.start_timestamp_ns)
    with TemporaryDirectory(prefix="tag-media-", dir=root.parent) as temporary:
        for key, feature in features.items():
            if feature["dtype"] != "video":
                continue
            checkpoint()
            original = VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
            work = Path(temporary) / key
            work.mkdir()
            evidence_root, evidence = None, ()
            depth = bool(feature.get("info", {}).get("is_depth_map"))
            if depth:
                prefix = modalities["depth"][key]["evidence_path"]
                evidence_root = work / "source-evidence"
                entries = []
                for path, entry in package.entries.items():
                    if path.startswith(prefix + "/"):
                        relative = path[len(prefix) + 1 :]
                        copy_member(package, path, evidence_root / relative, checkpoint)
                        entries.append(entry.model_copy(update={"path": relative}))
                evidence = tuple(entries)
            with package.file(original) as path:
                entry = package.entries[original]
                media = PreparedVideo(
                    path, entry.sha256, entry.size, feature, evidence_root, evidence
                )
                selected = (
                    prepare_depth_window(
                        media, window, work / "selected", preserve_parent_times=True
                    )
                    if depth
                    else prepare_rgb_window(media, window, work / "rgb.mp4")
                )
                destination = root / original
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(selected.path, destination)
                if depth:
                    target = root / modalities["depth"][key]["evidence_path"]
                    for file in selected.evidence_files:
                        (target / file.path).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(selected.evidence_root / file.path, target / file.path)

                    def masks(mask_path=target / "validity/flags.parquet"):
                        for mask in parquet_rows(mask_path):
                            with Image.open(io.BytesIO(mask["flags_png"])) as image:
                                yield np.asarray(image) & 1 != 0

                    current, statistics = inspect_depth_video(
                        destination,
                        frame_count=count,
                        fps=fps,
                        info=selected.feature["info"],
                        masks=masks(),
                    )
                else:
                    current, statistics = inspect_video(destination, frame_count=count, fps=fps)
                features[key] = current
                if statistics:
                    stats[key] = statistics
                receipts.append(
                    {
                        "feature": key,
                        "parent_video_sha256": entry.sha256,
                        "video_sha256": file_hash(destination),
                        "requantized": False,
                        "operation": "byte_copy"
                        if selected is media
                        else "lossless_code_selection"
                        if depth
                        else "rgb_clip_encoding",
                        "transcoded": selected is not media,
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
        for kind, value in statistics.items():
            episode[f"stats/{key}/{kind}"] = value
    (root / EPISODES_PATH).parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([episode]), root / EPISODES_PATH)
    pq.write_table(make_task_table(tasks), root / "meta/tasks.parquet")
    info.update(total_frames=count, total_tasks=len(tasks))
    write_provenance(package, root, snapshot, plan, receipts, checkpoint)
    write_json(root, "meta/info.json", info)
    write_json(root, "meta/stats.json", stats)
    write_json(root, "hc/modalities.json", modalities)
    if "quality" in plan.encoding:
        evaluate_tag_quality(root, plan, info, checkpoint=checkpoint)
    checkpoint()
    return validate_package(root, plan.input)


def source_frames(package, root, plan, checkpoint):
    with package.file("hc/source_frames.parquet") as path:
        schema = pq.read_schema(path)
        with pq.ParquetWriter(root / "hc/source_frames.parquet", schema) as output:
            pending = []
            for index, row in enumerate(window_rows(path, plan.start_step, plan.end_step)):
                checkpoint()
                if row["frame_index"] != plan.start_step + index:
                    raise PackageConflict("parent source frame order differs from Tag selection")
                row["frame_index"] = index
                if index == 0:
                    row["repeated"] = canonical(
                        {k: False for k in json.loads(row["repeated"])}
                    ).decode()
                pending.append(row)
                if len(pending) == 1024:
                    output.write_table(pa.Table.from_pylist(pending, schema=schema))
                    pending.clear()
            if pending:
                output.write_table(pa.Table.from_pylist(pending, schema=schema))


def write_provenance(package, root, snapshot, plan, receipts, checkpoint):
    for path in package.entries:
        if path.startswith("hc/recording/") or path == "hc/calibration.json":
            copy_member(package, path, root / path, checkpoint)
        elif (
            path.startswith("hc/")
            and path.endswith(".json")
            and not path.startswith(("hc/depth/", "hc/derivation/"))
        ):
            copy_member(package, path, root / "hc/derivation/parent" / path, checkpoint)
    write_json(root, "hc/derivation/parent/manifest.json", package.manifest.model_dump(mode="json"))
    write_json(root, "hc/derivation/parent/commit.json", package.receipt.model_dump(mode="json"))
    (root / "hc/derivation/parent/annotation.json").write_bytes(snapshot.content)
    write_json(root, "hc/derivation/plan.json", plan.model_dump(mode="json"))
    source = package.document("hc/source.json")
    parent_window = {
        k: source[k]
        for k in (
            "recording_id",
            "slice_id",
            "start_offset_ns",
            "end_offset_ns",
            "start_ns",
            "end_ns",
            "clock",
            "alignment_clock",
        )
        if k in source
    }
    # Exact raw offsets require a recorded parent origin. At the last frame use
    # the parent's recorded end; no inferred nanosecond boundary is claimed.
    for begin, end in (("start_offset_ns", "end_offset_ns"), ("start_ns", "end_ns")):
        if type(source.get(begin)) is int and type(source.get(end)) is int:
            origin = source[begin]
            source[begin] = origin + plan.start_timestamp_ns - plan.parent_origin_timestamp_ns
            if plan.next_parent_timestamp_ns is not None:
                source[end] = (
                    origin + plan.next_parent_timestamp_ns - plan.parent_origin_timestamp_ns
                )
            if not parent_window[begin] <= source[begin] < source[end] <= parent_window[end]:
                raise PackageConflict("derived source interval falls outside its parent recording")
    source.pop("slice_id", None)
    source.update(
        identity=plan.input.identity.model_dump(mode="json"),
        source_sha256=plan.input.source_sha256,
        display_name=plan.display_name,
        derivation={
            "kind": "annotation_interval",
            "parent_data": plan.parent.model_dump(mode="json"),
            "parent_recording_window": parent_window,
            "annotation_id": plan.annotation_id,
            "annotation_task_id": plan.annotation.binding.task_id,
            "annotation_revision": plan.annotation.binding.revision,
            "annotation_file_sha256": plan.annotation.binding.annotation_file_sha256,
            "start_step": plan.start_step,
            "end_step": plan.end_step,
            "start_timestamp_ns": plan.start_timestamp_ns,
            "next_parent_timestamp_ns": plan.next_parent_timestamp_ns,
            "child_approvals_inherited": False,
        },
    )
    write_json(root, "hc/source.json", source)
    write_json(
        root,
        "hc/quality.json",
        {
            "status": "UNKNOWN",
            "reason": "DERIVED_INTERVAL_REQUIRES_OWN_QC",
            "parent_report_path": "hc/derivation/parent/hc/quality.json",
            "parent_decision_inherited": False,
        },
    )
    write_json(
        root,
        "hc/conversion_report.json",
        {
            "processor_version": plan.processor_version,
            "media": receipts,
            "encoding": plan.encoding,
            "requantized": False,
            "numeric_policy": "parent Arrow types and selected values preserved",
            "timestamp_policy": "relative seconds reset; exact parent hc.timestamp_ns preserved",
            "repeated_policy": "first sample has no predecessor; original trace bound to parent",
            "annotation_policy": "unchanged parent revision attached; no inherited child approval",
        },
    )
