"""Capture bundle evidence uses the same real decoder as production registration."""

import json
from pathlib import Path

from mcap.reader import make_reader

from .models import OutputContract
from .processing import require_snapshot, validate_mcap_output


def validate_prepared(root, destination, config, snapshot):
    from hc_data_platform.continuous_recordings.mcap_contracts import McapProcessingConfig

    snapshot = require_snapshot(snapshot)
    root, destination = Path(root), Path(destination)
    session = json.loads((root / "session.json").read_text())
    cfg = McapProcessingConfig.model_validate(snapshot["effective_adapter"]["configuration"])
    actual = {}
    frames = 0
    with (destination / "sensors/robot.mcap").open("rb") as stream:
        for _, channel, message in make_reader(stream, validate_crcs=True).iter_messages():
            name = {"/observation/state": "observation.state", "/action": "action"}.get(
                channel.topic
            )
            if name:
                value = json.loads(message.data)
                feature = {
                    "dtype": "float32",
                    "shape": [len(value["values"])],
                    "names": value["names"],
                    "units": value["units"],
                }
                if name in actual and actual[name] != feature:
                    raise ValueError("capture bundle vector layout changes during recording")
                actual[name] = feature
                frames += name == "observation.state"
    for camera in config["cameras"]:
        depth = camera["camera_id"].endswith("_depth")
        actual[camera["topic"]] = {
            "dtype": "video",
            "shape": [camera["height"], camera["width"], 1 if depth else 3],
            "names": ["height", "width", "channels"],
            "info": {
                "video.fps": camera["fps"],
                "video.codec": camera["codec"],
                "is_depth_map": depth,
            },
        }
    actual["source.timestamp_ns"] = {"dtype": "int64", "shape": [1]}
    # Native packages own their source layout in session.json. A generic ROS
    # mapping must never appear accepted while the native decoder ignores it.
    for role, topic in (("state", "/observation/state"), ("action", "/action")):
        if getattr(cfg, role + "_topic") not in (None, topic):
            raise ValueError(
                f"native capture bundle {role}_topic must be {topic}; its source layout is frozen in session.json"
            )
        names, units = cfg.layout(role)
        feature = actual.get("observation.state" if role == "state" else "action")
        if feature and (
            (names and list(names) != feature["names"])
            or (units and list(units) != feature["units"])
        ):
            raise ValueError("native capture bundle axis mapping differs from session.json")
    camera_topics = {camera["topic"] for camera in config["cameras"]}
    if any(camera.topic not in camera_topics for camera in cfg.cameras) or (
        cfg.primary_camera and cfg.primary_camera not in camera_topics
    ):
        raise ValueError("native capture bundle camera mapping differs from session.json")
    observed = session["config"]["dataset"]["fps"]
    evidence = validate_mcap_output(
        OutputContract.model_validate(snapshot["output"]),
        cfg,
        {
            "output_features": actual,
            "source_frequency_hz": observed,
            "config": cfg.model_dump(mode="json"),
        },
    )
    evidence.update(frame_count=frames, frequency_hz=observed)
    path = destination / "capture-provenance.json"
    provenance = json.loads(path.read_text())
    provenance.update(capture_snapshot=snapshot, capture_validation=evidence)
    path.write_text(json.dumps(provenance))
    return evidence


def decode_sample(root, destination, adapter, output):
    from hc_data_platform.continuous_recordings.openarm_raw.prepare import prepare
    from hc_data_platform.robot_ingest.recording_contract import OpenArmRecordingComplete

    marker = OpenArmRecordingComplete.model_validate_json(
        (root / "recording-complete.json").read_bytes()
    )
    command = marker.recording_upload
    config, _ = prepare(root, destination, command)
    # Samples are not recording receipts. Only the actual output comparison is used.
    return validate_prepared(
        root,
        destination,
        config,
        {"output": output.model_dump(mode="json"), "effective_adapter": adapter},
    )
