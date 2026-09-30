"""Real package/revision services; derived primary data, not sensor acceptance."""

import json
import os
import time
from pathlib import Path

import av
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.annotation.delivery import AnnotationDeliveryService
from hc_data_platform.annotation.delivery_models import RequestAnnotationDelivery
from hc_data_platform.annotation.delivery_repository import PostgresAnnotationDeliveries
from hc_data_platform.annotation.revision_files import SnapshotBusy
from hc_data_platform.episode_store.derivation import freeze_tag_episode, write_tag_episode
from hc_data_platform.episode_store.derivation_quality import evaluate_tag_quality
from hc_data_platform.episode_store.models import canonical
from hc_data_platform.episode_store.postgres import PackageConflict
from hc_data_platform.episode_store.quality import read_episode_quality
from hc_data_platform.episode_store.validation import parquet_rows
from hc_data_platform.publishing.lerobot_export import DATA_PATH
from hc_data_platform.quality.models import QualityCode
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import decoded, forbid_quantization


@pytest.fixture
def selected(files, target):  # noqa: F811
    source, task, revision_files, auth = files
    packages = target[0]
    delivery = AnnotationDeliveryService(
        revision_files, packages, PostgresAnnotationDeliveries(packages.repository.connections)
    )
    with scoped(source):
        for _ in range(100):
            state = revision_files.status(task.task_id, 1, auth)
            if state.status == "READY":
                break
            try:
                revision_files.generate(
                    task.task_id, 1, state.generation, state.revision_content_hash, auth
                )
            except SnapshotBusy:
                time.sleep(0.1)
        assert state.status == "READY"

    def freeze(tag, revision=1):
        annotation, _ = delivery._freeze(
            task.task_id,
            RequestAnnotationDelivery(
                revision=revision,
                allow_unreviewed=True,
                track={
                    "track_id": "tag-episode",
                    "label": "创建新 Episode",
                    "annotation_ids": [tag],
                },
            ),
            auth,
        )
        snapshot = delivery.snapshot_for_plan(annotation, auth)
        data = annotation.binding.data
        package = packages.files.open(data.identity, data.data_version_id)
        schema = packages.schemas.schema_for(data.identity.project_id, data.identity.dataset_id)
        plan = freeze_tag_episode(package, annotation, snapshot, schema)
        return package, snapshot, plan

    return source, packages, freeze


