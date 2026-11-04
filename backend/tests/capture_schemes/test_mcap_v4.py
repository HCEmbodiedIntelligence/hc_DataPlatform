"""Full-position normalization boundaries, using real CDR serialization."""

import hashlib
import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from mcap_ros2._dynamic import serialize_dynamic

from hc_data_platform.capture_schemes.hc_tj import configuration
from hc_data_platform.continuous_recordings.joint_normalization import JOINTS
from hc_data_platform.continuous_recordings.mcap_native import decoded
from hc_data_platform.continuous_recordings.mcap_selection import select_mcap
from hc_data_platform.verification.ports import McapRos2DecoderProbe
from .test_mcap_v3 import JOINT


def config():
    cfg, _ = configuration(holobrain=False, clock_evidence="synthetic shared ROS clock")
    output = dict(cfg.semantic_output)
    output["features"] = {k: v for k, v in output["features"].items() if k in {"action", "observation.state"}}
    output["time_source"] = "STATE_LOG_TIME"
    return cfg.model_copy(update={"collection_mode": "JOINT_ONLY", "time_source": "STATE_LOG_TIME",
                                  "primary_camera": None, "cameras": (), "semantic_output": output})


def fixture(path, commands, *, state_stamps=(100, 120, 140), old=False, corrupt=None):
    encoder = serialize_dynamic("sensor_msgs/msg/JointState", JOINT)["sensor_msgs/msg/JointState"]
    with path.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="ros2")
        schema = writer.register_schema("sensor_msgs/JointState" if old else "sensor_msgs/msg/JointState", "ros2msg", JOINT.encode())
        state = writer.register_channel("io_teleop/joint_states" if old else "/observation/joint_states", "cdr", schema)
        action = writer.register_channel("io_teleop/joint_cmd" if old else "/action/joint_states", "cdr", schema)
        junk = writer.register_channel("/unselected/broken", "cdr", schema)
        for channel, rows in ((state, [(t, list(reversed(JOINTS))) for t in state_stamps]), (action, commands)):
            for i, (ms, names) in enumerate(rows):
                stamp = 10**12 + ms * 10**6
                values = [0.0 if name == "leg_1" and channel == action else ms / 1000 + (JOINTS.index(name) if name in JOINTS else 99) for name in names]
                if channel == action and corrupt == "nan":
                    values[names.index("leg_1")] = float("nan")
                obj = dict(header=dict(stamp=dict(sec=stamp // 10**9, nanosec=stamp % 10**9), frame_id="body"),
                           name=names, position=values, velocity=[], effort=[])
                writer.add_message(channel, stamp + 5_000_000, encoder(obj), stamp, i)
        writer.add_message(junk, 10**12, b"broken", 10**12)
        writer.add_attachment(0, 0, "secret.bin", "application/octet-stream", b"hidden-original")
        writer.add_metadata("private", {"secret": "unselected"})
        writer.finish()
    return path


@pytest.mark.parametrize("old", [True, False])
def test_layouts_missing_waist_tie_earlier_and_real_zero(tmp_path, old):
    names = list(JOINTS)
    commands = [(110, names[:14]), (120, names[:14] + ["R_ban", "L_ban", "head_1", "head_2"] + names[14:]),
                (140, names[:15]), (140, list(reversed(names)))]
    raw = fixture(tmp_path / "raw.mcap", commands, old=old)
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    out = tmp_path / "selected.mcap"
    result = select_mcap(raw, out, config())
    assert result["normalization"]["fallback_messages"] == 2
    assert result["normalization"]["fallback_by_axis"] == {"leg_1": 1, "leg_2": 2, "zhi": 2}
    audit = json.loads(out.with_suffix(".audit.json").read_bytes())
    assert audit["messages"][0]["axes"]["leg_1"]["delta_ns"] == -10_000_000
    with out.open("rb") as stream:
        reader = make_reader(stream, validate_crcs=True)
        rows = [(s, c, m) for s, c, m in reader.iter_messages()]
        assert not list(reader.iter_attachments())
        assert [m.name for m in reader.iter_metadata()] == ["hc.capture.format"]
    decoder = McapRos2DecoderProbe()
    actions = [decoded(s, c, m, decoder) for s, c, m in rows if c.topic == config().action_topic]
    assert all(list(a.name) == names and len(a.position) == 17 for a in actions)
    assert actions[0].position[14] == 14.1
    assert actions[1].position[14] == 0.0
    assert actions[2].position[14] == 0.0
    assert actions[2].position[15] == 15.14
    assert {c.topic for _, c, _ in rows} == {config().state_topic, config().action_topic}
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("stamp,kept", [(80, True), (79, False), (160, True), (161, False)])
def test_20ms_boundary(tmp_path, stamp, kept):
    raw = fixture(tmp_path / "raw.mcap", [(stamp, list(JOINTS[:14]))])
    result = select_mcap(raw, tmp_path / "out.mcap", config())
    assert result["normalization"]["dropped_action_messages"] == (0 if kept else 1)


@pytest.mark.parametrize("names,corrupt", [(list(JOINTS), "nan"), (list(JOINTS[1:]), None), ([JOINTS[0], *JOINTS[:-1]], None)])
def test_invalid_commands_cannot_become_state_fallback(tmp_path, names, corrupt):
    raw = fixture(tmp_path / "raw.mcap", [(120, names)], corrupt=corrupt)
    with pytest.raises(ValueError, match="MCAP_(NONFINITE_JOINT|JOINT_LAYOUT_MISMATCH)"):
        select_mcap(raw, tmp_path / "out.mcap", config())


def ruled_config(**overrides):
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig

    doc = config().model_dump()
    doc["postprocessing_rules"] = {
        "version": 1, "missing_position_axes": ["leg_1"], "nan_position_axes": ["leg_1"],
        "max_state_delta_ms": 20, "missing_action_topic": "copy_state", **overrides,
    }
    return McapProcessingConfig.model_validate(doc)


def test_selected_rule_repairs_nan_only_on_allowed_axes(tmp_path):
    raw = fixture(tmp_path / "raw.mcap", [(110, list(JOINTS))], corrupt="nan")
    target = tmp_path / "derived.mcap"
    result = select_mcap(raw, target, ruled_config())
    assert result["normalization"]["fallback_by_axis"] == {"leg_1": 1}
    assert result["normalization"]["generated_action_messages"] == 0
    with target.open("rb") as stream:
        row, = make_reader(stream).iter_messages(topics=["/action/joint_states"])
        assert decoded(*row, McapRos2DecoderProbe()).position[14] == pytest.approx(14.1)
    with pytest.raises(ValueError, match="MCAP_NONFINITE_JOINT"):
        select_mcap(raw, target, ruled_config(nan_position_axes=[]))
    summary = select_mcap(raw, target, ruled_config(max_state_delta_ms=9))["normalization"]
    assert summary["dropped_action_messages"] == 1
    assert summary["generated_action_messages"] == 0


def test_absent_action_topic_uses_state_frames_only_if_selected(tmp_path):
    raw = fixture(tmp_path / "raw.mcap", [], old=True)
    target = tmp_path / "derived.mcap"
    with pytest.raises(ValueError, match="MISSING"):
        select_mcap(raw, target, ruled_config(missing_action_topic="reject"))
    result = select_mcap(raw, target, ruled_config())
    assert result["normalization"]["generated_action_messages"] == 3
    assert result["message_counts"]["/action/joint_states"] == 3
    decoder = McapRos2DecoderProbe()
    with target.open("rb") as stream:
        rows = list(make_reader(stream).iter_messages())
        def positions(topic):
            return [(m.log_time, m.sequence, list(decoded(s,c,m,decoder).position))
                    for s,c,m in rows if c.topic == topic]
        assert positions("/action/joint_states") == positions("/observation/joint_states")
    audit = json.loads(target.with_suffix(".audit.json").read_bytes())
    assert all(row["generated_from_state"] for row in audit["messages"])
