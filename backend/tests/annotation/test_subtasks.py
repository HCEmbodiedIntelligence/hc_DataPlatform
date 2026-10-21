"""Pure projection contracts; these do not substitute for export/service tests."""

import copy

import pytest
from pydantic import ValidationError

from hc_data_platform.annotation.models import AnnotationRevision, AnnotationTag, TagSchemaDocument
from hc_data_platform.annotation.subtasks import (
    SourceFrame,
    SubtaskProjection,
    SubtaskProjectionError,
    SubtaskTrack,
)
from hc_data_platform.annotation.validation import revision_content_hash, schema_content_hash
from hc_data_platform.episode_store.models import DataVersionRef, EpisodeIdentity, digest


@pytest.fixture
def snapshot():
    data = DataVersionRef(
        identity=EpisodeIdentity(
            organization_id="org",
            project_id="project",
            region_code="region",
            dataset_id="dataset_subtasks",
            episode_id="episode_source",
            rollout_id="rollout",
        ),
        version=1,
        data_version_id="dv_" + "a" * 64,
        content_hash="b" * 64,
        frame_count=18,
        schema_snapshot_id="schema",
        package_prefix="episodes/source",
    )
    intervals = ((0, 2), (3, 5), (5, 7), (9, 11), (12, 14), (16, 18))
    tags = tuple(
        AnnotationTag(
            annotation_id=f"segment-{i}",
            tag_id=f"tag-{i}",
            path=(f"tag-{i}",),
            label=f"步骤 {i}",
            start_step=start,
            end_step=end,
            attributes={"side": "left"},
        )
        for i, (start, end) in enumerate(intervals)
    )
    crossed = AnnotationTag(
        annotation_id="crossed",
        tag_id="quality",
        path=("quality",),
        label="交叉标签",
        start_step=1,
        end_step=17,
        parent_annotation_id="segment-2",
        subject={"object_id": "cup", "object_type": "item"},
        relations=(
            {"relation_type": "contact", "target": {"object_id": "hand", "object_type": "part"}},
        ),
    )
    schema = TagSchemaDocument(nodes=())
    all_tags = (crossed, *reversed(tags))
    revision = AnnotationRevision(
        task_id="task",
        revision=0,
        parent_revision=None,
        author_id="author",
        client_mutation_id="save",
        base_lance_version=None,
        data_ref=data,
        tag_schema_id="labels",
        tag_schema_version=1,
        tags=all_tags,
        operations=(),
        content_hash=revision_content_hash(
            base_lance_version=None,
            data_ref=data,
            tag_schema_id="labels",
            tag_schema_version=1,
            tags=all_tags,
            operations=(),
        ),
    )
    document = {
        "format_version": "hc-annotation/1",
        "data_binding": {
            **dict(
                zip(
                    ("organization_id", "project_id", "region_code"),
                    data.identity.scope,
                    strict=True,
                )
            ),
            "dataset_id": data.identity.dataset_id,
            "rollout_id": data.identity.rollout_id,
            "data_ref": data.model_dump(mode="json"),
            "base_step_count": 18,
        },
        "revision": revision.model_dump(mode="json"),
        "tag_schema": {
            "schema_id": "labels",
            "version": 1,
            "content_hash": schema_content_hash(schema),
            "document": schema.model_dump(mode="json"),
        },
        "interval_convention": {
            "unit": "step",
            "origin": 0,
            "bounds": "[start_step,end_step)",
            "timeline": "bound_data_version",
            "exclusion_scope": "ALL_MODALITIES",
        },
        "source": {"task_id": "task"},
        "cumulative_operations": [],
        "effective_exclusions": [],
    }
    track = SubtaskTrack(
        track_id="task-segments",
        label="任务分段",
        annotation_ids=tuple(t.annotation_id for t in tags),
    )
    return document, data, track


def freeze(snapshot):
    document, data, track = snapshot
    return SubtaskProjection.freeze(document, data, digest(document), track)


def mapping(indices):
    return [
        SourceFrame(output_frame_index=i, source_frame_index=source)
        for i, source in enumerate(indices)
    ]


