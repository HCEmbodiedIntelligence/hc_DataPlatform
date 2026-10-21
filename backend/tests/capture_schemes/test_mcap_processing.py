"""Full MCAP decode with independent axes and the 167-frame 15 Hz regression."""

import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.capture_schemes.models import OutputContract
from hc_data_platform.capture_schemes.processing import validate_mcap_output
from hc_data_platform.capture_schemes.schema import present_fields, semantic_schema
from hc_data_platform.continuous_recordings.asset_models import RecordingConfigurationV1
from hc_data_platform.continuous_recordings.mcap_native import McapProcessingConfig, prepare
from hc_data_platform.core.errors import ProblemException


def joint_source(path, *, bad_last=False, reverse_order=False, include_camera=False):
    with path.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True, enable_data_crcs=True)
        writer.start()
        schema = writer.register_schema("sensor_msgs/JointState", "jsonschema", b"{}")
        channels = {
            topic: writer.register_channel(topic, "json", schema)
            for topic in ("/feedback", "/command")
        }
        if include_camera:
            import io

            from PIL import Image

            from tests.workflow.test_mcap_camera import SCHEMA, message

            image = io.BytesIO()
            Image.new("RGB", (640, 400), (80, 110, 160)).save(image, "JPEG")
            camera_schema = writer.register_schema(SCHEMA.name, SCHEMA.encoding, SCHEMA.data)
            camera = writer.register_channel("/optional/head", "cdr", camera_schema)
        for index in range(167):
            stamp = 10**12 + round(index * 10**9 / 15)
            if include_camera:
                writer.add_message(
                    camera, stamp, message(image.getvalue(), "jpeg", index).data, stamp, index
                )
            for topic, width in (("/feedback", 29), ("/command", 19)):
                names = [f"axis-{i}" for i in range(width)]
                values = [index * 0.001 + i for i in range(width)]
                if reverse_order:
                    names, values = names[::-1], values[::-1]
                if bad_last and index == 166 and topic == "/command":
                    names[-1] = "unexpected"
                writer.add_message(
                    channels[topic],
                    stamp,
                    json.dumps({"name": names, "position": values}).encode(),
                    stamp,
                    index,
                )
        writer.finish()
    return path


def config():
    return {
        "collection_mode": "RGB",
        "time_source": "STATE_LOG_TIME",
        "state_topic": "/feedback",
        "action_topic": "/command",
        "fps": 15,
        "state_joint_names": [f"axis-{i}" for i in range(29)],
        "state_joint_units": ["rad"] * 29,
        "action_joint_names": [f"axis-{i}" for i in range(19)],
        "action_joint_units": ["rad"] * 19,
        "cameras": [
            {
                "camera_id": "head",
                "topic": "/optional/head",
                "message_type": "sensor_msgs/msg/CompressedImage",
                "required": False,
            }
        ],
    }


def contract():
    return OutputContract(
        frequency_hz=15,
        time_source="STATE_LOG_TIME",
        features={
            **{
                name: {
                    "role": role,
                    "dtype": "float64",
                    "shape": [width],
                    "names": [f"axis-{i}" for i in range(width)],
                    "units": ["rad"] * width,
                }
                for name, role, width in (
                    ("observation.state", "state", 29),
                    ("action", "action", 19),
                )
            },
            "observation.images.head": {
                "role": "rgb",
                "dtype": "video",
                "shape": [400, 640, 3],
                "required": False,
            },
        },
    )


def test_camera_recording_keeps_explicit_state_clock_on_serialization(tmp_path):
    source = joint_source(tmp_path / "camera.mcap", include_camera=True)
    recording, _ = prepare(source, tmp_path / "recording", config())
    parsed = RecordingConfigurationV1.model_validate(recording)
    assert parsed.time_source == "STATE_LOG_TIME"
    assert RecordingConfigurationV1.model_validate_json(parsed.model_dump_json()) == parsed
    legacy = parsed.model_copy(update={"time_source": "RGB_LOG_TIME"})
    assert "time_source" not in legacy.model_dump(mode="json")
    assert RecordingConfigurationV1.model_validate_json(legacy.model_dump_json()) == legacy


@pytest.mark.parametrize("reverse_order", [False, True])
def test_independent_layout_and_no_fake_optional_camera(tmp_path, reverse_order):
    source = joint_source(tmp_path / "source.mcap", reverse_order=reverse_order)
    recording, evidence = prepare(source, tmp_path / "recording", config())
    RecordingConfigurationV1.model_validate(recording)
    validation = validate_mcap_output(contract(), McapProcessingConfig(**config()), evidence)
    assert evidence["frames"] == 167
    assert evidence["duration_ns"] == round(167 * 1e9 / 15)
    assert recording["cameras"] == []
    assert not list((tmp_path / "recording").rglob("*.mp4"))
    assert validation["presence"]["observation.images.head"] is False
    with (tmp_path / "recording/sensors/robot.mcap").open("rb") as stream:
        rows = [
            (channel.topic, message.log_time, json.loads(message.data))
            for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages()
        ]
    states = [(time, value) for topic, time, value in rows if topic == "/observation/state"]
    actions = [(time, value) for topic, time, value in rows if topic == "/action"]
    assert len(states) == len(actions) == 167
    assert states[-1][0] == round(166 * 1e9 / 15)
    assert len(states[-1][1]["values"]) == 29
    assert actions[-1][1]["values"] == pytest.approx([0.166 + i for i in range(19)])
    assert all(actions[-1][1]["validity"])
    schema = semantic_schema(
        "p",
        "d",
        contract().model_dump(mode="json"),
        {"/observation/state": "json", "/action": "json"},
    )
    assert "observation.images.head" not in present_fields(schema, validation["presence"])
    changed = contract().model_dump(mode="json")
    changed["features"]["action"]["units"][0] = "degree"
    with pytest.raises(ProblemException) as error:
        validate_mcap_output(OutputContract(**changed), McapProcessingConfig(**config()), evidence)
    assert error.value.problem.code == "CAPTURE_STRUCTURE_MISMATCH"


def test_late_layout_corruption_is_not_hidden_by_first_message(tmp_path):
    with pytest.raises(ValueError, match="MCAP_JOINT_LAYOUT_MISMATCH"):
        prepare(joint_source(tmp_path / "bad.mcap", bad_last=True), tmp_path / "out", config())
