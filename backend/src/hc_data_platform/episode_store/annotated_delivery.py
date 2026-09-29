"""Revision-bound, complete standard package derivation with unchanged media.

This creates an export artifact, not another primary data version. Ordinary Tag
saves never call it. Public commands must obtain an authorized immutable revision
file first and queue this work; this module does not grant access or publish READY.
"""

import hashlib
import json
import shutil
from bisect import bisect_right
from pathlib import Path
from typing import Literal

import pyarrow as pa
import pyarrow.parquet as pq
from pydantic import Field, model_validator

from hc_data_platform.annotation.subtasks import SourceFrame, SubtaskBinding, SubtaskProjection
from hc_data_platform.publishing.depth_evidence import file_hash
from hc_data_platform.publishing.lerobot_export import DATA_PATH, EPISODES_PATH

from .models import DataVersionRef, FileEntry, FrozenModel, canonical, digest
from .writer import Moments, write_json

PROCESSOR = "hc-annotated-episode/1"


class ReviewReference(FrozenModel):
    status: Literal["APPROVED", "UNREVIEWED"]
    review_id: str | None = None
    submission_id: str | None = None

    @model_validator(mode="after")
    def immutable_review_reference(self):
        if self.status == "APPROVED":
            if not self.review_id or not self.submission_id:
                raise ValueError("approved annotation requires its immutable review and submission")
        elif self.review_id is not None or self.submission_id is not None:
            raise ValueError("unreviewed annotation cannot claim approval references")
        return self


class AnnotatedDeliveryManifest(FrozenModel):
    format: Literal["hc-annotated-episode/1"] = PROCESSOR
    base_data: DataVersionRef
    subtasks: SubtaskBinding
    review: ReviewReference
    files: tuple[FileEntry, ...] = Field(min_length=1)
    includes_collected_depth: Literal[True] = True
    excluded_modalities: tuple[str, ...] = Field(default=(), max_length=0)
    # Explicit cleaning masks preserve the complete parent. No frames are dropped.
    exclusion_policy: Literal["mark_parent_frames"] = "mark_parent_frames"

    @model_validator(mode="after")
    def coherent(self):
        paths = [entry.path for entry in self.files]
        if self.subtasks.data != self.base_data or paths != sorted(set(paths)):
            raise ValueError("annotated delivery binding/inventory differs from its parent")
        return self


