"""Reassemble real immutable service packages; sensor data remains synthetic."""

import copy
import json
import os
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pyarrow.parquet as pq
import pytest

from hc_data_platform.annotation.models import AnnotationTag
from hc_data_platform.annotation.revision_files import SnapshotBusy
from hc_data_platform.annotation.subtasks import SubtaskTrack
from hc_data_platform.episode_store.collection_export import (
    CollectionIncompatible,
    CollectionSource,
    pooled_stats,
    write_collection,
)
from hc_data_platform.lance_catalog.models import DatasetSchemaSnapshot
from hc_data_platform.publishing.depth_evidence import file_hash
from tests.episode_store.test_annotation_bindings_services import actor
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_commit_services import scoped
from tests.episode_store.test_depth_window import forbid_quantization
from tests.episode_store.test_writer import build_rgbd_package


@pytest.fixture
def pair(target, tmp_path, request):
    options = getattr(request, "param", {})
    packages, source, _, _, request = target
    identity = source.identity.model_copy(
        update={"episode_id": "second", "rollout_id": "second-rollout"}
    )
    directory = tmp_path / "second"
    directory.mkdir()
    root, other, _, _ = build_rgbd_package(
        directory,
        identity,
        intensity_offset=50,
        depth_offset=1000,
        task="z distinct second task",
        **options,
    )
    with scoped(source):
        receipt = packages.committer.commit(root, other)
        first = packages.files.open(source.identity, request.data_ref.data_version_id)
        second = packages.files.open(other.identity, receipt.data.data_version_id)
    return source, first, second


@pytest.mark.integration
def test_collection_rebuilds_indexes_tasks_stats_and_reuses_rgbd(pair, tmp_path, monkeypatch):
    source, first, second = pair
    with scoped(source):
        forbid_quantization(monkeypatch)
        evidence = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "collection-export"
            / first.receipt.data.data_version_id
        )
        manifest, checksum = write_collection(
            evidence / "rgbd",
            [CollectionSource(second), CollectionSource(first)],
            modalities="rgbd",
        )
        assert manifest["total_frames"] == 8 and len(manifest["members"]) == 2
        assert len(pq.read_table(evidence / "rgbd/meta/tasks.parquet")) == 2
        data = pq.read_table(evidence / "rgbd/data")
        assert data["index"].to_pylist() == list(range(8))
        assert data["episode_index"].to_pylist() == [0] * 4 + [1] * 4
        assert data["frame_index"].to_pylist() == list(range(4)) * 2
        stats = json.loads((evidence / "rgbd/meta/stats.json").read_bytes())
        for key in ("index", "episode_index", "task_index", "action", "observation.state"):
            np.testing.assert_allclose(stats[key]["mean"], [np.mean(data[key].to_pylist())])
            np.testing.assert_allclose(stats[key]["std"], [np.std(data[key].to_pylist())])
        info = json.loads((evidence / "rgbd/meta/info.json").read_bytes())
        assert info["total_episodes"] == 2 and info["total_frames"] == 8
        episodes = pq.read_table(evidence / "rgbd/meta/episodes").to_pylist()
        for member, episode in zip(manifest["members"], episodes, strict=True):
            assert member["dataset_from_index"] == episode["episode_index"] * 4
            assert set(member["camera_mapping"].values()) == set(member["media"])
            for feature, media in member["media"].items():
                assert file_hash(evidence / "rgbd" / media["path"]) == media["sha256"]
                assert episode[f"videos/{feature}/file_index"] == member["episode_index"]
                assert episode[f"videos/{feature}/from_timestamp"] == 0
                if media["evidence_path"]:
                    assert (
                        evidence / "rgbd" / media["evidence_path"] / "validity/flags.parquet"
                    ).is_file()
        depth = "observation.images.head_depth"
        assert (
            manifest["members"][0]["media"][depth]["sha256"]
            != manifest["members"][1]["media"][depth]["sha256"]
        )
        rgb, rgb_hash = write_collection(
            evidence / "rgb", [CollectionSource(first), CollectionSource(second)], modalities="rgb"
        )
        assert all(m["excluded_depth_features"] == [depth] for m in rgb["members"])
        rgb_info = json.loads((evidence / "rgb/meta/info.json").read_bytes())
        assert depth not in rgb_info["features"]
        assert not any(
            depth in p.as_posix() and p.suffix in {".mp4", ".parquet"}
            for p in (evidence / "rgb").rglob("*")
        )
        again, repeated_hash = write_collection(
            tmp_path / "again",
            [CollectionSource(first), CollectionSource(second)],
            modalities="rgbd",
        )
        assert checksum == repeated_hash and manifest == again
        for package in (first, second):
            for entry in package.manifest.files:
                with package.file(entry.path) as path:
                    assert file_hash(path) == entry.sha256
        (evidence / "result.json").write_text(
            json.dumps(
                {
                    "synthetic_input": True,
                    "rgbd_content_hash": checksum,
                    "rgb_content_hash": rgb_hash,
                    "frozen_data": [m["data"] for m in manifest["members"]],
                    "distinct_videos": True,
                    "primary_files_unchanged": True,
                    "transcoded": False,
                    "requantized": False,
                    "queue_api_browser_verified": False,
                },
                indent=2,
            )
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "prepared_bundle,pair,all_absent",
    [
        ({"depth_missing_frames": [0, 1, 2, 3]}, {}, False),
        (
            {"depth_missing_frames": [0, 1, 2, 3]},
            {"depth_invalid_frames": [0, 1, 2, 3]},
            True,
        ),
    ],
    indirect=["prepared_bundle", "pair"],
)
def test_collection_preserves_unmeasured_depth_and_only_pools_valid_statistics(
    pair, all_absent, tmp_path, monkeypatch
):
    source, first, second = pair
    depth = "observation.images.head_depth"
    root = (
        Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
        / "missing-depth-collection"
        / first.receipt.data.data_version_id
        / ("all-absent" if all_absent else "mixed")
    )
    with scoped(source):
        forbid_quantization(monkeypatch)
        manifest, _ = write_collection(
            root, [CollectionSource(first), CollectionSource(second)], modalities="rgbd"
        )
        info = json.loads((root / "meta/info.json").read_bytes())["features"][depth]["info"]
        stats = json.loads((root / "meta/stats.json").read_bytes())
        rows = pq.read_table(root / "meta/episodes").to_pylist()
        for member, row in zip(manifest["members"], rows, strict=True):
            absent = all_absent or member["data"] == first.receipt.data.model_dump(mode="json")
            assert (row.get(f"stats/{depth}/mean") is None) == absent
            media = member["media"][depth]
            assert file_hash(root / media["path"]) == media["sha256"]
            assert (root / media["evidence_path"] / "validity/flags.parquet").is_file()
        if all_absent:
            assert depth not in stats
            assert info["hc.stats_valid_pixels"] == 0
            assert info["hc.stats_status"] == "NO_VALID_PIXELS"
        else:
            expected = second.document("meta/stats.json")[depth]
            assert stats[depth] == {**expected, "count": [8]}
            assert "hc.stats_status" not in info
            assert info["hc.stats_valid_pixels"] > 0


