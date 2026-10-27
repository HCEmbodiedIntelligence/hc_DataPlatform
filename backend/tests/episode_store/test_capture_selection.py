import json

import pyarrow.parquet as pq
import pytest

from hc_data_platform.core.context import (
    RequestContext,
    bind_request_context,
    reset_request_context,
)
from hc_data_platform.episode_store.models import PackageInput
from hc_data_platform.episode_store.prepared_assets import PreparedAssets
from hc_data_platform.episode_store.validation import validate_package
from hc_data_platform.episode_store.writer import write_episode
from hc_data_platform.lance_catalog.models import StepRecord
from tests.episode_store.test_writer import build_rgbd_package, identity


def test_historical_sparse_source_map_uses_parent_indices_once():
    import pyarrow as pa

    from hc_data_platform.episode_store.frame_mapping import aligned_mapping

    original = pa.table({"frame_index": [0, 1, 2, 3], "timestamp_ns": [0, 33, 66, 99]})
    rows = pa.table({"frame_index": [0, 1, 2], "hc.source_frame_index": [0, 2, 3]})
    repaired = aligned_mapping(original, rows, {})
    assert repaired["frame_index"].to_pylist() == [0, 1, 2]
    assert repaired["timestamp_ns"].to_pylist() == [0, 66, 99]
    assert aligned_mapping(repaired, rows, {"hc.frame_map_version": 2}).equals(repaired)
    with pytest.raises(ValueError, match="does not cover"):
        aligned_mapping(original, rows, {"hc.frame_map_version": 2})


def test_repetition_is_recomputed_against_retained_depth_identity():
    from hc_data_platform.episode_store.frame_policy import refresh_repetition

    previous = {}
    for number, expected in ((7, False), (7, True), (9, False)):
        row = {"hc.repeated.depth": True, "hc.valid.depth": True,
               "hc.source_ref.depth": json.dumps({"depth_source_id": "head", "depth_source_seq": number})}
        refresh_repetition(row, previous)
        assert row["hc.repeated.depth"] == expected


@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("leading_missing", [False, True])
def test_invalid_rows_are_compacted_without_legacy_capture_quality_gate(tmp_path, shared, leading_missing):
    ident = identity()
    source = PackageInput(
        identity=ident,
        source_sha256="1" * 64,
        processor_version="test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id="schema-1",
        required_features=("action",),
    )
    rows = [
        StepRecord(
            rollout_id=ident.rollout_id,
            step_index=i,
            timestamp_ns=i * 33_333_333,
            modalities={"action": None if leading_missing and i == 0 else [float(i)]},
            valid={"action": i != (0 if leading_missing else 1)},
            sample_valid=i != (0 if leading_missing else 1),
        )
        for i in range(4)
    ]
    metadata = (
        {
            "quality": {"capture_quality": {"valid_ratio": 0.75}},
            "capture": {
                "effective_adapter": {"configuration": {"semantic_output": {"present": True}}}
            },
        }
        if shared
        else {}
    )
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        )
    )
    try:
        root = tmp_path / "episode"
        write_episode(
            root, source, rows, PreparedAssets(ident, "attempt", {}), fps=30, metadata=metadata
        )
        data = pq.read_table(root / "data/chunk-000/file-000.parquet")
        assert data["action"].to_pylist() == ([1., 2., 3.] if leading_missing else [0., 2., 3.])
        assert data["frame_index"].to_pylist() == [0, 1, 2]
        assert validate_package(root, source).frame_count == 3
    finally:
        reset_request_context(token)


