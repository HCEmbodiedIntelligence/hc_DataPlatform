"""Deterministic logical-to-LeRobot names, without video/numerical dependencies."""

import re
from collections.abc import Sequence

from hc_data_platform.core.errors import problem

NATIVE_FEATURE_PREFIX = "/lerobot/features/"


def native_feature_topic(feature: str) -> str:
    return NATIVE_FEATURE_PREFIX + feature.encode("utf-8").hex()


def camera_feature(name: str) -> bool:
    return name.startswith(("/camera/", "camera.", "observation.images.")) or bool(
        re.fullmatch(r"/?io_teleop/camera_[A-Za-z0-9_]+/(?:color|depth)", name)
    )


def feature_names(
    names: Sequence[str], *, camera_topics: Sequence[str] | None = None
) -> dict[str, str]:
    if camera_topics is not None and not set(camera_topics).issubset(names):
        raise ValueError("declared cameras must belong to the logical feature inventory")
    result: dict[str, str] = {}
    for name in names:
        if name.startswith(NATIVE_FEATURE_PREFIX):
            encoded = name.removeprefix(NATIVE_FEATURE_PREFIX)
            target = bytes.fromhex(encoded).decode("utf-8")
            if not target or native_feature_topic(target) != name:
                raise ValueError("invalid native feature identifier")
        elif name in {"/humanoid/action", "/action"}:
            target = "action"
        elif name in {"/humanoid/observation/state", "/observation/state"} or (
            name in {"/robot/joint_states", "/joint_states"}
            and "/humanoid/observation/state" not in names
        ):
            target = "observation.state"
        elif (camera_topics is None and camera_feature(name)) or (
            camera_topics is not None and name in camera_topics
        ):
            camera = (
                name.removeprefix("/camera/")
                .removesuffix("/image_raw")
                .removesuffix("/image")
                .removeprefix("camera.")
                .removeprefix("observation.images.")
                .strip("/")
            )
            target = "observation.images." + re.sub(r"[^A-Za-z0-9_.-]", "_", camera)
        elif (
            name == "source.timestamp_ns"
            or name.startswith(("observation.", "action"))
            and "/" not in name
        ):
            target = name
        else:
            target = "hc.source." + re.sub(
                r"[^A-Za-z0-9_.-]", "_", name.strip("/").replace("/", ".")
            )
        if target in result.values():
            raise problem(
                status=409,
                code="LEROBOT_MODALITY_SCHEMA_MISMATCH",
                title="Export source is incomplete",
                detail="Two source modalities map to the same feature.",
            )
        result[name] = target
    return result