def write_annotated_delivery(root, package, annotation_file, track):
    data, body = package.receipt.data, annotation_file.content
    if hashlib.sha256(body).hexdigest() != annotation_file.file.content_sha256:
        raise ValueError("annotation file differs from its authorized receipt")
    document = json.loads(body)
    projection = SubtaskProjection.freeze(
        document, data, annotation_file.file.content_sha256, track
    )
    if (
        projection.binding.task_id != annotation_file.file.task_id
        or projection.binding.revision != annotation_file.file.revision
        or projection.binding.revision_content_hash != annotation_file.file.revision_content_hash
        or annotation_file.file.status != "READY"
        or annotation_file.approved
        and not (annotation_file.review_id and annotation_file.submission_id)
    ):
        raise ValueError("annotation delivery differs from its frozen revision/review receipt")
    review = ReviewReference(
        status="APPROVED" if annotation_file.approved else "UNREVIEWED",
        review_id=annotation_file.review_id,
        submission_id=annotation_file.submission_id,
    )
    reserved = {
        "hc/annotation.json",
        "hc/annotation-delivery.json",
        "hc/subtasks.jsonl",
        "hc/subtask-track.json",
        "hc/annotation_source_frames.parquet",
        "hc/parent_manifest.json",
        "hc/parent_commit.json",
        "hc/delivery_manifest.json",
    }
    if reserved.intersection(entry.path for entry in package.manifest.files):
        raise ValueError("parent package already contains an annotation delivery extension")
    info = package.document("meta/info.json")
    if {"subtask_index", "hc.annotation.excluded"}.intersection(info["features"]):
        raise ValueError("parent package already contains annotation projection columns")
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    for entry in package.manifest.files:
        if entry.path in {DATA_PATH, "meta/info.json", "meta/stats.json", EPISODES_PATH}:
            continue
        target = root / entry.path
        target.parent.mkdir(parents=True, exist_ok=True)
        with package.file(entry.path) as source:
            shutil.copyfile(source, target)
        if target.stat().st_size != entry.size or file_hash(target) != entry.sha256:
            raise ValueError("copied package member differs from its parent receipt")
    (root / DATA_PATH).parent.mkdir(parents=True, exist_ok=True)
    source_schema = pa.schema(
        [
            ("output_frame_index", pa.int64()),
            ("source_frame_index", pa.int64()),
            ("source_timestamp_ns", pa.int64()),
        ]
    )
    source_frames = (
        SourceFrame(output_frame_index=i, source_frame_index=i) for i in range(data.frame_count)
    )
    indexes, moments = iter(projection.iter_indexes(source_frames)), Moments()
    excluded_moments = Moments()
    exclusions = document["effective_exclusions"]
    previous_end = 0
    for segment in exclusions:
        if (
            type(segment["start_step"]) is not int
            or type(segment["end_step"]) is not int
            or not previous_end <= segment["start_step"] < segment["end_step"] <= data.frame_count
            or segment.get("modality_scope") != "ALL_MODALITIES"
        ):
            raise ValueError("annotation exclusion is outside its parent data version")
        previous_end = segment["end_step"]
    exclusion_starts = tuple(segment["start_step"] for segment in exclusions)

    def is_excluded(frame_index):
        index = bisect_right(exclusion_starts, frame_index) - 1
        return index >= 0 and frame_index < exclusions[index]["end_step"]

    offset = 0
    with package.file(DATA_PATH) as parent:
        parquet = pq.ParquetFile(parent)
        schema = parquet.schema_arrow.append(pa.field("subtask_index", pa.int64())).append(
            pa.field("hc.annotation.excluded", pa.bool_())
        )
        with (
            pq.ParquetWriter(root / DATA_PATH, schema) as output,
            pq.ParquetWriter(
                root / "hc/annotation_source_frames.parquet", source_schema
            ) as mapping,
        ):
            for batch in parquet.iter_batches(batch_size=1024):
                frames = batch.column("frame_index").to_pylist()
                if frames != list(range(offset, offset + len(frames))):
                    raise ValueError("parent frame indexes are incomplete or unordered")
                indices = [next(indexes) for _ in frames]
                excluded = [is_excluded(i) for i in frames]
                table = (
                    pa.Table.from_batches([batch])
                    .append_column("subtask_index", pa.array(indices, type=pa.int64()))
                    .append_column("hc.annotation.excluded", pa.array(excluded, type=pa.bool_()))
                )
                output.write_table(table)
                moments.add(indices)
                excluded_moments.add(excluded)
                mapping.write_table(
                    pa.Table.from_arrays(
                        [
                            pa.array(frames, type=pa.int64()),
                            pa.array(frames, type=pa.int64()),
                            batch.column("hc.timestamp_ns"),
                        ],
                        schema=source_schema,
                    )
                )
                offset += len(frames)
    if offset != data.frame_count or next(indexes, None) is not None:
        raise ValueError("annotation projection differs from parent frame count")
    statistics = {
        "subtask_index": moments.document(),
        "hc.annotation.excluded": excluded_moments.document(),
    }
    stats = package.document("meta/stats.json")
    stats.update(statistics)
    with package.file(EPISODES_PATH) as parent:
        episodes = pq.read_table(parent).to_pylist()
    if len(episodes) != 1:
        raise ValueError("annotation delivery requires one independent parent Episode")
    for feature, values in statistics.items():
        for key, value in values.items():
            episodes[0][f"stats/{feature}/{key}"] = value
    (root / EPISODES_PATH).parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(pa.Table.from_pylist(episodes), root / EPISODES_PATH)
    info["features"].update(
        {
            "subtask_index": {"dtype": "int64", "shape": [1], "names": None},
            "hc.annotation.excluded": {"dtype": "bool", "shape": [1], "names": None},
        }
    )
    info["hc.annotation_delivery"] = {"format": PROCESSOR, "manifest": "hc/delivery_manifest.json"}
    (root / "hc/annotation.json").write_bytes(body)
    (root / "hc/subtasks.jsonl").write_bytes(
        b"".join(canonical(row) + b"\n" for row in projection.rows())
    )
    for path, value in {
        "meta/info.json": info,
        "meta/stats.json": stats,
        "hc/subtask-track.json": projection.binding.model_dump(mode="json"),
        "hc/annotation-delivery.json": {
            "format": PROCESSOR,
            "review": review.model_dump(mode="json"),
            "base_data": data.model_dump(mode="json"),
            "projection_sha256": projection.configuration_sha256,
            "frame_mapping": "hc/annotation_source_frames.parquet",
            "exclusion_policy": "mark_parent_frames",
            "media_policy": "verified_byte_copy",
            "requantized": False,
            "transcoded": False,
        },
        "hc/parent_manifest.json": package.manifest.model_dump(mode="json"),
        "hc/parent_commit.json": package.receipt.model_dump(mode="json"),
    }.items():
        write_json(root, path, value)
    entries = tuple(
        FileEntry(
            path=path.relative_to(root).as_posix(),
            size=path.stat().st_size,
            sha256=file_hash(path),
            media_type="application/octet-stream",
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )
    manifest = AnnotatedDeliveryManifest(
        base_data=data, subtasks=projection.binding, review=review, files=entries
    )
    write_json(root, "hc/delivery_manifest.json", manifest.model_dump(mode="json"))
    return manifest, digest(manifest.model_dump(mode="json"))
