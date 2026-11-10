from types import SimpleNamespace

import pytest

from hc_data_platform.storage.oss_prefix import PrefixedOssBucket


class Bucket:
    bucket_name = "test-bucket"

    def __init__(self):
        self.objects = {"elsewhere/file": b"private", "hc_dataplatform/raw/a": b"sample"}
        self.last_list = None

    def get_object(self, key):
        return self.objects[key]

    def put_object(self, key, data):
        self.objects[key] = data

    def delete_object(self, key):
        del self.objects[key]

    def list_objects_v2(self, prefix, **kwargs):
        self.last_list = (prefix, kwargs)
        return SimpleNamespace(
            object_list=[SimpleNamespace(key=k) for k in self.objects if k.startswith(prefix)],
            prefix_list=[],
            next_continuation_token="opaque-token",
        )

    def sign_url(self, method, key, expires):
        return f"https://test-bucket.example/{key}?method={method}&expires={expires}"

    def copy_object(self, bucket, source, target):
        self.objects[target] = self.objects[source]


def test_operations_are_confined_and_listing_returns_logical_keys():
    real = Bucket()
    scoped = PrefixedOssBucket(real, "hc_dataplatform/")
    assert scoped.get_object("raw/a") == b"sample"
    scoped.put_object("episodes/b", b"result")
    assert real.objects["hc_dataplatform/episodes/b"] == b"result"
    page = scoped.list_objects_v2(prefix="", continuation_token="opaque-token")
    assert {i.key for i in page.object_list} == {"raw/a", "episodes/b"}
    assert real.last_list == ("hc_dataplatform/", {"continuation_token": "opaque-token"})
    scoped.copy_object("test-bucket", "raw/a", "raw/c")
    assert scoped.get_object("raw/c") == b"sample"
    scoped.delete_object("raw/c")
    assert real.objects["elsewhere/file"] == b"private"
    assert "/hc_dataplatform/raw/a?" in scoped.sign_url("GET", "raw/a", 60)


@pytest.mark.parametrize(
    "key", ["/elsewhere/file", "../elsewhere/file", "raw/../file", "a//b", "a\\b", "a\x00b"]
)
def test_rejects_path_escape_for_reads_writes_and_lists(key):
    scoped = PrefixedOssBucket(Bucket(), "hc_dataplatform")
    with pytest.raises(ValueError):
        scoped.put_object(key, b"x")
    with pytest.raises(ValueError):
        scoped.get_object(key)
    with pytest.raises(ValueError):
        scoped.list_objects_v2(prefix=key)


def test_foreign_listing_or_cross_bucket_copy_fails_closed():
    real = Bucket()
    real.list_objects_v2 = lambda **kwargs: SimpleNamespace(
        object_list=[SimpleNamespace(key="outside/file")]
    )
    scoped = PrefixedOssBucket(real, "hc_dataplatform")
    with pytest.raises(ValueError):
        scoped.list_objects_v2()
    with pytest.raises(ValueError):
        scoped.copy_object("another-bucket", "raw/a", "raw/b")
    with pytest.raises(AttributeError):
        scoped.delete_bucket()
