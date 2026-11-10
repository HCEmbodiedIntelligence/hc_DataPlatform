"""Selected annotation identity defines exported episodes, not label text."""

import copy
import json
import shutil
import zipfile
from types import SimpleNamespace

import duckdb
import pytest
from mcap.reader import make_reader

from hc_data_platform.annotation.models import AnnotationRevision
from hc_data_platform.annotation.validation import revision_content_hash
from hc_data_platform.episode_store.collection_delivery import CollectionDelivery
from hc_data_platform.episode_store.export_formats import get_format
from hc_data_platform.episode_store.holobrain_export import RGB
from hc_data_platform.episode_store.models import canonical, digest
from hc_data_platform.episode_store.normalized_export import frame_selection
from tests.annotation.test_subtasks import snapshot  # noqa: F401
from tests.episode_store.test_holobrain_compaction import committed_selection
from tests.episode_store.test_holobrain_export import rows, source


def labels(tmp_path, snapshot, *, invalid=(), excluded=()):  # noqa: F811
    document, data, track = copy.deepcopy(snapshot)
    tags = [
        dict(
            document["revision"]["tags"][-1],
            annotation_id=f"label-{i}",
            label="dagger",
            start_step=start,
            end_step=end,
        )
        for i, (start, end) in enumerate(((1, 2), (2, 3), (4, 5)))
    ]
    document["revision"]["tags"] = tags
    revision = AnnotationRevision.model_validate(document["revision"])
    document["revision"]["content_hash"] = revision_content_hash(
        base_lance_version=None,
        data_ref=data,
        tag_schema_id=revision.tag_schema_id,
        tag_schema_version=revision.tag_schema_version,
        tags=revision.tags,
        operations=(),
    )
    document["effective_exclusions"] = [dict(start_step=i, end_step=i + 1) for i in excluded]
    selected = committed_selection(tmp_path, [i not in invalid for i in range(18)])
    selected.package.receipt.data = data
    selected.annotation_file = SimpleNamespace(
        content=canonical(document),
        file=SimpleNamespace(content_sha256=digest(document), revision=0),
    )
    selected.track = track.model_copy(update={"annotation_ids": ("label-2", "label-1", "label-0")})
    return selected


def test_adjacent_same_name_labels_are_independent_and_only_selected_frames_remain(
    tmp_path, snapshot  # noqa: F811
):
    selected = labels(tmp_path / "parent", snapshot)
    frames = list(frame_selection(selected, compact_invalid=True).values())
    assert [r["parent_frame_index"] for r in frames] == [1, 2, 4]
    assert [r["segment"] for r in frames] == [0, 1, 2]
    assert [r["annotation_id"] for r in frames] == ["label-0", "label-1", "label-2"]
    legacy = list(frame_selection(selected, split_annotations=False).values())
    assert len(legacy) == 18
    assert legacy[1]["segment"] == legacy[2]["segment"]
    selected.track = selected.track.model_copy(update={"annotation_ids": ()})
    assert len(frame_selection(selected)) == 18


@pytest.mark.parametrize("invalid,excluded,parents", [((1,), (4,), [2]), ((1, 2, 4), (), [])])
def test_invalid_and_excluded_selection_never_falls_back_to_full_episode(
    tmp_path, snapshot, invalid, excluded, parents  # noqa: F811
):
    selected = labels(tmp_path / "parent", snapshot, invalid=invalid, excluded=excluded)
    assert [
        r["parent_frame_index"] for r in frame_selection(selected, compact_invalid=True).values()
    ] == parents


@pytest.mark.parametrize("output_format", ["holobrain", "mcap"])
def test_download_zip_has_three_same_name_episodes_and_exact_selected_media(
    tmp_path, snapshot, monkeypatch, output_format  # noqa: F811
):
    from hc_data_platform.episode_store import (
        holobrain_export,
        native_sources,
        original_export,
        selected_cache,
    )

    raw = tmp_path / "raw"
    raw.mkdir()
    real = source(raw)
    real["provenance"]["format"] = "hc-mcap-processing/1"
    selected = labels(tmp_path / "parent", snapshot)
    file = SimpleNamespace(
        object=SimpleNamespace(sha256=real["provenance"]["selected_mcap"]["sha256"])
    )
    monkeypatch.setattr(
        native_sources, "export_source", lambda *_: (file, real["provenance"], real["audit_path"])
    )
    monkeypatch.setattr(
        selected_cache,
        "materialize_selected",
        lambda storage, file, provenance, path, checkpoint: shutil.copyfile(real["path"], path),
    )
    monkeypatch.setattr(holobrain_export, "selected_source", lambda *_, **__: None)
    monkeypatch.setattr(
        holobrain_export,
        "copy_original",
        lambda storage, file, path, checkpoint: shutil.copyfile(file, path),
    )
    monkeypatch.setattr(
        original_export,
        "copy_original",
        lambda storage, file, path, checkpoint: shutil.copyfile(file, path),
    )
    repository = SimpleNamespace(
        phase=lambda *_: None, pin_totals=lambda *_: None, progress=lambda *_: None
    )
    delivery = CollectionDelivery(SimpleNamespace(repository=repository, storage=None))
    monkeypatch.setattr(delivery, "sources", lambda _: [selected])
    archive_format = get_format(output_format).archive
    plan = SimpleNamespace(
        archive_format=archive_format,
        modalities="rgbd",
        job_id="test",
        model_dump=lambda **_: {"archive_format": archive_format},
    )
    archive_path = tmp_path / "download.zip"
    delivery.build(plan, tmp_path, archive_path, {}, lambda: None)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        archive.extractall(tmp_path / "download")
    if output_format == "holobrain":
        downloaded = tmp_path / "download"
        actual = rows(downloaded)
        assert [r["episode_index"] for r in actual] == [0, 1, 2]
        assert [r["frame_index"] for r in actual] == [0, 0, 0]
        with duckdb.connect(str(downloaded / "meta_db.duckdb"), read_only=True) as db:
            assert db.execute('SELECT frame_num FROM episode ORDER BY "index"').fetchall() == [
                (1,),
                (1,),
                (1,),
            ]
            assert db.execute("SELECT name FROM task").fetchall() == [("dagger",)]
        holobrain_export.write_dataset(tmp_path / "full", [real])
        full = rows(tmp_path / "full")
        for row, index in zip(actual, (1, 2, 4), strict=True):
            for key in (
                "head",
                "head_depth",
                "joints",
                "actions",
                "timestamp_min",
                "timestamp_max",
            ):
                assert row[key] == full[index][key]
        report = json.loads((downloaded / "conversion_report.json").read_bytes())
        assert report["episode_count"] == 3 and report["total_frames"] == 3
    else:
        downloaded = tmp_path / "download/collection"
        files = sorted(downloaded.glob("episode-*.mcap"))
        assert len(files) == 3
        for path, index in zip(files, (1, 2, 4), strict=True):
            with path.open("rb") as stream:
                stamps = [m.log_time for _, _, m in make_reader(stream).iter_messages(topics=[RGB])]
            assert stamps == [10**12 + index * 33_000_000]
            frame_map = json.loads(path.with_name(path.stem + "-frames.json").read_bytes())
            assert [r["parent_frame_index"] for r in frame_map] == [index]
