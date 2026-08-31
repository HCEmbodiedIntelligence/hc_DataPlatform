from __future__ import annotations

import json
from pathlib import Path

import pytest

from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS
from hc_data_platform.tools.lerobot_unitree_g1_import import (
    build_parser,
    find_local_source_root,
    is_canonical_lerobot_object,
    validate_source_profile,
)


def _info() -> bytes:
    features = {
        "observation.state.ee_state": {},
        "observation.state.hand_state": {},
        "observation.state.robot_q_current": {},
        "action.ee_action": {},
        "action.hand_cmd": {},
        "action.robot_q_desired": {},
        **{camera.feature_key: {} for camera in CAMERAS},
    }
    return json.dumps(
        {
            "codebase_version": "v3.0",
            "robot_type": "unitree_g1",
            "fps": 30,
            "data_path": "data/chunk-{chunk_index:03d}/file-{file_index:03d}.parquet",
            "video_path": ("videos/{video_key}/chunk-{chunk_index:03d}/file-{file_index:03d}.mp4"),
            "features": features,
        }
    ).encode()


def test_canonical_source_filter_keeps_parquet_and_mp4_but_not_cache_files() -> None:
    assert is_canonical_lerobot_object("meta/info.json")
    assert is_canonical_lerobot_object("meta/episodes/chunk-000/file-000.parquet")
    assert is_canonical_lerobot_object("data/chunk-000/file-000.parquet")
    assert is_canonical_lerobot_object(
        "videos/observation.images.head_stereo_left/chunk-000/file-000.mp4"
    )
    assert not is_canonical_lerobot_object("data/chunk-000/file-000.parquet.part")
    assert not is_canonical_lerobot_object(".cache/huggingface/file.lock")
    assert not is_canonical_lerobot_object("videos/unknown/chunk-000/file-000.mp4")


def test_import_cli_only_accepts_a_local_source_directory() -> None:
    help_text = build_parser().format_help()

    assert "--source-dir" in help_text
    assert "--oss-uri" not in help_text


def test_find_local_source_root_accepts_revision_or_single_parent(tmp_path: Path) -> None:
    revision = tmp_path / "repository" / "revision"
    info = revision / "meta/info.json"
    info.parent.mkdir(parents=True)
    info.write_bytes(_info())

    assert find_local_source_root(revision) == revision
    assert find_local_source_root(revision.parent) == revision
    assert validate_source_profile(revision)["robot_type"] == "unitree_g1"


def test_find_local_source_root_translates_a_windows_drive_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    expected = Path("/mnt/e/dataset/revision")
    monkeypatch.setattr(Path, "is_file", lambda self: self == expected / "meta/info.json")
    monkeypatch.setattr(
        "hc_data_platform.tools.lerobot_unitree_g1_import.validate_source_profile",
        lambda source: {"source": str(source)},
    )

    assert find_local_source_root(Path(r"E:\dataset\revision")) == expected