@pytest.mark.integration
@pytest.mark.parametrize("segment_ids", [("parent",), ()])
def test_annotation_collection_keeps_full_tags_and_global_subtasks(
    pair,
    files,  # noqa: F811
    tmp_path,
    monkeypatch,
    segment_ids,
):
    source, first, second = pair
    _, task, service, auth = files
    with scoped(source):
        for _ in range(100):
            state = service.status(task.task_id, 1, auth)
            if state.status == "READY":
                break
            try:
                service.generate(
                    task.task_id, 1, state.generation, state.revision_content_hash, auth
                )
            except SnapshotBusy:
                time.sleep(0.1)
        annotation = service.download(task.task_id, auth, revision=1, allow_unreviewed=True)
        track = SubtaskTrack(track_id="chosen", label="任务分段", annotation_ids=segment_ids)
        forbid_quantization(monkeypatch)
        output = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / ("collection-annotated" if segment_ids else "collection-without-segments")
            / first.receipt.data.data_version_id
        )
        manifest, _ = write_collection(
            output,
            [
                CollectionSource(first, annotation, track),
                CollectionSource(second),
            ],
            modalities="rgbd",
        )
        for member in manifest["members"]:
            table = pq.read_table(output / member["data_path"])
            if member["annotation"]:
                assert (
                    output / member["attachments_root"] / "hc/annotation.json"
                ).read_bytes() == annotation.content
                assert table["subtask_index"].to_pylist() == (
                    [0, 0, -1, -1] if segment_ids else [-1] * 4
                )
                assert table["hc.annotation.excluded"].to_pylist() == [False, False, True, False]
                assert (
                    member["annotation"]["revision_content_hash"]
                    == annotation.file.revision_content_hash
                )
            else:
                assert table["subtask_index"].to_pylist() == [-1] * 4
                assert table["hc.annotation.excluded"].to_pylist() == [False] * 4
        if segment_ids:
            segments = [
                json.loads(line)
                for line in (output / "hc/subtasks.jsonl").read_bytes().splitlines()
            ]
            assert len(segments) == 1 and segments[0]["subtask_index"] == 0
            assert segments[0]["source_subtask_index"] == 0
        else:
            assert not (output / "hc/subtasks.jsonl").exists()


