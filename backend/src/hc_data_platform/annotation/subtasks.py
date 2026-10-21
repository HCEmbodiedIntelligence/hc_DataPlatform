"""Explicit revision-bound subtask projection, without editing annotation history.

Track membership is a frozen export selection of annotation IDs. It is never
inferred from every Tag, tree depth, or UI lanes. Full annotation JSON remains
an independent, unchanged attachment. Every output index needs its parent-frame
mapping, including identity mappings; resampling cannot silently copy indexes.
"""

from bisect import bisect_right
from dataclasses import dataclass
from typing import Literal

from pydantic import Field, StrictInt, field_validator

from hc_data_platform.episode_store.models import DataVersionRef, Digest, FrozenModel, digest

from .models import AnnotationOperation, AnnotationRevision, AnnotationTag, TagSchemaDocument
from .validation import revision_content_hash, schema_content_hash

FORMAT = "hc-subtask-track/1"


class SubtaskTrack(FrozenModel):
    """User-selected members of one exported track in one frozen revision."""

    format: Literal["hc-subtask-track/1"] = FORMAT
    track_id: str = Field(min_length=1, max_length=256)
    label: str = Field(min_length=1, max_length=256)
    # Empty membership exports the saved annotation without a task segmentation.
    # All frames then have index -1, while the full annotation attachment is retained.
    annotation_ids: tuple[str, ...] = Field(min_length=0, max_length=10000)

    @field_validator("track_id", "label")
    @classmethod
    def clean_label(cls, value):
        if value != value.strip():
            raise ValueError("subtask track identity/label cannot have outer whitespace")
        return value

    @field_validator("annotation_ids")
    @classmethod
    def unique_members(cls, values):
        if len(set(values)) != len(values) or any(
            not value or len(value) > 256 for value in values
        ):
            raise ValueError("subtask membership requires distinct, nonempty annotation IDs")
        # Reordering a checkbox list does not create another export configuration.
        return tuple(sorted(values))


class SubtaskBinding(FrozenModel):
    data: DataVersionRef
    task_id: str = Field(min_length=1)
    revision: int = Field(ge=0)
    revision_content_hash: Digest
    annotation_file_sha256: Digest
    track: SubtaskTrack


class SourceFrame(FrozenModel):
    output_frame_index: StrictInt = Field(ge=0)
    # None denotes an output frame with no parent sample; it cannot receive a Tag.
    source_frame_index: StrictInt | None = Field(default=None, ge=0)


class SubtaskProjectionError(ValueError):
    def __init__(self, code, message, *, annotation_ids=()):
        super().__init__(message)
        self.code, self.annotation_ids = code, tuple(annotation_ids)


def verify_snapshot(document, data):
    """Check a downloaded immutable annotation file against its exact parent."""
    if document.get("format_version") != "hc-annotation/1":
        raise SubtaskProjectionError("SUBTASK_SNAPSHOT_INVALID", "Unsupported annotation file")
    binding, schema = document["data_binding"], document["tag_schema"]
    saved = AnnotationRevision.model_validate(document["revision"])
    if (
        DataVersionRef.model_validate(binding.get("data_ref")) != data
        or saved.data_ref != data
        or saved.base_lance_version is not None
        or binding["base_step_count"] != data.frame_count
        or (binding["organization_id"], binding["project_id"], binding["region_code"])
        != data.identity.scope
        or binding["dataset_id"] != data.identity.dataset_id
        or binding["rollout_id"] != data.identity.rollout_id
        or document["source"]["task_id"] != saved.task_id
        or saved.tag_schema_id != schema["schema_id"]
        or saved.tag_schema_version != schema["version"]
        or document["interval_convention"]
        != {
            "unit": "step",
            "origin": 0,
            "bounds": "[start_step,end_step)",
            "timeline": "bound_data_version",
            "exclusion_scope": "ALL_MODALITIES",
        }
    ):
        raise SubtaskProjectionError(
            "SUBTASK_DATA_BINDING_MISMATCH", "Annotation file belongs to another data version"
        )
    operations = tuple(
        AnnotationOperation.model_validate(value) for value in document["cumulative_operations"]
    )
    if (
        schema_content_hash(TagSchemaDocument.model_validate(schema["document"]))
        != schema["content_hash"]
        or revision_content_hash(
            base_lance_version=None,
            data_ref=data,
            tag_schema_id=saved.tag_schema_id,
            tag_schema_version=saved.tag_schema_version,
            tags=saved.tags,
            operations=operations,
        )
        != saved.content_hash
    ):
        raise SubtaskProjectionError(
            "SUBTASK_SNAPSHOT_INVALID", "Annotation content differs from its frozen hash"
        )
    return saved


