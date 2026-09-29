"""Deterministic logical-to-LeRobot names, without video/numerical dependencies."""

import re
from collections.abc import Sequence

from hc_data_platform.core.errors import problem


def camera_feature(name: str) -> bool:
    return name.startswith(("/camera/", "camera.", "observation.images."))


def feature_names(names: Sequence[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for name in names:
        if name in {"/humanoid/action", "/action"}:
            target = "action"
        elif name in {"/humanoid/observation/state", "/observation/state"} or (
            name in {"/robot/joint_states", "/joint_states"}
            and "/humanoid/observation/state" not in names
        ):
            target = "observation.state"
        elif camera_feature(name):
            camera = (
                name.removeprefix("/camera/")
                .removesuffix("/image_raw")
                .removesuffix("/image")
                .removeprefix("camera.")
                .removeprefix("observation.images.")
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
