import hashlib
import io
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.object_storage.immutable import MultipartImmutableWriter
from hc_data_platform.object_storage.providers import OssObjectStorage
from hc_data_platform.storage.local_cache import CacheError, CacheRoutingBucket, LocalCacheBucket

KEY = "episode-deliveries/project/job/archive.zip"


def test_streamed_immutable_multipart_and_partial_read(tmp_path):
    cache = LocalCacheBucket(tmp_path, "test-secret")
    storage = OssObjectStorage(cache)
    body = b"archive-content" * 600000
    sha = hashlib.sha256(body).hexdigest()
    MultipartImmutableWriter(storage).write_immutable(
        KEY, io.BytesIO(body), size=len(body), sha256=sha
    )
    assert storage.head(KEY).size == len(body)
    assert b"".join(storage.read_chunks(KEY)) == body
    response = cache.get_object(KEY, byte_range=(10, 29))
    assert response.read() == body[10:30]
    assert response.read() == b""
    response.close()
    with pytest.raises(CacheError):
        cache.put_object(KEY, b"overwrite", headers={"x-oss-forbid-overwrite": "true"})
    assert not list((tmp_path / ".uploads").iterdir())


def test_expiry_uses_reads_not_capacity_inspection(tmp_path):
    now = [1800000000.0]
    cache = LocalCacheBucket(tmp_path, "test", ttl_days=30, clock=lambda: now[0])
    cache.put_object(KEY, b"zip")
    now[0] += 29 * 86400
    cache.head_object(KEY)
    cache.listing()
    now[0] += 86401
    assert cache.sweep(force=True) == 1
    assert cache.listing() == []
    cache.put_object(KEY, b"zip")
    now[0] += 29 * 86400
    cache.get_object(KEY).close()
    now[0] += 2 * 86400
    assert cache.sweep(force=True) == 0
    with pytest.raises(ValueError):
        cache.put_object("raw/source.mcap", b"raw")
    with pytest.raises(ValueError):
        cache.put_object("episode-deliveries/../source", b"bad")


def test_signed_http_range_tampering_and_expiry(tmp_path, monkeypatch):
    from hc_data_platform.core.config import Settings
    from hc_data_platform.storage import local_cache_http

    settings = Settings(
        object_store_cache_root=str(tmp_path),
        data_source_credential_key="test-key-for-local-cache-signing",
    )
    monkeypatch.setattr(local_cache_http, "get_settings", lambda: settings)
    cache = LocalCacheBucket(tmp_path, "test-key-for-local-cache-signing")
    cache.put_object(KEY, b"0123456789")
    app = FastAPI()
    app.include_router(local_cache_http.router)
    with TestClient(app) as client:
        url = cache.sign_url("GET", KEY, 300)
        response = client.get(url, headers={"Range": "bytes=2-5"})
        assert response.status_code == 206 and response.content == b"2345"
        assert response.headers["content-range"] == "bytes 2-5/10"
        assert client.head(url).status_code == 200
        named = cache.sign_url(
            "GET",
            KEY,
            300,
            params={
                "response-content-disposition": "attachment; filename*=UTF-8''frozen-export.zip"
            },
        )
        assert "frozen-export.zip" in client.get(named).headers["content-disposition"]
        assert client.get(named.replace("frozen-export.zip", "changed.zip")).status_code == 403
        assert client.get(url.replace("signature=", "signature=bad")).status_code == 403
        parsed = urlsplit(url)
        query = parse_qs(parsed.query)
        assert (
            client.get(
                parsed.path, params={"expires": 1, "signature": query["signature"][0]}
            ).status_code
            == 403
        )
        assert (
            client.get(
                parsed.path, params={"expires": query["expires"][0], "signature": "中文"}
            ).status_code
            == 403
        )


def test_routing_never_writes_cache_to_oss_and_lists_both(tmp_path):
    remote_calls = []

    class Remote:
        bucket_name = "bucket"
        key_prefix = "root/"
        key_layout = None

        def put_object(self, key, body, **kwargs):
            remote_calls.append(key)

        def list_objects_v2(self, **kwargs):
            return SimpleNamespace(
                object_list=[
                    SimpleNamespace(key="raw/source", size=10),
                    SimpleNamespace(key=KEY, size=3),
                ],
                prefix_list=[],
                is_truncated=False,
                next_continuation_token="",
            )

    cache = LocalCacheBucket(tmp_path, "test")
    bucket = CacheRoutingBucket(Remote(), cache)
    bucket.put_object(KEY, b"zip")
    bucket.put_object("raw/source", b"raw")
    assert remote_calls == ["raw/source"]
    assert bucket.local_key("oss://bucket/root/" + KEY) == KEY
    page = bucket.list_objects_v2(max_keys=1)
    assert [x.key for x in page.object_list] == ["raw/source"]
    assert page.is_truncated
    page = bucket.list_objects_v2(max_keys=1, continuation_token=page.next_continuation_token)
    assert [x.key for x in page.object_list] == [KEY]
    assert not page.is_truncated
    assert [x.key for x in bucket.list_objects_v2(prefix="episode-deliveries/").object_list] == [
        KEY
    ]
