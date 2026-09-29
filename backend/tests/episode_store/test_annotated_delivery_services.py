"""Real immutable package + revision file -> local derived artifact.

This covers the writer, not the delivery queue/API/browser that will call it.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import pyarrow.parquet as pq
import pytest

from hc_data_platform.annotation.revision_files import SnapshotBusy
from hc_data_platform.annotation.subtasks import SubtaskTrack
from hc_data_platform.episode_store.annotated_delivery import write_annotated_delivery
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization


@pytest.mark.integration
def test_full_annotation_derivative_reuses_depth_media_and_preserves_parent(
    files,  # noqa: F811
    target,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    source, task, service, auth = files
    packages = target[0]
    with scoped(source):
        state = service.status(task.task_id, 1, auth)
        # The live isolated outbox may own the same legitimate generation lease.
        for _ in range(100):
            if state.status == "READY":
                break
            try:
                service.generate(
                    task.task_id, 1, state.generation, state.revision_content_hash, auth
                )
            except SnapshotBusy:
                time.sleep(0.1)
            state = service.status(task.task_id, 1, auth)
        assert state.status == "READY", state
        annotation = service.download(task.task_id, auth, revision=1, allow_unreviewed=True)
        data = target[4].data_ref
        package = packages.files.open(data.identity, data.data_version_id)
        parent_hash = package.manifest.content_hash
        track = SubtaskTrack(track_id="task-segments", label="任务分段", annotation_ids=("parent",))
        forbid_quantization(monkeypatch)
        destination = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "annotated-delivery"
            / data.data_version_id
        )
        manifest, content_hash = write_annotated_delivery(destination, package, annotation, track)
        assert manifest.review.status == "UNREVIEWED"
        assert manifest.base_data == data and manifest.includes_collected_depth
        assert manifest.subtasks.revision == 1
        assert (destination / "hc/annotation.json").read_bytes() == annotation.content
        with package.file("data/chunk-000/file-000.parquet") as path:
            original = pq.read_table(path)
        output = pq.read_table(destination / "data/chunk-000/file-000.parquet")
        assert output.select(original.column_names).equals(original)
        assert output["subtask_index"].to_pylist() == [0, 0, -1, -1]
        assert output["hc.annotation.excluded"].to_pylist() == [False, False, True, False]
        mapping = pq.read_table(destination / "hc/annotation_source_frames.parquet").to_pylist()
        assert [row["source_frame_index"] for row in mapping] == [0, 1, 2, 3]
        assert [row["source_timestamp_ns"] for row in mapping] == original[
            "hc.timestamp_ns"
        ].to_pylist()
        copied = []
        for entry in package.manifest.files:
            if entry.path.startswith(("videos/", "hc/depth/")):
                assert (
                    hashlib.sha256((destination / entry.path).read_bytes()).hexdigest()
                    == entry.sha256
                )
                copied.append(entry.path)
            # Reload every parent member after generation; primary data is unchanged.
            with package.file(entry.path):
                pass
        assert package.manifest.content_hash == parent_hash
        assert any(name.endswith("validity/flags.parquet") for name in copied)
        document = json.loads((destination / "hc/annotation.json").read_text())
        assert len(document["revision"]["tags"]) == 2
        assert document["revision"]["tags"][1]["relations"][0]["target"]["object_id"] == "cup"
        evidence = {
            "synthetic_input": True,
            "layer": "writer using real package and revision services",
            "queue_api_browser_verified": False,
            "base_data": data.model_dump(mode="json"),
            "artifact_content_hash": content_hash,
            "manifest": manifest.model_dump(mode="json"),
            "unchanged_media_and_depth_evidence": copied,
            "primary_unchanged": True,
            "annotation_file_bytes_unchanged": True,
            "subtask_index": [0, 0, -1, -1],
        }
        (destination.parent / (data.data_version_id + ".json")).write_text(
            json.dumps(evidence, indent=2)
        )