@pytest.mark.integration
@pytest.mark.parametrize("tag,start,end", [("parent", 0, 2), ("child", 1, 4)])
def test_tag_interval_commits_new_rgbd_identity_and_preserves_parent(
    selected, tmp_path, monkeypatch, tag, start, end
):
    source, packages, freeze = selected
    with scoped(source):
        package, snapshot, plan = freeze(tag)
        parent = package.receipt.data
        assert plan.input.identity.episode_id != parent.identity.episode_id
        assert plan.input.identity.rollout_id != parent.identity.rollout_id
        assert (plan.start_step, plan.end_step) == (start, end)
        forbid_quantization(monkeypatch)
        root = tmp_path / "derived"
        manifest = write_tag_episode(root, package, snapshot, plan)
        again = write_tag_episode(tmp_path / "again", package, snapshot, plan)
        assert manifest == again
        receipt = packages.committer.commit(root, plan.input)
        assert packages.committer.commit(root, plan.input) == receipt
        version, ready = packages._publish_committed(receipt, plan.input, plan.schema_snapshot)
        assert ready.data == receipt.data and ready.step_count == end - start
        assert len(version.episodes) == 2 and parent in version.episodes
        child = packages.files.open(plan.input.identity, receipt.data.data_version_id)
        quality = read_episode_quality(child)
        assert quality.status == "PASS" and quality.evidence_status == "AVAILABLE"
        assert quality.report.rollout_id == plan.input.identity.rollout_id
        assert {r.code for r in quality.rules} == set(QualityCode)
        assert all(m.expected_frame_count == end - start for m in quality.report.topic_metrics)
        black = next(
            r
            for r in quality.rules
            if r.code == QualityCode.IMAGE_BLACK and r.topic.endswith("head_rgb")
        )
        assert black.observed["observed_count"] == (1 if start == 0 else 0)
        for rule in quality.rules:
            if rule.topic and rule.topic.endswith("head_depth"):
                if rule.code == QualityCode.IMAGE_CORRUPT:
                    assert rule.status == "pass" and rule.evaluated_count == end - start
                if rule.code in {QualityCode.IMAGE_BLACK, QualityCode.IMAGE_REPEATED}:
                    assert rule.status == "skip" and rule.evaluated_count == 0
        with package.file(DATA_PATH) as path:
            original = pq.read_table(path)
        result = pq.read_table(root / DATA_PATH)
        assert result.schema == original.schema
        for key in original.column_names:
            if key in {"index", "frame_index", "timestamp", "task_index"} or key.startswith(
                "hc.repeated."
            ):
                continue
            assert result[key].equals(original[key].slice(start, end - start)), key
        assert result["index"].to_pylist() == list(range(end - start))
        trace = pq.read_table(root / "hc/derivation/frames.parquet").to_pylist()
        assert [r["parent_frame_index"] for r in trace] == list(range(start, end))
        assert [r["parent_timestamp_ns"] for r in trace] == original["hc.timestamp_ns"].to_pylist()[
            start:end
        ]
        assert (root / "hc/derivation/parent/annotation.json").read_bytes() == snapshot.content
        origin = json.loads((root / "hc/source.json").read_bytes())
        assert origin["derivation"]["parent_data"] == parent.model_dump(mode="json")
        assert origin["derivation"]["child_approvals_inherited"] is False
        assert "start_offset_ns" not in origin  # This parent did not record a raw clock/window.
        for key, feature in package.document("meta/info.json")["features"].items():
            if feature["dtype"] != "video":
                continue
            video = f"videos/{key}/chunk-000/file-000.mp4"
            with package.file(video) as path:
                if feature["info"].get("is_depth_map"):
                    np.testing.assert_array_equal(decoded(root / video), decoded(path)[start:end])
                else:
                    with av.open(str(path)) as container:
                        rgb = list(container.decode(video=0))[start:end]
                    with av.open(str(root / video)) as container:
                        clipped = list(container.decode(video=0))
                    assert len(rgb) == len(clipped) == end - start
            if feature["info"].get("is_depth_map"):
                evidence = child.document("hc/modalities.json")["depth"][key]["evidence_path"]
                frames = list(parquet_rows(root / evidence / "frames.parquet"))
                assert [r["target_timestamp_ns"] for r in frames] == result[
                    "hc.timestamp_ns"
                ].to_pylist()
                assert [r["parent_frame_index"] for r in frames] == list(range(start, end))
                assert child.document(evidence + "/report.json")["window"]["requantized"] is False
                for index in range(end - start):
                    before = packages.depth.read(
                        parent.identity, parent.data_version_id, key, start + index, unit="m"
                    )
                    after = packages.depth.read(
                        receipt.data.identity, receipt.data.data_version_id, key, index, unit="m"
                    )
                    np.testing.assert_equal(after.values, before.values)
                    np.testing.assert_array_equal(after.flags, before.flags)
        # Primary parent and its immutable annotation remain readable after child publication.
        for path in package.entries:
            with package.file(path):
                pass
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "tag-derivations"
            / receipt.data.data_version_id
        )
        evidence.mkdir(parents=True)
        for entry in child.manifest.files:
            with child.file(entry.path) as path:
                destination = evidence / "episode" / entry.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(path.read_bytes())
        (evidence / "episode/hc/manifest.json").write_bytes(
            canonical(child.manifest.model_dump(mode="json"))
        )
        (evidence / "episode/hc/commit.json").write_bytes(
            canonical(child.receipt.model_dump(mode="json"))
        )
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "data": receipt.data.model_dump(mode="json"),
                    "plan": plan.model_dump(mode="json"),
                    "parent_unchanged": True,
                    "codes_and_physical_values_preserved": True,
                    "synthetic_input": True,
                    "queue_api_browser_verified": False,
                    "model_depth_training_support": "NOT_VALIDATED",
                },
                indent=2,
            )
        )