@pytest.mark.integration
@pytest.mark.parametrize("field", ["fps", "units", "shape", "camera", "profile"])
def test_incompatible_members_are_explicitly_rejected(pair, tmp_path, monkeypatch, field):
    source, first, second = pair
    original = second.document

    def changed(path):
        result = copy.deepcopy(original(path))
        if path == "meta/info.json":
            if field == "fps":
                result["fps"] = 60
            if field == "units":
                result["features"]["action"]["units"] = ["rad"]
            if field == "shape":
                result["features"]["action"]["shape"] = [2]
            if field == "camera":
                result["hc.feature_mapping"]["different-camera"] = "observation.images.head_rgb"
        if path.endswith("/profile.json") and field == "profile":
            result["version"] = "different"
        return result

    monkeypatch.setattr(second, "document", changed)
    with scoped(source), pytest.raises(CollectionIncompatible, match="incompatible") as caught:
        write_collection(
            tmp_path / "rejected",
            [CollectionSource(first), CollectionSource(second)],
            modalities="rgbd",
        )
    assert not (tmp_path / "rejected").exists()
    assert (
        caught.value.field
        == {
            "fps": "fps",
            "units": "features.action.units",
            "shape": "features.action.shape",
            "camera": "hc.feature_mapping.different-camera",
            "profile": "depth_profile.observation.images.head_depth.version",
        }[field]
    )


def test_depth_pool_uses_valid_pixels_instead_of_frame_weight():
    a = {"count": [4], "mean": [[[10.0]]], "std": [[[0.0]]], "min": [[[10.0]]], "max": [[[10.0]]]}
    b = {"count": [4], "mean": [[[0.0]]], "std": [[[0.0]]], "min": [[[0.0]]], "max": [[[0.0]]]}
    result = pooled_stats([(a, 4), (b, 1)])
    assert result == {
        "count": [8],
        "mean": [[[8.0]]],
        "std": [[[4.0]]],
        "min": [[[0.0]]],
        "max": [[[10.0]]],
    }


@pytest.mark.integration
def test_two_selected_revisions_rebase_segment_ids_across_episode_boundary(
    pair,
    files,  # noqa: F811
    target,
    tmp_path,
    monkeypatch,
):
    source, first, second = pair
    _, first_task, revision_files, auth = files
    packages, _, annotation_service, automatic, request = target
    identity = second.receipt.data.identity
    with scoped(source):
        schema = DatasetSchemaSnapshot.create(
            project_id=identity.project_id,
            dataset_id=identity.dataset_id,
            schema_snapshot_id=source.schema_snapshot_id,
            frequency_hz=30,
            fields={
                "/observation/state": "list<float64>",
                "action": "list<float64>",
                "observation.images.head_rgb": "json",
                "observation.images.head_depth": "json",
            },
        )
        collection = packages.collections.publish(second.receipt.data, schema)
        second_task = automatic.ensure_task(
            replace(
                request,
                rollout_id=identity.rollout_id,
                data_ref=second.receipt.data,
                dataset_version=collection.version,
                source_workflow_id="collection-second-annotation/"
                + second.receipt.data.data_version_id,
            )
        )
        claimed = annotation_service.claim(second_task.task_id, actor(identity))
        annotation_service.save_draft(
            second_task.task_id,
            actor(identity),
            (),
            tags=(
                AnnotationTag(
                    annotation_id="second-segment",
                    tag_id="s",
                    path=("s",),
                    label="第二段",
                    start_step=1,
                    end_step=3,
                ),
            ),
            expected_revision=0,
            if_match=claimed.etag,
            client_mutation_id="second-revision",
        )
        selections = []
        for package, task, annotation_id in (
            (first, first_task, "parent"),
            (second, second_task, "second-segment"),
        ):
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
            file = revision_files.download(task.task_id, auth, revision=1, allow_unreviewed=True)
            selections.append(
                CollectionSource(
                    package,
                    file,
                    SubtaskTrack(
                        track_id="explicit",
                        label="任务分段",
                        annotation_ids=(annotation_id,),
                    ),
                )
            )
        forbid_quantization(monkeypatch)
        output = (
            Path(os.getenv("HC_G1_WRITER_EVIDENCE_ROOT", str(tmp_path)))
            / "collection-two-revisions"
            / first.receipt.data.data_version_id
        )
        manifest, _ = write_collection(output, selections, modalities="rgbd")
        segments = [
            json.loads(line) for line in (output / "hc/subtasks.jsonl").read_bytes().splitlines()
        ]
        assert [s["subtask_index"] for s in segments] == [0, 1]
        for i, member in enumerate(manifest["members"]):
            indices = pq.read_table(output / member["data_path"])["subtask_index"].to_pylist()
            if member["data"]["data_version_id"] == first.receipt.data.data_version_id:
                assert indices == [i, i, -1, -1]
            else:
                assert indices == [-1, i, i, -1]
            assert segments[i]["episode_index"] == i and segments[i]["source_subtask_index"] == 0
