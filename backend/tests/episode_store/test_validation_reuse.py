import os
from unittest.mock import patch

import pytest

from hc_data_platform.episode_store import validation
from tests.episode_store.test_writer import build_rgbd_package


def test_unchanged_rgbd_package_skips_second_hevc_decode_but_changed_bytes_do_not(tmp_path):
    root, source, manifest, _ = build_rgbd_package(tmp_path)
    with patch.object(validation.av, "open", side_effect=AssertionError("unexpected decode")):
        assert validation.validate_package(root, source) == manifest
    video = next(root.rglob("*.mp4"))
    times = video.stat()
    original = video.read_bytes()
    video.write_bytes(b"\x00" * len(original))
    # Matching length and restored mtime are insufficient to reuse validation.
    os.utime(video, ns=(times.st_atime_ns, times.st_mtime_ns))
    with pytest.raises(Exception):
        validation.validate_package(root, source)


def test_new_or_replaced_metadata_invalidates_verified_package(tmp_path):
    root, source, _, _ = build_rgbd_package(tmp_path)
    info = root / "meta/info.json"
    info.write_text('{"codebase_version":"wrong"}')
    with pytest.raises(ValueError, match="LeRobot"):
        validation.validate_package(root, source)