@pytest.mark.integration
@pytest.mark.parametrize("field", ["window", "encoder", "annotation", "tenant", "quality_count"])
def test_forged_tag_derivation_rejected_before_output(selected, tmp_path, field):
    source, _, freeze = selected
    with scoped(source):
        package, snapshot, plan = freeze("parent")
        if field == "window":
            plan = plan.model_copy(update={"start_step": 1})
        elif field == "encoder":
            plan = plan.model_copy(update={"encoding": {**plan.encoding, "pyav": "other"}})
        elif field == "annotation":
            from dataclasses import replace

            snapshot = replace(snapshot, content=snapshot.content + b" ")
        elif field == "quality_count":
            quality = plan.encoding["quality"]
            plan = plan.model_copy(
                update={
                    "encoding": {
                        **plan.encoding,
                        "quality": {
                            **quality,
                            "expected_frames": {k: 99 for k in quality["expected_frames"]},
                        },
                    }
                }
            )
        else:
            plan = plan.model_copy(
                update={
                    "parent": plan.parent.model_copy(
                        update={
                            "identity": plan.parent.identity.model_copy(
                                update={"organization_id": "other"}
                            )
                        }
                    )
                }
            )
        with pytest.raises((ValueError, PermissionError, PackageConflict)):
            write_tag_episode(tmp_path / "rejected", package, snapshot, plan)
        assert not (tmp_path / "rejected").exists()


@pytest.mark.integration
@pytest.mark.parametrize(
    "target",
    [
        {
            "metadata": {
                "source": {
                    "recording_id": "actual-frozen-parent-id",
                    "slice_id": "parent-slice",
                    "start_offset_ns": 5_000_000_000,
                    "end_offset_ns": 5_133_333_333,
                    "clock": "recording_relative",
                }
            }
        }
    ],
    indirect=True,
)
def test_recording_window_uses_parent_clock_and_full_interval_reuses_bytes(
    selected,
    files,  # noqa: F811
    target,
    tmp_path,
    monkeypatch,  # noqa: F811
):
    source, packages, freeze = selected
    _, task, revision_files, auth = files
    annotation = target[2]
    with scoped(source):
        package, snapshot, plan = freeze("child")
        write_tag_episode(tmp_path / "cut", package, snapshot, plan)
        origin = json.loads((tmp_path / "cut/hc/source.json").read_bytes())
        assert origin["start_offset_ns"] == 5_033_333_333
        assert origin["end_offset_ns"] == 5_133_333_333
        assert origin["recording_id"] == "actual-frozen-parent-id"
        assert "slice_id" not in origin
        assert origin["derivation"]["parent_recording_window"]["slice_id"] == "parent-slice"
        history = annotation.get_history(task.task_id, auth)
        # Make an explicit, different immutable revision whose chosen Tag spans the parent.
        tags = tuple(
            t.model_copy(update={"end_step": 4}) if t.annotation_id == "parent" else t
            for t in history.revisions[1].tags
        )
        annotation.save_draft(
            task.task_id,
            actor(source.identity),
            (),
            tags=tags,
            expected_revision=1,
            if_match=history.task.etag,
            client_mutation_id="whole-selection",
        )
        for _ in range(100):
            state = revision_files.status(task.task_id, 2, auth)
            if state.status == "READY":
                break
            try:
                revision_files.generate(
                    task.task_id, 2, state.generation, state.revision_content_hash, auth
                )
            except SnapshotBusy:
                time.sleep(0.1)
        assert state.status == "READY"
        parent, whole, whole_plan = freeze("parent", 2)
        forbid_quantization(monkeypatch)
        from hc_data_platform.publishing.holobrain_depth import DepthVideo

        monkeypatch.setattr(
            DepthVideo, "__init__", lambda *a, **k: pytest.fail("whole depth reencoded")
        )
        write_tag_episode(tmp_path / "whole", parent, whole, whole_plan)
        for path, entry in parent.entries.items():
            if path.startswith(("videos/", "hc/depth/")):
                import hashlib

                assert (
                    hashlib.sha256((tmp_path / "whole" / path).read_bytes()).hexdigest()
                    == entry.sha256
                )
        original = annotation.get_history(task.task_id, auth)
        assert original.revisions[1].tags == history.revisions[1].tags
        assert whole_plan.input.identity != plan.input.identity