def test_six_segments_gaps_half_open_boundaries_and_all_other_tags_remain_intact(snapshot):
    before = copy.deepcopy(snapshot[0])
    projection = freeze(snapshot)
    assert list(projection.iter_indexes(mapping(range(18)))) == [
        0,
        0,
        -1,
        1,
        1,
        2,
        2,
        -1,
        -1,
        3,
        3,
        -1,
        4,
        4,
        -1,
        -1,
        5,
        5,
    ]
    rows = list(projection.rows())
    assert [row["subtask_index"] for row in rows] == list(range(6))
    assert [row["annotation_id"] for row in rows] == [f"segment-{i}" for i in range(6)]
    assert snapshot[0] == before
    assert snapshot[0]["revision"]["tags"][0]["relations"][0]["target"]["object_id"] == "hand"


def test_resampling_uses_parent_frame_mapping_including_repeats_and_missing_samples(snapshot):
    projection = freeze(snapshot)
    assert list(projection.iter_indexes(mapping((0, 0, 3, 4, None, 7, 9, 10, 16, 17)))) == [
        0,
        0,
        1,
        1,
        -1,
        -1,
        3,
        3,
        5,
        5,
    ]
    # Copying destination frame numbers would instead claim segment 0 for frame 1,
    # and miss segment 5; the mapping is mandatory even for an identity export.
    assert list(projection.iter_indexes(mapping((3, 4)))) == [1, 1]


def test_selection_order_does_not_change_binding_and_wrong_members_are_located(snapshot):
    doc, data, track = snapshot
    reverse = SubtaskTrack(
        track_id=track.track_id,
        label=track.label,
        annotation_ids=tuple(reversed(track.annotation_ids)),
    )
    assert (
        freeze(snapshot).configuration_sha256 == freeze((doc, data, reverse)).configuration_sha256
    )
    wrong = SubtaskTrack(track_id="one", label="one", annotation_ids=("unknown",))
    with pytest.raises(SubtaskProjectionError) as error:
        freeze((doc, data, wrong))
    assert error.value.code == "SUBTASK_MEMBER_NOT_FOUND" and error.value.annotation_ids == (
        "unknown",
    )


def test_overlapping_members_only_in_selected_track_return_both_annotation_ids(snapshot):
    doc, data, track = snapshot
    selected = SubtaskTrack(
        track_id=track.track_id, label=track.label, annotation_ids=("segment-0", "crossed")
    )
    with pytest.raises(SubtaskProjectionError) as error:
        freeze((doc, data, selected))
    assert error.value.code == "SUBTASK_TRACK_OVERLAP"
    assert error.value.annotation_ids == ("segment-0", "crossed")


@pytest.mark.parametrize("indices", [(18,), (3, 2)])
def test_invalid_source_frame_mapping_rejected(snapshot, indices):
    with pytest.raises(SubtaskProjectionError, match="out of range or moves backwards"):
        list(freeze(snapshot).iter_indexes(mapping(indices)))


def test_no_implicit_frame_grid_or_fractional_frame_mapping(snapshot):
    with pytest.raises(SubtaskProjectionError, match="contiguous"):
        list(
            freeze(snapshot).iter_indexes([SourceFrame(output_frame_index=1, source_frame_index=0)])
        )
    for value in (0.5, True, -1):
        with pytest.raises(ValidationError):
            SourceFrame(output_frame_index=0, source_frame_index=value)


@pytest.mark.parametrize("mutation", ["version", "label", "schema", "checksum"])
def test_cross_version_and_tampered_snapshots_fail_closed(snapshot, mutation):
    doc, data, track = snapshot
    sha = digest(doc)
    if mutation == "version":
        data = data.model_copy(update={"data_version_id": "dv_" + "c" * 64})
    elif mutation == "label":
        doc["revision"]["tags"][1]["label"] = "changed"
        sha = digest(doc)
    elif mutation == "schema":
        doc["tag_schema"]["content_hash"] = "0" * 64
        sha = digest(doc)
    else:
        sha = "0" * 64
    with pytest.raises(SubtaskProjectionError):
        SubtaskProjection.freeze(doc, data, sha, track)


def test_empty_track_keeps_annotations_without_assigning_segment_numbers(snapshot):
    document, data, _ = snapshot
    original = copy.deepcopy(document)
    track = SubtaskTrack(track_id="task-segments", label="任务分段", annotation_ids=())
    projection = freeze((document, data, track))
    assert list(projection.iter_indexes(mapping(range(data.frame_count)))) == [-1] * 18
    assert list(projection.rows()) == []
    assert document == original
    assert len(document["revision"]["tags"]) == 7


def test_track_cannot_repeat_or_contain_blank_members():
    for members in (("",), ("tag", "tag")):
        with pytest.raises(ValidationError):
            SubtaskTrack(track_id="selected", label="任务分段", annotation_ids=members)
