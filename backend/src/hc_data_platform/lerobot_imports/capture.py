"""Validate and retain openarm-capture/v1 provenance, never infer command validity."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from hc_data_platform.ingest.ports import crc64_ecma

from .profiles import OPENARM_AXES, OPENARM_UNITS, profile_for_info


def canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode()


def verify_completion(
    root: Path, descriptors: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Compare a sealed package to local bytes or the verified committed Raw inventory."""
    from .reader import local_file, safe_relative

    if root.name.startswith(".export-") or (root / "FAILED.json").exists():
        raise ValueError("OPENARM_EXPORT_INCOMPLETE")
    marker = json.loads(local_file(root, "export-complete.json").read_text())
    if (
        marker.get("schema_version") != "openarm-export-complete/v1"
        or marker.get("contract_version") != "openarm-capture/v1"
        or marker.get("status") != "COMPLETE"
    ):
        raise ValueError("OPENARM_EXPORT_INCOMPLETE")
    inventory = marker["assets"]
    paths = [safe_relative(item["path"]) for item in inventory]
    if (
        paths != sorted(set(paths))
        or "export-complete.json" in paths
        or any(
            p == "FAILED.json"
            or any(
                part.startswith(".export-") or part.endswith((".part", ".incomplete"))
                for part in p.split("/")
            )
            for p in paths
        )
        or hashlib.sha256(canonical(inventory)).hexdigest() != marker["content_sha256"]
    ):
        raise ValueError("OPENARM_ASSET_INTEGRITY: invalid completion inventory")
    if descriptors is None:
        actual = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
        if actual != set(paths) | {"export-complete.json"}:
            raise ValueError("OPENARM_ASSET_INTEGRITY: completion inventory differs from files")
    elif set(descriptors) != set(paths) | {"export-complete.json"}:
        raise ValueError("OPENARM_ASSET_INTEGRITY: completion inventory differs from committed Raw")
    for item in inventory:
        if descriptors is None:
            path = local_file(root, item["path"])
            digest, size, crc = hashlib.sha256(), 0, 0
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
                    size += len(chunk)
                    crc = crc64_ecma(chunk, crc)
            actual_size, actual_hash, actual_crc = size, digest.hexdigest(), str(crc)
        else:
            descriptor = descriptors[item["path"]]
            actual_size, actual_hash = descriptor["size"], descriptor["sha256"]
            actual_crc = str(descriptor.get("crc64", item["crc64"]))
        if (actual_size, actual_hash, actual_crc) != (
            item["size_bytes"],
            item["sha256"],
            item["crc64"],
        ):
            raise ValueError(f"OPENARM_ASSET_INTEGRITY: {item['path']}")
    return marker


def read_capture_episode(
    root: Path, info: dict[str, Any], metadata: dict[str, Any], rows: list[dict[str, Any]]
):
    try:
        return _read_capture_episode(root, info, metadata, rows)
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"OPENARM_METADATA_INVALID: {exc}") from exc


