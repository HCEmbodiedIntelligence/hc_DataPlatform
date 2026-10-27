"""Drop unmatched samples without shifting images, controls or source references."""

import copy
import json
from contextlib import contextmanager
from types import SimpleNamespace

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from hc_data_platform.data_formats.lerobot_layout import DATA_PATH
from hc_data_platform.episode_store.holobrain_export import write_dataset
from hc_data_platform.episode_store.holobrain_validation import validate
from hc_data_platform.episode_store.models import canonical, digest
from hc_data_platform.episode_store.normalized_export import frame_selection
from tests.annotation.test_subtasks import snapshot  # noqa: F401
from tests.episode_store.test_holobrain_export import rows, source


def committed_selection(root, valid, *, task_indexes=None):
    """Small committed frame table with original acquisition times and validity."""
    stamps = [10**12 + i * 33_000_000 for i in range(len(valid))]
    (root / DATA_PATH).parent.mkdir(parents=True)
    pq.write_table(pa.table({
        "source.timestamp_ns": stamps,
        "task_index": task_indexes or [0] * len(valid),
        "hc.sample_valid": valid,
    }), root / DATA_PATH)
    (root / "meta").mkdir()
    pq.write_table(pa.table({"task_index": [0, 1], "task": ["抓取", "放下"]}),
                   root / "meta/tasks.parquet")

    @contextmanager
    def file(relative):
        yield root / relative

    package = SimpleNamespace(
        file=file,
        document=lambda _: {"end_offset_ns": stamps[-1] + 33_000_000},
        receipt=SimpleNamespace(data=None),
    )
    return SimpleNamespace(package=package, annotation_file=None, track=None)


def test_missing_actions_compact_all_output_indexes_and_keep_exact_modalities(tmp_path):
    complete, missing = tmp_path / "complete", tmp_path / "missing"
    complete.mkdir()
    missing.mkdir()
    write_dataset(tmp_path / "reference", [source(complete)])
    expected = rows(tmp_path / "reference")

    selected = committed_selection(tmp_path / "parent", [True, False, False, True, True])
    legacy = frame_selection(selected)
    compact = frame_selection(selected, compact_invalid=True)
    assert [f["segment"] for f in legacy.values()] == [0, 1, 1]
    assert [f["segment"] for f in compact.values()] == [0, 0, 0]
    current = source(missing, missing_actions=(1, 2))
    current["frames"] = compact
    manifest, _ = write_dataset(tmp_path / "output", [current])
    actual = rows(tmp_path / "output")

    assert [r["index"] for r in actual] == [0, 1, 2]
    assert [r["frame_index"] for r in actual] == [0, 1, 2]
    assert [r["episode_index"] for r in actual] == [0, 0, 0]
    for row, parent_index in zip(actual, (0, 3, 4), strict=True):
        for key in ("joints", "actions", "head", "head_depth", "timestamp_min", "timestamp_max"):
            assert row[key] == expected[parent_index][key]
    traces = [json.loads(line) for line in
              (tmp_path / "output/alignment_provenance.jsonl").read_text().splitlines()]
    assert [r["parent_frame_index"] for r in traces] == [0, 3, 4]
    assert [r["frame_index"] for r in traces] == [0, 1, 2]
    assert [r["rgb_time_ns"] for r in traces] == list(compact)
    with duckdb.connect(str(tmp_path / "output/meta_db.duckdb"), read_only=True) as db:
        episodes = db.execute(
            'SELECT "index",dataset_begin_index,frame_num FROM episode'
        ).fetchall()
        assert episodes == [(0, 0, 3)]
    report = json.loads((tmp_path / "output/conversion_report.json").read_text())
    assert report["episodes"] == 1 and report["episode_frame_counts"] == [3]
    assert len(report["sources"][0]["sync_drops"]) == 2
    assert manifest["format"] == "hc-holobrain-collection/2"
    assert validate(tmp_path / "output")["provenance_indexes"]


@pytest.mark.parametrize("valid, parents", [
    ([False, True, True, False, True, False], [1, 2, 4]),
    ([False, False, False], []),
    ([True, False, False], [0]),
])
def test_leading_trailing_and_all_invalid_frames(tmp_path, valid, parents):
    selected = committed_selection(tmp_path / "parent", valid)
    frames = frame_selection(selected, compact_invalid=True)
    assert [f["parent_frame_index"] for f in frames.values()] == parents
    assert [f["segment"] for f in frames.values()] == [0] * len(parents)


def test_task_change_inside_discarded_run_still_breaks_segment(tmp_path):
    selected = committed_selection(
        tmp_path / "parent", [True, False, False, True, True], task_indexes=[0, 1, 0, 0, 0]
    )
    frames = frame_selection(selected, compact_invalid=True)
    assert [f["parent_frame_index"] for f in frames.values()] == [0, 3, 4]
    assert [f["segment"] for f in frames.values()] == [0, 1, 1]


def test_annotation_exclusion_remains_a_boundary_when_invalid_frames_are_compacted(
    tmp_path, snapshot  # noqa: F811
):
    document, data, track = copy.deepcopy(snapshot)
    document["effective_exclusions"] = [{"start_step": 6, "end_step": 7}]
    selected = committed_selection(tmp_path / "parent", [i not in (2, 7) for i in range(18)])
    selected.package.receipt.data = data
    selected.annotation_file = SimpleNamespace(
        content=canonical(document),
        file=SimpleNamespace(content_sha256=digest(document)),
    )
    selected.track = track.model_copy(update={"annotation_ids": ()})
    frames = frame_selection(selected, compact_invalid=True)
    assert [f["parent_frame_index"] for f in frames.values()] == [0, 1, 3, 4, 5, *range(8, 18)]
    assert [f["segment"] for f in frames.values()] == [0] * 5 + [1] * 10


@pytest.mark.parametrize("field,value,error", [
    ("frame_index", 7, "INDEX_MISMATCH"),
    ("episode_index", 1, "INDEX_MISMATCH"),
    ("parent_frame_index", 0, "SOURCE_FRAME_MAP_INVALID"),
])
def test_validator_rejects_shifted_or_backwards_frame_mapping(tmp_path, field, value, error):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_dataset(tmp_path / "out", [source(raw)])
    path = tmp_path / "out/alignment_provenance.jsonl"
    traces = [json.loads(line) for line in path.read_text().splitlines()]
    traces[1][field] = value
    path.write_bytes(b"".join(canonical(row) + b"\n" for row in traces))
    with pytest.raises(ValueError, match=error):
        validate(tmp_path / "out")
