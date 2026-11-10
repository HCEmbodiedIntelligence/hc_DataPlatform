import json
from contextlib import nullcontext
from types import SimpleNamespace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from temporalio.testing import ActivityEnvironment

from hc_data_platform.annotation.automation import (
    AutomaticAnnotationTaskService,
    InMemoryAutomaticAnnotationRepository,
)
from hc_data_platform.annotation.validation import validate_tag_revision
from hc_data_platform.continuous_recordings.takeovers import (
    committed_takeover_steps,
    parse_takeovers,
)
from hc_data_platform.episode_store.frame_mapping import SOURCE_MAP
from hc_data_platform.episode_store.models import DataVersionRef, EpisodeIdentity
from hc_data_platform.lerobot_imports.mcap_processing import mcap_document
from hc_data_platform.workflow import activities
from hc_data_platform.workflow.models import AutomaticAnnotationActivityInput
from tests.annotation.test_automatic_tasks import _publish_compatible_schema


def sidecar(intervals, **changes):
    return json.dumps(
        {
            "schema": "hc-dagger-takeovers/v1",
            "tag": "dagger",
            "clock": "ros",
            "recording": "demo.mcap",
            "recording_start_ns": 0,
            "complete": True,
            "intervals": [{"start_ns": start, "end_ns": end} for start, end in intervals],
            **changes,
        }
    ).encode()


def test_only_exact_sibling_is_bound_to_the_processing_receipt():
    names = ("a/demo.mcap", "a/demo.takeovers.json", "b/demo.mcap", "b/other.takeovers.json")
    members = {name: {"path": name, "size": 50, "sha256": "a" * 64} for name in names}
    raw = SimpleNamespace(storage_prefix="raw/batch")
    matched = mcap_document(members[names[0]], members, raw)
    assert matched["takeover_file"]["object_key"] == "raw/batch/a/demo.takeovers.json"
    assert matched["takeover_file"]["sha256"] == members[names[1]]["sha256"]
    assert "takeover_file" not in mcap_document(members[names[2]], members, raw)


def test_recorder_checkpoint_only_contributes_closed_nonempty_intervals():
    assert parse_takeovers(sidecar([(1, 1), (2, 4), (6, None)], complete=False), "demo.mcap") == [
        (2, 4)
    ]
    assert parse_takeovers(sidecar([]), "demo.mcap") == []


@pytest.mark.parametrize(
    "content",
    [
        sidecar([], recording="other.mcap"),
        sidecar([(2, None)]),
        sidecar([(2, 1)]),
        sidecar([(True, 2)]),
        sidecar([], clock="media"),
        sidecar([], tag="other"),
    ],
)
def test_malformed_or_misbound_recording_never_produces_tags(content):
    with pytest.raises(ValueError, match="MCAP_TAKEOVERS_INVALID"):
        parse_takeovers(content, "demo.mcap")


@pytest.mark.parametrize("labels", [("pick", "place"), ()])
@pytest.mark.asyncio
async def test_takeovers_are_children_on_the_retained_slice_timeline(tmp_path, labels, monkeypatch):
    intervals = [(0, 10), (10, 40), (50, 60), (80, 90), (100, 200), (205, 215)]
    parsed = parse_takeovers(sidecar(intervals), "demo.mcap")
    # The slice starts inside an intervention and has dropped the frame at 55.
    stamps = [20, 39, 40, 79, 80, 89, 90, 102]
    frame_map = tmp_path / "frames.parquet"
    pq.write_table(pa.table({"timestamp_ns": stamps}), frame_map)
    identity = EpisodeIdentity(
        organization_id="org",
        project_id="project-a",
        region_code="cn-hz",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        episode_id="episode-1",
    )
    data = DataVersionRef(
        identity=identity,
        data_version_id="dv1",
        version=1,
        schema_snapshot_id="dataset-schema-3",
        content_hash="a" * 64,
        package_prefix="test",
        frame_count=len(stamps),
    )
    package = SimpleNamespace(
        receipt=SimpleNamespace(data=data),
        document={"hc/source.json": {"mcap": {"takeover_intervals_ns": parsed}}}.__getitem__,
        file={SOURCE_MAP: nullcontext(frame_map)}.__getitem__,
    )
    steps = committed_takeover_steps(package)
    assert steps == ((0, 2), (4, 6), (7, 8))
    repo = InMemoryAutomaticAnnotationRepository()
    _publish_compatible_schema(repo)
    service = AutomaticAnnotationTaskService(repo)

    def open_package(target, version):
        assert (target, version) == (identity, data.data_version_id)
        return package

    monkeypatch.setattr(
        activities,
        "_dependencies",
        SimpleNamespace(
            annotation_tasks=service,
            episode_packages=SimpleNamespace(files=SimpleNamespace(open=open_package)),
        ),
    )
    request = AutomaticAnnotationActivityInput(
        organization_id=identity.organization_id,
        project_id=identity.project_id,
        region_code=identity.region_code,
        rollout_id=identity.rollout_id,
        dataset_id=identity.dataset_id,
        dataset_version=1,
        lance_version=None,
        data_ref=data,
        dataset_schema_snapshot_id=data.schema_snapshot_id,
        base_step_count=len(stamps),
        source_workflow_id="test",
        task_labels=labels,
        has_recorded_takeovers=True,
    )
    result = await ActivityEnvironment().run(activities.create_annotation_task, request)
    assert result == await ActivityEnvironment().run(activities.create_annotation_task, request)
    aggregate = repo.get(result.task_id)
    task = aggregate.task
    (initial,) = aggregate.revisions
    parents, children = initial.tags[: len(labels)], initial.tags[len(labels) :]
    assert [(t.label, t.start_step, t.end_step) for t in parents] == [
        (label, 0, len(stamps)) for label in labels
    ]
    assert [(t.label, t.start_step, t.end_step) for t in children] == [
        ("dagger", start, end) for start, end in steps
    ]
    assert all(
        t.parent_annotation_id == (parents[0].annotation_id if parents else None) for t in children
    )
    schema = repo.get_tag_schema_version(
        schema_id=task.tag_schema_id, version=task.tag_schema_version
    )
    validate_tag_revision(
        schema=schema, base_step_count=len(stamps), tags=initial.tags, operations=()
    )