def _read_capture_episode(
    root: Path, info: dict[str, Any], metadata: dict[str, Any], rows: list[dict[str, Any]]
):
    from .reader import local_file

    context = json.loads(local_file(root, "capture-context.json").read_text())
    marker = json.loads(local_file(root, "export-complete.json").read_text())
    if context["contract_version"] != "openarm-capture/v1":
        raise ValueError("OPENARM_CONTRACT_VERSION")
    for key in ("export_id", "session_id", "collection_task_id", "robot_id", "synthetic"):
        if context[key] != marker[key]:
            raise ValueError(f"OPENARM_IDENTITY_MISMATCH: {key}")
    start, end = (
        datetime.fromisoformat(context[key].replace("Z", "+00:00"))
        for key in ("capture_started_at", "capture_ended_at")
    )
    if start.tzinfo is None or end.tzinfo is None or end <= start:
        raise ValueError("OPENARM_CAPTURE_TIME")
    profile = context["profile"]
    axes = profile["axes"]
    names, units = [a["name"] for a in axes], [a["unit"] for a in axes]
    if profile_for_info(info).profile_id == "openarmx-v1" and profile["robot_type"] != "openarmx":
        raise ValueError("OPENARM_PROFILE_IDENTITY")
    if profile["robot_type"] == "openarmx" and (
        profile["profile_version"] != "openarmx-v1"
        or profile["command_boundary"] != "motion_or_teleop_to_driver_request"
    ):
        raise ValueError("OPENARM_PROFILE_IDENTITY")
    if info["robot_type"] not in (profile["robot_type"], context["local_robot_id"]):
        raise ValueError("OPENARM_ROBOT_TYPE")
    if profile["robot_type"] == "openarmx" and (tuple(names), tuple(units)) != (
        OPENARM_AXES,
        OPENARM_UNITS,
    ):
        raise ValueError("OPENARM_PROFILE_AXES")
    if profile["fps"] != info["fps"] or profile["action_stage"] != "sent":
        raise ValueError("OPENARM_PROFILE_GRID_OR_ACTION_STAGE")
    for key in ("observation.state", "action"):
        if info["features"][key]["names"] != names:
            raise ValueError("OPENARM_FEATURE_IDENTITY")
        if info["features"][key].get("units", units) != units:
            raise ValueError("OPENARM_FEATURE_UNITS")
    cameras = profile["cameras"]
    if {"observation.images." + c["id"] for c in cameras} != {
        key for key, feature in info["features"].items() if feature["dtype"] == "video"
    }:
        raise ValueError("OPENARM_CAMERA_INVENTORY")
    for camera in cameras:
        if info["features"]["observation.images." + camera["id"]]["shape"] != [
            camera["height"],
            camera["width"],
            3,
        ]:
            raise ValueError("OPENARM_CAMERA_SHAPE")
    if len(context["episodes"]) != info["total_episodes"]:
        raise ValueError("OPENARM_EPISODE_MAPPING")
    source = context["episodes"][metadata["episode_index"]]
    if (
        source["episode_index"] != metadata["episode_index"]
        or not source["export_qualified"]
        or source["end_frame"] - source["start_frame"] != len(rows)
        or metadata["tasks"] != [source["task"]]
        or any(
            source["start_frame"] < i["end_frame"] and source["end_frame"] > i["start_frame"]
            for i in context["invalid_intervals"]
        )
    ):
        raise ValueError("OPENARM_INVALID_INTERVAL_OR_EPISODE")
    mappings = tuple(
        json.loads(line)
        for line in local_file(root, "source_mapping.jsonl").read_text().splitlines()
    )
    if len(mappings) != info["total_frames"]:
        raise ValueError("OPENARM_SOURCE_MAPPING_COUNT")
    mappings = mappings[metadata["dataset_from_index"] : metadata["dataset_to_index"]]
    for frame, mapping in enumerate(mappings):
        if (
            mapping["clock_epoch"] != mappings[0]["clock_epoch"]
            or abs(
                mapping["target_ns"] - mappings[0]["target_ns"] - round(frame * 1e9 / info["fps"])
            )
            > 1
        ):
            raise ValueError("OPENARM_SOURCE_TIME_GRID")
        if (
            mapping["episode_index"] != metadata["episode_index"]
            or mapping["frame_index"] != frame
            or mapping["session_id"] != context["session_id"]
            or mapping["source_episode_id"] != source["source_episode_id"]
            or mapping["source_frame"] != source["start_frame"] + frame
        ):
            raise ValueError("OPENARM_SOURCE_MAPPING")
        for key in ("action_valid", "state_valid"):
            if len(mapping[key]) != len(axes) or not all(v is True for v in mapping[key]):
                raise ValueError(f"OPENARM_{key.upper()}")
        brackets = mapping["state_brackets_ns"]
        if len(brackets) != len(axes) or any(
            not left <= mapping["target_ns"] <= right for left, right in brackets
        ):
            raise ValueError("OPENARM_STATE_TIMING")
        stamps = mapping["action_source_ns"]
        if len(stamps) != len(axes):
            raise ValueError("OPENARM_ACTION_DIMENSION")
        for axis, stamp in zip(axes, stamps, strict=True):
            age = mapping["target_ns"] - stamp
            if not 0 <= age <= profile["validity"][axis["group"]]["max_age_ms"] * 1e6:
                raise ValueError("OPENARM_ACTION_TIME: future or stale command")
        if set(mapping["cameras"]) != {c["id"] for c in cameras}:
            raise ValueError("OPENARM_CAMERA_MAPPING")
    return context, source, mappings
