"""Actual IPC/DuckDB readback, legacy-name merge and annotation-value equivalence."""

import io
import json

import numpy as np
import pyarrow as pa
import pytest
from mcap.writer import Writer
from PIL import Image

from hc_data_platform.capture_schemes.hc_tj import configuration
from hc_data_platform.continuous_recordings.joint_normalization import HANDS, JOINTS
from hc_data_platform.continuous_recordings.mcap_selection import ALIASES, select_mcap
from hc_data_platform.episode_store.holobrain_export import write_dataset
from hc_data_platform.episode_store.holobrain_validation import validate


def source(tmp_path, *, old=False, missing_actions=()):
    cfg, _ = configuration(clock_evidence="synthetic shared ROS time")
    raw = tmp_path / "raw.mcap"
    rgb, depth = io.BytesIO(), io.BytesIO()
    Image.new("RGB", (640, 400), (50, 100, 150)).save(rgb, "JPEG")
    Image.fromarray(np.full((400, 640), 321, dtype=np.uint16)).save(depth, "PNG")
    calibration = {"resolution": {k: {"width": 640, "height": 400} for k in ("color", "depth")},
                   "rgb": {"fx": 400, "fy": 401, "cx": 320, "cy": 200,
                           "distortion_order": ["k1", "k2", "p1", "p2", "k3"], "distortion_coefficients": [0] * 5}}
    extrinsics = {"transforms": [{"parent_frame": "zhi_Link", "child_frame": "camera_head",
                                  "translation_m": [0.1, 0.2, 0.3], "rotation_xyzw": [0, 0, 0, 1]}]}
    with raw.open("wb") as stream:
        w = Writer(stream)
        w.start(profile="ros2")
        channel_ids = {}
        for topic in (cfg.state_topic, cfg.action_topic, *HANDS, *(c.topic for c in cfg.cameras), *cfg.metadata_sources.values()):
            kind = "sensor_msgs/msg/JointState" if topic in {cfg.state_topic, cfg.action_topic, *HANDS} else "sensor_msgs/msg/CompressedImage" if topic.endswith("compressed") else "std_msgs/msg/String"
            alias = ALIASES[topic][0] if old else topic
            channel_ids[topic] = w.register_channel(alias, "json", w.register_schema(kind, "jsonschema", b"{}"))
        for i in range(5):
            stamp = 10**12 + i * 33_000_000
            header = {"stamp": {"sec": stamp // 10**9, "nanosec": stamp % 10**9}, "frame_id": "eye"}
            for topic, channel in channel_ids.items():
                if topic == cfg.action_topic and i in missing_actions:
                    continue
                if topic in {cfg.state_topic, cfg.action_topic}:
                    names = list(reversed(JOINTS)) if old or topic == cfg.state_topic else list(JOINTS[:14])
                    value = {"header": header, "name": names,
                             "position": [JOINTS.index(n) + i / 10 for n in names], "velocity": [], "effort": []}
                elif topic in HANDS:
                    value = {"header": header, "name": [HANDS[topic], "discarded_finger"], "position": [i % 2, 123], "velocity": [], "effort": []}
                elif topic.endswith("compressed"):
                    value = {"header": header, "format": "16UC1; png" if "depth" in topic else "jpeg",
                             "data": list(depth.getvalue() if "depth" in topic else rgb.getvalue())}
                else:
                    value = {"data": json.dumps(calibration if topic.endswith("calibration") else extrinsics)}
                w.add_message(channel, stamp, json.dumps(value).encode(), stamp, i)
        w.finish()
    selected = tmp_path / "selected.mcap"
    evidence = select_mcap(raw, selected, cfg)
    return {"path": selected, "audit_path": selected.with_suffix(".audit.json"),
            "provenance": {"config": cfg.model_dump(mode="json"), "selected_mcap": evidence},
            "task": "合成旧样本" if old else "合成新样本"}


def rows(path):
    state = json.loads((path / "state.json").read_text())
    return [row for entry in state["_data_files"] for row in pa.ipc.open_stream(str(path / entry["filename"])).read_all().to_pylist()]


def test_arrow_schema_merge_and_annotation_common_values(tmp_path):
    import duckdb
    a, b = tmp_path / "old", tmp_path / "new"
    a.mkdir(); b.mkdir()
    old, new = source(a, old=True), source(b)
    write_dataset(tmp_path / "full", [old, new])
    original = rows(tmp_path / "full")
    assert len(original) == 10
    assert [r["episode_index"] for r in original] == [0] * 5 + [1] * 5
    assert original[0]["actions"]["position"]["data"][16:] == [14.0, 15.0, 16.0]
    assert original[5]["actions"]["position"]["data"][16:] == [14.0, 15.0, 16.0]
    new["frames"] = {10**12 + i * 33_000_000: {"task": label, "segment": segment, "parent_frame_index": i}
                     for i, label, segment in ((1, "抓取", 0), (2, "抓取", 0), (4, "放下", 1))}
    write_dataset(tmp_path / "annotated", [new])
    annotated = rows(tmp_path / "annotated")
    assert [r["index"] for r in annotated] == [0, 1, 2]
    assert [r["episode_index"] for r in annotated] == [0, 0, 1]
    for row, index in zip(annotated, (6, 7, 9), strict=True):
        for key in ("joints", "actions", "head", "head_depth", "timestamp_min", "timestamp_max"):
            assert row[key] == original[index][key]
    db = duckdb.connect(str(tmp_path / "annotated/meta_db.duckdb"), read_only=True)
    assert db.execute('SELECT name FROM task ORDER BY "index"').fetchall() == [("抓取",), ("放下",)]
    assert db.execute('SELECT success FROM episode').fetchall() == [(None,), (None,)]
    db.close()
    assert not list((tmp_path / "annotated").rglob("*.mcap"))
    assert validate(tmp_path / "annotated")["frames"] == 3


def test_corrupt_target_tensor_is_rejected(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir()
    write_dataset(tmp_path / "out", [source(raw)])
    arrow = tmp_path / "out/data-00000-of-00001.arrow"
    table = pa.ipc.open_stream(str(arrow)).read_all()
    values = table.to_pylist()
    values[0]["actions"]["position"]["shape"] = [1, 16]
    with arrow.open("wb") as stream, pa.ipc.new_stream(stream, table.schema) as writer:
        writer.write_table(pa.Table.from_pylist(values, schema=table.schema))
    with pytest.raises(ValueError, match="TENSOR"):
        validate(tmp_path / "out")
