from __future__ import annotations

import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from hc_data_platform.tools import hf_unitree_g1_to_mcap as converter

_CANONICAL_DATA = re.compile(r"^data/chunk-\d{3}/file-\d{3}\.parquet$")
_CANONICAL_EPISODES = re.compile(r"^meta/episodes/chunk-\d{3}/file-\d{3}\.parquet$")
_CANONICAL_VIDEO = re.compile(r"^videos/([^/]+)/chunk-\d{3}/file-\d{3}\.mp4$")
MAX_LEROBOT_IMPORT_EPISODES = 10_000
LEROBOT_TRANSIENT_SUFFIXES = (".part", ".lock", ".incomplete", ".oss-download")


def is_lerobot_local_cache_path(relative_path: str) -> bool:
    return ".cache" in PurePosixPath(relative_path).parts


def is_lerobot_transient_path(relative_path: str) -> bool:
    return PurePosixPath(relative_path).name.endswith(LEROBOT_TRANSIENT_SUFFIXES)


def is_canonical_lerobot_object(relative_path: str) -> bool:
    path = PurePosixPath(relative_path)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return False
    normalized = path.as_posix()
    if normalized == "meta/info.json" or _CANONICAL_EPISODES.fullmatch(normalized):
        return True
    if _CANONICAL_DATA.fullmatch(normalized):
        return True
    video_match = _CANONICAL_VIDEO.fullmatch(normalized)
    if video_match is None:
        return False
    return video_match.group(1) in {camera.feature_key for camera in converter.CAMERAS}


def find_local_source_root(value: Path) -> Path:
    raw_value = str(value)
    windows_match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw_value)
    if windows_match:
        candidate = (
            Path("/mnt")
            / windows_match.group(1).lower()
            / windows_match.group(2).replace("\\", "/")
        ).resolve()
    else:
        candidate = value.expanduser().resolve()
    if (candidate / "meta/info.json").is_file():
        validate_source_profile(candidate)
        return candidate
    matches = sorted(candidate.glob("*/meta/info.json"))
    if len(matches) != 1:
        raise ValueError(
            "local source must be a LeRobot revision root, or contain exactly one revision root"
        )
    root = matches[0].parent.parent
    validate_source_profile(root)
    return root


def validate_source_profile(source_root: Path) -> dict[str, Any]:
    try:
        info = json.loads((source_root / "meta/info.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("LeRobot meta/info.json is missing or invalid") from exc
    return validate_source_info(info)


def validate_source_info(info: object) -> dict[str, Any]:
    if not isinstance(info, dict):
        raise ValueError("LeRobot meta/info.json must contain an object")
    if info.get("codebase_version") != "v3.0":
        raise ValueError("this importer currently requires LeRobotDataset v3.0")
    if info.get("robot_type") != "unitree_g1":
        raise ValueError("this importer currently requires the Unitree G1 source profile")
    fps = info.get("fps")
    if not isinstance(fps, (int, float)) or isinstance(fps, bool) or not 0 < float(fps) <= 240:
        raise ValueError("LeRobot metadata has an invalid fps")
    if not isinstance(info.get("data_path"), str) or not isinstance(info.get("video_path"), str):
        raise ValueError("LeRobot v3 metadata is missing data/video path templates")
    features = info.get("features")
    if not isinstance(features, dict):
        raise ValueError("LeRobot metadata has no feature inventory")
    required = {
        "observation.state.ee_state",
        "observation.state.hand_state",
        "observation.state.robot_q_current",
        "action.ee_action",
        "action.hand_cmd",
        "action.robot_q_desired",
        *(camera.feature_key for camera in converter.CAMERAS),
    }
    missing = sorted(required - set(features))
    if missing:
        raise ValueError(f"LeRobot Unitree G1 profile is missing features: {', '.join(missing)}")
    return info
