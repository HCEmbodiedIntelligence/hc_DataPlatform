import hashlib

import pytest

from hc_data_platform.ingest.ports import InMemoryObjectStorage
from hc_data_platform.lerobot_imports.mcap_processing import _publish_derived


def test_derived_files_publish_content_addressed_in_input_order(tmp_path):
    root = tmp_path / "derived"
    files = []
    for name, body in (("a.json", b"a"), ("videos/head.mp4", b"video" * 1000), ("z.parquet", b"z")):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        files.append(path)
    storage = InMemoryObjectStorage()
    beats = []
    published = _publish_derived(storage, "prefix", root, files, lambda: beats.append(1))
    assert [path for path, *_ in published] == files
    for path, key, size, digest, _ in published:
        assert digest == hashlib.sha256(path.read_bytes()).hexdigest() and size == path.stat().st_size
        assert key == f"prefix/{digest}/{path.relative_to(root).as_posix()}"
        assert b"".join(storage.read_chunks(key)) == path.read_bytes()
    assert beats
    # A retry reuses the identical objects instead of failing or rewriting them.
    assert _publish_derived(storage, "prefix", root, files, lambda: None) == published


def test_derived_publish_stops_on_cancellation(tmp_path):
    path = tmp_path / "a.bin"
    path.write_bytes(b"x")

    def cancelled():
        raise RuntimeError("activity cancelled")

    with pytest.raises(RuntimeError, match="cancelled"):
        _publish_derived(InMemoryObjectStorage(), "prefix", tmp_path, [path], cancelled)
