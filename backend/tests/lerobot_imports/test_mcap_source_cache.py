import hashlib
import json
from types import SimpleNamespace

from hc_data_platform.lerobot_imports import mcap_source_cache as cache


def test_registered_mcap_reuses_one_read_and_checks_source_version_and_cache_file(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "local_cache_for", lambda _: SimpleNamespace(root=tmp_path))
    meta = SimpleNamespace(size=6, etag="version-1")
    data = [b"source"]
    requests = []
    def chunks(key):
        requests.append(key)
        yield from data
    storage = SimpleNamespace(head=lambda _: meta, read_chunks=chunks)
    scope = ("org", "project", "region")
    def register():
        return cache.registration_digest(storage, scope, "key", meta, cacheable=True)
    digest, size = register()
    assert (digest, size) == (hashlib.sha256(b"source").hexdigest(), 6)
    original = {"object_key": "key", "size": 6, "sha256": digest}
    target = tmp_path / "worker.mcap"
    assert not (tmp_path / "registered-mcap").exists()
    assert not cache.copy_registered_input(storage, scope, original, target)
    identity = hashlib.sha256(json.dumps([*scope, "key", size]).encode()).hexdigest()
    directory = tmp_path / "registered-mcap" / identity
    directory.mkdir(parents=True)
    source = directory / "source.mcap"
    source.write_bytes(data[0])
    (directory / "receipt.json").write_text(json.dumps([digest, meta.etag, cache._signature(source)]))
    assert cache.copy_registered_input(storage, scope, original, target)
    assert target.read_bytes() == b"source"
    assert register() == (digest, size)
    assert requests == ["key"]
    assert not cache.copy_registered_input(storage, ("org", "other", "region"), original, target)
    next((tmp_path / "registered-mcap").glob("*/source.mcap")).write_bytes(b"broken")
    assert not cache.copy_registered_input(storage, scope, original, target)
    assert register() == (digest, size)
    meta.etag = "version-2"
    data[:] = [b"second"]
    assert not cache.copy_registered_input(storage, scope, original, target)
    assert register() == (hashlib.sha256(b"second").hexdigest(), 6)
    assert requests == ["key"] * 3
