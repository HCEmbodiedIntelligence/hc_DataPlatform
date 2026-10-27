"""Real SQL/media packages; synthetic four-frame annotation selection fixture."""

import copy
import json

import pyarrow.parquet as pq
import pytest

from hc_data_platform.annotation.subtasks import SubtaskTrack
from hc_data_platform.episode_store.collection_export import CollectionSource, write_collection
from hc_data_platform.episode_store.normalized_export import frame_selection
from tests.episode_store.test_annotation_revision_files_services import files  # noqa: F401
from tests.episode_store.test_collection_export_services import pair  # noqa: F401
from tests.episode_store.test_commit_services import scoped


@pytest.mark.integration
def test_invalid_gap_exports_one_episode_with_matching_source_maps(pair, tmp_path, monkeypatch):  # noqa: F811
    from contextlib import contextmanager

    import pyarrow as pa

    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH

    source, first, _ = pair
    with scoped(source):
        original_file = first.file
        with original_file(DATA_PATH) as path:
            table = pq.read_table(path)
        table = table.set_column(
            table.schema.get_field_index("hc.sample_valid"),
            "hc.sample_valid",
            pa.array([True, False, True, True]),
        )
        changed = tmp_path / "with-gap.parquet"
        pq.write_table(table, changed)

        @contextmanager
        def file(relative):
            if relative == DATA_PATH:
                yield changed
            else:
                with original_file(relative) as path:
                    yield path

        monkeypatch.setattr(first, "file", file)
        root = tmp_path / "selected"
        manifest, _ = write_collection(root, [CollectionSource(first)], modalities="rgbd")
        assert manifest["total_frames"] == 3 and len(manifest["members"]) == 1
        data = pq.read_table(root / "data")
        assert data["index"].to_pylist() == [0, 1, 2]
        assert data["frame_index"].to_pylist() == [0, 1, 2]
        assert data["action"].to_pylist() == table["action"].take(pa.array([0, 2, 3])).to_pylist()
        trace = pq.read_table(root / "hc/collection_frames.parquet")
        assert trace["parent_frame_index"].to_pylist() == [0, 2, 3]
        import av

        for path in (root / "videos").rglob("*.mp4"):
            with av.open(str(path)) as video:
                assert len(list(video.decode(video=0))) == 3
        # The independent-package download is also a training format. It must
        # not reintroduce the same old invalid row or claim a parent commit.
        import io
        import zipfile
        from types import SimpleNamespace

        from hc_data_platform.episode_store.delivery import EpisodeDeliveryService, archive_packages
        from hc_data_platform.episode_store.delivery_models import RequestEpisodeDelivery

        packages = SimpleNamespace(files=SimpleNamespace(open=lambda *_: first))
        service = SimpleNamespace(packages=packages)
        plan = EpisodeDeliveryService._freeze(service, RequestEpisodeDelivery(data=(first.receipt.data,)))
        archive = tmp_path / "independent.zip"
        totals = []
        archive_packages(packages, plan, archive, lambda: None, lambda *_: None,
                         pin_totals=lambda files, size: totals.append((files, size)))
        with zipfile.ZipFile(archive) as stored:
            assert stored.testzip() is None
            result = pq.read_table(io.BytesIO(stored.read("episode/" + DATA_PATH)))
            assert result["frame_index"].to_pylist() == [0,1,2]
            assert "episode/hc/commit.json" not in stored.namelist()
            assert json.loads(stored.read("episode/hc/parent_commit.json"))["data"]["frame_count"] == 4
            assert totals[-1][0] == len(stored.namelist())


@pytest.mark.integration
def test_selected_frames_tasks_media_and_values_agree(pair, files, tmp_path, monkeypatch):  # noqa: F811
    source, first, _ = pair
    _, task, service, auth = files
    original = first.document

    def document(path):
        value = copy.deepcopy(original(path))
        if path == "meta/info.json":
            value["hc.capture_contract"] = {"algorithm": "capture-structure/v3"}
        return value

    monkeypatch.setattr(first, "document", document)
    with scoped(source):
        state = service.status(task.task_id, 1, auth)
        if state.status != "READY":
            service.generate(task.task_id, 1, state.generation, state.revision_content_hash, auth)
        annotation = service.download(task.task_id, auth, revision=1, allow_unreviewed=True)
        selected = CollectionSource(
            first,
            annotation,
            SubtaskTrack(track_id="chosen", label="任务分段", annotation_ids=("parent",)),
        )
        frames = frame_selection(selected)
        assert [r["parent_frame_index"] for r in frames.values()] == [0, 1, 3]
        manifest, _ = write_collection(tmp_path / "selected", [selected], modalities="rgbd")
        assert manifest["format"] == "hc-lerobot-collection/4"
        assert manifest["total_frames"] == 3 and len(manifest["members"]) == 2
        rows = pq.read_table(tmp_path / "selected/data").to_pylist()
        assert [r["index"] for r in rows] == [0, 1, 2]
        assert [r["episode_index"] for r in rows] == [0, 0, 1]
        assert [r["frame_index"] for r in rows] == [0, 1, 0]
        with first.file("data/chunk-000/file-000.parquet") as path:
            parent = pq.read_table(path).to_pylist()
        for row, index in zip(rows, (0, 1, 3), strict=True):
            for key in ("action", "observation.state", "hc.timestamp_ns"):
                assert row[key] == parent[index][key]
            assert not row["hc.annotation.excluded"]
        mapping = pq.read_table(tmp_path / "selected/hc/collection_frames.parquet").to_pylist()
        assert [r["parent_frame_index"] for r in mapping] == [0, 1, 3]
        info = json.loads((tmp_path / "selected/meta/info.json").read_bytes())
        assert info["total_episodes"] == 2
        assert not list((tmp_path / "selected").rglob("*.mcap"))
