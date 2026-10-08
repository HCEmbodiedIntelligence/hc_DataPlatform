"""Validated local paths shared by LeRobot readers and capture receipt checks."""

from pathlib import Path, PurePosixPath
from typing import Any


def safe_relative(value: str) -> str:
    if (
        not value
        or PurePosixPath(value).is_absolute()
        or "\\" in value
        or ":" in value
        or any(p in {"", ".", ".."} for p in value.split("/"))
    ):
        raise ValueError(f"LEROBOT_UNSAFE_PATH: {value}")
    return value


def local_file(root: Path, relative: str) -> Path:
    path = root / safe_relative(relative)
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError(f"LEROBOT_SYMLINK: {relative}")
    if not path.is_file():
        raise ValueError(f"LEROBOT_ASSET_MISSING: {relative}")
    return path


def format_path(template: str, **values: Any) -> str:
    try:
        return safe_relative(template.format(**values))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError("LEROBOT_PATH_TEMPLATE: unsupported v3 path template") from exc