@dataclass(frozen=True)
class SubtaskProjection:
    binding: SubtaskBinding
    segments: tuple[AnnotationTag, ...]

    @property
    def configuration_sha256(self):
        return digest(self.binding.model_dump(mode="json"))

    @classmethod
    def freeze(cls, document, data, annotation_file_sha256, track):
        if digest(document) != annotation_file_sha256:
            raise SubtaskProjectionError(
                "SUBTASK_SNAPSHOT_INVALID", "Annotation file checksum differs from its receipt"
            )
        saved = verify_snapshot(document, data)
        ids = [tag.annotation_id for tag in saved.tags]
        if len(set(ids)) != len(ids):
            raise SubtaskProjectionError(
                "SUBTASK_SNAPSHOT_INVALID", "Annotation IDs are duplicated"
            )
        by_id = {tag.annotation_id: tag for tag in saved.tags}
        missing = set(track.annotation_ids) - set(by_id)
        if missing:
            raise SubtaskProjectionError(
                "SUBTASK_MEMBER_NOT_FOUND",
                "Selected track members are absent from this revision",
                annotation_ids=sorted(missing),
            )
        segments = tuple(
            sorted(
                (by_id[key] for key in track.annotation_ids),
                key=lambda tag: (tag.start_step, tag.end_step, tag.annotation_id),
            )
        )
        previous = None
        for tag in segments:
            if tag.end_step > data.frame_count:
                raise SubtaskProjectionError(
                    "SUBTASK_RANGE_INVALID",
                    "Selected segment exceeds the bound Episode",
                    annotation_ids=(tag.annotation_id,),
                )
            if previous is not None and tag.start_step < previous.end_step:
                raise SubtaskProjectionError(
                    "SUBTASK_TRACK_OVERLAP",
                    "Selected track contains overlapping segments",
                    annotation_ids=(previous.annotation_id, tag.annotation_id),
                )
            previous = tag
        return cls(
            SubtaskBinding(
                data=data,
                task_id=saved.task_id,
                revision=saved.revision,
                revision_content_hash=saved.content_hash,
                annotation_file_sha256=annotation_file_sha256,
                track=track,
            ),
            segments,
        )

    def iter_indexes(self, source_frames):
        """Stream one int64-compatible subtask index per explicitly mapped frame."""
        starts = tuple(tag.start_step for tag in self.segments)
        previous = -1
        for output_index, raw in enumerate(source_frames):
            frame = SourceFrame.model_validate(raw)
            if frame.output_frame_index != output_index:
                raise SubtaskProjectionError(
                    "SUBTASK_FRAME_MAP_INVALID", "Output frame mapping must be contiguous from zero"
                )
            source = frame.source_frame_index
            if source is None:
                yield -1
                continue
            if not previous <= source < self.binding.data.frame_count:
                raise SubtaskProjectionError(
                    "SUBTASK_FRAME_MAP_INVALID",
                    "Source frame mapping is out of range or moves backwards",
                )
            previous = source
            index = bisect_right(starts, source) - 1
            yield index if index >= 0 and source < self.segments[index].end_step else -1

    def rows(self):
        """Stable segment numbering; full tags live in the unchanged attachment."""
        for index, tag in enumerate(self.segments):
            yield {
                "format": "hc-subtask/1",
                "subtask_index": index,
                "track_id": self.binding.track.track_id,
                "annotation_id": tag.annotation_id,
                "label": tag.label or tag.tag_id,
                "source_start_step": tag.start_step,
                "source_end_step": tag.end_step,
                "bounds": "[start_step,end_step)",
                "projection_sha256": self.configuration_sha256,
            }