@pytest.mark.integration
def test_legacy_plan_replays_unchanged_and_new_qc_is_a_separate_data_version(selected, tmp_path):
    source, _, freeze = selected
    with scoped(source):
        package, snapshot, current = freeze("child")
        legacy = freeze_tag_episode(
            package,
            current.annotation,
            snapshot,
            current.schema_snapshot,
            display_name=current.display_name,
            quality_contract=None,
        )
        assert legacy.input.identity == current.input.identity
        assert legacy.input.data_version_id != current.input.data_version_id
        assert legacy.encoding == {k: v for k, v in current.encoding.items() if k != "quality"}
        root = tmp_path / "legacy"
        manifest = write_tag_episode(root, package, snapshot, legacy)
        assert write_tag_episode(tmp_path / "replay", package, snapshot, legacy) == manifest
        report = json.loads((root / "hc/quality.json").read_bytes())
        assert report["status"] == "UNKNOWN" and report["parent_decision_inherited"] is False
        assert not (root / "hc/quality_rules.json").exists()


@pytest.mark.integration
def test_quality_reads_selected_primary_values_and_validity_instead_of_parent_verdict(
    selected, tmp_path
):
    source, _, freeze = selected
    with scoped(source):
        package, snapshot, plan = freeze("child")
        root = tmp_path / "selected"
        write_tag_episode(root, package, snapshot, plan)
        info = json.loads((root / "meta/info.json").read_bytes())
        contract = json.loads(json.dumps(plan.encoding["quality"]))
        contract["profile"]["action"]["maximum_jump_risk"] = 0.5
        contract["profile"]["action"]["maximum_jump_reject"] = 2.0
        stricter = plan.model_copy(update={"encoding": {**plan.encoding, "quality": contract}})
        assert stricter.input.identity == plan.input.identity
        assert stricter.input.data_version_id != plan.input.data_version_id
        evaluation = evaluate_tag_quality(root, stricter, info)
        jumps = [f for f in evaluation.report.findings if f.code == QualityCode.ACTION_JUMP]
        assert len(jumps) == 2 and all(f.observed == 1.0 for f in jumps)
        assert evaluation.report.status.value == "RISK"
        # Mutate only this uncommitted test output; no input package or product
        # pointer changes. Actual child validity must change its QC result.
        table = pq.read_table(root / DATA_PATH)
        index = table.schema.get_field_index("hc.valid.action")
        pq.write_table(
            table.set_column(index, table.schema.field(index), pa.array([False] * 3)),
            root / DATA_PATH,
        )
        evaluation = evaluate_tag_quality(root, plan, info)
        assert any(f.code == QualityCode.ACTION_MISSING for f in evaluation.report.findings)
        action = next(r for r in evaluation.evidence.rules if r.code == QualityCode.ACTION_MISSING)
        assert action.observed["observation_count"] == 0
        metric = next(m for m in evaluation.report.topic_metrics if m.topic == "action")
        assert metric.message_count == 0 and metric.expected_frame_count == 3
        with package.file(DATA_PATH) as path:
            assert pq.read_table(path)["hc.valid.action"].to_pylist() == [True] * 4
