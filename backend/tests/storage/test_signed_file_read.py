from io import BytesIO
from types import SimpleNamespace
from unittest.mock import MagicMock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hc_data_platform.security.http import require_auth_context
from hc_data_platform.storage import local_cache_http


def test_signed_read_streams_exact_bytes_and_discards_upstream_headers(monkeypatch):
    app = FastAPI()
    app.include_router(local_cache_http.router)
    app.state.settings = SimpleNamespace(
        object_store_provider="oss", object_store_bucket="dataset",
        object_store_public_endpoint="https://oss-cn-beijing.aliyuncs.com",
    )
    app.dependency_overrides[require_auth_context] = lambda: object()
    data = b"example archive"
    upstream = BytesIO(data)
    upstream.status = 200
    upstream.length = len(data)
    conn = MagicMock()
    conn.getresponse.return_value = upstream
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr(local_cache_http, "HTTPSConnection", connect)
    host = "dataset.oss-cn-beijing.aliyuncs.com"
    path = "/folder/data%20file?Expires=100&OSSAccessKeyId=test&Signature=test%2B"
    client = TestClient(app)
    response = client.post("/api/v1/storage-downloads/content", json={"url": "https://" + host + path})
    assert response.content == data
    assert int(response.headers["Content-Length"]) == len(data)
    connect.assert_called_once_with(host, timeout=90)
    conn.request.assert_called_once_with("GET", path)
    assert upstream.closed
    connect.reset_mock()
    declined = client.post("/api/v1/storage-downloads/content", json={"url": "https://other.example/file"})
    assert declined.status_code == 400
    connect.assert_not_called()

def test_archive_range_requires_an_exact_partial_response(monkeypatch):
    app = FastAPI()
    app.include_router(local_cache_http.router)
    app.state.settings = SimpleNamespace(
        object_store_provider="oss", object_store_bucket="dataset",
        object_store_public_endpoint="https://oss-cn-beijing.aliyuncs.com",
    )
    app.dependency_overrides[require_auth_context] = lambda: object()
    conn = MagicMock()
    connect = MagicMock(return_value=conn)
    monkeypatch.setattr(local_cache_http, "HTTPSConnection", connect)
    path = "/archive.zip?Expires=100&OSSAccessKeyId=test&Signature=test"
    body = {"url": "https://dataset.oss-cn-beijing.aliyuncs.com" + path, "byte_range": [4, 7, 12]}
    with TestClient(app) as client:
        for status, header, expected in [(206, "bytes 4-7/12", 200), (200, None, 502), (206, "bytes 0-3/12", 502)]:
            upstream = BytesIO(b"abcd")
            upstream.status = status
            upstream.length = 4
            upstream.getheader = lambda name: header
            conn.getresponse.return_value = upstream
            response = client.post("/api/v1/storage-downloads/content", json=body)
            assert response.status_code == expected
            if expected == 200:
                assert response.content == b"abcd"
            conn.request.assert_called_with("GET", path, headers={"Range": "bytes=4-7"})
            assert upstream.closed
        connect.reset_mock()
        body["byte_range"] = [0, 8 * 1024**2, 9 * 1024**2]
        assert client.post("/api/v1/storage-downloads/content", json=body).status_code == 422
        connect.assert_not_called()