def test_missing_row_drops_state_and_action_but_preserves_actual_time(tmp_path):
    ident = identity()
    source = PackageInput(
        identity=ident,
        source_sha256="1" * 64,
        processor_version="test/1",
        configuration_sha256="2" * 64,
        schema_snapshot_id="schema-1",
        required_features=("observation.state", "action"),
    )
    origin = 1790000000000000001
    rows = []
    for i in range(4):
        good = i != 1
        modalities = {
            "/observation/state": {
                "values": [float(i) if good else None],
                "names": ["joint"],
                "units": ["rad"],
            },
            "/action": {
                "values": [float(10 + i) if good else None],
                "names": ["joint"],
                "units": ["rad"],
                "source_origins": {"arm": "recorded_command"},
            },
            "source.timestamp_ns": origin + i * 33_333_333 if good else None,
        }
        rows.append(
            StepRecord(
                rollout_id=ident.rollout_id,
                step_index=i,
                timestamp_ns=i * 33_333_333,
                modalities=modalities,
                valid={key: good for key in modalities},
                sample_valid=good,
            )
        )
    token = bind_request_context(
        RequestContext(
            organization_id=ident.organization_id,
            project_id=ident.project_id,
            region_code=ident.region_code,
        )
    )
    try:
        root = tmp_path / "episode"
        manifest = write_episode(
            root,
            source,
            rows,
            PreparedAssets(ident, "attempt", {}),
            fps=30,
            metadata={"quality": {"capture_quality": {"valid_ratio": 0.75}}},
        )
        assert manifest.frame_count == 3
        table = pq.read_table(root / "data/chunk-000/file-000.parquet").to_pydict()
        assert table["observation.state"] == [0.0, 2.0, 3.0]
        assert table["action"] == [10.0, 12.0, 13.0]
        assert table["hc.timestamp_ns"] == [origin, origin + 66_666_666, origin + 99_999_999]
        assert table["timestamp"] == [0.0, 0.066666666, 0.099999999]
        assert table["hc.video_frame_index"] == [0, 1, 2]
        assert table["hc.source_frame_index"] == [0, 2, 3]
        assert table["hc.video_timestamp"] == [0.0, 1 / 30, 2 / 30]
        assert table["hc.sample_valid"] == [True] * 3
        mapping = pq.read_table(root / "hc/source_frames.parquet").to_pydict()
        assert mapping["frame_index"] == [0, 1, 2]
        assert mapping["parent_frame_index"] == [0, 2, 3]
        assert mapping["timestamp_ns"] == table["hc.timestamp_ns"]
        stats = json.loads((root / "meta/stats.json").read_bytes())
        assert stats["action"]["count"] == [3]
        assert stats["action"]["min"] == [10.0]
        assert validate_package(root, source).frame_count == 3
        import pyarrow as pa

        mapping["timestamp_ns"][1] = mapping["timestamp_ns"][0] + 33_333_333
        pq.write_table(pa.table(mapping), root / "hc/source_frames.parquet")
        with pytest.raises(ValueError, match="source frame map"):
            validate_package(root, source)
    finally:
        reset_request_context(token)


def test_sparse_rgb_depth_compacts_all_media_together(tmp_path):
    root, source, _, _ = build_rgbd_package(tmp_path)
    import pyarrow as pa

    from hc_data_platform.data_formats.lerobot_layout import DATA_PATH
    from hc_data_platform.episode_store.capture_selection import compact_capture_rows

    table = pq.read_table(root / DATA_PATH)
    rows = table.to_pylist()
    for i, row in enumerate(rows):
        row["hc.sample_valid"] = i != 1
        row["source.timestamp_ns"] = 1000000000 + i * 33_333_333
    pq.write_table(pa.Table.from_pylist(rows), root / DATA_PATH)
    info = json.loads((root / "meta/info.json").read_bytes())
    info["hc.feature_mapping"]["source.timestamp_ns"] = "source.timestamp_ns"
    info["features"]["source.timestamp_ns"] = {"dtype": "int64", "shape": [1], "names": None}
    (root / "meta/info.json").write_text(json.dumps(info))
    compact_capture_rows(root)
    assert validate_package(root, source).frame_count == 3

    import av

    from hc_data_platform.data_formats.lerobot_layout import VIDEO_PATH

    info = json.loads((root / "meta/info.json").read_bytes())
    assert info["hc.media_frame_count"] == 3
    assert info["hc.source_frame_count"] == 4
    assert info["hc.video_duration_seconds"] == 0.1
    for key, feature in info["features"].items():
        if feature["dtype"] != "video":
            continue
        path = root / VIDEO_PATH.format(video_key=key, chunk_index=0, file_index=0)
        with av.open(str(path)) as video:
            frames = list(video.decode(video=0))
            assert len(frames) == 3
            assert [float(f.pts * f.time_base) for f in frames] == pytest.approx(
                [0.0, 1 / 30, 2 / 30]
            )
            if not feature["info"].get("is_depth_map"):
                assert [
                    float(f.to_ndarray(format="rgb24").mean()) for f in frames
                ] == pytest.approx([0.0, 100.0, 150.0], abs=4)
    retained = pq.read_table(root / DATA_PATH).to_pylist()
    assert [r["hc.source_frame_index"] for r in retained] == [0, 2, 3]
    assert [r["hc.timestamp_ns"] for r in retained] == [1000000000, 1066666666, 1099999999]
    assert [r["hc.video_frame_index"] for r in retained] == [0, 1, 2]
