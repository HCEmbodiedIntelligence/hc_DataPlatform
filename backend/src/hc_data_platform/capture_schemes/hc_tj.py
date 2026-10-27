"""Versioned HC TJ position scheme, with optional HoloBrain dependencies."""

from hc_data_platform.continuous_recordings.joint_normalization import HANDS, JOINTS
from hc_data_platform.continuous_recordings.mcap_contracts import JointNormalization, McapProcessingConfig

from .models import OutputContract


def configuration(*, holobrain=True, clock_evidence):
    policy = JointNormalization(clock_domain="ros_system_time", clock_evidence=clock_evidence)
    features = {
        name: dict(dtype="float64", shape=[17], names=JOINTS, units=["rad"] * 17, role=role)
        for name, role in (("observation.state", "state"), ("action", "action"))
    }
    cameras = [dict(camera_id="head", topic="/observation/cameras/head/color/compressed",
                    message_type="sensor_msgs/msg/CompressedImage", kind="rgb")]
    numeric = {}
    metadata = {}
    if holobrain:
        cameras.append(dict(camera_id="head_depth", topic="/observation/cameras/head/depth/compressed",
                            message_type="sensor_msgs/msg/CompressedImage", kind="depth"))
        for topic, joint in HANDS.items():
            name = "action.hand." + ("right" if joint == "R_ban" else "left")
            numeric[name] = dict(topic=topic, message_type="sensor_msgs/msg/JointState", fields=["position." + joint])
            features[name] = dict(dtype="float64", shape=[1], names=[joint], units=["command"], role="numeric")
        metadata = {"head." + name: "/observation/cameras/head/" + name for name in ("calibration", "extrinsics")}
    for camera in cameras:
        features["observation.images." + camera["camera_id"]] = dict(
            dtype="video", shape=[400, 640, 1 if camera["kind"] == "depth" else 3],
            names=["height", "width", "channels"], role=camera["kind"],
        )
    output = OutputContract(algorithm="capture-structure/v3", time_source="RGB_LOG_TIME",
                            normalization=policy, metadata_features=tuple(metadata), features=features)
    cfg = McapProcessingConfig(
        state_topic="/observation/joint_states", action_topic="/action/joint_states",
        primary_camera=cameras[0]["topic"], state_joint_names=JOINTS, action_joint_names=JOINTS,
        state_joint_units=["rad"] * 17, action_joint_units=["rad"] * 17,
        normalization=policy, cameras=cameras, numeric_sources=numeric, metadata_sources=metadata,
        depth_scale_m_per_unit=0.001 if holobrain else None,
        depth_unit_evidence="HC TJ depth recording 16UC1 millimetres" if holobrain else None,
        semantic_output=output.model_dump(mode="json"), maximum_time_error_ms=20,
    )
    return cfg, output
