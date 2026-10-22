"""One logical definition and one ROS source map for all supported transports.

Automatic schemes validate their definition against a real sample once. Other
transports remain subject to full validation on each upload; a passed sample is
never recorded as if files of the other formats had also been inspected.
"""

import copy

from hc_data_platform.continuous_recordings.mcap_contracts import McapDraftConfig

FORMATS = {"MCAP", "CAPTURE_BUNDLE", "LEROBOT_V3"}
GENERATED = {
    "observation.source_time",
    "observation.source_age",
    "observation.source_available",
    "observation.source_valid",
    "observation.recording_time",
}
POSES = {
    "observation.left_arm.ee_pose",
    "observation.right_arm.ee_pose",
    "observation.headset.pose",
    "observation.base.pose",
    "action.left_arm.ee_pose",
    "action.right_arm.ee_pose",
}


def complete_adapters(document):
    result = copy.deepcopy(document)
    if result.get("input_mode") != "AUTO":
        return result
    output = result["output"]
    features = output["features"]
    adapters = {a["source_format"]: a for a in result.get("adapters", [])}
    previous = adapters.get("MCAP", adapters.get("CAPTURE_BUNDLE", {})).get("configuration", {})
    cameras = {c["camera_id"]: c for c in previous.get("cameras", [])}
    numeric = previous.get("numeric_sources", {})
    cfg = {
        **previous,
        "fps": output.get("frequency_hz"),
        "semantic_output": output,
        "alignment_policy": "FIXED_GRID",
        "time_source": "STATE_LOG_TIME",
        "state_topic": previous.get("state_topic") or "/observation/joint_states",
        "action_topic": previous.get("action_topic") or "/action/joint_states",
        "cameras": [],
        "numeric_sources": {},
        "primary_camera": None,
    }
    for name, feature in features.items():
        role = feature["role"]
        if role in {"state", "action"}:
            cfg[role + "_joint_names"] = feature.get("names") or []
            cfg[role + "_joint_units"] = feature.get("units", [])
        elif role in {"rgb", "depth"}:
            camera_id = name.removeprefix("observation.images.")
            old = cameras.get(camera_id, {})
            if camera_id.startswith("observation_cameras_") and camera_id.endswith("_color"):
                device = camera_id.removeprefix("observation_cameras_").removesuffix("_color")
                topic = f"/observation/cameras/{device}/color/compressed"
            else:
                topic = f"/camera/{camera_id}/image"
            cfg["cameras"].append(
                {
                    "camera_id": camera_id,
                    "topic": old.get("topic") or topic,
                    "message_type": old.get("message_type")
                    or (
                        "sensor_msgs/msg/CompressedImage"
                        if topic.endswith("/compressed")
                        else "sensor_msgs/msg/Image"
                    ),
                    "kind": role,
                    "required": feature.get("required", True),
                }
            )
        elif name not in GENERATED:
            if name in POSES:
                kind = "geometry_msgs/msg/PoseStamped"
                fields = [f"pose.position.{x}" for x in "xyz"] + [
                    f"pose.orientation.{x}" for x in "xyzw"
                ]
            elif name == "action.base.velocity":
                kind = "geometry_msgs/msg/TwistStamped"
                fields = [
                    f"twist.{part}.{axis}" for part in ("linear", "angular") for axis in "xyz"
                ]
            else:
                kind = "std_msgs/msg/Float64MultiArray"
                fields = [f"data.{i}" for i in range(feature["shape"][0])]
            cfg["numeric_sources"][name] = numeric.get(name) or {
                "topic": "/" + name.replace(".", "/"),
                "message_type": kind,
                "fields": fields,
            }
    cfg["collection_mode"] = "RGB" if cfg["cameras"] else "JOINT_ONLY"
    configuration = McapDraftConfig.model_validate(cfg).model_dump(mode="json")
    lerobot = adapters.get("LEROBOT_V3", {"source_format": "LEROBOT_V3"})
    lerobot["feature_sources"] = {
        k: v for k, v in lerobot.get("feature_sources", {}).items() if k in features
    }
    result["adapters"] = [
        lerobot,
        {"source_format": "MCAP", "configuration": configuration},
        {"source_format": "CAPTURE_BUNDLE", "configuration": copy.deepcopy(configuration)},
    ]
    return result


def validation_coverage(document, passed):
    """Coverage of the unified definition, distinct from inspected file formats."""
    return FORMATS if document.get("input_mode") == "AUTO" and passed else set(passed)
