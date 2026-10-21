"""Real local writers preserve saved tags even when no segments are selected."""

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pyarrow.parquet as pq
import pytest

from hc_data_platform.annotation.models import AnnotationRevision
from hc_data_platform.annotation.revision_models import AnnotationRevisionFile, RevisionFileDownload
from hc_data_platform.annotation.subtasks import SubtaskTrack
from hc_data_platform.annotation.validation import revision_content_hash
from hc_data_platform.core.context import RequestContext, bind_request_context
from hc_data_platform.episode_store.collection_export import CollectionSource, write_collection
from hc_data_platform.episode_store.models import CommitReceipt, PackageInput, canonical, digest
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.lance_catalog.models import StepRecord
from tests.annotation.test_subtasks import snapshot  # noqa: F401


@pytest.mark.parametrize("segment_ids", [(), ("segment-0",)])
def test_collection_writer_preserves_all_tags_with_optional_segments(
    snapshot,  # noqa: F811
    tmp_path,
    segment_ids,
):
    document, data, _ = snapshot
    identity = data.identity
    bind_request_context(
        RequestContext(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            region_code=identity.region_code,
        )
    )
    source = PackageInput(
        identity=identity,
        source_sha256="1" * 64,
        processor_version="optional-segments-test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id=data.schema_snapshot_id,
        required_features=("observation.state",),
    )
    root = tmp_path / "parent"
    write_episode(
        root,
        source,
        (
            StepRecord(
                rollout_id=identity.rollout_id,
                step_index=i,
                timestamp_ns=i * 33_333_333,
                modalities={"/observation/state": [float(i)]},
                valid={"/observation/state": True},
            )
            for i in range(data.frame_count)
        ),
        PreparedAssets(identity, "optional-segments", {}),
        fps=30,
        metadata={"task": "optional export segments"},
    )
    manifest = validate_package(root, source)
    data = data.model_copy(update={"content_hash": manifest.content_hash})
    reference = data.model_dump(mode="json")
    document["data_binding"]["data_ref"] = reference
    document["revision"]["data_ref"] = reference
    revision = AnnotationRevision.model_validate(document["revision"])
    document["revision"]["content_hash"] = revision_content_hash(
        base_lance_version=None,
        data_ref=data,
        tag_schema_id=revision.tag_schema_id,
        tag_schema_version=revision.tag_schema_version,
        tags=revision.tags,
        operations=revision.operations,
    )
    body = canonical(document)
    annotation = RevisionFileDownload(
        body,
        AnnotationRevisionFile(
            task_id=revision.task_id,
            revision=revision.revision,
            revision_content_hash=document["revision"]["content_hash"],
            status="READY",
            generation=1,
            attempts=1,
            content_sha256=digest(document),
            size_bytes=len(body),
        ),
        False,
        None,
        None,
    )

    @contextmanager
    def file(relative):
        yield root / relative

    package = SimpleNamespace(
        manifest=manifest,
        receipt=CommitReceipt(
            data=data, idempotency_key="3" * 64, manifest_sha256=manifest.content_hash
        ),
        file=file,
        document=lambda relative: json.loads((root / relative).read_bytes()),
    )
    track = SubtaskTrack(track_id="task-segments", label="任务分段", annotation_ids=segment_ids)
    output = tmp_path / "collection"
    result, _ = write_collection(
        output, [CollectionSource(package, annotation, track)], modalities="rgbd"
    )
    member = result["members"][0]
    assert (output / member["attachments_root"] / "hc/annotation.json").read_bytes() == body
    table = pq.read_table(output / member["data_path"])
    assert len(table) == data.frame_count
    assert table["subtask_index"].to_pylist() == (
        [0, 0] + [-1] * 16 if segment_ids else [-1] * data.frame_count
    )
    assert (output / "hc/subtasks.jsonl").exists() == bool(segment_ids)
    assert all(entry["size"] > 0 for entry in result["files"])
