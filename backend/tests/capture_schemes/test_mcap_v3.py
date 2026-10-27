"""Synthetic layouts, with real CDR and immutable selected-file evidence."""

import hashlib
import io
import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from mcap_ros2._dynamic import serialize_dynamic
from PIL import Image

from hc_data_platform.capture_schemes.models import OutputContract
from hc_data_platform.capture_schemes.processing import validate_mcap_output
from hc_data_platform.capture_schemes.schema import format_version, native_topic, semantic_schema
from hc_data_platform.continuous_recordings.capture_quality import summarize
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.mcap_native import decoded, prepare
from hc_data_platform.verification.ports import McapRos2DecoderProbe
from tests.continuous_recordings.test_capture_quality import profile
from tests.workflow.test_mcap_camera import SCHEMA, message

NAMES = [f"Joint{i}_{side}" for side in ("R", "L") for i in range(1, 8)] + ["leg_1", "leg_2", "zhi"]
EXTRA = ["R_ban", "L_ban", "head_1", "head_2"]
JOINT = """std_msgs/Header header
string[] name
float64[] position
float64[] velocity
float64[] effort
================================================================================
MSG: std_msgs/Header
builtin_interfaces/Time stamp
string frame_id
================================================================================
MSG: builtin_interfaces/Time
int32 sec
uint32 nanosec
"""


def configuration(*, camera=True):
    features = {name: dict(dtype="float64", shape=[17], names=NAMES, units=["rad"]*17, role=role,
                          required_axes=NAMES if role == "state" else NAMES[:14], validity_feature=name+".valid")
                for name, role in (("observation.state", "state"), ("action", "action"))}
    features.update({name+".valid": dict(dtype="bool", shape=[17], names=NAMES, role="validity")
                     for name in ("observation.state", "action")})
    if camera:
        features["observation.images.head"] = dict(dtype="video", shape=[24,32,3], names=["height","width","channels"], role="rgb")
    output = OutputContract(algorithm="capture-structure/v2", time_source="FRAME_TIMESTAMP", features=features)
    cfg = McapProcessingConfig(collection_mode="RGB" if camera else "JOINT_ONLY", time_source="STATE_LOG_TIME",
        state_topic="/observation/joint_states", action_topic="/action/joint_states",
        state_joint_names=NAMES, action_joint_names=NAMES, state_joint_units=["rad"]*17, action_joint_units=["rad"]*17,
        cameras=[dict(camera_id="head", topic="/observation/cameras/head/color/compressed", message_type="sensor_msgs/msg/CompressedImage")] if camera else [],
        semantic_output=output.model_dump(mode="json"))
    return cfg, output


def source(path, layouts, *, old=False, corrupt=None, gap=False):
    encoder = serialize_dynamic("sensor_msgs/msg/JointState", JOINT)["sensor_msgs/msg/JointState"]
    image = io.BytesIO()
    Image.new("RGB",(32,24),(80,110,160)).save(image,"JPEG")
    with path.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="ros2")
        schema = writer.register_schema("sensor_msgs/JointState" if old else "sensor_msgs/msg/JointState", "ros2msg", JOINT.encode())
        state = writer.register_channel("io_teleop/joint_states" if old else "/observation/joint_states", "cdr", schema)
        action = writer.register_channel("io_teleop/joint_cmd" if old else "/action/joint_states", "cdr", schema)
        camera = writer.register_channel("/io_teleop/camera_head/color" if old else "/observation/cameras/head/color/compressed", "cdr", writer.register_schema("sensor_msgs/CompressedImage" if old else SCHEMA.name, SCHEMA.encoding, SCHEMA.data))
        junk = writer.register_channel("/unselected/broken", "cdr", schema)
        for i, names in enumerate(layouts):
            stamp = 10**12 + round(i * 1e9/30)
            for channel, order in ((state, list(reversed(NAMES))), (action, list(names))):
                values = [NAMES.index(n)+0.125 if n in NAMES else 99 for n in order]
                obj = dict(header=dict(stamp=dict(sec=i, nanosec=123),frame_id="body"), name=order, position=values, velocity=[], effort=[v/2 for v in values])
                if channel == action and i == len(layouts)-1:
                    if corrupt == "duplicate": obj["name"][1] = obj["name"][0]
                    if corrupt == "length": obj["position"].pop()
                    if corrupt == "nan": obj["position"][0] = float("nan")
                if not (gap and channel == action and i>0):
                    writer.add_message(channel,stamp,encoder(obj),stamp-567,i)
            writer.add_message(camera,stamp,message(image.getvalue(),"jpeg",i).data,stamp-890,i)
            writer.add_message(junk,stamp,b"not decodable CDR",stamp,i)
        writer.finish()
    return path


