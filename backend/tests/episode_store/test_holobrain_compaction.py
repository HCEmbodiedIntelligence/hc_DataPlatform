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

@pytest.mark.parametrize("remove_command_stream", [False, True])
def test_committed_state_labels_follow_selected_rows_and_preserve_command_audit(tmp_path, remove_command_stream):
    from mcap.reader import make_reader
    from mcap.writer import Writer
    from hc_data_platform.continuous_recordings.joint_normalization import JOINTS
    from hc_data_platform.episode_store.holobrain_export import ACTION, STATE, committed_action_labels

    raw = tmp_path / "raw"
    raw.mkdir()
    src = source(raw, missing_actions=(0, 4))
    selected = committed_selection(tmp_path / "parent", [True, True, False, True, True])
    names = list(reversed(JOINTS))
    selected.package.document = lambda key: {"features": {"action": {"names": names}}} if key == "meta/info.json" else {}
    stamps = [10**12 + i * 33_000_000 for i in range(5)]
    kinds = ["next_frame_state", "next_frame_state", "unfilled_state", "recorded_command", "terminal_state"]
    origins = [json.dumps({"origin": k, "source_topic": STATE, "source_timestamp_ns": t + 7,
                           "source_frame": i + int(k == "next_frame_state")})
               for i, (k, t) in enumerate(zip(kinds, stamps, strict=True))]
    with selected.package.file(DATA_PATH) as path:
        table = pq.read_table(path)
        for key, values in {
            "action": [[100 * i + JOINTS.index(n) for n in names] for i in range(5)],
            "hc.valid.action": [True, True, False, True, True],
            "hc.axis_validity.action": [json.dumps([True] * len(names))] * 5,
            "hc.action_source_origins": origins,
        }.items():
            table = table.append_column(key, pa.array(values))
        pq.write_table(table, path)
    frames = frame_selection(selected, compact_invalid=True)
    src.update(frames=frames, action_labels=committed_action_labels(selected.package, frames))
    if remove_command_stream:
        filtered = tmp_path / "without-action.mcap"
        with src["path"].open("rb") as incoming, filtered.open("wb") as outgoing:
            writer = Writer(outgoing)
            writer.start()
            channels = {}
            for schema, channel, message in make_reader(incoming).iter_messages():
                if channel.topic == ACTION:
                    continue
                if channel.id not in channels:
                    sid = writer.register_schema(schema.name, schema.encoding, schema.data)
                    channels[channel.id] = writer.register_channel(channel.topic, channel.message_encoding, sid)
                writer.add_message(channels[channel.id], message.log_time, message.data,
                                   message.publish_time, message.sequence)
            writer.finish()
        src["path"] = filtered

    out = tmp_path / "dataset"
    write_dataset(out, [src], reference_report=True)
    traces = [json.loads(line) for line in (out / "alignment_provenance.jsonl").read_text().splitlines()]
    assert [tr["parent_frame_index"] for tr in traces] == ([0, 1, 4] if remove_command_stream else [0, 1, 3, 4])
    for row, tr in zip(rows(out), traces, strict=True):
        i = tr["parent_frame_index"]
        if i == 3 or (i == 1 and not remove_command_stream):
            assert row["actions"]["timestamps"] == [stamps[i]]
            assert "axes" in tr["action_normalization"]
            continue
        assert tr["action_normalization"]["origin"] == kinds[i]
        assert row["actions"]["timestamps"] == [stamps[i] + 7]
        assert row["actions"]["position"]["data"] == [
            i % 2 if n in ("L_ban", "R_ban") else 100 * i + JOINTS.index(n) for n in row["actions"]["names"]
        ]
    report = json.loads((out / "conversion_report.json").read_bytes())
    assert report["episodes"][0]["frames"]["state_derived_actions"] == {
        "next_frame_state": 2 if remove_command_stream else 1, "terminal_state": 1,
    }
