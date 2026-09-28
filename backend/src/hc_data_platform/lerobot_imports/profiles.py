"""Schema driven native LeRobot profiles; legacy G1 remains a separate adapter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

from hc_data_platform.recording_fields import AXES, SCHEMA
from hc_data_platform.tools import hf_unitree_g1_to_mcap as g1

SOURCE_TOPIC = "/metadata/source"
OPENARM_AXES = tuple(
    f"openarmx_{side}_joint{i}" for side in ("left", "right") for i in range(1, 8)
) + ("left_gripper", "right_gripper")
OPENARM_UNITS = ("rad",) * 14 + ("m", "m")


@dataclass(frozen=True)
class Camera:
    feature_key: str
    camera_id: str
    topic: str


@dataclass(frozen=True)
class NativeProfile:
    profile_id: str
    cameras: tuple[Any, ...]
    topics: tuple[str, ...]
    state_names: tuple[str, ...]
    action_names: tuple[str, ...]
    joint_topic: str
    action_topic: str
    legacy_g1: bool = False


def profile_for_info(info: dict[str, Any]) -> NativeProfile:
    if info.get("robot_type") == "unitree_g1" or not info:
        return NativeProfile(
            "native-g1-v3",
            tuple(g1.CAMERAS),
            tuple(g1.ACTUAL_TOPICS),
            tuple(g1.G1_JOINT_NAMES),
            tuple(g1.G1_JOINT_NAMES),
            g1.JOINT_TOPIC,
            g1.ACTION_TOPIC,
            True,
        )
    features = info.get("features", {})
    public_fields = info.get("recording_field_schema") == SCHEMA
    names = {}
    for key in ("observation.state", "action"):
        feature = features.get(key, {})
        if not isinstance(feature, dict):
            raise ValueError(f"LEROBOT_FEATURE_SCHEMA: invalid {key}")
        shape, axes = feature.get("shape"), feature.get("names")
        if (
            feature.get("dtype") != "float32"
            or not isinstance(shape, list)
            or len(shape) != 1
            or type(shape[0]) is not int
            or shape[0] < 1
            or not isinstance(axes, list)
            or len(axes) != shape[0]
            or any(not isinstance(x, str) or not x.strip() for x in axes)
            or len(set(axes)) != len(axes)
        ):
            raise ValueError(
                f"LEROBOT_FEATURE_SCHEMA: {key} requires float32, shape and unique axis names"
            )
        names[key] = tuple(axes)
        units = feature.get("units")
        if units is not None and (
            not isinstance(units, list)
            or len(units) != len(axes)
            or any(not isinstance(unit, str) or not unit for unit in units)
        ):
            raise ValueError(f"LEROBOT_FEATURE_UNITS: invalid {key}")
    fps = info.get("fps")
    if not isinstance(fps, (int, float)) or not math.isfinite(fps) or int(fps) != fps:
        raise ValueError("LEROBOT_UNSUPPORTED_FPS: native alignment requires integer fps")
    cameras = []
    for key, feature in features.items():
        if not isinstance(feature, dict):
            raise ValueError(f"LEROBOT_FEATURE_SCHEMA: invalid {key}")
        if feature.get("dtype") != "video":
            continue
        if not re.fullmatch(r"observation\.images\.[A-Za-z0-9_.-]+", key):
            raise ValueError(
                "LEROBOT_CAMERA_SCHEMA: video feature must use observation.images.<id>"
            )
        shape = feature.get("shape")
        if (
            not isinstance(shape, list)
            or len(shape) != 3
            or shape[2] != (1 if feature.get("info", {}).get("is_depth_map") else 3)
            or any(type(x) is not int or x <= 0 for x in shape)
        ):
            raise ValueError("LEROBOT_CAMERA_SCHEMA: expected [height,width,3]")
        cameras.append(
            Camera(
                key,
                key.removeprefix("observation.images."),
                key
                if public_fields
                else "/camera/" + key.removeprefix("observation.images.") + "/image",
            )
        )
    robot_type = info.get("robot_type", "unknown")
    openarm = robot_type == "openarmx" or re.fullmatch(r"openarmx_\d+", robot_type or "")
    expected_axes = tuple(name for _, name in AXES) if public_fields else OPENARM_AXES
    if openarm and any(value != expected_axes for value in names.values()):
        raise ValueError("OPENARM_FEATURE_IDENTITY: axes differ from openarmx-v1")
    topics = (
        "/humanoid/observation/state",
        "/humanoid/action",
        SOURCE_TOPIC,
        *(c.topic for c in cameras),
        *(("source.timestamp_ns",) if "source.timestamp_ns" in features else ()),
    )
    return NativeProfile(
        "openarmx-v1" if openarm else "native-generic-v3",
        tuple(cameras),
        topics,
        names["observation.state"],
        names["action"],
        "/humanoid/observation/state",
        "/humanoid/action",
    )


def quality_profile(profile: NativeProfile, fps: int):
    from hc_data_platform.quality.models import QualityProfileV1

    if profile.legacy_g1:
        return QualityProfileV1(
            profile_id="native-g1-v3-30hz-v1",
            profile_version=3,
            required_topics=frozenset(profile.topics),
            default_timing={"target_frequency_hz": 30},
            joint_topic=profile.joint_topic,
            action={"topic": profile.action_topic},
        )
    return QualityProfileV1(
        profile_id=f"{profile.profile_id}-{fps}hz-v1",
        profile_version=1,
        required_topics=frozenset(profile.topics),
        joint_topic=profile.joint_topic,
        action={"topic": profile.action_topic},
        default_timing={
            "target_frequency_hz": fps,
            "minimum_frequency_hz_risk": fps * 0.95,
            "minimum_frequency_hz_reject": fps * 0.75,
        },
    )
