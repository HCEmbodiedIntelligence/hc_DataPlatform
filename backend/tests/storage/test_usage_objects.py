import sys
from types import SimpleNamespace


from hc_data_platform.storage import usage_objects
from hc_data_platform.storage.cache_overflow import CacheOverflow
from hc_data_platform.storage.local_cache import CacheRoutingBucket


def item(key, *, directory=False, size=1):
    return SimpleNamespace(key=key, size=size, last_modified=1, is_prefix=lambda: directory)


def test_directory_listing_keeps_loose_objects_and_all_child_keys(monkeypatch):
    def listing(bucket, prefix, delimiter="", **kwargs):
        if delimiter:
            return iter([item("p/a", size=0), item("p/a/", directory=True),
                         item("p/b/", directory=True), item("p/c")])
        return iter([item(prefix), item(prefix + "object")])
    monkeypatch.setitem(sys.modules, "oss2", SimpleNamespace(ObjectIteratorV2=listing))
    keys = [o.key for o in usage_objects.iter_oss_objects(None, "p/") if not o.key.endswith("/")]
    assert keys == ["p/a", "p/a/object", "p/b/object", "p/c"]


def test_high_fanout_uses_one_complete_listing(monkeypatch):
    calls = []
    def listing(bucket, prefix, delimiter="", **kwargs):
        calls.append(delimiter)
        if delimiter:
            return (item(f"p/{n}/", directory=True) for n in range(40))
        return iter([item("p/actual")])
    monkeypatch.setitem(sys.modules, "oss2", SimpleNamespace(ObjectIteratorV2=listing))
    assert [o.key for o in usage_objects.iter_oss_objects(None, "p/")] == ["p/actual"]
    assert calls == ["/", ""]


def test_overflow_uses_object_size_and_excludes_missing_payloads(tmp_path, monkeypatch):
    remote = SimpleNamespace(bucket_name="bucket", key_prefix="platform/", key_layout=None, _delegate=None)
    provided = []
    overflow = CacheOverflow(str(tmp_path/"mount"), 0)
    monkeypatch.setattr(overflow, "check_mount", lambda: None)
    for n in (1, 2):
        token = str(n) * 32
        directory = tmp_path/str(n)
        directory.mkdir()
        (directory/"payload").symlink_to(overflow.root/token[:2]/token)
        provided.append(item("episode-previews/"+str(n), size=999))
    regular = tmp_path/"3"
    regular.mkdir()
    (regular/"payload").write_bytes(b"local")
    provided.append(item("episode-previews/3", size=999))
    def cached_objects(*, sweep):
        assert not sweep
        return provided

    cache = SimpleNamespace(overflow=overflow, directory=lambda key: tmp_path/key.split("/")[-1],
                            listing=cached_objects)
    bucket = CacheRoutingBucket(remote, cache)
    monkeypatch.setattr(usage_objects, "iter_oss_objects",
                        lambda *_: iter([item("platform/spill/11/"+"1"*32,size=123)]))
    index, cloud = usage_objects.oss_usage_objects(SimpleNamespace(_bucket=bucket),
                                                   overflow_uri="oss://bucket/platform/spill")
    assert index.head("episode-previews/1").physical_bytes == cloud[0].physical_bytes == 123
    assert index.head("episode-previews/2") is None
    assert index.head("episode-previews/3").physical_bytes == 5
    assert usage_objects.overflow_prefix(remote, "oss://other/platform/spill") is None
    assert usage_objects.overflow_prefix(remote, "oss://bucket/platform-else/spill") is None
    fallback, _ = usage_objects.oss_usage_objects(SimpleNamespace(_bucket=bucket))
    assert fallback.head("episode-previews/1") is None


def test_ranges_include_pivot_unregistered_and_unicode_objects(monkeypatch):
    hints = [f"episodes/{n:04}" for n in range(1536)]
    keys = sorted(["p/episodes/", "p/episodes/!", "p/episodes/0512",
                   "p/episodes/0512/+", "p/episodes/1024/", "p/episodes/场景"])
    afters = []

    def listing(bucket, prefix, delimiter="", start_after="", **kwargs):
        if delimiter:
            return iter([item(prefix + "episodes/", directory=True)])
        afters.append(start_after)
        return (item(key) for key in keys if key > start_after)
    monkeypatch.setitem(sys.modules, "oss2", SimpleNamespace(ObjectIteratorV2=listing))
    assert [obj.key for obj in usage_objects.iter_oss_objects(None, "p/", hints)] == keys
    assert sorted(afters) == ["", "p/episodes/0512", "p/episodes/1024"]