@pytest.mark.parametrize("layouts,old", [([NAMES]*4,True), ([NAMES+EXTRA, NAMES[:14]+EXTRA+NAMES[14:], NAMES[:14]+EXTRA, NAMES[:14]+EXTRA],False), ([NAMES[:14]+EXTRA]*4,False)])
def test_old_current_and_future_have_one_format_and_preserve_real_values(tmp_path, layouts, old):
    cfg, output = configuration()
    raw = source(tmp_path/"raw.mcap",layouts,old=old)
    original = hashlib.sha256(raw.read_bytes()).hexdigest()
    _, evidence = prepare(raw,tmp_path/"out",cfg)
    assert all(validate_mcap_output(output,cfg,evidence)["presence"].values())
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == original
    selected = tmp_path/"out/source/selected.mcap"
    decoder = McapRos2DecoderProbe()
    with selected.open("rb") as stream:
        rows = list(make_reader(stream,validate_crcs=True).iter_messages())
    assert {c.topic for _,c,_ in rows} == {cfg.state_topic,cfg.action_topic,cfg.cameras[0].topic}
    commands = [(decoded(s,c,m,decoder),m) for s,c,m in rows if c.topic==cfg.action_topic]
    for i,(value,m) in enumerate(commands):
        expected = [n for n in NAMES if n in layouts[i]]
        assert list(value.name) == expected
        assert list(value.position) == [NAMES.index(n)+0.125 for n in expected]
        assert list(value.velocity) == []
        assert list(value.effort) == [(NAMES.index(n)+0.125)/2 for n in expected]
        assert value.header.stamp.sec == i and value.header.stamp.nanosec == 123
        assert m.publish_time == m.log_time-567
    with (tmp_path/"out/sensors/robot.mcap").open("rb") as stream:
        matrices = [json.loads(m.data) for _,c,m in make_reader(stream).iter_messages() if c.topic==native_topic("action.valid")]
    assert matrices == [[n in names for n in NAMES] for names in layouts]
    qc = summarize([tmp_path/"out/sensors/robot.mcap"],0,200_000_000,profile(),output=cfg.semantic_output)
    assert not qc["reject_codes"] and qc["valid_rows"]==len(layouts)
    # A future standard upload uses the very same reader, with identical output.
    _, future = prepare(selected,tmp_path/"future",cfg)
    assert future["format_version"] == evidence["format_version"] == format_version(cfg.semantic_output)
    assert future["selected_mcap"]["joint_evidence"] == evidence["selected_mcap"]["joint_evidence"]


@pytest.mark.parametrize("corrupt",["duplicate","length","nan"])
def test_selected_malformed_messages_still_fail(tmp_path,corrupt):
    cfg,_ = configuration()
    raw = source(tmp_path/"raw.mcap",[NAMES]*3,corrupt=corrupt)
    with pytest.raises(ValueError,match="MCAP_(JOINT_LAYOUT_MISMATCH|NONFINITE_JOINT)"):
        prepare(raw,tmp_path/"out",cfg)


def test_required_arm_missing_and_stale_commands_are_not_optional(tmp_path):
    cfg,_ = configuration()
    raw = source(tmp_path/"missing.mcap",[NAMES,NAMES[1:]])
    with pytest.raises(ValueError,match="Joint1_R"):
        prepare(raw,tmp_path/"missing",cfg)
    raw = source(tmp_path/"gap.mcap",[NAMES]*8,gap=True)
    prepare(raw,tmp_path/"out",cfg)
    qc = summarize([tmp_path/"out/sensors/robot.mcap"],0,300_000_000,profile(),output=cfg.semantic_output)
    assert qc["invalid_rows"] == 6
    assert "CAPTURE_VALID_RATIO_LOW" in qc["reject_codes"]
    assert qc["axis_validity"]["action"]["missing_rows_by_axis"]["Joint1_R"] == 6


def test_schema_identity_ignores_batch_rate_but_includes_missing_semantics():
    cfg,output = configuration()
    a=semantic_schema("p","d",cfg.semantic_output,{},frequency_hz=30)
    b=semantic_schema("p","d",{**cfg.semantic_output,"frequency_hz":99},{},frequency_hz=99)
    assert a==b
    modified=output.model_dump(mode="json")
    modified["features"]["action"]["required_axes"] = NAMES
    assert format_version(modified)!=format_version(cfg.semantic_output)
