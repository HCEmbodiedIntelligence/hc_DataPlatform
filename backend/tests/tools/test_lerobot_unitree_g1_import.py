from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hc_data_platform.tools.hf_unitree_g1_to_mcap import CAMERAS
from hc_data_platform.tools.lerobot_unitree_g1_import import (
    OssSource,
    find_local_source_root,
    is_canonical_lerobot_object,
    materialize_oss_source,
    parse_oss_source_uri,
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
            "features": features,
        }
    ).encode()


class _FakeBody(io.BytesIO):
    pass


class _FakeBucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def list_objects_v2(self, *, prefix: str, continuation_token: str, max_keys: int):
        assert continuation_token == ""
        assert max_keys == 1000
        items = [
            SimpleNamespace(key=key, size=len(value))
            for key, value in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return SimpleNamespace(
            object_list=items,
            is_truncated=False,
            next_continuation_token="",
        )

    def get_object(self, key: str) -> _FakeBody:
        return _FakeBody(self.objects[key])


def test_parse_oss_source_uri_rejects_credentials_and_ambiguous_roots() -> None:
    assert parse_oss_source_uri("oss://bucket/oss_test/unitree") == OssSource(
        bucket="bucket",
        prefix="oss_test/unitree",
    )
    with pytest.raises(ValueError):
        parse_oss_source_uri("oss://user:secret@bucket/source")
    with pytest.raises(ValueError):
        parse_oss_source_uri("oss://bucket/source?Signature=secret")


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


def test_materialize_oss_source_ignores_transient_download_files(tmp_path: Path) -> None:
    root = "oss_test/unitree/revision/"
    objects = {
        f"{root}meta/info.json": _info(),
        f"{root}meta/episodes/chunk-000/file-000.parquet": b"episodes",
        f"{root}data/chunk-000/file-000.parquet": b"data",
        f"{root}data/chunk-000/file-000.parquet.part": b"partial",
        f"{root}.cache/huggingface/download.lock": b"",
    }

    source = materialize_oss_source(
        _FakeBucket(objects),
        source=OssSource(bucket="bucket", prefix="oss_test/unitree"),
        cache_dir=tmp_path,
    )

    assert (source / "meta/info.json").read_bytes() == _info()
    assert (source / "data/chunk-000/file-000.parquet").read_bytes() == b"data"
    assert not (source / "data/chunk-000/file-000.parquet.part").exists()
    assert not (source / ".cache").exists()


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
