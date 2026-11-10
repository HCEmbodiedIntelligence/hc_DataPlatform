import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from mcap_ros2._dynamic import serialize_dynamic

from hc_data_platform.capture_schemes.schema import native_topic
from hc_data_platform.continuous_recordings.joint_normalization import JOINTS
from hc_data_platform.continuous_recordings.mcap_native import prepare

from .test_mcap_v3 import JOINT
from .test_mcap_v4 import config


@pytest.mark.parametrize(("enabled", "empty_message"), [(False, False), (True, False), (True, True)])
def test_hand_commands_keep_independent_history_and_provenance(tmp_path, enabled, empty_message):
    cfg = config().model_dump(mode="json")
    cfg["numeric_sources"] = {}
    cfg["postprocessing_rules"] = {"version": 2, "rules": [{"kind": "forward_fill_hands", "enabled": enabled}]}
    for side, axis in (("left", "L_ban"), ("right", "R_ban")):
        name = "action.hand." + side
        cfg["semantic_output"]["features"][name] = dict(
            role="numeric", dtype="float64", shape=[1], names=[axis], units=["command"],
        )
        cfg["numeric_sources"][name] = dict(
            topic=f"/action/{side}_hand/joint_states", fields=["position." + axis],
            message_type="sensor_msgs/msg/JointState",
        )

    src, dest = tmp_path / "raw.mcap", tmp_path / "derived"
    kind = "sensor_msgs/msg/JointState"
    encode = serialize_dynamic(kind, JOINT)[kind]
    with src.open("wb") as stream:
        writer = Writer(stream)
        writer.start(profile="ros2")
        schema = writer.register_schema(kind, "ros2msg", JOINT.encode())
        topics = [cfg["state_topic"], cfg["action_topic"],
                  *(mapping["topic"] for mapping in cfg["numeric_sources"].values())]
        state, action, left, right = [writer.register_channel(topic, "cdr", schema) for topic in topics]
        def sample(channel, i, names, positions):
            ns = 10**12 + i * 50_000_000
            header = {"frame_id": "body", "stamp": {"sec": ns // 10**9, "nanosec": ns % 10**9}}
            data = encode(dict(header=header, name=names, position=positions, velocity=[], effort=[]))
            writer.add_message(channel, ns, data, ns, i)
        for i in range(19):
            for channel in (state, action):
                sample(channel, i, list(JOINTS), [float(i)] * len(JOINTS))
        sample(left, 4, ["L_ban"], [0.0])
        if empty_message:
            sample(left, 9, ["L_ban"], [])
        sample(left, 16, ["L_ban"], [2.0])
        sample(right, 0, ["R_ban"], [7.0])
        sample(right, 17, ["R_ban"], [8.0])
        writer.finish()

    _, evidence = prepare(src, dest, cfg)
    values = {}
    with (dest / "sensors/robot.mcap").open("rb") as stream:
        for _, channel, message in make_reader(stream).iter_messages():
            values.setdefault(channel.topic, {})[message.sequence] = json.loads(message.data)
    left = values[native_topic("action.hand.left")]
    right = values[native_topic("action.hand.right")]
    provenance = values["/metadata/source"]
    assert left[0] == [None] and right[0] == [7.0]
    assert left[4] == [0.0] and right[17] == [8.0] and left[16] == [2.0]
    if not enabled:
        assert left[9] == right[9] == [None]
        assert "hand_command_forward_fill" not in evidence
        return
    assert left[9] == [0.0] and right[9] == [7.0]
    left_ref = provenance[9]["hand_command_forward_fill"]["action.hand.left"]
    assert left_ref["source_timestamp_ns"] == 10**12 + 4 * 50_000_000
    assert provenance[10]["hand_command_forward_fill"]["action.hand.left"] == left_ref
    assert provenance[9]["hand_command_forward_fill"]["action.hand.right"]["source_timestamp_ns"] == 10**12
    assert "action.hand.left" not in provenance[16].get("hand_command_forward_fill", {})
    assert evidence["hand_command_forward_fill"]["unfilled_frames"]["action.hand.left"] > 0
