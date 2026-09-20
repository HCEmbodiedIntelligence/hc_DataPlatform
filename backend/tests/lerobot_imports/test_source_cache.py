import os
import time
from pathlib import Path

import pytest

from hc_data_platform.lerobot_imports.cache import SourceCache


def _entry(root: Path, key: str, size: int, age: int = 0) -> Path:
    path = root / (key * 64)
    path.mkdir(parents=True)
    (path / "source.mp4").write_bytes(b"v" * size)
    os.utime(path, (time.time() - age, time.time() - age))
    return path


def test_cache_admission_evicts_idle_lru_and_preserves_active_sources(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path, max_bytes=100, ttl_seconds=60)
    old = _entry(tmp_path, "a", 40, age=30)
    active = _entry(tmp_path, "b", 50)
    with cache.pin("b" * 64), cache.reserve(40):
        assert active.is_dir()
        assert not old.exists()
        (active / "metadata").write_bytes(b"x" * 40)
    assert cache.run_once() == 0


def test_cache_refuses_to_delete_active_files_to_meet_quota(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path, max_bytes=100, ttl_seconds=60)
    active = _entry(tmp_path, "a", 80)
    with (
        cache.pin("a" * 64),
        pytest.raises(RuntimeError, match="SOURCE_CACHE_LIMIT_EXCEEDED"),
        cache.reserve(30),
    ):
        pytest.fail("must reject the download before allocating more space")
    assert (active / "source.mp4").stat().st_size == 80


def test_expired_cache_sweep_never_removes_raw_or_unrecognized_directories(tmp_path: Path) -> None:
    cache = SourceCache(tmp_path, max_bytes=100, ttl_seconds=10)
    expired = _entry(tmp_path, "a", 20, age=30)
    raw = tmp_path / "raw"
    raw.mkdir()
    (raw / "original").write_bytes(b"original")
    assert cache.run_once() == 1
    assert not expired.exists()
    assert (raw / "original").read_bytes() == b"original"
