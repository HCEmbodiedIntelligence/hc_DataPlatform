"""Prepare an OpenArm description directory for the existing model asset uploader.

The platform receives URDF/mesh bytes, never a robot-side filesystem locator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path
from xml.etree import ElementTree as ET

from hc_data_platform.lerobot_imports.profiles import OPENARM_AXES
from hc_data_platform.lerobot_imports.reader import local_file


def build_bundle(description: Path, output: Path) -> dict:
    if output.exists():
        raise ValueError("output must be a new directory")
    source = local_file(description, "urdf/robot/openarmx_robot.urdf")
    tree = ET.parse(source)
    joints = {j.attrib["name"]: j for j in tree.findall("joint")}
    mappings = [
        {"source_joint_name": name, "target_joint_name": name, "direction": "SAME"}
        for name in OPENARM_AXES[:-2]
    ]
    for side in ("left", "right"):
        target = f"openarmx_{side}_finger_joint1"
        follower = joints[f"openarmx_{side}_finger_joint2"]
        mimic = follower.find("mimic")
        if (
            joints[target].attrib["type"] != "prismatic"
            or mimic is None
            or mimic.attrib["joint"] != target
            or float(mimic.get("multiplier", "1")) != 1
            or float(mimic.get("offset", "0")) != 0
        ):
            raise ValueError(
                "OpenArm gripper geometry differs from the supported single-finger metre mapping"
            )
        mappings.append(
            {
                "source_joint_name": f"{side}_gripper",
                "target_joint_name": target,
                "direction": "SAME",
            }
        )
    if any(m["target_joint_name"] not in joints for m in mappings):
        raise ValueError("OpenArm model is missing a declared training axis")
    if any(joints[name].attrib["type"] != "revolute" for name in OPENARM_AXES[:-2]):
        raise ValueError("OpenArm arm axes must be revolute radians")
    assets = {}
    for mesh in tree.findall(".//mesh"):
        original = mesh.attrib["filename"]
        prefix = "package://openarmx_description/"
        if not original.startswith(prefix):
            raise ValueError("model mesh must belong to the supplied description package")
        relative = original.removeprefix(prefix)
        assets[relative] = local_file(description, relative)
        mesh.set("filename", relative)
    output.mkdir(parents=True)
    tree.write(output / "robot.urdf", encoding="utf-8", xml_declaration=True)
    for relative, path in assets.items():
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    config = {
        "format": "hc-robot-description/v1",
        "robot": {"id": "openarmx-v1", "display_name": "OpenArm X", "serial_no": "MODEL-TEMPLATE"},
        "urdf": {"entry": "robot.urdf", "name": tree.getroot().attrib["name"]},
        "joint_mapping": mappings,
        "mapping_provenance": {
            "profile": "openarmx-v1",
            "gripper_unit": "m",
            "gripper_meaning": "single finger travel",
            "mimic": "URDF multiplier 1",
        },
        "source": {"urdf_sha256": hashlib.sha256(source.read_bytes()).hexdigest()},
    }
    (output / "robot.config.json").write_text(json.dumps(config, indent=2) + "\n")
    return config


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("description", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    build_bundle(args.description, args.output)
