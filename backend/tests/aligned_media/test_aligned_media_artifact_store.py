from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path

from hc_data_platform.aligned_media.artifact_store import S3AlignedMediaArtifactStore
from hc_data_platform.aligned_media.models import (
    AlignedMediaGenerationRequestV1,
    AlignedMediaScopeV1,
    AlignmentStagingArtifactV1,
    EncodedAlignedMediaV1,
)


class PreconditionFailed(Exception):
    response = {"Error": {"Code": "PreconditionFailed"}}


class MemoryS3:
    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str, dict[str, str]]] = {}
        self.puts: list[str] = []
        self.deleted: list[str] = []
        self.presigned: list[tuple[dict[str, str], int]] = []

    def put_object(self, **kwargs: object) -> dict[str, str]:
        key = str(kwargs["Key"])
        self.puts.append(key)
        if key in self.objects and kwargs.get("IfNoneMatch") == "*":
            raise PreconditionFailed
        body = kwargs["Body"]
        payload = body.read() if hasattr(body, "read") else bytes(body)  # type: ignore[arg-type]
        media_type = str(kwargs["ContentType"])
        metadata = dict(kwargs["Metadata"])  # type: ignore[arg-type]
        self.objects[key] = (payload, media_type, metadata)
        return {"ETag": f'"{hashlib.md5(payload).hexdigest()}"'}  # noqa: S324

    def head_object(self, **kwargs: object) -> dict[str, object]:
        payload, media_type, metadata = self.objects[str(kwargs["Key"])]
        return {
            "ContentLength": len(payload),
            "ContentType": media_type,
            "Metadata": metadata,
            "ETag": f'"{hashlib.md5(payload).hexdigest()}"',  # noqa: S324
        }

    def delete_object(self, **kwargs: object) -> dict[str, object]:
        key = str(kwargs["Key"])
        self.deleted.append(key)
        self.objects.pop(key, None)
        return {}

    def generate_presigned_url(
        self, client_method: str, Params: dict[str, str], ExpiresIn: int
    ) -> str:
        assert client_method == "get_object"
        self.presigned.append((Params, ExpiresIn))
        return f"https://s3.invalid/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"

    def get_object(self, **kwargs: object) -> dict[str, object]:
        return {"Body": BytesIO(self.objects[str(kwargs["Key"])][0])}

    def list_objects_v2(self, **kwargs: object) -> dict[str, object]:
        del kwargs
        return {"Contents": [], "IsTruncated": False}


def test_s3_publication_is_immutable_retry_safe_authorized_and_exactly_deleted(
    tmp_path: Path,
) -> None:
    client = MemoryS3()
    store = S3AlignedMediaArtifactStore(client, "media-bucket")
    media = tmp_path / "media.mp4"
    media.write_bytes(b"immutable-canonical-mp4")
    now = datetime(2026, 8, 31, tzinfo=timezone.utc)
    scope = AlignedMediaScopeV1(
        organization_id="organization-1",
        project_id="project-1",
        region_code="cn-test",
    )
    request = AlignedMediaGenerationRequestV1(
        project_id="project-1",
        dataset_id="dataset-1",
        rollout_id="rollout-1",
        expected_dataset_version=7,
        camera_id="/camera/front/image",
        source_sha256="a" * 64,
        alignment=AlignmentStagingArtifactV1(
            object_key="staging/alignment.arrow",
            content_sha256="b" * 64,
            size_bytes=10,
            row_count=1_800,
            alignment_version="be07-align/1:causal-30hz-v1",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        ),
    )
    encoded = EncodedAlignedMediaV1(
        file_uri=media.resolve().as_uri(),
        frame_count=1_800,
        duration_seconds=60,
        width=1280,
        height=720,
        placeholder_count=0,
        first_timestamp_ns=0,
    )

    first = store.publish(
        scope=scope,
        request=request,
        artifact_key="c" * 64,
        publication_token="publication-1",
        encoded=encoded,
    )
    second = store.publish(
        scope=scope,
        request=request,
        artifact_key="c" * 64,
        publication_token="publication-1",
        encoded=encoded,
    )

    assert second == first
    assert len(client.objects) == 2
    assert set(client.objects) == {item.key for item in first.objects}
    assert client.puts[0].endswith("/publication-intent.json")
    assert client.puts[1].endswith("/media.mp4")
    assert first.media_object_key.endswith("/media.mp4")
    assert "/datasets/dataset-1/versions/7/rollouts/rollout-1/cameras/" in (first.media_object_key)
    assert store.verify_publication(first) is True
    url = store.authorize_object(first.media_object_key, expires_in=timedelta(minutes=5))
    assert url.endswith("expires=300")
    assert client.presigned == [
        (
            {
                "Bucket": "media-bucket",
                "Key": first.media_object_key,
                "ResponseCacheControl": "private,max-age=300,immutable",
                "ResponseContentType": "video/mp4",
            },
            300,
        )
    ]

    assert store.delete_exact(first.objects) == first.total_bytes
    assert client.objects == {}
    assert client.deleted == [item.key for item in first.objects]
