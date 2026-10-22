import copy
import json

import pytest
from mcap.reader import make_reader
from mcap.writer import Writer

from hc_data_platform.capture_schemes.models import CaptureDefinition, OutputContract
from hc_data_platform.capture_schemes.processing import validate_mcap_output
from hc_data_platform.capture_schemes.schema import native_topic
from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig
from hc_data_platform.continuous_recordings.mcap_native import prepare


def example(tmp_path, *, missing=False, corrupt=False, count=4):
    features = {
        name: dict(role=role, dtype="float32", shape=[1], names=["joint"], units=["rad"])
        for name, role in (("observation.state", "state"), ("action", "action"))
    }
    features["observation.base.pose"] = dict(
        role="numeric", dtype="float32", shape=[3], names=["x", "y", "z"]
    )
    for name, role, dtype in (
        ("source_time", "timestamp", "float64"),
        ("source_age", "numeric", "float32"),
        ("source_available", "validity", "float32"),
        ("source_valid", "validity", "float32"),
    ):
        features["observation." + name] = dict(
            role=role,
            dtype=dtype,
            shape=[3],
            names=["observation.base.pose", "action", "observation.state"],
        )
    features["observation.recording_time"] = dict(
        role="timestamp", dtype="float64", shape=[1], names=["seconds_since_first_log_time"]
    )
    output = OutputContract(frequency_hz=15, time_source="FRAME_TIMESTAMP", features=features)
    cfg = McapProcessingConfig(
        collection_mode="JOINT_ONLY",
        time_source="STATE_LOG_TIME",
        fps=15,
        state_topic="/feedback",
        action_topic="/command",
        joint_names=["joint"],
        joint_units=["rad"],
        semantic_output=output.model_dump(mode="json"),
        numeric_sources={
            "observation.base.pose": {
                "topic": "/base",
                "message_type": "geometry_msgs/msg/PoseStamped",
                "fields": ["pose.position.x", "pose.position.y", "pose.position.z"],
            }
        },
    )
    source = tmp_path / "source.mcap"
    with source.open("wb") as stream:
        writer = Writer(stream, enable_crcs=True)
        writer.start()
        schema = writer.register_schema("sensor_msgs/msg/JointState", "jsonschema", b"{}")
        state = writer.register_channel("/feedback", "json", schema)
        action = writer.register_channel("/command", "json", schema)
        pose = writer.register_channel(
            "/base",
            "json",
            writer.register_schema("geometry_msgs/msg/PoseStamped", "jsonschema", b"{}"),
        )
        for i in range(count):
            stamp = 10**12 + round(i * 1e9 / 15)
            if not missing:
                value = {
                    "pose": {
                        "position": {
                            "x": float("nan") if corrupt and i == 3 else i + 0.125,
                            "y": 2,
                            "z": 3,
                        }
                    }
                }
                writer.add_message(pose, stamp, json.dumps(value).encode(), stamp, i)
            for channel in (state, action):
                writer.add_message(
                    channel,
                    stamp,
                    json.dumps({"name": ["joint"], "position": [i * 0.125]}).encode(),
                    stamp,
                    i,
                )
        writer.finish()
    return source, cfg, output


def test_auxiliary_values_timestamps_and_precision_survive_mcap(tmp_path):
    source, cfg, output = example(tmp_path)
    original = source.read_bytes()
    recording, evidence = prepare(source, tmp_path / "out", cfg)
    result = validate_mcap_output(output, cfg, evidence)
    assert all(result["presence"].values())
    assert evidence["frames"] == 4 and evidence["source_frequency_hz"] == pytest.approx(15)
    with (tmp_path / "out/sensors/robot.mcap").open("rb") as stream:
        rows = [
            (c.topic, m.log_time, json.loads(m.data))
            for _, c, m in make_reader(stream).iter_messages()
        ]
    assert [v for k, _, v in rows if k == native_topic("observation.base.pose")] == [
        [i + 0.125, 2, 3] for i in range(4)
    ]
    assert [v for k, _, v in rows if k == native_topic("observation.source_valid")] == [
        [1, 1, 1]
    ] * 4
    assert [v for k, _, v in rows if k == native_topic("observation.recording_time")][
        -1
    ] == pytest.approx([0.2])
    assert {s["topic"] for s in recording["sensors"]} >= {
        native_topic("observation.base.pose"),
        native_topic("observation.source_time"),
    }
    assert source.read_bytes() == original


@pytest.mark.parametrize(
    "kwargs,error",
    [({"missing": True}, "REQUIRED_SIGNAL_MISSING"), ({"corrupt": True}, "NUMERIC_INVALID")],
)
def test_required_numeric_sources_are_decoded_and_checked(tmp_path, kwargs, error):
    source, cfg, _ = example(tmp_path, **kwargs)
    with pytest.raises(ValueError, match=error):
        prepare(source, tmp_path / "out", cfg)


def test_embedded_adapter_output_cannot_override_shared_contract(tmp_path):
    _, cfg, output = example(tmp_path)
    adapter = dict(source_format="MCAP", configuration=cfg.model_dump(mode="json"))
    document = dict(name="all formats", adapters=[adapter], output=output.model_dump(mode="json"))
    CaptureDefinition.model_validate(document)
    changed = copy.deepcopy(document)
    changed["adapters"][0]["configuration"]["semantic_output"]["features"]["action"]["units"] = [
        "m"
    ]
    with pytest.raises(ValueError, match="must match"):
        CaptureDefinition.model_validate(changed)


def test_fixed_grid_uses_causal_samples_and_records_the_actual_source_age(tmp_path):
    source, cfg, output = example(tmp_path)
    output = output.model_copy(update={"frequency_hz": 10})
    cfg = cfg.model_copy(
        update={
            "fps": 10,
            "semantic_output": output.model_dump(mode="json"),
            "alignment_policy": "FIXED_GRID",
        }
    )
    _, evidence = prepare(source, tmp_path / "grid", cfg)
    validate_mcap_output(output, cfg, evidence)
    with (tmp_path / "grid/sensors/robot.mcap").open("rb") as stream:
        rows = [(c.topic, json.loads(m.data)) for _, c, m in make_reader(stream).iter_messages()]
    assert [v["values"] for k, v in rows if k == native_topic("observation.state")] == [
        [0],
        [0.125],
        [0.375],
    ]
    times = [v for k, v in rows if k == native_topic("observation.source_time")]
    assert times[1] == pytest.approx([1 / 15] * 3, abs=1e-9)
    ages = [v for k, v in rows if k == native_topic("observation.source_age")]
    assert ages[1] == pytest.approx([0.1 - 1 / 15] * 3)


def test_joint_only_bundle_requires_no_dummy_video(tmp_path):
    from hc_data_platform.continuous_recordings.topic_video import restore

    source, cfg, output = example(tmp_path)
    root = tmp_path / "bundle"
    root.mkdir()
    (root / "raw.mcap").write_bytes(source.read_bytes())
    (root / "session.json").write_text(
        json.dumps({"schema_version": "hc-topic-video/v1", "videos": []})
    )
    restored = restore(root, tmp_path / "restored.mcap")
    _, evidence = prepare(restored, tmp_path / "decoded", cfg)
    assert validate_mcap_output(output, cfg, evidence)["presence"]["observation.state"]
    assert evidence["frames"] == 4


def test_fixed_grid_keeps_fractional_period_last_frame(tmp_path):
    source, cfg, _ = example(tmp_path, count=3)
    cfg = cfg.model_copy(update={"alignment_policy": "FIXED_GRID"})
    _, evidence = prepare(source, tmp_path / "out", cfg)
    assert evidence["frames"] == 3
