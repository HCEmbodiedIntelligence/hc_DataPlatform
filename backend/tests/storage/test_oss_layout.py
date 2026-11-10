import pytest

from hc_data_platform.storage.oss_layout import PostgresOssPrefixAliases, compact_alias
from hc_data_platform.storage.oss_prefix import PrefixedOssBucket

from .test_oss_prefix import Bucket


class Aliases(PostgresOssPrefixAliases):
    def __init__(self):
        self.rows = {}
        self._known = {}

    def all(self):
        self._known = dict(self.rows)
        return self._known

    def register(self, logical, physical):
        if any(p == physical and l != logical for l, p in self.rows.items()):
            raise ValueError("physical prefix already belongs to another logical owner")
        self.rows[logical] = physical


def episode(scope="id-org"):
    return (
        f"episodes/{scope}/id-project/id-global/id-date/id-robot/id-task/id-episode/id-dv_"
        + "a" * 64
    )


def test_compact_writes_keep_logical_manifests_and_signed_reads_compatible():
    native, aliases = Bucket(), Aliases()
    scoped = PrefixedOssBucket(native, "hc_dataplatform", key_layout=aliases)
    key = episode() + "/meta/info.json"
    scoped.put_object(key, b"unchanged manifest")
    short = "episodes/dv_" + "a" * 64 + "/meta/info.json"
    assert native.objects["hc_dataplatform/" + short] == b"unchanged manifest"
    assert scoped.get_object(key) == b"unchanged manifest"
    assert scoped.get_object("oss://test-bucket/hc_dataplatform/" + key) == b"unchanged manifest"
    assert "/hc_dataplatform/" + short + "?" in scoped.sign_url("GET", key, 60)
    assert [o.key for o in scoped.list_objects_v2(prefix=episode() + "/meta/").object_list] == [key]
    assert [o.key for o in scoped.list_objects_v2(prefix="episodes/id-org/").object_list] == [key]
    assert scoped.list_objects_v2(prefix="episodes/another-org/").object_list == []
    scoped.copy_object("test-bucket", key, episode() + "/copy.json")
    assert scoped.get_object(episode() + "/copy.json") == b"unchanged manifest"
    scoped.delete_object(key)
    assert "hc_dataplatform/" + short not in native.objects
    assert native.objects["elsewhere/file"] == b"private"


def test_legacy_reads_do_not_register_or_relocate_objects():
    native, aliases = Bucket(), Aliases()
    key = episode() + "/old.json"
    native.objects["hc_dataplatform/" + key] = b"legacy"
    scoped = PrefixedOssBucket(native, "hc_dataplatform", key_layout=aliases)
    assert scoped.get_object(key) == b"legacy"
    assert not aliases.rows
    assert scoped.get_object("raw/a") == b"sample"
    with pytest.raises(ValueError):
        scoped.put_object("../escape", b"bad")


def test_prefix_collision_fails_before_overwriting_a_different_owner():
    native, aliases = Bucket(), Aliases()
    scoped = PrefixedOssBucket(native, "hc_dataplatform", key_layout=aliases)
    scoped.put_object(episode() + "/data", b"first")
    with pytest.raises(ValueError):
        scoped.put_object(episode("id-another-org") + "/data", b"other")
    assert scoped.get_object(episode() + "/data") == b"first"


def test_short_layout_retains_full_version_identity_and_package_relative_paths():
    long = episode()
    assert compact_alias(long + "/data/chunk-000/file-000.parquet") == (
        long,
        "episodes/dv_" + "a" * 64,
    )
    key = "episode-deliveries/" + "a" * 64 + "/ed_" + "b" * 64 + "/" + "c" * 64 + ".zip"
    assert compact_alias(key) == (key, "episode-deliveries/ed_" + "b" * 64 + ".zip")
    assert compact_alias("oss-smoke/test/source.mcap") is None
    assert compact_alias("raw/a") is None
