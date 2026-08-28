from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import pytest

from hc_data_platform.preview.artifact_store import S3PreviewArtifactStore
from hc_data_platform.preview.models import EncodedPreviewArtifactV1


class FakeS3:
    def __init__(self, *, fail_member: str | None = None) -> None:
        self.fail_member = fail_member
        self.objects: dict[str, tuple[bytes, dict[str, str]]] = {}
        self.put_calls: list[str] = []
        self.delete_calls: list[str] = []

    def put_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        self.put_calls.append(key)
        if key.endswith(f"/{self.fail_member}"):
            raise RuntimeError("simulated object-store publication failure")
        body = kwargs["Body"]
        metadata = kwargs["Metadata"]
        assert isinstance(body, bytes)
        assert isinstance(metadata, dict)
        self.objects[key] = (body, metadata)
        return {"ETag": f'"etag-{len(self.put_calls)}"'}

    def head_object(self, **kwargs: object) -> Mapping[str, object]:
        body, metadata = self.objects[str(kwargs["Key"])]
        return {
            "ContentLength": len(body),
            "Metadata": metadata,
            "ETag": '"verified"',
        }

    def delete_object(self, **kwargs: object) -> Mapping[str, object]:
        key = str(kwargs["Key"])
        self.delete_calls.append(key)
        self.objects.pop(key, None)
        return {}

    def get_object(self, **_: object) -> Mapping[str, object]:
        raise AssertionError("publication test does not read objects")

    def generate_presigned_url(
        self, operation_name: str, *, Params: Mapping[str, str], ExpiresIn: int
    ) -> str:
        del operation_name, Params, ExpiresIn
        return "https://objects.invalid/signed"


def encoded_hls(tmp_path: Path) -> EncodedPreviewArtifactV1:
    directory = tmp_path / "encoded"
    directory.mkdir()
    (directory / "init.mp4").write_bytes(b"init")
    (directory / "segment_00000.m4s").write_bytes(b"segment")
    (directory / "index.m3u8").write_text(
        '#EXTM3U\n#EXT-X-MAP:URI="init.mp4"\nsegment_00000.m4s\n',
        encoding="utf-8",
    )
    return EncodedPreviewArtifactV1(
        artifact_uri=(directory / "index.m3u8").as_uri(),
        duration_seconds=1,
        frame_count=1,
    )


def test_s3_publication_verifies_members_and_uploads_playlist_last(tmp_path: Path) -> None:
    client = FakeS3()

    publication = S3PreviewArtifactStore(client, "preview-bucket").publish(
        project_id="project-1",
        artifact_key="a" * 64,
        encoded=encoded_hls(tmp_path),
    )

    assert client.put_calls[-1].endswith("/index.m3u8")
    assert tuple(item.key for item in publication.objects) == tuple(client.put_calls)
    assert publication.total_bytes == sum(len(item[0]) for item in client.objects.values())
    assert all(item.sha256 for item in publication.objects)


def test_failed_playlist_barrier_deletes_only_members_created_by_that_attempt(
    tmp_path: Path,
) -> None:
    client = FakeS3(fail_member="index.m3u8")

    with pytest.raises(RuntimeError, match="publication failure"):
        S3PreviewArtifactStore(client, "preview-bucket").publish(
            project_id="project-1",
            artifact_key="b" * 64,
            encoded=encoded_hls(tmp_path),
        )

    assert client.put_calls[-1].endswith("/index.m3u8")
    assert set(client.delete_calls) == set(client.put_calls[:-1])
    assert client.objects == {}
