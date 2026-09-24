"""Platform validation of the frozen A profile; no recorder/runtime dependency."""

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from hc_data_platform.ingest.models import Identifier

CONTRACTS = Path(__file__).with_name("contracts")
Text = Annotated[str, Field(min_length=1)]
Positive = Annotated[int, Field(ge=1, strict=True)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Axis(Model):
    name: Text
    unit: Literal["rad", "m"]
    group: Literal["arm", "gripper"]


class Camera(Model):
    id: Identifier
    width: Positive
    height: Positive
    calibration_ref: Text


class Rule(Model):
    mode: Literal["periodic"]
    max_age_ms: Positive
    invalidate_on: list[Text] = Field(min_length=1)


class Validity(Model):
    arm: Rule
    gripper: Rule


class CaptureProfile(Model):
    profile_version: Text
    robot_type: Text
    fps: Positive
    axes: list[Axis] = Field(min_length=1)
    cameras: list[Camera] = Field(min_length=1)
    action_stage: Literal["sent"]
    command_boundary: Literal["motion_or_teleop_to_driver_request"]
    validity: Validity


def openarm_profile():
    return json.loads((CONTRACTS / "openarmx-v1.json").read_text())


def validate_capture_config(cfg):
    profile = cfg["capture"]["profile"]
    CaptureProfile.model_validate(profile)
    names = [a["name"] for a in profile["axes"]]
    cameras = [c["id"] for c in profile["cameras"]]
    if len(set(names)) != len(names) or len(set(cameras)) != len(cameras):
        raise ValueError("Duplicate profile identity")
    # G0 source-mapping schema fixes time_base to 1/30, including generic profiles.
    if profile["fps"] != 30 or cfg["dataset"]["fps"] != profile["fps"]:
        raise ValueError("openarm-capture/v1 requires 30 Hz; other rates need a contract revision")
    if profile["robot_type"] == "openarmx" and profile != openarm_profile():
        raise ValueError("OpenArm profile differs from frozen G0; request E revision")
    for kind in ("state", "action"):
        actual = [
            a
            for s in cfg["sources"].values()
            if s["kind"] == kind and s["required"]
            for a in s.get("named_axes", [])
        ]
        if len(actual) != len(names) or {a["name"]: a["unit"] for a in actual} != {
            a["name"]: a["unit"] for a in profile["axes"]
        }:
            raise ValueError("Every profile axis needs exactly one named " + kind + " source")
        for axis in actual:
            expected = next(a for a in profile["axes"] if a["name"] == axis["name"])
            rule = profile["validity"][expected["group"]]
            if (
                axis["max_age_ms"] != rule["max_age_ms"]
                or axis.get("mode", "periodic") != rule["mode"]
            ):
                raise ValueError("Axis validity differs from profile")
    actual_cameras = {n for n, s in cfg["sources"].items() if s["kind"] == "rgbd" and s["required"]}
    if actual_cameras != set(cameras):
        raise ValueError("Required cameras differ from profile")
