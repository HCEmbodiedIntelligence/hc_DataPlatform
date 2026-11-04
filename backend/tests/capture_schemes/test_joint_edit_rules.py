import hashlib
import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from mcap_ros2._dynamic import serialize_dynamic

from hc_data_platform.continuous_recordings.joint_normalization import JOINTS
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.mcap_native import decoded
from hc_data_platform.continuous_recordings.mcap_selection import select_mcap
from hc_data_platform.verification.ports import McapRos2DecoderProbe
from .test_mcap_v3 import JOINT
from .test_mcap_v4 import config


def rules(**updates):
    ops = [
        {"kind":"zero_joints", "enabled":True, "axes":updates.get("zero_joints", ["head_1", "head_2"])},
        {"kind":"action_from_state", "enabled":True, "axes":updates.get("action_from_state", list(JOINTS[7:])), "match":"at_or_after"},
        {"kind":"generate_action", "enabled":True, "source":"state"},
    ]
    return McapProcessingConfig.model_validate({**config().model_dump(),
        "postprocessing_rules": {"version":2, "rules":updates.get("rules", ops)}})


def record(path, actions):
    encode = serialize_dynamic("sensor_msgs/msg/JointState", JOINT)["sensor_msgs/msg/JointState"]
    with path.open("wb") as stream:
        w = Writer(stream); w.start(profile="ros2")
        schema = w.register_schema("sensor_msgs/msg/JointState", "ros2msg", JOINT.encode())
        for topic, times in (("/observation/joint_states", [100, 140]), ("/action/joint_states", actions)):
            c = w.register_channel(topic, "cdr", schema)
            for i,t in enumerate(times):
                stamp = 10**12 + t * 10**6
                names = list(reversed((*JOINTS, "head_2", "head_1")))
                action = topic.startswith("/action")
                values = [float("nan") if n.startswith("head") or (action and n == "leg_1")
                          else (2000 if action else 0) + t + JOINTS.index(n) for n in names]
                obj = {"name": names, "position": values, "velocity": [v+1 for v in values],
                       "effort": [v+2 for v in values],
                       "header": {"stamp": {"sec": stamp//10**9, "nanosec": stamp%10**9}, "frame_id": "body"}}
                w.add_message(c, stamp + (70 if action else 0)*10**6, encode(obj), stamp, i)
        w.finish()


@pytest.mark.parametrize("actions", [[101, 140], []])
def test_forward_match_and_constant_heads_preserve_training_contract(tmp_path, actions):
    raw, out = tmp_path/"raw.mcap", tmp_path/"derived.mcap"
    record(raw, actions)
    cfg = rules()
    before = hashlib.sha256(raw.read_bytes()).digest()
    summary = select_mcap(raw, out, cfg)["normalization"]
    assert hashlib.sha256(raw.read_bytes()).digest() == before
    assert cfg.layout("action")[0] == cfg.layout("state")[0] == JOINTS
    assert summary["replaced_action_messages"] == len(actions)
    assert summary["generated_action_messages"] == (0 if actions else 2)
    with out.open("rb") as stream:
        rows = list(make_reader(stream).iter_messages())
    assert len(rows) == 4
    for s,c,m in rows:
        v = decoded(s,c,m,McapRos2DecoderProbe())
        action = c.topic == cfg.action_topic and bool(actions)
        ms = v.header.stamp.nanosec//10**6
        assert m.log_time == 10**12 + (ms + (70 if action else 0))*10**6
        for offset, field in enumerate(("position", "velocity", "effort")):
            by_name = dict(zip(v.name, getattr(v, field), strict=True))
            assert by_name["head_1"] == by_name["head_2"] == 0
            for i,n in enumerate(JOINTS):
                base = (140 if i >= 7 else 2000 + ms) if action else ms
                assert by_name[n] == base + i + offset
    if actions:
        audit = json.loads(out.with_suffix(".audit.json").read_text())
        refs = [r["axes"]["leg_1"] for r in audit["messages"] if r["topic"] == cfg.action_topic]
        assert [ref["delta_ns"] for ref in refs] == [39_000_000, 0]


def test_no_following_state_reports_error(tmp_path):
    raw = tmp_path/"raw.mcap"
    record(raw, [141])
    with pytest.raises(ValueError, match="MCAP_FOLLOWING_STATE_UNAVAILABLE"):
        select_mcap(raw, tmp_path/"derived.mcap", rules())


def test_conflicting_rules_or_unknown_copy_axis_rejected():
    with pytest.raises(ValueError, match="disjoint"):
        rules(zero_joints=["leg_1"])
    with pytest.raises(ValueError, match="named action joints"):
        rules(action_from_state=["typo"])

def test_independent_selection_preserves_other_commands(tmp_path):
    from .test_mcap_v4 import fixture
    raw = fixture(tmp_path/"raw.mcap", [(110,list(JOINTS))], corrupt="nan")
    target = tmp_path/"selected.mcap"
    ops = [
        {"kind":"action_from_state","enabled":False,"axes":list(JOINTS[7:]),"match":"at_or_after"},
        {"kind":"fill_position","enabled":True,"missing_position_axes":None,"nan_position_axes":["leg_1"],"max_state_delta_ms":20},
    ]
    result = select_mcap(raw,target,rules(rules=ops))
    assert result["normalization"]["fallback_by_axis"] == {"leg_1":1}
    assert result["normalization"]["replaced_action_messages"] == 0
    with target.open("rb") as stream:
        row, = make_reader(stream).iter_messages(topics=["/action/joint_states"])
        action = decoded(*row,McapRos2DecoderProbe())
        assert list(action.name) == list(JOINTS)
        assert action.position[7] == pytest.approx(7.11)
        assert action.position[14] == pytest.approx(14.1)
