"""Real CDR decoding, physical-depth media and explicit missing-frame evidence."""

import io
import json

import av
import numpy as np
import pytest
from mcap.reader import make_reader
from mcap.writer import Writer
from PIL import Image

from hc_data_platform.continuous_recordings.asset_models import RecordingConfigurationV1
from hc_data_platform.continuous_recordings.mcap_native import inspect, prepare, resolve
from tests.workflow.test_mcap_camera import SCHEMA, message


def synthetic_mcap(path, missing=()):
    image = io.BytesIO()
    Image.new("RGB", (32, 24), (80, 110, 160)).save(image, "JPEG")
    depth = io.BytesIO()
    Image.fromarray(np.full((24, 32), 1000, dtype=np.uint16)).save(depth, "PNG")
    with path.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True, enable_data_crcs=True)
        writer.start(profile="ros2")
        image_schema = writer.register_schema(SCHEMA.name, SCHEMA.encoding, SCHEMA.data)
        channels = {
            k: writer.register_channel(k, "cdr", image_schema) for k in ("/color", "/depth")
        }
        joint_schema = writer.register_schema("sensor_msgs/JointState", "jsonschema", b"{}")
        channels.update(
            {
                k: writer.register_channel(k, "json", joint_schema)
                for k in ("/joint_states", "/joint_cmd")
            }
        )
        for i in range(12):
            stamp = 1_000_000_000 + i * 10**9 // 30
            for topic, body in (
                ("/color", message(image.getvalue(), "jpeg", i).data),
                ("/depth", message(depth.getvalue(), "16UC1; png", i).data),
            ):
                if i not in missing:
                    writer.add_message(channels[topic], stamp, body, stamp, i)
            for topic, value in (("/joint_states", i * 0.01), ("/joint_cmd", i * 0.01 + 0.02)):
                writer.add_message(
                    channels[topic],
                    stamp,
                    json.dumps({"name": ["joint"], "position": [value]}).encode(),
                    stamp,
                    i,
                )
        writer.finish()
    return path


def test_prepare_cdr_mcap_retains_absolute_values_and_exposes_gaps(tmp_path):
    source = synthetic_mcap(tmp_path / "raw.mcap", missing=(4, 5, 6))
    with pytest.raises(ValueError, match="DEPTH_UNITS_REQUIRED"):
        resolve({}, inspect(source))
    config, provenance = prepare(source, tmp_path / "recording", {"depth_scale_m_per_unit": 0.001})
    RecordingConfigurationV1.model_validate(config)
    assert len(config["cameras"]) == 2
    assert provenance["invalid_frames"] >= 2
    assert provenance["duration_ns"] >= 400_000_000
    for camera in config["cameras"]:
        with av.open(str(tmp_path / "recording/videos" / (camera["camera_id"] + ".mp4"))) as video:
            assert len(list(video.decode(video=0))) == provenance["frames"]
    with (tmp_path / "recording/sensors/robot.mcap").open("rb") as stream:
        rows = [
            (c.topic, m.log_time, json.loads(m.data))
            for _, c, m in make_reader(stream, validate_crcs=True).iter_messages()
        ]
    actions = [value["values"][0] for topic, _, value in rows if topic == "/action"]
    assert actions[:3] == pytest.approx(
        [0.02, 0.03, 0.04]
    )  # Absolute positions, never delta angles.
    assert all(value["units"] == ["unknown"] for topic, _, value in rows if topic == "/action")
    assert any(
        not value["valid"] for topic, _, value in rows if topic == "/openarm/capture_validity"
    )


def test_explicit_robot_mapping_can_select_nonstandard_topic_names():
    inventory = {
        "/feedback": {"kind": "joints", "joint_names": ["j"]},
        "/goal": {"kind": "joints", "joint_names": ["j"]},
        "/cam": {"kind": "rgb"},
    }
    config = resolve({"state_topic": "/feedback", "action_topic": "/goal"}, inventory)
    assert config.state_topic == "/feedback" and config.action_topic == "/goal"
