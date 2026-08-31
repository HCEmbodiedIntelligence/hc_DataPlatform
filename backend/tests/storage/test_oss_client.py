from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from typing import Any

import pytest

from hc_data_platform.storage.oss_client import OssBotoCompatClient, OssClientError


class NativeOssError(Exception):
    status = 412
    code = "FileAlreadyExists"


class FakeOssBucket:
    bucket_name = "hc-oss-test"

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.last_sign: dict[str, Any] = {}

    def put_object(self, key: str, body: Any, *, headers: dict[str, str] | None) -> Any:
        content = body.read() if hasattr(body, "read") else bytes(body)
        if headers and headers.get("x-oss-forbid-overwrite") == "true" and key in self.objects:
            raise NativeOssError("object exists")
        self.objects[key] = (content, dict(headers or {}))
        return SimpleNamespace(etag="ABC123")

    def get_object_meta(self, key: str) -> Any:
        content, headers = self.objects[key]
        return SimpleNamespace(
            content_length=len(content),
            etag="ABC123",
            last_modified=1_700_000_000,
            headers={**headers, "Content-Length": str(len(content))},
        )

    def get_object(self, key: str) -> Any:
        content, _ = self.objects[key]
        body = BytesIO(content)
        body.content_length = len(content)  # type: ignore[attr-defined]
        body.headers = {"Content-Length": str(len(content))}  # type: ignore[attr-defined]
        return body

    def list_objects_v2(self, **kwargs: Any) -> Any:
        prefix = kwargs["prefix"]
        return SimpleNamespace(
            object_list=[
                SimpleNamespace(
                    key=key,
                    size=len(content),
                    etag="ABC123",
                    last_modified=1_700_000_000,
                )
                for key, (content, _) in sorted(self.objects.items())
                if key.startswith(prefix)
            ],
            is_truncated=False,
            next_continuation_token="",
        )

    def sign_url(self, method: str, key: str, expires: int, **kwargs: Any) -> str:
        self.last_sign = {"method": method, "key": key, "expires": expires, **kwargs}
        return f"https://hc-oss-test.oss-cn-hangzhou.aliyuncs.com/{key}"


def test_oss_client_translates_immutable_put_metadata_get_and_listing() -> None:
    bucket = FakeOssBucket()
    client = OssBotoCompatClient(bucket)

    response = client.put_object(
        Bucket="hc-oss-test",
        Key="artifacts/report.json",
        Body=b"payload",
        ContentType="application/json",
        Metadata={"sha256": "digest"},
        IfNoneMatch="*",
    )

    assert response["ETag"] == '"ABC123"'
    head = client.head_object(Bucket="hc-oss-test", Key="artifacts/report.json")
    assert head["ContentLength"] == 7
    assert head["Metadata"] == {"sha256": "digest"}
    downloaded = client.get_object(Bucket="hc-oss-test", Key="artifacts/report.json")
    assert downloaded["Body"].read() == b"payload"  # type: ignore[union-attr]
    listing = client.list_objects_v2(Bucket="hc-oss-test", Prefix="artifacts/")
    assert listing["Contents"][0]["Key"] == "artifacts/report.json"  # type: ignore[index]

    with pytest.raises(OssClientError) as captured:
        client.put_object(
            Bucket="hc-oss-test",
            Key="artifacts/report.json",
            Body=b"different",
            IfNoneMatch="*",
        )
    assert captured.value.response["Error"]["Code"] == "PreconditionFailed"


def test_oss_client_maps_boto_presign_parameters_to_native_oss() -> None:
    bucket = FakeOssBucket()
    client = OssBotoCompatClient(bucket)

    url = client.generate_presigned_url(
        "upload_part",
        Params={
            "Bucket": "hc-oss-test",
            "Key": "raw/recording.mcap",
            "UploadId": "upload-1",
            "PartNumber": 3,
        },
        ExpiresIn=900,
        HttpMethod="PUT",
    )

    assert url.startswith("https://hc-oss-test.")
    assert bucket.last_sign == {
        "method": "PUT",
        "key": "raw/recording.mcap",
        "expires": 900,
        "params": {"uploadId": "upload-1", "partNumber": "3"},
        "slash_safe": True,
    }
